"""Backtest impact analysis against real migrations developers shipped in public repositories.

The ground truth is the set of lines a human changed in a merged migration, frozen by
benchmarks/build_backtest.py before any analyzer run. Scoring lines rather than repositories
matters: declaring the package alone is enough for a repository-level "affected".
"""

import argparse
import json
import re
import statistics
import tarfile
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx

from app.hardening.benchmark import _change
from app.repository_intelligence.analyzer import MultiLanguageImpactAnalyzer
from app.repository_intelligence.inventory import RepositoryInventoryBuilder
from app.repository_intelligence.models import RepositoryRef, RepositoryWorkspace
from app.repository_intelligence.workspace import workspace_fingerprint

MAX_ARCHIVE_BYTES = 300_000_000
DOWNLOAD_ATTEMPTS = 4
HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@")
COMMENT_PREFIXES = ("#", "//", "*", "/*")


def changed_lines(patch: str, pattern: re.Pattern[str]) -> tuple[list[int], int]:
    """Return old-file line numbers of removed matches and the count of added matches."""
    removed: list[int] = []
    added = 0
    old_line = 0
    for line in patch.splitlines():
        header = HUNK_HEADER.match(line)
        if header:
            old_line = int(header.group(1))
        elif line.startswith("-"):
            code = line[1:]
            if pattern.search(code) and not code.lstrip().startswith(COMMENT_PREFIXES):
                removed.append(old_line)
            old_line += 1
        elif line.startswith("+"):
            code = line[1:]
            if pattern.search(code) and not code.lstrip().startswith(COMMENT_PREFIXES):
                added += 1
        elif not line.startswith("\\"):
            old_line += 1
    return removed, added


@dataclass
class CaseResult:
    id: str
    migration: str
    repository: str
    ground_truth_lines: int = 0
    found_lines: int = 0
    missed: list[str] = field(default_factory=list)
    flagged_sites: int = 0
    flagged_confirmed: int = 0
    flagged_unconfirmed: list[str] = field(default_factory=list)
    before_conclusion: str | None = None
    after_conclusion: str | None = None
    after_flagged_sites: int = 0
    after_flagged_unconfirmed: list[str] = field(default_factory=list)
    analyze_seconds: float = 0.0
    files_considered: int = 0
    error: str | None = None


def score_call_sites(
    ground_truth: set[tuple[str, int]],
    call_sites: list,
    read_lines,
    pattern: re.Pattern[str],
) -> tuple[set[tuple[str, int]], int, list[str]]:
    """Match sites to ground truth by span; anything unmatched must contain the changed API."""
    found: set[tuple[str, int]] = set()
    confirmed = 0
    unconfirmed: list[str] = []
    for site in call_sites:
        span = range(site.start_line, site.end_line + 1)
        hits = {(site.path, line) for line in span if (site.path, line) in ground_truth}
        found |= hits
        source = read_lines(site.path)[site.start_line - 1 : site.end_line]
        if hits or any(pattern.search(line) for line in source):
            confirmed += 1
        else:
            first = source[0].strip()[:120] if source else ""
            unconfirmed.append(f"{site.path}:{site.start_line} {first}")
    return found, confirmed, unconfirmed


def _fetch_archive(client: httpx.Client, url: str, archive: Path) -> None:
    written = 0
    with client.stream("GET", url) as response, archive.open("wb") as output:
        response.raise_for_status()
        for chunk in response.iter_bytes():
            written += len(chunk)
            if written > MAX_ARCHIVE_BYTES:
                raise ValueError("archive exceeds backtest size limit")
            output.write(chunk)


def _download(client: httpx.Client, repository: str, sha: str, directory: Path) -> Path:
    archive = directory / "archive.tar.gz"
    url = f"https://codeload.github.com/{repository}/tar.gz/{sha}"
    # A dropped connection says nothing about the analyzer, so it must not remove a case.
    for attempt in range(DOWNLOAD_ATTEMPTS):
        try:
            _fetch_archive(client, url, archive)
            break
        except httpx.TransportError:
            if attempt == DOWNLOAD_ATTEMPTS - 1:
                raise
            time.sleep(2**attempt)
    extracted = directory / "source"
    extracted.mkdir()
    with tarfile.open(archive) as bundle:
        for member in bundle:
            try:
                # Repository content is untrusted: never write outside the directory, never run it.
                bundle.extract(member, extracted, filter="data")
            except tarfile.TarError:
                continue
    archive.unlink()
    children = list(extracted.iterdir())
    if len(children) != 1:
        raise ValueError("archive does not have a single top-level directory")
    return children[0]


def _analyze(root: Path, repository: str, sha: str, change, identifier: str):
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
        workspace_id="backtest",
        full_name=repository,
        clone_url=f"https://github.com/{repository}.git",
        default_branch="main",
        installation_id=1,
    )
    inventory = RepositoryInventoryBuilder().build(workspace)
    return MultiLanguageImpactAnalyzer().analyze(reference, workspace, inventory, change)


def _line_reader(root: Path):
    cache: dict[str, list[str]] = {}

    def read(relative: str) -> list[str]:
        if relative not in cache:
            content = (root / relative).read_text(encoding="utf-8", errors="replace")
            cache[relative] = content.splitlines()
        return cache[relative]

    return read


def run_case(client: httpx.Client, case: dict, migration: dict, index: int) -> CaseResult:
    repository = case["repository"]
    result = CaseResult(id=case["id"], migration=case["migration"], repository=repository)
    pattern = re.compile(migration["pattern"])
    change = _change({"targets": migration["targets"]}, index)
    ground_truth = {(item["path"], item["line"]) for item in case["ground_truth"]}
    result.ground_truth_lines = len(ground_truth)
    try:
        with tempfile.TemporaryDirectory(prefix="delta-code-backtest-") as directory:
            before_root = _download(client, repository, case["parent_sha"], Path(directory))
            started = time.perf_counter()
            before = _analyze(before_root, repository, case["parent_sha"], change, case["id"])
            result.analyze_seconds = round(time.perf_counter() - started, 3)
            result.files_considered = before.coverage.files_considered
            result.before_conclusion = before.conclusion
            result.flagged_sites = len(before.call_sites)
            found, result.flagged_confirmed, result.flagged_unconfirmed = score_call_sites(
                ground_truth, before.call_sites, _line_reader(before_root), pattern
            )
            result.found_lines = len(found)
            result.missed = [f"{path}:{line}" for path, line in sorted(ground_truth - found)]
        with tempfile.TemporaryDirectory(prefix="delta-code-backtest-") as directory:
            after_root = _download(client, repository, case["migration_sha"], Path(directory))
            after = _analyze(after_root, repository, case["migration_sha"], change, case["id"])
            result.after_conclusion = after.conclusion
            result.after_flagged_sites = len(after.call_sites)
            _, _, result.after_flagged_unconfirmed = score_call_sites(
                set(), after.call_sites, _line_reader(after_root), pattern
            )
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"[:300]
    return result


def summarize(results: list[CaseResult]) -> dict:
    scored = [item for item in results if not item.error]

    def totals(items: list[CaseResult]) -> dict:
        lines = sum(item.ground_truth_lines for item in items)
        found = sum(item.found_lines for item in items)
        flagged = sum(item.flagged_sites for item in items)
        confirmed = sum(item.flagged_confirmed for item in items)
        return {
            "repositories": len(items),
            "ground_truth_lines": lines,
            "found_lines": found,
            "line_recall": round(found / lines, 4) if lines else None,
            "repositories_with_a_found_line": sum(1 for item in items if item.found_lines),
            "flagged_sites": flagged,
            "flagged_confirmed": confirmed,
            "flagged_unconfirmed": flagged - confirmed,
            "flag_precision": round(confirmed / flagged, 4) if flagged else None,
            "after_migration_conclusion_affected": sum(
                1 for item in items if item.after_conclusion == "affected"
            ),
            "after_migration_flagged_unconfirmed": sum(
                len(item.after_flagged_unconfirmed) for item in items
            ),
        }

    timings = [item.analyze_seconds for item in scored]
    return {
        "overall": totals(scored),
        "by_migration": {
            name: totals([item for item in scored if item.migration == name])
            for name in sorted({item.migration for item in scored})
        },
        "failed_cases": [f"{item.id}: {item.error}" for item in results if item.error],
        "median_analyze_seconds": round(statistics.median(timings), 3) if timings else None,
        "max_analyze_seconds": max(timings, default=None),
        "max_files_considered": max((item.files_considered for item in scored), default=None),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    arguments = parser.parse_args()
    document = json.loads(arguments.dataset.read_text(encoding="utf-8"))
    migrations = {item["id"]: item for item in document["migrations"]}
    results = []
    with httpx.Client(timeout=httpx.Timeout(120, connect=15), follow_redirects=True) as client:
        for index, case in enumerate(document["cases"], start=1):
            result = run_case(client, case, migrations[case["migration"]], index)
            results.append(result)
            print(
                f"[{index}/{len(document['cases'])}] {case['id']} "
                f"{result.found_lines}/{result.ground_truth_lines} {result.error or ''}",
                flush=True,
            )
    report = {"summary": summarize(results), "cases": [asdict(item) for item in results]}
    arguments.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
