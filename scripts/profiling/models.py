from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProfileConfig:
    cpu: str
    memory: str
    container_concurrency: int

    @property
    def cpu_float(self) -> float:
        if self.cpu.endswith("m"):
            return float(self.cpu[:-1]) / 1000.0
        return float(self.cpu)

    @property
    def key(self) -> str:
        return f"cpu={self.cpu},memory={self.memory},cc={self.container_concurrency}"


@dataclass
class HeyStats:
    total_requests: int | None
    status_counts: dict[str, int]
    p95_seconds: float | None
    rps: float | None
    errors: list[str]


@dataclass
class MetricSnapshot:
    prom_p95_seconds: float | None
    prom_rps_avg: float | None
    prom_rps_max: float | None
    cpu_usage_avg: float | None
    cpu_usage_max: float | None
    cpu_throttling_ratio_avg: float | None
    cpu_throttling_ratio_max: float | None
    memory_peak_bytes: int | None
    failures: list[str]


@dataclass
class RunResult:
    phase: str
    repeat: int
    config: dict[str, Any]
    client_concurrency: int
    started_at: float
    ended_at: float
    valid: bool
    passed_slo: bool
    failure_reasons: list[str]
    warnings: list[str]
    prom_p95_seconds: float | None
    prom_rps_avg: float | None
    prom_rps_max: float | None
    hey_p95_seconds: float | None
    hey_rps: float | None
    hey_total_requests: int | None
    hey_status_counts: dict[str, int]
    cpu_usage_avg: float | None
    cpu_usage_max: float | None
    cpu_throttling_ratio_avg: float | None
    cpu_throttling_ratio_max: float | None
    memory_peak_bytes: int | None
    pod_restarts_before: dict[str, int]
    pod_restarts_after: dict[str, int]
    oom_killed_pods: list[str]
    hey_returncode: int
    hey_stdout: str
    hey_stderr: str


@dataclass
class CandidateSummary:
    config: dict[str, Any]
    client_concurrency: int
    run_count: int
    valid_run_count: int
    passed_run_count: int
    passed_slo: bool
    recommendable: bool
    failure_reasons: list[str]
    warnings: list[str]
    prom_p95_worst: float | None
    prom_rps_avg: float | None
    hey_rps_avg: float | None
    rps_diff_ratio: float | None
    score_rps_per_cpu: float | None
    cpu_usage_avg: float | None
    cpu_usage_max: float | None
    cpu_throttling_ratio_avg: float | None
    cpu_throttling_ratio_max: float | None
    memory_peak_bytes_by_run: list[int]
    max_memory_peak_bytes: int | None
    recommended_memory: str | None
