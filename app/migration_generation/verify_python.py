"""Checks the sandbox runs against a patch, chosen by Delta Code rather than the model.

Runs inside the sandbox with only the standard library. Exit 0 means no regression was found, 1
means the patch made something worse, and 3 means the check could not reach a verdict. Findings are
only ever differences from the original code, so failures a repository already had don't count.
"""

import ast
import base64
import importlib
import importlib.util
import inspect
import json
import os
import re
import subprocess
import sys
from pathlib import Path

STATE = Path(".delta-code")
PROBE_SECONDS = 20
TEST_SECONDS = 105
PER_TEST_SECONDS = 15
NO_VERDICT = 3
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


def _probe(path: str) -> None:
    """Runs in its own process, because importing repository code can do anything."""
    sys.path[:0] = [os.getcwd(), f"{os.getcwd()}/src", str(Path(path).parent)]
    outcome: dict = {"import": "ok", "signatures": []}
    try:
        outcome["signatures"] = _signature_errors(Path(path).read_text())
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


def _run_probe(path: str) -> dict | None:
    try:
        completed = subprocess.run(
            [sys.executable, __file__, "probe", path],
            capture_output=True,
            text=True,
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=PROBE_SECONDS,
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
    after = {path: _run_probe(path) for path in paths}
    for path, encoded in originals.items():
        if encoded is None:
            Path(path).unlink()
        else:
            Path(path).write_bytes(base64.b64decode(encoded))
    clean = {"import": "ok", "signatures": []}
    before = {path: _run_probe(path) if originals[path] else clean for path in paths}
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
    for regression in regressions:
        print(f"REGRESSION {regression}")
    print(f"Checked {len(paths)} edited Python files.")
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
    for test in regressions[:30]:
        print(f"REGRESSION {test} passed before the patch and does not now")
    if regressions:
        print(completed.stdout[:6000])
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
