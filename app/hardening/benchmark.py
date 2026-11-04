"""Repeatable labeled impact-analysis benchmark used as a release gate."""

import argparse
import json
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from app.control_plane.models import NormalizedChange
from app.repository_intelligence.analyzer import MultiLanguageImpactAnalyzer
from app.repository_intelligence.inventory import RepositoryInventoryBuilder
from app.repository_intelligence.models import RepositoryRef, RepositoryWorkspace
from app.repository_intelligence.workspace import workspace_fingerprint


@dataclass(frozen=True)
class BenchmarkScore:
    cases: int
    true_positives: int
    false_positives: int
    true_negatives: int
    false_negatives: int
    precision: float
    recall: float
    f1: float
    classifications: dict[str, str]
    abstained: int = 0
    by_category: dict[str, dict[str, int]] = field(default_factory=dict)
    by_difficulty: dict[str, dict[str, int]] = field(default_factory=dict)
    misclassified: list[str] = field(default_factory=list)


def _safe_fixture_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("benchmark fixture path escapes its repository")
    return path


def _change(case: dict, index: int) -> NormalizedChange:
    captured = datetime(2026, 8, 6, tzinfo=UTC)
    return NormalizedChange.model_validate(
        {
            "id": f"benchmark-change-{index}",
            "dedupe_key": f"benchmark:{index}",
            "provider": {"id": "benchmark", "name": "Benchmark Provider"},
            "status": "ready",
            "detected_at": captured,
            "change_type": "sdk_symbol_removed",
            "severity": "high",
            "breaking": True,
            "summary": "A labeled provider surface changed.",
            "source_artifacts": [
                {
                    "id": f"benchmark-artifact-{index}",
                    "source_type": "sdk_release",
                    "canonical_url": "https://benchmark.example/releases/1",
                    "captured_at": captured,
                    "sha256": "a" * 64,
                    "authoritative": True,
                }
            ],
            "targets": case["targets"],
            "claims": [
                {
                    "id": f"benchmark-claim-{index}",
                    "summary": "The labeled surface changed.",
                    "provenance": "provider_stated",
                    "source_artifact_ids": [f"benchmark-artifact-{index}"],
                }
            ],
            "confidence": {"score": 1, "basis": "deterministic"},
        }
    )


def run_benchmark(path: Path) -> BenchmarkScore:
    document = json.loads(path.read_text(encoding="utf-8"))
    cases = document.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("benchmark must contain a non-empty cases array")
    predictions: dict[str, str] = {}
    true_positives = false_positives = true_negatives = false_negatives = 0
    abstained = 0
    by_category: dict[str, dict[str, int]] = {}
    by_difficulty: dict[str, dict[str, int]] = {}
    misclassified: list[str] = []
    analyzer = MultiLanguageImpactAnalyzer()
    builder = RepositoryInventoryBuilder()

    for index, case in enumerate(cases, start=1):
        if not isinstance(case, dict) or not isinstance(case.get("files"), dict):
            raise ValueError("benchmark cases must contain a files object")
        identifier = str(case.get("id") or f"case-{index}")
        with tempfile.TemporaryDirectory(prefix="delta-code-benchmark-") as directory:
            root = Path(directory)
            for relative, content in case["files"].items():
                safe = _safe_fixture_path(relative)
                if not isinstance(content, str) or len(content.encode()) > 100_000:
                    raise ValueError("benchmark fixture content is invalid or too large")
                target = root.joinpath(*safe.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            digest, file_count, size_bytes, symlinks = workspace_fingerprint(root)
            workspace = RepositoryWorkspace(
                repository_id=f"benchmark-repo-{index}",
                root=str(root),
                commit_sha="b" * 40,
                content_digest=digest,
                file_count=file_count,
                size_bytes=size_bytes,
                symlink_count=symlinks,
            )
            repository = RepositoryRef(
                id=workspace.repository_id,
                workspace_id="benchmark",
                full_name=f"benchmark/case-{index}",
                clone_url=f"https://github.com/benchmark/case-{index}.git",
                default_branch="main",
                installation_id=1,
            )
            inventory = builder.build(workspace)
            result = analyzer.analyze(repository, workspace, inventory, _change(case, index))
        predictions[identifier] = result.conclusion
        expected = bool(case.get("affected"))
        predicted = result.conclusion == "affected"
        if expected and predicted:
            true_positives += 1
            outcome = "tp"
        elif expected:
            false_negatives += 1
            outcome = "fn"
        elif predicted:
            false_positives += 1
            outcome = "fp"
        else:
            true_negatives += 1
            outcome = "tn"
        if result.conclusion in {"uncertain", "unsupported"}:
            abstained += 1
        if outcome in {"fp", "fn"}:
            misclassified.append(f"{identifier} [{outcome}: {result.conclusion}]")
        for bucket, key in (
            (by_category, case.get("category")),
            (by_difficulty, case.get("difficulty")),
        ):
            if key:
                counts = bucket.setdefault(key, {"tp": 0, "fp": 0, "tn": 0, "fn": 0})
                counts[outcome] += 1

    precision = true_positives / (true_positives + false_positives or 1)
    recall = true_positives / (true_positives + false_negatives or 1)
    f1 = 2 * precision * recall / (precision + recall or 1)
    return BenchmarkScore(
        cases=len(cases),
        true_positives=true_positives,
        false_positives=false_positives,
        true_negatives=true_negatives,
        false_negatives=false_negatives,
        precision=precision,
        recall=recall,
        f1=f1,
        classifications=predictions,
        abstained=abstained,
        by_category=dict(sorted(by_category.items())),
        by_difficulty=dict(sorted(by_difficulty.items())),
        misclassified=misclassified,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--min-precision", type=float, default=0.9)
    parser.add_argument("--min-recall", type=float, default=0.9)
    parser.add_argument("--summary", action="store_true", help="omit per-case classifications")
    arguments = parser.parse_args()
    score = run_benchmark(arguments.dataset)
    report = asdict(score)
    if arguments.summary:
        del report["classifications"]
    print(json.dumps(report, indent=2, sort_keys=True))
    if score.precision < arguments.min_precision or score.recall < arguments.min_recall:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
