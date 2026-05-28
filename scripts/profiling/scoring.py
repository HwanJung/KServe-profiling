"""Profiling run을 후보별 summary와 추천 결과로 집계한다."""

from __future__ import annotations

import argparse
import math
import re
import statistics
import sys
from dataclasses import asdict

from .models import CandidateSummary, ProfileConfig, RunResult


def summarize_config(
    config: ProfileConfig,
    client_concurrency: int,
    phases: set[str],
    runs: list[RunResult],
    args: argparse.Namespace,
) -> CandidateSummary:
    """동일 후보의 반복 측정 결과를 recommendation에 쓰는 단일 summary로 줄인다."""
    matching_runs = [
        item
        for item in runs
        if item.config == asdict(config)
        and item.client_concurrency == client_concurrency
        and item.phase in phases
    ]
    valid_runs = [item for item in matching_runs if item.valid]
    passed_runs = [item for item in valid_runs if item.passed_slo]
    prom_p95_values = [
        item.prom_p95_seconds for item in valid_runs if item.prom_p95_seconds is not None
    ]
    prom_rps_values = [item.prom_rps_avg for item in valid_runs if item.prom_rps_avg is not None]
    hey_rps_values = [item.hey_rps for item in valid_runs if item.hey_rps is not None]
    cpu_avg_values = [item.cpu_usage_avg for item in valid_runs if item.cpu_usage_avg is not None]
    cpu_max_values = [item.cpu_usage_max for item in valid_runs if item.cpu_usage_max is not None]
    throttle_avg_values = [
        item.cpu_throttling_ratio_avg
        for item in valid_runs
        if item.cpu_throttling_ratio_avg is not None
    ]
    throttle_max_values = [
        item.cpu_throttling_ratio_max
        for item in valid_runs
        if item.cpu_throttling_ratio_max is not None
    ]
    memory_peaks = [
        int(item.memory_peak_bytes) for item in valid_runs if item.memory_peak_bytes is not None
    ]
    failure_reasons = sorted({reason for item in matching_runs for reason in item.failure_reasons})
    warnings = sorted({warning for item in matching_runs for warning in item.warnings})

    prom_rps_avg = statistics.fmean(prom_rps_values) if prom_rps_values else None
    hey_rps_avg = statistics.fmean(hey_rps_values) if hey_rps_values else None
    cpu_usage_avg = statistics.fmean(cpu_avg_values) if cpu_avg_values else None
    cpu_usage_max = max(cpu_max_values) if cpu_max_values else None
    cpu_throttling_ratio_avg = (
        statistics.fmean(throttle_avg_values) if throttle_avg_values else None
    )
    cpu_throttling_ratio_max = max(throttle_max_values) if throttle_max_values else None
    rps_diff_ratio = None
    if prom_rps_avg is not None and hey_rps_avg is not None and hey_rps_avg > 0:
        rps_diff_ratio = abs(prom_rps_avg - hey_rps_avg) / hey_rps_avg

    prom_p95_worst = max(prom_p95_values) if prom_p95_values else None
    passed_slo = (
        len(matching_runs) > 0
        and len(passed_runs) == len(matching_runs)
        and prom_p95_worst is not None
        and prom_p95_worst <= args.slo_p95_seconds
    )
    # latency가 좋아도 throttling이 크면 같은 설정을 운영 권장값에서 제외한다.
    cpu_stability_failures = cpu_stability_failure_reasons(
        args=args,
        cpu_throttling_ratio_avg=cpu_throttling_ratio_avg,
        cpu_throttling_ratio_max=cpu_throttling_ratio_max,
    )
    failure_reasons = sorted(set(failure_reasons + cpu_stability_failures))
    recommendable = (
        passed_slo
        and bool(memory_peaks)
        and not cpu_stability_failures
        and not warnings
    )
    score = prom_rps_avg / config.cpu_float if recommendable and prom_rps_avg is not None else None

    max_memory_peak = max(memory_peaks) if memory_peaks else None
    # 메모리 추천값은 측정 peak에 여유율을 곱한 뒤 Gi 단위로 올림한다.
    recommended_memory = (
        memory_with_headroom(max_memory_peak, args.memory_headroom_factor)
        if max_memory_peak is not None
        else None
    )

    return CandidateSummary(
        config=asdict(config),
        client_concurrency=client_concurrency,
        run_count=len(matching_runs),
        valid_run_count=len(valid_runs),
        passed_run_count=len(passed_runs),
        passed_slo=passed_slo,
        recommendable=recommendable,
        failure_reasons=failure_reasons,
        warnings=warnings,
        prom_p95_worst=prom_p95_worst,
        prom_rps_avg=prom_rps_avg,
        hey_rps_avg=hey_rps_avg,
        rps_diff_ratio=rps_diff_ratio,
        score_rps_per_cpu=score,
        cpu_usage_avg=cpu_usage_avg,
        cpu_usage_max=cpu_usage_max,
        cpu_throttling_ratio_avg=cpu_throttling_ratio_avg,
        cpu_throttling_ratio_max=cpu_throttling_ratio_max,
        memory_peak_bytes_by_run=memory_peaks,
        max_memory_peak_bytes=max_memory_peak,
        recommended_memory=recommended_memory,
    )


def pick_recommendation(summaries: list[CandidateSummary]) -> CandidateSummary | None:
    """RPS per CPU가 가장 높은 recommendable 후보를 고른다."""
    valid = [item for item in summaries if item.score_rps_per_cpu is not None]
    if not valid:
        return None
    return sorted(valid, key=summary_sort_key)[0]


def cpu_stability_failure_reasons(
    args: argparse.Namespace,
    cpu_throttling_ratio_avg: float | None,
    cpu_throttling_ratio_max: float | None,
) -> list[str]:
    reasons: list[str] = []
    if cpu_throttling_ratio_avg is None:
        reasons.append("average CPU throttling ratio unavailable")
    elif cpu_throttling_ratio_avg > args.max_cpu_throttling_avg:
        reasons.append(
            "average CPU throttling ratio "
            f"{cpu_throttling_ratio_avg:.3f} exceeded limit "
            f"{args.max_cpu_throttling_avg:.3f}"
        )

    if cpu_throttling_ratio_max is None:
        reasons.append("max CPU throttling ratio unavailable")
    elif cpu_throttling_ratio_max > args.max_cpu_throttling_max:
        reasons.append(
            "max CPU throttling ratio "
            f"{cpu_throttling_ratio_max:.3f} exceeded limit "
            f"{args.max_cpu_throttling_max:.3f}"
        )

    return reasons


def excluded_candidates(
    summaries: list[CandidateSummary],
    args: argparse.Namespace,
) -> list[dict]:
    """추천에서 제외된 후보와 제외 이유를 summary에 남긴다."""
    excluded: list[dict] = []
    for item in summaries:
        if item.score_rps_per_cpu is not None:
            continue
        reasons = list(item.failure_reasons)
        if item.warnings:
            reasons.extend(item.warnings)
        if item.prom_p95_worst is not None:
            if item.prom_p95_worst > args.slo_p95_seconds:
                reasons.append(
                    f"worst p95 {item.prom_p95_worst:.6f}s exceeded SLO "
                    f"{args.slo_p95_seconds:.6f}s"
                )
        if item.max_memory_peak_bytes is None:
            reasons.append("memory peak unavailable")
        excluded.append(
            {
                "config": item.config,
                "client_concurrency": item.client_concurrency,
                "reasons": sorted(set(reasons)) or ["candidate was not recommendable"],
            }
        )
    return excluded


def summary_sort_key(item: CandidateSummary) -> tuple[float, int, int, float, float]:
    return (
        -(item.score_rps_per_cpu or 0.0),
        memory_sort_value(item.recommended_memory),
        int(item.config["container_concurrency"]),
        item.prom_p95_worst or float("inf"),
        item.cpu_throttling_ratio_avg or float("inf"),
    )


def memory_with_headroom(memory_peak_bytes: int, factor: float) -> str:
    gib = 1024**3
    return f"{max(1, math.ceil(memory_peak_bytes * factor / gib))}Gi"


def memory_sort_value(memory: str | None) -> int:
    if memory is None:
        return sys.maxsize
    match = re.fullmatch(r"(\d+)(Gi|Mi)", memory)
    if not match:
        return sys.maxsize
    value = int(match.group(1))
    unit = match.group(2)
    return value * 1024 if unit == "Gi" else value
