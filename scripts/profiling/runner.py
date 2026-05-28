"""KServe profiling의 baseline 탐색, 검증, 결과 저장 흐름을 조율한다."""

from __future__ import annotations

import argparse
import json
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .kserve import KServeClient
from .loadgen import HeyClient, parse_hey
from .models import CandidateSummary, ProfileConfig, RunResult
from .prometheus import PrometheusClient
from .scoring import (
    cpu_stability_failure_reasons,
    excluded_candidates,
    pick_recommendation,
    summarize_config,
)
from .utils import write_json


class Profiler:
    """CPU와 concurrency 후보를 측정해 운영 추천값을 만든다."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.results: list[RunResult] = []
        self.baseline_stable_by_cpu: dict[str, int] = {}
        self.baseline_bounds_by_cpu: dict[str, dict] = {}
        self.saturation_failures: dict[str, str] = {}
        self.kserve = KServeClient(args)
        self.hey = HeyClient(args)
        self.prometheus = PrometheusClient(args)

    def run(self) -> dict:
        self.validate_environment()
        if self.args.configure_observability and not self.args.no_apply:
            self.kserve.configure_observability()
        elif self.args.refresh_observability and not self.args.no_apply:
            self.kserve.refresh_observability_revision()

        baseline_configs = self.run_baseline()
        validation_configs = self.run_validation()
        validation_summaries = [
            summarize_config(
                config,
                self.baseline_stable_by_cpu[config.cpu],
                {"validation"},
                self.results,
                self.args,
            )
            for config in validation_configs
        ]

        final_summaries: list[CandidateSummary] = []
        recommendation = pick_recommendation(validation_summaries)
        memory_revalidation: RunResult | None = None

        if recommendation is not None and recommendation.recommended_memory is not None:
            final_config = ProfileConfig(
                cpu=recommendation.config["cpu"],
                memory=recommendation.recommended_memory,
                container_concurrency=int(recommendation.config["container_concurrency"]),
            )
            memory_revalidation = self.run_memory_revalidation(
                final_config,
                recommendation.client_concurrency,
            )

        payload = self.summary_payload(
            baseline_configs,
            validation_summaries,
            final_summaries,
            recommendation,
            memory_revalidation,
        )
        write_json(Path(self.args.results_file), [asdict(item) for item in self.results])
        write_json(Path(self.args.summary_file), payload)
        return payload

    def run_baseline(self) -> list[ProfileConfig]:
        """CPU별로 SLO와 RPS 증가율을 만족하는 안정 client concurrency를 찾는다."""
        configs: list[ProfileConfig] = []
        for cpu in self.args.cpus:
            config = ProfileConfig(cpu, self.args.fixed_memory, 0)
            configs.append(config)
            print_apply_start("baseline", config)
            self.prepare_config(config)

            boundary = self.scan_baseline(config)
            if boundary is None:
                self.saturation_failures[cpu] = "no client concurrency passed SLO"
                continue

            self.baseline_bounds_by_cpu[cpu] = asdict(boundary)
            if boundary.bad_c is None:
                self.saturation_failures[cpu] = (
                    "saturation point not found; extend --client-concurrencies"
                )
                continue

            self.baseline_stable_by_cpu[cpu] = boundary.stable_c
        return configs

    def scan_baseline(self, config: ProfileConfig) -> BaselineBoundary | None:
        """coarse 후보를 훑고 첫 실패 구간을 세밀하게 보정한다."""
        measured: dict[int, BaselinePoint] = {}
        coarse = sorted(set(self.args.client_concurrencies))
        if not coarse:
            return None

        last_good: BaselinePoint | None = None
        bad: BaselinePoint | None = None
        for client_c in coarse:
            point = self.measure_baseline_point(
                config,
                client_c,
                self.args.baseline_repeats,
                "coarse",
            )
            measured[client_c] = point
            if self.is_bad_boundary(last_good, point):
                bad = point
                break
            last_good = point

        if last_good is None:
            return None
        if bad is None:
            return BaselineBoundary(
                last_good_c=last_good.client_concurrency,
                bad_c=None,
                stable_c=last_good.client_concurrency,
                refinement_method="none",
                reason="saturation point not found",
                bad_marginal_rps_efficiency=None,
            )

        method = self.refine_baseline_boundary(config, measured, last_good, bad)
        return self.boundary_from_points(measured, method)

    def measure_baseline_point(
        self,
        config: ProfileConfig,
        client_concurrency: int,
        repeats: int,
        stage: str,
    ) -> BaselinePoint:
        runs = [
            self.run_once(f"baseline_{stage}", config, client_concurrency, repeat)
            for repeat in range(1, repeats + 1)
        ]
        passed = all(item.valid and item.passed_slo for item in runs)
        prom_rps_values = [item.prom_rps_avg for item in runs if item.prom_rps_avg is not None]
        prom_p95_values = [
            item.prom_p95_seconds for item in runs if item.prom_p95_seconds is not None
        ]
        return BaselinePoint(
            client_concurrency=client_concurrency,
            passed=passed,
            prom_rps_avg=(
                sum(prom_rps_values) / len(prom_rps_values) if prom_rps_values else None
            ),
            prom_p95_worst=max(prom_p95_values) if prom_p95_values else None,
        )

    def refine_baseline_boundary(
        self,
        config: ProfileConfig,
        measured: dict[int, BaselinePoint],
        last_good: BaselinePoint,
        bad: BaselinePoint,
    ) -> str:
        low_c = last_good.client_concurrency
        high_c = bad.client_concurrency
        method = "linear"

        if high_c - low_c > self.args.refinement_linear_threshold:
            method = "binary_then_linear"
            # 넓은 구간은 binary로 좁힌 뒤 인접 concurrency를 linear로 확인한다.
            while high_c - low_c > self.args.refinement_linear_threshold:
                mid_c = (low_c + high_c) // 2
                if mid_c in measured:
                    break
                point = self.measure_baseline_point(
                    config,
                    mid_c,
                    self.args.refinement_repeats,
                    "binary_refinement",
                )
                measured[mid_c] = point
                if self.is_bad_boundary(measured[low_c], point):
                    high_c = mid_c
                else:
                    low_c = mid_c

        for client_c in range(low_c + 1, high_c):
            if client_c in measured:
                continue
            measured[client_c] = self.measure_baseline_point(
                config,
                client_c,
                self.args.refinement_repeats,
                "linear_refinement",
            )

        self.confirm_boundary_points(config, measured)
        return method

    def confirm_boundary_points(
        self,
        config: ProfileConfig,
        measured: dict[int, BaselinePoint],
    ) -> None:
        """경계 양쪽 점을 한 번 더 측정해 일시적 흔들림을 줄인다."""
        confirmed: set[int] = set()
        while True:
            boundary = self.boundary_from_points(measured, "confirmation")
            if boundary is None or boundary.bad_c is None:
                return

            boundary_points = {boundary.last_good_c, boundary.bad_c}
            pending = boundary_points - confirmed
            if not pending:
                return

            for client_c in pending:
                measured[client_c] = self.measure_baseline_point(
                    config,
                    client_c,
                    self.args.refinement_repeats,
                    "boundary_confirmation",
                )
                confirmed.add(client_c)

    def boundary_from_points(
        self,
        measured: dict[int, BaselinePoint],
        method: str,
    ) -> BaselineBoundary | None:
        last_good: BaselinePoint | None = None
        first_bad: BaselinePoint | None = None
        reason = "slo_failure"
        bad_marginal_rps_efficiency: float | None = None

        for client_c in sorted(measured):
            point = measured[client_c]
            if self.is_bad_boundary(last_good, point):
                first_bad = point
                if point.passed and last_good is not None:
                    reason = "marginal_rps_efficiency"
                    bad_marginal_rps_efficiency = marginal_rps_efficiency(last_good, point)
                break
            last_good = point

        if last_good is None:
            return None
        return BaselineBoundary(
            last_good_c=last_good.client_concurrency,
            bad_c=first_bad.client_concurrency if first_bad else None,
            stable_c=last_good.client_concurrency,
            refinement_method=method,
            reason=reason if first_bad else "saturation point not found",
            bad_marginal_rps_efficiency=bad_marginal_rps_efficiency,
        )

    def is_bad_boundary(
        self,
        previous_good: BaselinePoint | None,
        current: BaselinePoint,
    ) -> bool:
        """SLO 실패 또는 낮은 marginal RPS 효율을 saturation 경계로 본다."""
        if not current.passed:
            return True
        if previous_good is None:
            return False
        if previous_good.prom_rps_avg is None or previous_good.prom_rps_avg <= 0:
            return False
        if current.prom_rps_avg is None:
            return False

        marginal_efficiency = marginal_rps_efficiency(previous_good, current)
        if marginal_efficiency is None:
            return False
        return marginal_efficiency < self.args.min_marginal_rps_efficiency

    def run_validation(self) -> list[ProfileConfig]:
        configs: list[ProfileConfig] = []
        for cpu, stable_c in self.baseline_stable_by_cpu.items():
            config = ProfileConfig(cpu, self.args.fixed_memory, stable_c)
            configs.append(config)
            print_apply_start("validation", config)
            self.prepare_config(config)
            for repeat in range(1, self.args.validation_repeats + 1):
                self.run_once("validation", config, stable_c, repeat)
        return configs

    def run_memory_revalidation(
        self,
        config: ProfileConfig,
        client_concurrency: int,
    ) -> RunResult:
        """권장 memory로 낮춘 최종 후보가 SLO와 CPU 안정성을 유지하는지 확인한다."""
        print_apply_start("memory_revalidation", config)
        self.prepare_config(config)
        result = self.run_once("memory_revalidation", config, client_concurrency, 1)
        if result.valid and result.prom_p95_seconds is not None:
            if result.prom_p95_seconds > self.args.slo_p95_seconds:
                result.valid = False
                result.passed_slo = False
                result.failure_reasons.append(
                    "memory revalidation p95 "
                    f"{result.prom_p95_seconds:.6f}s exceeded SLO "
                    f"{self.args.slo_p95_seconds:.6f}s"
                )
        cpu_stability_failures = cpu_stability_failure_reasons(
            args=self.args,
            cpu_throttling_ratio_avg=result.cpu_throttling_ratio_avg,
            cpu_throttling_ratio_max=result.cpu_throttling_ratio_max,
        )
        if result.valid and cpu_stability_failures:
            result.valid = False
            result.passed_slo = False
            result.failure_reasons.extend(cpu_stability_failures)
            result.failure_reasons = sorted(set(result.failure_reasons))
        return result

    def run_once(
        self,
        phase: str,
        config: ProfileConfig,
        client_concurrency: int,
        repeat: int,
    ) -> RunResult:
        print(
            f"[{phase}] starting profiling: "
            f"{config.key}, client_c={client_concurrency}, repeat={repeat}",
            flush=True,
        )

        # warmup 요청은 cold start와 JIT/캐시 영향을 본 측정에서 분리한다.
        self.hey.run(seconds=self.args.warmup_seconds, client_concurrency=client_concurrency)
        time.sleep(self.args.scrape_lag_seconds)

        restarts_before = self.kserve.pod_restarts()
        start_ts = time.time()
        proc = self.hey.run(
            seconds=self.args.measure_seconds,
            client_concurrency=client_concurrency,
        )
        end_ts = time.time()
        restarts_after = self.kserve.pod_restarts()

        # 종료 직후 scrape되지 않은 request metric을 Prometheus가 수집할 시간을 둔다.
        time.sleep(self.args.scrape_lag_seconds)

        hey_stats = parse_hey(proc.stdout)
        metrics = self.prometheus.collect(start_ts, end_ts)
        oom_killed = self.kserve.oom_killed_pods()
        failure_reasons = failure_reasons_for_run(
            proc_returncode=proc.returncode,
            hey_errors=hey_stats.errors,
            hey_status_counts=hey_stats.status_counts,
            metric_failures=metrics.failures,
            prom_p95=metrics.prom_p95_seconds,
            memory_peak_bytes=metrics.memory_peak_bytes,
            restarts_before=restarts_before,
            restarts_after=restarts_after,
            oom_killed=oom_killed,
        )
        warnings = warning_reasons(
            metrics.prom_rps_avg,
            hey_stats.rps,
            self.args.rps_diff_warn_ratio,
        )

        valid = not failure_reasons
        passed_slo = (
            valid
            and metrics.prom_p95_seconds is not None
            and metrics.prom_p95_seconds <= self.args.slo_p95_seconds
        )
        if valid and not passed_slo:
            failure_reasons.append(
                f"prometheus p95 {metrics.prom_p95_seconds:.6f}s exceeded SLO "
                f"{self.args.slo_p95_seconds:.6f}s"
            )

        result = RunResult(
            phase=phase,
            repeat=repeat,
            config=asdict(config),
            client_concurrency=client_concurrency,
            started_at=start_ts,
            ended_at=end_ts,
            valid=valid,
            passed_slo=passed_slo,
            failure_reasons=sorted(set(failure_reasons)),
            warnings=warnings,
            prom_p95_seconds=metrics.prom_p95_seconds,
            prom_rps_avg=metrics.prom_rps_avg,
            prom_rps_max=metrics.prom_rps_max,
            hey_p95_seconds=hey_stats.p95_seconds,
            hey_rps=hey_stats.rps,
            hey_total_requests=hey_stats.total_requests,
            hey_status_counts=hey_stats.status_counts,
            cpu_usage_avg=metrics.cpu_usage_avg,
            cpu_usage_max=metrics.cpu_usage_max,
            cpu_throttling_ratio_avg=metrics.cpu_throttling_ratio_avg,
            cpu_throttling_ratio_max=metrics.cpu_throttling_ratio_max,
            memory_peak_bytes=metrics.memory_peak_bytes,
            pod_restarts_before=restarts_before,
            pod_restarts_after=restarts_after,
            oom_killed_pods=oom_killed,
            hey_returncode=proc.returncode,
            hey_stdout=proc.stdout,
            hey_stderr=proc.stderr,
        )
        self.results.append(result)
        print_run_result(result)
        return result

    def prepare_config(self, config: ProfileConfig) -> None:
        self.kserve.apply_and_wait(config)
        self.hey.preflight_request()

    def validate_environment(self) -> None:
        payload_path = Path(self.args.payload_file)
        if not payload_path.is_file():
            raise RuntimeError(f"payload file not found: {payload_path}")
        if shutil.which("hey") is None:
            raise RuntimeError("hey executable not found in PATH")
        if not self.args.no_apply and shutil.which("kubectl") is None:
            raise RuntimeError("kubectl executable not found in PATH")

        self.prometheus.check()

    def summary_payload(
        self,
        baseline_configs: list[ProfileConfig],
        validation_summaries: list[CandidateSummary],
        final_summaries: list[CandidateSummary],
        recommendation: CandidateSummary | None,
        memory_revalidation: RunResult | None,
    ) -> dict:
        return {
            "metadata": {
                "created_at": time.time(),
                "target_url": self.args.target_url,
                "model_name": self.args.model_name,
                "host_header": self.args.host_header,
                "prometheus_url": self.args.prom_url,
                "namespace": self.args.namespace,
                "inference_service": self.args.inferenceservice,
                "kn_service": self.args.kn_service,
                "queue_container": self.args.queue_container,
                "model_container": self.args.model_container,
                "observability_namespace": self.args.observability_namespace,
                "observability_configmap": self.args.observability_configmap,
                "cpu_candidates": self.args.cpus,
                "fixed_memory": self.args.fixed_memory,
                "memory_headroom_factor": self.args.memory_headroom_factor,
                "request_aggregation_window": "measurement_duration_plus_scrape_lag",
                "cpu_rate_window": self.args.cpu_rate_window,
                "slo_p95_seconds": self.args.slo_p95_seconds,
                "rps_diff_warn_ratio": self.args.rps_diff_warn_ratio,
                "max_cpu_throttling_avg": self.args.max_cpu_throttling_avg,
                "max_cpu_throttling_max": self.args.max_cpu_throttling_max,
                "min_marginal_rps_efficiency": self.args.min_marginal_rps_efficiency,
                "refinement_linear_threshold": self.args.refinement_linear_threshold,
            },
            "baseline_stable_by_cpu": self.baseline_stable_by_cpu,
            "baseline_bounds_by_cpu": self.baseline_bounds_by_cpu,
            "excluded_cpu_baselines": self.saturation_failures,
            "recommendation": asdict(recommendation) if recommendation else None,
            "memory_revalidation": asdict(memory_revalidation) if memory_revalidation else None,
            "baseline_summaries": [
                asdict(
                    summarize_config(
                        config,
                        self.baseline_summary_client_concurrency(config.cpu),
                        {
                            "baseline_coarse",
                            "baseline_binary_refinement",
                            "baseline_linear_refinement",
                            "baseline_boundary_confirmation",
                        },
                        self.results,
                        self.args,
                    )
                )
                for config in baseline_configs
            ],
            "validation_summaries": [asdict(item) for item in validation_summaries],
            "final_summaries": [asdict(item) for item in final_summaries],
            "excluded_candidates": excluded_candidates(
                validation_summaries + final_summaries,
                self.args,
            ),
        }

    def baseline_summary_client_concurrency(self, cpu: str) -> int:
        stable_c = self.baseline_stable_by_cpu.get(cpu)
        if stable_c is not None:
            return stable_c

        bounds = self.baseline_bounds_by_cpu.get(cpu)
        if bounds is not None:
            return int(bounds["stable_c"])

        if self.args.client_concurrencies:
            return min(self.args.client_concurrencies)
        return 0


@dataclass(frozen=True)
class BaselinePoint:
    client_concurrency: int
    passed: bool
    prom_rps_avg: float | None
    prom_p95_worst: float | None


@dataclass(frozen=True)
class BaselineBoundary:
    last_good_c: int
    bad_c: int | None
    stable_c: int
    refinement_method: str
    reason: str
    bad_marginal_rps_efficiency: float | None


def marginal_rps_efficiency(
    previous: BaselinePoint,
    current: BaselinePoint,
) -> float | None:
    """concurrency 증가율 대비 RPS 증가율을 계산한다."""
    if previous.prom_rps_avg is None or previous.prom_rps_avg <= 0:
        return None
    if current.prom_rps_avg is None:
        return None
    if current.client_concurrency <= previous.client_concurrency:
        return None

    rps_growth_ratio = (current.prom_rps_avg - previous.prom_rps_avg) / previous.prom_rps_avg
    concurrency_growth_ratio = (
        current.client_concurrency - previous.client_concurrency
    ) / previous.client_concurrency
    if concurrency_growth_ratio <= 0:
        return None
    return rps_growth_ratio / concurrency_growth_ratio


def print_apply_start(phase: str, config: ProfileConfig) -> None:
    print(f"\n[{phase}] applying config: {config.key}", flush=True)


def failure_reasons_for_run(
    proc_returncode: int,
    hey_errors: list[str],
    hey_status_counts: dict[str, int],
    metric_failures: list[str],
    prom_p95: float | None,
    memory_peak_bytes: int | None,
    restarts_before: dict[str, int],
    restarts_after: dict[str, int],
    oom_killed: list[str],
) -> list[str]:
    """run 단위 결과를 무효로 만드는 실패 원인을 모은다."""
    reasons = list(metric_failures)
    if proc_returncode != 0:
        reasons.append(f"hey exited with code {proc_returncode}")
    if hey_errors:
        reasons.extend(hey_errors)
    if not hey_status_counts:
        reasons.append("hey did not report HTTP status counts")
    else:
        bad_statuses = {
            code: count
            for code, count in hey_status_counts.items()
            if not code.startswith("2") and count > 0
        }
        if bad_statuses:
            reasons.append(f"non-2xx HTTP responses: {bad_statuses}")
    for pod, after in restarts_after.items():
        before = restarts_before.get(pod, 0)
        if after > before:
            reasons.append(f"pod restart increased: {pod} {before}->{after}")
    if oom_killed:
        reasons.append(f"OOMKilled detected: {oom_killed}")
    if prom_p95 is None:
        reasons.append("Prometheus p95 is unavailable")
    if memory_peak_bytes is None:
        reasons.append("memory peak unavailable")
    return sorted(set(reasons))


def warning_reasons(
    prom_rps_avg: float | None,
    hey_rps: float | None,
    warn_ratio: float,
) -> list[str]:
    """Prometheus와 hey 처리량 차이가 큰 경우 경고를 만든다."""
    if prom_rps_avg is None or hey_rps is None or hey_rps <= 0:
        return []
    diff_ratio = abs(prom_rps_avg - hey_rps) / hey_rps
    if diff_ratio > warn_ratio:
        return [
            "prom_rps_avg and hey_rps differ by "
            f"{diff_ratio:.3f}, above {warn_ratio:.3f}"
        ]
    return []


def print_run_result(result: RunResult) -> None:
    print(
        json.dumps(
            {
                "valid": result.valid,
                "passed_slo": result.passed_slo,
                "prom_p95_seconds": result.prom_p95_seconds,
                "prom_rps_avg": result.prom_rps_avg,
                "hey_rps": result.hey_rps,
                "cpu_usage_avg": result.cpu_usage_avg,
                "cpu_throttling_ratio_avg": result.cpu_throttling_ratio_avg,
                "memory_peak_bytes": result.memory_peak_bytes,
                "status_counts": result.hey_status_counts,
                "warnings": result.warnings,
                "failure_reasons": result.failure_reasons,
            },
            indent=2,
        ),
        flush=True,
    )
