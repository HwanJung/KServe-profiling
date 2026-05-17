from __future__ import annotations

import argparse
import json
import math
import shutil
import time
from dataclasses import asdict
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
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.results: list[RunResult] = []
        self.baseline_best_by_cpu: dict[str, int] = {}
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
        exploration_configs = self.run_exploration()
        exploration_summaries = [
            summarize_config(
                config,
                self.baseline_best_by_cpu[config.cpu],
                {"exploration"},
                self.results,
                self.args,
            )
            for config in exploration_configs
        ]

        final_summaries: list[CandidateSummary] = []
        recommendation = pick_recommendation(exploration_summaries)
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
            exploration_summaries,
            final_summaries,
            recommendation,
            memory_revalidation,
        )
        write_json(Path(self.args.results_file), [asdict(item) for item in self.results])
        write_json(Path(self.args.summary_file), payload)
        return payload

    def run_baseline(self) -> list[ProfileConfig]:
        configs: list[ProfileConfig] = []
        for cpu in self.args.cpus:
            config = ProfileConfig(cpu, self.args.fixed_memory, 0)
            configs.append(config)
            print_apply_start("baseline", config)
            self.prepare_config(config)

            best_c = self.scan_baseline(config)
            if best_c is None:
                self.saturation_failures[cpu] = "no client concurrency passed SLO"
                continue

            if best_c == max(self.args.client_concurrencies):
                self.saturation_failures[cpu] = (
                    "saturation point not found; extend --client-concurrencies"
                )
                continue

            self.baseline_best_by_cpu[cpu] = best_c
        return configs

    def scan_baseline(self, config: ProfileConfig) -> int | None:
        best_c: int | None = None
        for client_c in self.args.client_concurrencies:
            runs = [
                self.run_once("baseline", config, client_c, repeat)
                for repeat in range(1, self.args.baseline_repeats + 1)
            ]
            if all(item.valid and item.passed_slo for item in runs):
                best_c = client_c
                continue
            break
        return best_c

    def run_exploration(self) -> list[ProfileConfig]:
        configs: list[ProfileConfig] = []
        for cpu, best_c in self.baseline_best_by_cpu.items():
            for cc in container_concurrency_candidates(best_c):
                config = ProfileConfig(cpu, self.args.fixed_memory, cc)
                configs.append(config)
                print_apply_start("exploration", config)
                self.prepare_config(config)
                for repeat in range(1, self.args.exploration_repeats + 1):
                    self.run_once("exploration", config, best_c, repeat)
        return configs

    def run_memory_revalidation(
        self,
        config: ProfileConfig,
        client_concurrency: int,
    ) -> RunResult:
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
            config=config,
            args=self.args,
            cpu_usage_avg=result.cpu_usage_avg,
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
        exploration_summaries: list[CandidateSummary],
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
                "request_aggregation_window": "measurement_duration",
                "cpu_rate_window": self.args.cpu_rate_window,
                "slo_p95_seconds": self.args.slo_p95_seconds,
                "rps_diff_warn_ratio": self.args.rps_diff_warn_ratio,
                "max_cpu_throttling_avg": self.args.max_cpu_throttling_avg,
                "max_cpu_throttling_max": self.args.max_cpu_throttling_max,
                "max_cpu_utilization_ratio_avg": self.args.max_cpu_utilization_ratio_avg,
            },
            "baseline_best_by_cpu": self.baseline_best_by_cpu,
            "excluded_cpu_baselines": self.saturation_failures,
            "recommendation": asdict(recommendation) if recommendation else None,
            "memory_revalidation": asdict(memory_revalidation) if memory_revalidation else None,
            "baseline_summaries": [
                asdict(
                    summarize_config(
                        config,
                        self.baseline_best_by_cpu.get(config.cpu, 0),
                        {"baseline"},
                        self.results,
                        self.args,
                    )
                )
                for config in baseline_configs
            ],
            "exploration_summaries": [asdict(item) for item in exploration_summaries],
            "final_summaries": [asdict(item) for item in final_summaries],
            "excluded_candidates": excluded_candidates(
                exploration_summaries + final_summaries,
                self.args,
            ),
        }


def container_concurrency_candidates(best_c: int) -> list[int]:
    candidates = {
        max(1, math.floor(best_c / 2)),
        best_c,
        max(1, math.ceil(best_c * 1.5)),
    }
    return sorted(candidates)


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
