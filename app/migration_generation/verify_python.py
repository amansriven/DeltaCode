"""Checks the sandbox runs against a patch, chosen by Delta Code rather than the model.

Runs inside the sandbox with only the standard library. Exit 0 means no regression was found, 1
means the patch made something worse, and 3 means the check could not reach a verdict. Findings are
only ever differences from the original code, so failures a repository already had don't count.
"""

import ast
import base64
import builtins
import importlib
import importlib.util
import inspect
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

STATE = Path(".delta-code")
PROBE_SECONDS = 20
# Inside the sandbox's 120-second command limit, so a slow repository gets a verdict on what ran.
IMPORT_CHECK_SECONDS = 100
TEST_SECONDS = 105
# The same limit the baseline ran under, or a slow test would look like a regression.
PER_TEST_SECONDS = 30
MAX_FAILURE_LOG_CHARS = 3000
MAX_REPORTED_TESTS = 10
NO_VERDICT = 3
MODULE_ATTRIBUTES = {
    "__annotations__",
    "__builtins__",
    "__cached__",
    "__class__",
    "__file__",
    "__loader__",
    "__path__",
    "__spec__",
}
# 0 and 1 mean tests ran. 5 means none were collected, which is what losing them all looks like.
PYTEST_RAN = {0, 1, 5}
PASSED_TEST = re.compile(r"^PASSED (\S+)", re.MULTILINE)


def _environment() -> dict[str, str]:
    # Without a key, as on a machine that merely imports the code.
    environment = {
        key: value
        for key, value in os.environ.items()
        if not re.search("KEY|TOKEN|SECRET|PASSWORD", key)
    }
    root = os.getcwd()
    return {
        **environment,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": f"{root}:{root}/src",
    }


def _first_party(name: str) -> bool:
    return any((Path(base) / name).exists() or (Path(base) / f"{name}.py").exists()
               for base in (".", "src"))


def _imported_names(tree: ast.Module) -> dict[str, str]:
    names = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    names[alias.asname] = alias.name
                else:
                    top = alias.name.split(".")[0]
                    names[top] = top
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                names[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return {
        local: dotted for local, dotted in names.items() if not _first_party(dotted.split(".")[0])
    }


def _dotted_call(function: ast.expr) -> list[str] | None:
    parts: list[str] = []
    while isinstance(function, ast.Attribute):
        parts.insert(0, function.attr)
        function = function.value
    if not isinstance(function, ast.Name):
        return None
    return [function.id, *parts]


def _resolve(dotted: str):
    parts = dotted.split(".")
    for split in range(len(parts), 0, -1):
        try:
            value = importlib.import_module(".".join(parts[:split]))
        except ImportError:
            continue
        for part in parts[split:]:
            value = getattr(value, part)
        return value
    raise ImportError(dotted)


def _signature_errors(source: str) -> list[str]:
    """Calls into installed packages whose arguments the installed version would refuse."""
    tree = ast.parse(source)
    names = _imported_names(tree)
    errors = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call = _dotted_call(node.func)
        if not call or call[0] not in names:
            continue
        keywords = [keyword.arg for keyword in node.keywords]
        if None in keywords or any(isinstance(arg, ast.Starred) for arg in node.args):
            continue
        dotted = ".".join([names[call[0]], *call[1:]])
        try:
            signature = inspect.signature(_resolve(dotted))
        except Exception:
            continue
        try:
            signature.bind(*[None] * len(node.args), **dict.fromkeys(keywords))
        except TypeError as exc:
            errors.add(f"{dotted}: {exc}")
    return sorted(errors)


def _undefined_names(source: str) -> list[str]:
    """Names nothing in the file defines. Python only notices when that line finally runs.

    Scopes are ignored on purpose: a name bound anywhere counts as bound, so this only reports
    names that can't resolve, and comparing with the original drops anything it already did.
    """
    tree = ast.parse(source)
    bound = set(dir(builtins)) | MODULE_ATTRIBUTES
    loaded = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names):
            return []
        if isinstance(node, ast.Import | ast.ImportFrom):
            bound.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            bound.add(node.name)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.ExceptHandler | ast.MatchAs | ast.MatchStar) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            bound.add(node.rest)
        elif isinstance(node, ast.Global | ast.Nonlocal):
            bound.update(node.names)
        elif isinstance(node, ast.Name):
            (loaded if isinstance(node.ctx, ast.Load) else bound).add(node.id)
    return sorted(loaded - bound)


def _probe(path: str) -> None:
    """Runs in its own process, because importing repository code can do anything."""
    sys.path[:0] = [os.getcwd(), f"{os.getcwd()}/src", str(Path(path).parent)]
    outcome: dict = {"import": "ok", "signatures": [], "undefined": []}
    try:
        source = Path(path).read_text(encoding="utf-8")
        outcome["undefined"] = _undefined_names(source)
        outcome["signatures"] = _signature_errors(source)
    except (SyntaxError, ValueError) as exc:
        outcome["import"] = f"does not parse: {exc}"
    module = path.removesuffix(".py").removesuffix("/__init__")
    module = module.removeprefix("src/").replace("/", ".")
    if outcome["import"] == "ok":
        try:
            importlib.import_module(module)
        except ModuleNotFoundError as exc:
            outcome["import"] = "ok" if exc.name != module else f"unavailable: {exc}"
        except BaseException as exc:
            outcome["import"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    print("DELTA-CODE-PROBE " + json.dumps(outcome))


def _run_probe(path: str, deadline: float) -> dict | None:
    remaining = deadline - time.monotonic()
    if remaining <= 1:
        return None
    try:
        completed = subprocess.run(
            [sys.executable, __file__, "probe", path],
            capture_output=True,
            text=True,
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=min(PROBE_SECONDS, remaining),
            env=_environment(),
        )
    except subprocess.TimeoutExpired:
        return None
    lines = [line for line in completed.stdout.splitlines() if "DELTA-CODE-PROBE " in line]
    if not lines:
        return None
    return json.loads(lines[-1].split("DELTA-CODE-PROBE ", 1)[1])


def _check_imports() -> int:
    originals = json.loads((STATE / "originals.json").read_text())
    patched = {path: Path(path).read_bytes() for path in originals}
    paths = [path for path in sorted(originals) if path.endswith(".py")]
    deadline = time.monotonic() + IMPORT_CHECK_SECONDS
    # Half for each side: a verdict needs both, so finishing one alone would be wasted.
    halfway = time.monotonic() + IMPORT_CHECK_SECONDS / 2
    after = {path: _run_probe(path, halfway) for path in paths}
    clean = {"import": "ok", "signatures": [], "undefined": []}
    try:
        for path, encoded in originals.items():
            if encoded is None:
                Path(path).unlink()
            else:
                Path(path).write_bytes(base64.b64decode(encoded))
        before = {
            path: _run_probe(path, deadline) if originals[path] is not None else clean
            for path in paths
        }
    finally:
        for path, content in patched.items():
            Path(path).write_bytes(content)
    regressions = []
    for path in paths:
        new, old = after[path], before[path]
        if new is None or old is None:
            continue
        if new["import"] != "ok" and old["import"] == "ok":
            regressions.append(f"{path} no longer imports: {new['import']}")
        for error in sorted(set(new["signatures"]) - set(old["signatures"])):
            regressions.append(f"{path} calls {error}")
        for name in sorted(set(new["undefined"]) - set(old["undefined"])):
            regressions.append(f"{path} uses {name}, which is never imported or defined")
    for regression in regressions:
        print(f"REGRESSION {regression}")
    compared = sum(1 for path in paths if after[path] is not None and before[path] is not None)
    print(f"Compared {compared} of {len(paths)} edited Python files.")
    return 1 if regressions else 0


def _check_tests() -> int:
    baseline = set(json.loads((STATE / "baseline-tests.json").read_text()))
    if not importlib.util.find_spec("pytest"):
        print("pytest is not installed in this sandbox, so there is no verdict.")
        return NO_VERDICT
    has_timeouts = importlib.util.find_spec("pytest_timeout")
    timeout = [f"--timeout={PER_TEST_SECONDS}"] if has_timeouts else []
    try:
        completed = subprocess.run(
            [
                sys.executable, "-m", "pytest", "-q", "-rA", "--tb=short", "-p", "no:cacheprovider",
                "--continue-on-collection-errors", "-o", "addopts=", *timeout,
            ],
            capture_output=True,
            text=True,
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=TEST_SECONDS,
            env=_environment(),
        )
    except subprocess.TimeoutExpired:
        print("Tests did not finish in time, so there is no verdict.")
        return NO_VERDICT
    if completed.returncode not in PYTEST_RAN:
        print(f"pytest stopped with exit code {completed.returncode}, so there is no verdict.")
        return NO_VERDICT
    regressions = sorted(baseline - set(PASSED_TEST.findall(completed.stdout)))
    for test in regressions[:MAX_REPORTED_TESTS]:
        print(f"REGRESSION {test} passed before the patch and does not now")
    if regressions:
        # Failure details sit before pytest's long list of passing tests, not at either end.
        start = max(completed.stdout.find("= FAILURES ="), completed.stdout.find("= ERRORS ="), 0)
        print(completed.stdout[start:][:MAX_FAILURE_LOG_CHARS])
        return 1
    print(f"All {len(baseline)} tests that passed before the patch still pass.")
    return 0


def main() -> int:
    mode = sys.argv[1]
    if mode == "probe":
        _probe(sys.argv[2])
        return 0
    return _check_imports() if mode == "imports" else _check_tests()


if __name__ == "__main__":
    sys.exit(main())
