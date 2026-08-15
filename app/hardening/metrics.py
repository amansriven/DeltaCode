"""Low-cardinality process metrics for workers and the web service."""

import threading
from collections import defaultdict
from time import monotonic

JOB_KINDS = frozenset(
    {"ingestion", "repository_analysis", "generation", "publication", "workspace_intelligence"}
)
JOB_STATUSES = frozenset({"completed", "failed", "skipped"})


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[tuple[str, str], int] = defaultdict(int)
        self._durations: dict[str, float] = defaultdict(float)
        self._duration_counts: dict[str, int] = defaultdict(int)

    def record_job(self, kind: str, status: str, duration_seconds: float) -> None:
        if kind not in JOB_KINDS or status not in JOB_STATUSES:
            raise ValueError("metric labels must use the fixed Phase 7 vocabulary")
        duration = max(0.0, float(duration_seconds))
        with self._lock:
            self._jobs[(kind, status)] += 1
            self._durations[kind] += duration
            self._duration_counts[kind] += 1

    def render_prometheus(self) -> str:
        with self._lock:
            jobs = dict(self._jobs)
            durations = dict(self._durations)
            counts = dict(self._duration_counts)
        lines = [
            "# HELP delta_code_jobs_total Durable jobs completed by kind and outcome.",
            "# TYPE delta_code_jobs_total counter",
        ]
        for kind in sorted(JOB_KINDS):
            for status in sorted(JOB_STATUSES):
                lines.append(
                    f'delta_code_jobs_total{{kind="{kind}",status="{status}"}} '
                    f"{jobs.get((kind, status), 0)}"
                )
        lines.extend(
            [
                "# HELP delta_code_job_duration_seconds_total Cumulative durable job duration.",
                "# TYPE delta_code_job_duration_seconds_total counter",
            ]
        )
        for kind in sorted(JOB_KINDS):
            lines.append(
                f'delta_code_job_duration_seconds_total{{kind="{kind}"}} '
                f"{durations.get(kind, 0.0):.6f}"
            )
        lines.extend(
            [
                "# HELP delta_code_job_duration_seconds_count Observed durable job durations.",
                "# TYPE delta_code_job_duration_seconds_count counter",
            ]
        )
        for kind in sorted(JOB_KINDS):
            lines.append(
                f'delta_code_job_duration_seconds_count{{kind="{kind}"}} {counts.get(kind, 0)}'
            )
        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        with self._lock:
            self._jobs.clear()
            self._durations.clear()
            self._duration_counts.clear()


registry = MetricsRegistry()


class JobObservation:
    def __init__(self, kind: str) -> None:
        if kind not in JOB_KINDS:
            raise ValueError("unknown durable job kind")
        self.kind = kind
        self.started = monotonic()
        self.recorded = False

    def finish(self, status: str) -> None:
        if not self.recorded:
            registry.record_job(self.kind, status, monotonic() - self.started)
            self.recorded = True
