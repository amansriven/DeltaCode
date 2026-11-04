"""Measure end-to-end migration generation on the frozen backtest cases.

Scores come only from checks the model does not choose: does the patch parse, is the old API
gone, and do tests that passed before still pass. Its own verification commands are recorded but
never counted. Results describe local Docker verification, not the hosted Cloudflare Sandbox.

Repository code runs only in IsolatedContainer. The single networked step installs dependencies
from wheels, which runs no repository code, using requirements re-serialized after parsing.
"""

import argparse
import ast
import hashlib
import json
import re
import statistics
import subprocess
import tempfile
import time
import tomllib
import traceback
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx
from packaging.requirements import InvalidRequirement, Requirement

# isort: split
# app.hardening.limits and the migration service import each other; this order resolves the cycle.
from app.migration_generation.service import MigrationGenerationService

# isort: split
from app.control_plane.models import Guidance, NormalizedChange
from app.hardening.backtest import COMMENT_PREFIXES, _download
from app.hardening.benchmark import _change
from app.hardening.limits import BudgetExceeded, GenerationLimits
from app.ingestion.storage import FilesystemArtifactStore
from app.migration_generation.context import assemble_planning_context
from app.migration_generation.executor import (
    REPOSITORY_ROOT,
    IsolatedContainer,
    LocalDockerSandboxExecutor,
)
from app.migration_generation.intelligence import MigrationIntelligence, OpenAIMigrationIntelligence
from app.migration_generation.models import GenerationProposal
from app.migration_generation.policy import (
    MAX_BUNDLE_BYTES,
    MAX_BUNDLE_FILES,
    PatchPolicyError,
    contains_secret,
    normalize_repository_path,
)
from app.openai_responses import (
    GPT_4O_INPUT_USD_PER_MILLION,
    GPT_4O_OUTPUT_USD_PER_MILLION,
    OpenAIBudgetExceeded,
    OpenAIResponsesClient,
    ProcessBudget,
)
from app.repository_intelligence.analyzer import MultiLanguageImpactAnalyzer
from app.repository_intelligence.inventory import RepositoryInventoryBuilder
from app.repository_intelligence.models import (
    RepositoryRef,
    RepositorySnapshot,
    RepositoryWorkspace,
)
from app.repository_intelligence.workspace import _walk_entries, workspace_fingerprint

PYTHON_IMAGE = "python:3.12-slim"
NODE_IMAGE = "node:22-slim"
CHARS_PER_TOKEN = 3.5
PROPOSE_MAX_OUTPUT_TOKENS = 6_000
REVIEW_MAX_OUTPUT_TOKENS = 3_000
EXPECTED_REVIEW_OUTPUT_TOKENS = 800
PROMPT_OVERHEAD_CHARS = 1_000
TEST_TIMEOUT_SECONDS = 240
INSTALL_TIMEOUT_SECONDS = 300
TEST_OUTPUT_LIMIT = 2_000_000
PYTEST_OUTCOME = re.compile(r"^(PASSED|FAILED|ERROR) (\S+)", re.MULTILINE)
MANIFEST_NAME = re.compile(r"^requirements[\w.-]*\.txt$")

# Provider guidance an engineer would give, restating each library's published migration notes.
GUIDES = {
    "openai-python-v1": {
        "language": "python",
        "after_requirement": "openai>=1,<2",
        "replacement": r"\.(?:chat\.completions|completions|embeddings)\.create\s*\(",
        "guidance": (
            "openai-python 1.0 removed module-level resources. Create a client with "
            "client = OpenAI() or AsyncOpenAI(). ChatCompletion.create becomes "
            "client.chat.completions.create, Completion.create becomes client.completions.create, "
            "and Embedding.create becomes client.embeddings.create; acreate becomes an awaited "
            "create on AsyncOpenAI. Responses are typed objects, so response['choices'][0] becomes "
            "response.choices[0]. Update the openai requirement to >=1."
        ),
    },
    "selenium-4-locators": {
        "language": "python",
        "after_requirement": "selenium>=4.3,<5",
        "replacement": r"\.find_elements?\s*\(\s*By\.",
        "guidance": (
            "Selenium 4 removed find_element_by_* and find_elements_by_*. Call "
            "find_element(By.ID, value) or find_elements(By.XPATH, value) instead, using By.ID, "
            "By.NAME, By.XPATH, By.CSS_SELECTOR, By.CLASS_NAME, By.TAG_NAME, By.LINK_TEXT, or "
            "By.PARTIAL_LINK_TEXT, and import By from selenium.webdriver.common.by."
        ),
    },
    "numpy-1.24-aliases": {
        "language": "python",
        "after_requirement": "numpy>=1.24",
        "replacement": None,
        "guidance": (
            "NumPy 1.24 removed np.float, np.int, np.bool, np.object, np.str, and np.complex, "
            "which were aliases of Python builtins. Use the builtin float, int, bool, object, "
            "str, or complex, or an explicit scalar type such as np.float64 where precision "
            "matters. Do not change np.float64, np.int32, or similar sized types."
        ),
    },
    "pydantic-v2-validators": {
        "language": "python",
        "after_requirement": "pydantic>=2,<3",
        "replacement": r"@(?:pydantic\.)?(?:field_validator|model_validator)\s*\(",
        "guidance": (
            "Pydantic 2 deprecated @validator and @root_validator. Use @field_validator with "
            "@classmethod, and @model_validator(mode='before'|'after'). pre=True becomes "
            "mode='before'; always=True is replaced by Field(validate_default=True); the values "
            "argument becomes info: ValidationInfo with info.data. An after model validator is an "
            "instance method returning self. Update the pydantic requirement to >=2."
        ),
    },
    "react-18-root": {
        "language": "javascript",
        "after_requirement": None,
        "replacement": r"\b(?:createRoot|hydrateRoot)\s*\(",
        "guidance": (
            "React 18 replaced ReactDOM.render(element, container) with "
            "createRoot(container).render(element), and ReactDOM.hydrate(element, container) with "
            "hydrateRoot(container, element). Import them from react-dom/client. Update react and "
            "react-dom to ^18 if the manifest pins an older version."
        ),
    },
    "openai-node-v4": {
        "language": "javascript",
        "after_requirement": None,
        "replacement": r"\.(?:chat\.completions|completions|embeddings)\.create\s*\(",
        "guidance": (
            "openai-node v4 removed Configuration and OpenAIApi. Use new OpenAI({ apiKey }). "
            "createChatCompletion becomes openai.chat.completions.create, createCompletion becomes "
            "openai.completions.create, and createEmbedding becomes openai.embeddings.create. "
            "Responses are returned directly, so response.data.choices becomes response.choices. "
            "Update the openai dependency to ^4."
        ),
    },
}


@dataclass
class EvalResult:
    id: str
    migration: str
    repository: str
    stage: str = "not_run"
    detail: str | None = None
    flagged_sites: int = 0
    context_bytes: int = 0
    expected_cost_usd: float = 0.0
    worst_case_cost_usd: float = 0.0
    cost_usd: float = 0.0
    seconds: float = 0.0
    edited_files: int = 0
    parses: bool | None = None
    usage_before: int = 0
    usage_after: int | None = None
    flagged_sites_after: int | None = None
    replacement_added: int | None = None
    recommendation: str | None = None
    model_check_status: str | None = None
    baseline_passed: int | None = None
    baseline_exit: int | None = None
    after_passed: int | None = None
    regressions: list[str] = field(default_factory=list)
    new_failures: list[str] = field(default_factory=list)
    human_passed: int | None = None
    human_regressions: int | None = None


def _usage_lines(root: Path, pattern: re.Pattern[str], extensions: list[str]) -> int:
    count = 0
    for _relative, entry_type, value in _walk_entries(root):
        if entry_type != "file" or value.suffix.lower() not in extensions:
            continue
        text = value.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            if pattern.search(line) and not line.lstrip().startswith(COMMENT_PREFIXES):
                count += 1
    return count


def _tree_files(root: Path) -> dict[str, bytes]:
    """The same files the sandbox request bundles, so tests see what verification saw."""
    files: dict[str, bytes] = {}
    for relative, entry_type, value in _walk_entries(root):
        if entry_type != "file":
            continue
        try:
            path = normalize_repository_path(relative)
        except PatchPolicyError:
            continue
        content = value.read_bytes()
        try:
            if contains_secret(content.decode()):
                continue
        except UnicodeDecodeError:
            pass
        files[path] = content
    return files


def _fits_sandbox_bundle(files: dict[str, bytes]) -> bool:
    size = sum(len(content) for content in files.values())
    return len(files) <= MAX_BUNDLE_FILES and size <= MAX_BUNDLE_BYTES


def _clean_requirement(line: str) -> str | None:
    """Accept only a named requirement; options, paths, and URLs never reach pip."""
    try:
        requirement = Requirement(line.split(" #")[0].strip())
    except InvalidRequirement:
        return None
    return None if requirement.url else str(requirement)


def python_requirements(root: Path) -> list[str]:
    lines: list[str] = []
    for directory in (root, root / "requirements"):
        if directory.is_dir():
            for path in sorted(directory.iterdir()):
                if path.is_file() and not path.is_symlink() and MANIFEST_NAME.match(path.name):
                    lines += path.read_text(encoding="utf-8", errors="replace").splitlines()
    pyproject = root / "pyproject.toml"
    if pyproject.is_file() and not pyproject.is_symlink():
        try:
            project = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project", {})
        except (tomllib.TOMLDecodeError, UnicodeDecodeError):
            project = {}
        lines += project.get("dependencies", [])
        optional = project.get("optional-dependencies", {})
        for extra in ("test", "tests", "dev"):
            lines += optional.get(extra, [])
    cleaned = {_clean_requirement(line) for line in lines if isinstance(line, str)}
    return sorted(item for item in cleaned if item)[:200]


def prepare_image(requirements: list[str], base: str = PYTHON_IMAGE) -> tuple[str, bool]:
    """Commit an image with wheel-only dependencies. Returns the tag and whether all installed."""
    digest = hashlib.sha256(json.dumps([base, requirements]).encode()).hexdigest()[:24]
    tag = f"delta-code-eval:{digest}"
    container = IsolatedContainer(base, network="bridge")
    install = ["python", "-m", "pip", "install", "--only-binary=:all:", "--no-cache-dir",
               "--disable-pip-version-check", "--root-user-action=ignore"]
    complete = True
    try:
        container.start()
        container.upload({})
        code, _out, _err = container.exec(
            [*install, "pytest", *requirements], timeout_seconds=INSTALL_TIMEOUT_SECONDS
        )
        if code != 0:
            # One unavailable wheel should not cost the rest; the outcome is recorded either way.
            container.exec([*install, "pytest"], timeout_seconds=INSTALL_TIMEOUT_SECONDS)
            for requirement in requirements:
                code, _out, _err = container.exec(
                    [*install, requirement], timeout_seconds=INSTALL_TIMEOUT_SECONDS
                )
                complete = complete and code == 0
        subprocess.run(
            [container.docker, "commit", container.name, tag],
            check=True,
            capture_output=True,
            timeout=300,
        )
    finally:
        container.destroy()
    return tag, complete


def remove_image(tag: str) -> None:
    subprocess.run(["docker", "image", "rm", "--force", tag], capture_output=True, timeout=120)


def parse_pytest_outcomes(output: str) -> tuple[set[str], set[str]]:
    passed: set[str] = set()
    failed: set[str] = set()
    for outcome, test in PYTEST_OUTCOME.findall(output):
        (passed if outcome == "PASSED" else failed).add(test)
    return passed, failed


def run_tests(image: str, files: dict[str, bytes]) -> tuple[int | None, set[str], set[str]]:
    container = IsolatedContainer(image)
    try:
        container.start()
        container.upload(files)
        exit_code, stdout, _stderr = container.exec(
            ["python", "-m", "pytest", "-q", "-rA", "--tb=no", "-p", "no:cacheprovider"],
            timeout_seconds=TEST_TIMEOUT_SECONDS,
            env={
                "CI": "true",
                "HOME": "/tmp/delta-code-home",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": f"{REPOSITORY_ROOT}:{REPOSITORY_ROOT}/src",
            },
            output_limit=TEST_OUTPUT_LIMIT,
        )
    finally:
        container.destroy()
    passed, failed = parse_pytest_outcomes(stdout)
    return exit_code, passed, failed


def _parses(path: str, content: str) -> bool | None:
    """True or False for Python; JavaScript is checked by the caller in a container."""
    if not path.endswith((".py", ".pyi")):
        return None
    try:
        ast.parse(content)
    except (SyntaxError, ValueError):
        return False
    return True


def _javascript_parses(files: dict[str, str]) -> bool | None:
    # node --check understands neither JSX nor TypeScript, so those stay unknown, not passed.
    scripts = {path: text for path, text in files.items() if path.endswith((".js", ".mjs", ".cjs"))}
    if not scripts:
        return None
    container = IsolatedContainer(NODE_IMAGE)
    try:
        container.start()
        container.upload({path: text.encode() for path, text in scripts.items()})
        for path in scripts:
            code, _out, stderr = container.exec(["node", "--check", path], timeout_seconds=60)
            if code != 0 and "Cannot use import statement" not in stderr:
                return False
    finally:
        container.destroy()
    return True


def _guided_change(migration: dict, index: int) -> NormalizedChange:
    guide = GUIDES[migration["id"]]
    change = _change({"targets": migration["targets"]}, index)
    artifact_id = change.source_artifacts[0].id
    return change.model_copy(
        update={
            "summary": migration["description"],
            "migration_guidance": [
                Guidance(
                    summary=guide["guidance"],
                    provenance="provider_stated",
                    source_artifact_ids=[artifact_id],
                )
            ],
        }
    )


def _analyze_tree(root: Path, repository: str, sha: str, change: NormalizedChange, identifier: str):
    digest, file_count, size_bytes, symlinks = workspace_fingerprint(root)
    workspace = RepositoryWorkspace(
        repository_id=identifier,
        root=str(root),
        commit_sha=sha,
        content_digest=digest,
        file_count=file_count,
        size_bytes=size_bytes,
        symlink_count=symlinks,
    )
    reference = RepositoryRef(
        id=identifier,
        workspace_id="evaluation",
        full_name=repository,
        clone_url=f"https://github.com/{repository}.git",
        default_branch="main",
        installation_id=1,
    )
    inventory = RepositoryInventoryBuilder().build(workspace)
    impact = MultiLanguageImpactAnalyzer().analyze(reference, workspace, inventory, change)
    snapshot = RepositorySnapshot(
        id=f"snapshot-{identifier}",
        repository_id=identifier,
        source_ref=sha,
        commit_sha=sha,
        content_digest=digest,
        inventory_digest=inventory.inventory_digest,
        inventory_version=inventory.inventory_version,
        analyzer_versions=[MultiLanguageImpactAnalyzer.analyzer_version],
        created_at=datetime.now(UTC),
    )
    return reference, snapshot, impact


def _estimate(context_json: str, context_file_chars: int) -> tuple[float, float]:
    schema_chars = len(json.dumps(GenerationProposal.model_json_schema()))
    propose_input = (len(context_json) + schema_chars + PROMPT_OVERHEAD_CHARS) / CHARS_PER_TOKEN
    # Edits carry whole files, so output tracks the size of what was supplied, up to the cap.
    expected_propose_output = min(
        context_file_chars / CHARS_PER_TOKEN + 500, PROPOSE_MAX_OUTPUT_TOKENS
    )
    review_input = (len(context_json) + PROMPT_OVERHEAD_CHARS + 6_000) / CHARS_PER_TOKEN

    def price(input_tokens: float, output_tokens: float) -> float:
        return (
            input_tokens * GPT_4O_INPUT_USD_PER_MILLION
            + output_tokens * GPT_4O_OUTPUT_USD_PER_MILLION
        ) / 1_000_000

    expected = price(
        propose_input + review_input, expected_propose_output + EXPECTED_REVIEW_OUTPUT_TOKENS
    )
    worst = price(
        propose_input + review_input, PROPOSE_MAX_OUTPUT_TOKENS + REVIEW_MAX_OUTPUT_TOKENS
    )
    return round(expected, 5), round(worst, 5)


def _apply_edits(root: Path, patch_bytes: bytes) -> dict[str, str]:
    edits = json.loads(patch_bytes)["edits"]
    applied: dict[str, str] = {}
    for edit in edits:
        path = normalize_repository_path(edit["path"])
        destination = root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(edit["content"], encoding="utf-8")
        applied[path] = edit["content"]
    return applied


def _score_patch(
    result: EvalResult,
    root: Path,
    case: dict,
    migration: dict,
    change: NormalizedChange,
    edited: dict[str, str],
) -> None:
    guide = GUIDES[migration["id"]]
    pattern = re.compile(migration["pattern"])
    result.edited_files = len(edited)
    outcomes = [_parses(path, text) for path, text in edited.items()]
    if guide["language"] == "javascript":
        outcomes.append(_javascript_parses(edited))
    known = [item for item in outcomes if item is not None]
    result.parses = all(known) if known else None
    result.usage_after = _usage_lines(root, pattern, migration["extensions"])
    _ref, _snapshot, impact = _analyze_tree(
        root, case["repository"], case["parent_sha"], change, case["id"]
    )
    result.flagged_sites_after = len(impact.call_sites)
    if guide["replacement"]:
        replacement = re.compile(guide["replacement"])
        result.replacement_added = sum(
            len(replacement.findall(text)) for text in edited.values()
        )


def _run_tests_after(
    result: EvalResult,
    root: Path,
    migration: dict,
    baseline: set[str],
    baseline_failed: set[str],
) -> None:
    guide = GUIDES[migration["id"]]
    extra = [guide["after_requirement"]] if guide["after_requirement"] else []
    image, _complete = prepare_image(sorted({*python_requirements(root), *extra}))
    try:
        exit_code, passed, failed = run_tests(image, _tree_files(root))
    finally:
        remove_image(image)
    result.after_passed = len(passed)
    result.regressions = sorted(baseline - passed)[:50]
    result.new_failures = sorted(failed - baseline_failed)[:50]
    if exit_code is None:
        result.detail = "after_tests_timed_out"


def _run_human_control(
    client: httpx.Client,
    result: EvalResult,
    case: dict,
    migration: dict,
    baseline: set[str],
) -> None:
    """Score the developers' real migration the same way, so a regression has a reference point."""
    guide = GUIDES[migration["id"]]
    with tempfile.TemporaryDirectory(prefix="delta-code-eval-") as directory:
        root = _download(client, case["repository"], case["migration_sha"], Path(directory))
        extra = [guide["after_requirement"]] if guide["after_requirement"] else []
        image, _complete = prepare_image(sorted({*python_requirements(root), *extra}))
        try:
            _exit, passed, _failed = run_tests(image, _tree_files(root))
        finally:
            remove_image(image)
    result.human_passed = len(passed)
    result.human_regressions = len(baseline - passed)


def evaluate_tree(
    root: Path,
    case: dict,
    migration: dict,
    index: int,
    intelligence: MigrationIntelligence | None,
    *,
    http: httpx.Client | None = None,
    usage=None,
) -> EvalResult:
    """Run one case through the production service. No intelligence means estimate only."""
    guide = GUIDES[migration["id"]]
    pattern = re.compile(migration["pattern"])
    result = EvalResult(id=case["id"], migration=case["migration"], repository=case["repository"])
    change = _guided_change(migration, index)
    repository, snapshot, impact = _analyze_tree(
        root, case["repository"], case["parent_sha"], change, f"eval-{index}"
    )
    result.flagged_sites = len(impact.call_sites)
    result.usage_before = _usage_lines(root, pattern, migration["extensions"])
    if not impact.call_sites:
        result.stage = "no_call_sites"
        return result
    try:
        context = assemble_planning_context(
            migration_id=f"migration-{index}",
            attempt_id=f"eval-{index}",
            repository=repository,
            snapshot=snapshot,
            change=change,
            impact=impact,
            root=root,
        )
        GenerationLimits.from_env().validate_context(context)
    except (PatchPolicyError, BudgetExceeded) as exc:
        result.stage = "context_too_large"
        result.detail = str(exc)
        return result
    files = _tree_files(root)
    if not _fits_sandbox_bundle(files):
        result.stage = "repository_too_large"
        return result
    context_json = json.dumps(context.model_dump(mode="json"), separators=(",", ":"))
    result.context_bytes = len(context_json.encode())
    result.expected_cost_usd, result.worst_case_cost_usd = _estimate(
        context_json, sum(len(item.content) for item in context.files)
    )
    if intelligence is None:
        result.stage = "estimated"
        return result

    baseline_passed: set[str] = set()
    baseline_failed: set[str] = set()
    image = NODE_IMAGE
    prepared = guide["language"] == "python"
    if prepared:
        image, _complete = prepare_image(python_requirements(root))
    try:
        if prepared:
            result.baseline_exit, baseline_passed, baseline_failed = run_tests(image, files)
            result.baseline_passed = len(baseline_passed)
        with tempfile.TemporaryDirectory(prefix="delta-code-eval-") as artifacts:
            store = FilesystemArtifactStore(Path(artifacts))
            service = MigrationGenerationService(
                intelligence, LocalDockerSandboxExecutor(image), store
            )
            started = time.monotonic()
            spent_before = usage().cost_usd if usage else 0.0
            try:
                generated = service.run(context, root)
            except PatchPolicyError as exc:
                result.stage = "patch_rejected"
                result.detail = str(exc)[:300]
                return result
            except OpenAIBudgetExceeded:
                raise
            except Exception as exc:
                result.stage = "generation_failed"
                result.detail = f"{type(exc).__name__}: {exc}"[:300]
                return result
            finally:
                result.seconds = round(time.monotonic() - started, 1)
                if usage:
                    result.cost_usd = round(usage().cost_usd - spent_before, 5)
            patch_bytes = store.read(generated.patch_object_ref)
        evidence = generated.evidence
        result.recommendation = evidence.recommendation.action
        statuses = {check.status for check in evidence.verification_checks}
        result.model_check_status = "passed" if statuses == {"passed"} else "not_passed"
        edited = _apply_edits(root, patch_bytes)
        _score_patch(result, root, case, migration, change, edited)
        result.stage = "completed"
        if baseline_passed:
            _run_tests_after(result, root, migration, baseline_passed, baseline_failed)
            if http is not None:
                _run_human_control(http, result, case, migration, baseline_passed)
    finally:
        if prepared:
            remove_image(image)
    return result


def summarize(results: list[EvalResult]) -> dict:
    stages: dict[str, int] = {}
    for item in results:
        stages[item.stage] = stages.get(item.stage, 0) + 1
    done = [item for item in results if item.stage == "completed"]
    parses = [item for item in done if item.parses is not False]
    gone = [item for item in done if item.usage_after == 0]
    clean = [item for item in done if item.parses is not False and item.usage_after == 0]
    replaced = [
        item for item in clean if item.replacement_added is None or item.replacement_added > 0
    ]
    tested = [item for item in done if item.baseline_passed and item.after_passed is not None]
    no_regressions = [item for item in tested if not item.regressions]
    controlled = [item for item in tested if item.human_regressions is not None]
    usage_before = sum(item.usage_before for item in done)
    usage_after = sum(item.usage_after or 0 for item in done)
    costs = [item.cost_usd for item in results if item.cost_usd]
    seconds = [item.seconds for item in done if item.seconds]
    return {
        "cases": len(results),
        "stages": dict(sorted(stages.items())),
        "patches_completed": len(done),
        "patches_parse": len(parses),
        "patches_remove_all_old_usage": len(gone),
        "patches_parse_and_remove_all_old_usage": len(clean),
        "patches_also_add_the_new_api": len(replaced),
        "old_usage_lines_before": usage_before,
        "old_usage_lines_after": usage_after,
        "repositories_with_passing_baseline_tests": len(tested),
        "repositories_without_test_regressions": len(no_regressions),
        "human_control_repositories": len(controlled),
        "human_control_without_regressions": sum(
            1 for item in controlled if item.human_regressions == 0
        ),
        "recommendations": {
            action: sum(1 for item in done if item.recommendation == action)
            for action in sorted({item.recommendation for item in done if item.recommendation})
        },
        "total_cost_usd": round(sum(costs), 4),
        "median_cost_usd": round(statistics.median(costs), 4) if costs else None,
        "median_generation_seconds": statistics.median(seconds) if seconds else None,
        "estimated_expected_cost_usd": round(sum(item.expected_cost_usd for item in results), 2),
        "estimated_worst_case_cost_usd": round(
            sum(item.worst_case_cost_usd for item in results), 2
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true", help="estimate cost; no model calls")
    parser.add_argument("--max-cost", type=float, default=0.0, help="hard USD limit for this run")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--migration", default=None)
    arguments = parser.parse_args()
    if not arguments.dry_run and arguments.max_cost <= 0:
        parser.error("a real run requires --max-cost")
    document = json.loads(arguments.dataset.read_text(encoding="utf-8"))
    migrations = {item["id"]: item for item in document["migrations"]}
    cases = [
        case for case in document["cases"]
        if not arguments.migration or case["migration"] == arguments.migration
    ][: arguments.limit]
    intelligence = None
    usage = None
    if not arguments.dry_run:
        budget = ProcessBudget(arguments.max_cost, arguments.max_cost)
        openai = OpenAIResponsesClient(budget=budget)
        intelligence = OpenAIMigrationIntelligence(openai)
        usage = lambda: openai.usage  # noqa: E731
    results: list[EvalResult] = []
    stopped = False
    with httpx.Client(timeout=httpx.Timeout(120, connect=15), follow_redirects=True) as http:
        for index, case in enumerate(cases, start=1):
            migration = migrations[case["migration"]]
            if stopped:
                results.append(
                    EvalResult(
                        id=case["id"],
                        migration=case["migration"],
                        repository=case["repository"],
                        stage="budget_exhausted",
                    )
                )
                continue
            try:
                with tempfile.TemporaryDirectory(prefix="delta-code-eval-") as directory:
                    root = _download(http, case["repository"], case["parent_sha"], Path(directory))
                    result = evaluate_tree(
                        root, case, migration, index, intelligence, http=http, usage=usage
                    )
            except OpenAIBudgetExceeded:
                stopped = True
                result = EvalResult(
                    id=case["id"],
                    migration=case["migration"],
                    repository=case["repository"],
                    stage="budget_exhausted",
                )
            except Exception as exc:
                detail = "".join(traceback.format_exception_only(exc)).strip()[:300]
                result = EvalResult(
                    id=case["id"],
                    migration=case["migration"],
                    repository=case["repository"],
                    stage="harness_error",
                    detail=detail,
                )
            results.append(result)
            print(f"[{index}/{len(cases)}] {case['id']} {result.stage} {result.detail or ''}",
                  flush=True)
            report = {"summary": summarize(results), "cases": [asdict(item) for item in results]}
            arguments.out.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
    print(json.dumps(summarize(results), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
