from __future__ import annotations

import argparse
import math
import re
import statistics
from typing import Any

import requests

from .models import MetricSnapshot


class PrometheusClient:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args

    def check(self) -> None:
        self.query("up", timeout=10)

    def collect(self, start_ts: float, end_ts: float) -> MetricSnapshot:
        cpu_query_start = start_ts + duration_seconds(self.args.cpu_rate_window)
        if cpu_query_start >= end_ts:
            cpu_query_start = start_ts

        measurement_seconds = end_ts - start_ts
        request_eval_ts = end_ts + self.args.scrape_lag_seconds
        request_window = promql_duration(measurement_seconds + self.args.scrape_lag_seconds)
        latency_value = self.instant_value(self.latency_query(request_window), request_eval_ts)
        rps_value = self.instant_value(
            self.rps_query(request_window, measurement_seconds),
            request_eval_ts,
        )
        cpu_values = self.range_values(self.cpu_usage_query(), cpu_query_start, end_ts)
        throttling_values = self.range_values(
            self.cpu_throttling_query(), cpu_query_start, end_ts
        )
        memory_values = self.range_values(self.memory_query(), start_ts, end_ts)

        failures = []
        if latency_value is None:
            failures.append("missing Prometheus latency histogram p95")
        if rps_value is None:
            failures.append("missing Prometheus RPS count")
        if not cpu_values:
            failures.append("missing Prometheus CPU usage")
        if not throttling_values:
            failures.append("missing Prometheus CPU throttling ratio")
        if not memory_values:
            failures.append("missing Prometheus pod memory working set")

        return MetricSnapshot(
            prom_p95_seconds=latency_value,
            prom_rps_avg=rps_value,
            prom_rps_max=rps_value,
            cpu_usage_avg=statistics.fmean(cpu_values) if cpu_values else None,
            cpu_usage_max=max(cpu_values) if cpu_values else None,
            cpu_throttling_ratio_avg=statistics.fmean(throttling_values)
            if throttling_values
            else None,
            cpu_throttling_ratio_max=max(throttling_values) if throttling_values else None,
            memory_peak_bytes=math.ceil(max(memory_values)) if memory_values else None,
            failures=failures,
        )

    def latency_query(self, request_window: str) -> str:
        return f"""
histogram_quantile(
  0.95,
  sum by (le) (
    increase(http_server_request_duration_seconds_bucket{{
      k8s_namespace_name="{self.args.namespace}",
      kn_service_name="{self.args.kn_service}",
      container_name="{self.args.queue_container}",
      http_request_method="POST"
    }}[{request_window}])
  )
)
""".strip()

    def rps_query(self, request_window: str, measurement_seconds: float) -> str:
        return f"""
sum(
  increase(http_server_request_duration_seconds_count{{
    k8s_namespace_name="{self.args.namespace}",
    kn_service_name="{self.args.kn_service}",
    container_name="{self.args.queue_container}",
    http_request_method="POST"
  }}[{request_window}])
)
/
{measurement_seconds}
""".strip()

    def cpu_usage_query(self) -> str:
        return f"""
sum(
  rate(container_cpu_usage_seconds_total{{
    namespace="{self.args.namespace}",
    container="{self.args.model_container}"
  }}[{self.args.cpu_rate_window}])
)
""".strip()

    def cpu_throttling_query(self) -> str:
        return f"""
sum(rate(container_cpu_cfs_throttled_periods_total{{
  namespace="{self.args.namespace}",
  container="{self.args.model_container}"
}}[{self.args.cpu_rate_window}]))
/
sum(rate(container_cpu_cfs_periods_total{{
  namespace="{self.args.namespace}",
  container="{self.args.model_container}"
}}[{self.args.cpu_rate_window}]))
""".strip()

    def memory_query(self) -> str:
        pod_pattern = f"{self.args.kn_service}-.*"
        return f"""
sum(
  container_memory_working_set_bytes{{
    namespace="{self.args.namespace}",
    pod=~"{pod_pattern}",
    container!="",
    container!="POD"
  }}
)
""".strip()

    def range_values(self, query: str, start_ts: float, end_ts: float) -> list[float]:
        payload = self.query(
            query,
            timeout=60,
            range_params={
                "start": start_ts,
                "end": end_ts,
                "step": f"{self.args.prom_step_seconds}s",
            },
        )
        series = payload.get("data", {}).get("result", [])
        if not series:
            return []
        if len(series) != 1:
            raise RuntimeError(f"Prometheus query returned {len(series)} series, expected 1")

        values: list[float] = []
        for _, raw in series[0].get("values", []):
            value = float(raw)
            if math.isfinite(value):
                values.append(value)
        return values

    def instant_value(self, query: str, eval_ts: float) -> float | None:
        payload = self.query(query, timeout=60, eval_ts=eval_ts)
        series = payload.get("data", {}).get("result", [])
        if not series:
            return None
        if len(series) != 1:
            raise RuntimeError(f"Prometheus query returned {len(series)} series, expected 1")

        _, raw = series[0].get("value", [None, "nan"])
        value = float(raw)
        if not math.isfinite(value):
            return None
        return value

    def query(
        self,
        query: str,
        timeout: int,
        range_params: dict[str, Any] | None = None,
        eval_ts: float | None = None,
    ) -> dict[str, Any]:
        endpoint = "query_range" if range_params else "query"
        params = {"query": query}
        if range_params:
            params.update(range_params)
        elif eval_ts is not None:
            params["time"] = eval_ts
        resp = requests.get(
            f"{self.args.prom_url}/api/v1/{endpoint}",
            params=params,
            timeout=timeout,
        )
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("status") != "success":
            raise RuntimeError(f"Prometheus query failed: {payload}")
        return payload


def duration_seconds(value: str) -> int:
    match = re.fullmatch(r"(\d+)([smhd])", value)
    if not match:
        return 0
    amount = int(match.group(1))
    unit = match.group(2)
    return amount * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


def promql_duration(seconds: float) -> str:
    return f"{max(1, math.ceil(seconds))}s"
