"""hey로 KServe v2 infer 요청 부하를 만들고 결과를 파싱한다."""

from __future__ import annotations

import argparse
import re
import subprocess
import time

from .models import HeyStats
from .utils import run_subprocess


class HeyClient:
    """hey CLI 호출을 profiling 설정에 맞게 구성한다."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args

    def preflight_request(self) -> None:
        """본 측정 전에 단일 요청이 정상 응답하는지 확인한다."""
        last_error = "preflight was not attempted"
        for attempt in range(1, self.args.preflight_attempts + 1):
            proc = run_subprocess(
                self.command(seconds=None, requests=1, client_concurrency=1),
                timeout=60,
            )
            stats = parse_hey(proc.stdout)
            if proc.returncode == 0 and stats.status_counts.get("200", 0) == 1:
                return

            if proc.returncode != 0:
                last_error = f"hey failed with code {proc.returncode}: {proc.stderr.strip()}"
            else:
                last_error = f"status_counts={stats.status_counts}"

            if attempt < self.args.preflight_attempts:
                print(
                    "[preflight] attempt "
                    f"{attempt}/{self.args.preflight_attempts} failed: {last_error}",
                    flush=True,
                )
                time.sleep(self.args.preflight_retry_seconds)

        raise RuntimeError(
            "preflight request did not return one HTTP 200 after "
            f"{self.args.preflight_attempts} attempts; {last_error}"
        )

    def run(self, seconds: int, client_concurrency: int) -> subprocess.CompletedProcess[str]:
        return run_subprocess(
            self.command(seconds=seconds, requests=None, client_concurrency=client_concurrency),
            timeout=seconds + self.args.hey_timeout_padding_seconds,
        )

    def command(
        self,
        seconds: int | None,
        requests: int | None,
        client_concurrency: int,
    ) -> list[str]:
        cmd = ["hey"]
        if seconds is not None:
            cmd.extend(["-z", f"{seconds}s"])
        if requests is not None:
            cmd.extend(["-n", str(requests)])
        cmd.extend(
            [
                "-c",
                str(client_concurrency),
                "-m",
                "POST",
                "-host",
                self.args.host_header,
                "-H",
                "Content-Type: application/json",
                "-D",
                self.args.payload_file,
                self.args.target_url,
            ]
        )
        return cmd


def parse_hey(stdout: str) -> HeyStats:
    """hey stdout에서 처리량, p95, status count, 오류 요약을 추출한다."""
    rps = first_float(stdout, r"Requests/sec:\s+([0-9.]+)")
    p95 = first_float(stdout, r"95%\s+(?:in\s+)?([0-9.]+)\s+secs")

    status_counts: dict[str, int] = {}
    for code, count in re.findall(r"\[(\d{3})\]\s+(\d+)\s+responses", stdout):
        status_counts[code] = int(count)
    total_requests = sum(status_counts.values()) if status_counts else None

    errors: list[str] = []
    in_error_distribution = False
    # hey는 일부 네트워크 오류를 return code보다 stdout 요약에 더 자세히 남긴다.
    for line in stdout.splitlines():
        stripped = line.strip()
        if "connection refused" in stripped.lower():
            errors.append("connection refused")
        if stripped.startswith("Error distribution:"):
            in_error_distribution = True
            continue
        if in_error_distribution and stripped:
            errors.append(f"hey error distribution: {stripped}")

    return HeyStats(total_requests, status_counts, p95, rps, sorted(set(errors)))


def first_int(text: str, pattern: str) -> int | None:
    match = re.search(pattern, text)
    return int(match.group(1)) if match else None


def first_float(text: str, pattern: str) -> float | None:
    match = re.search(pattern, text)
    return float(match.group(1)) if match else None
