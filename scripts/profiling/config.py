from __future__ import annotations

import argparse
import re
from pathlib import Path


DEFAULT_PROM_URL = "http://localhost:9090"
DEFAULT_TARGET_URL = "http://localhost:8080/v1/models/mobilenet-v3-large:predict"
DEFAULT_HOST_HEADER = "mobilenet-v3-large.kserve-test.example.com"
DEFAULT_PAYLOAD_FILE = "config/input.json"
DEFAULT_OUTPUT_DIR = "results"
DEFAULT_RESULTS_FILENAME = "profiling_runs.json"
DEFAULT_SUMMARY_FILENAME = "profiling_summary.json"
DEFAULT_INFERENCESERVICE_MANIFEST = "k8s/manifests/mobilenet-v3-large.yaml"

DEFAULT_NAMESPACE = "kserve-test"
DEFAULT_INFERENCESERVICE = "mobilenet-v3-large"
DEFAULT_KN_SERVICE = "mobilenet-v3-large-predictor"
DEFAULT_QUEUE_CONTAINER = "queue-proxy"
DEFAULT_MODEL_CONTAINER = "kserve-container"
DEFAULT_OBSERVABILITY_NAMESPACE = "knative-serving"
DEFAULT_OBSERVABILITY_CONFIGMAP = "config-observability"

CPU_CANDIDATES = ("1", "2", "4")
CLIENT_CONCURRENCY_CANDIDATES = (1, 2, 4, 8, 12, 16, 24, 32, 48, 64)

FIXED_MEMORY = "4Gi"
MEMORY_HEADROOM_FACTOR = 1.3
WARMUP_SECONDS = 30
MEASURE_SECONDS = 120
SCRAPE_LAG_SECONDS = 20
COOLDOWN_SECONDS = 20
PROM_STEP_SECONDS = 5
CPU_RATE_WINDOW = "1m"
PREFLIGHT_ATTEMPTS = 12
PREFLIGHT_RETRY_SECONDS = 5

SLO_P95_SECONDS = 0.500
BASELINE_REPEATS = 1
EXPLORATION_REPEATS = 2
RPS_DIFF_WARN_RATIO = 0.15
MAX_CPU_THROTTLING_AVG = 0.10
MAX_CPU_THROTTLING_MAX = 0.20
MAX_CPU_UTILIZATION_RATIO_AVG = 0.80


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return parsed


def positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be > 0")
    return parsed


def comma_strings(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def comma_ints(value: str) -> list[int]:
    return [positive_int(item.strip()) for item in value.split(",") if item.strip()]


def derive_model_name(args: argparse.Namespace) -> str:
    if args.model_name:
        return args.model_name

    match = re.search(r"/models/([^/:]+)", args.target_url)
    if match:
        return match.group(1)

    return args.inferenceservice


def normalize_output_paths(args: argparse.Namespace) -> argparse.Namespace:
    model_name = derive_model_name(args)
    model_output_dir = Path(args.output_dir) / model_name

    args.model_name = model_name
    if args.results_file is None:
        args.results_file = str(model_output_dir / DEFAULT_RESULTS_FILENAME)
    if args.summary_file is None:
        args.summary_file = str(model_output_dir / DEFAULT_SUMMARY_FILENAME)
    return args


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Profile KServe mobilenet-v3-large CPU/containerConcurrency candidates.",
    )
    parser.add_argument("--prom-url", default=DEFAULT_PROM_URL)
    parser.add_argument("--target-url", default=DEFAULT_TARGET_URL)
    parser.add_argument("--host-header", default=DEFAULT_HOST_HEADER)
    parser.add_argument("--payload-file", default=DEFAULT_PAYLOAD_FILE)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--model-name",
        help="Model name used for default result paths. Defaults to the target URL model name.",
    )
    parser.add_argument(
        "--results-file",
        help="Override the run-level JSON output path.",
    )
    parser.add_argument(
        "--summary-file",
        help="Override the summary JSON output path.",
    )
    parser.add_argument("--inferenceservice-manifest", default=DEFAULT_INFERENCESERVICE_MANIFEST)
    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    parser.add_argument("--inferenceservice", default=DEFAULT_INFERENCESERVICE)
    parser.add_argument("--kn-service", default=DEFAULT_KN_SERVICE)
    parser.add_argument("--queue-container", default=DEFAULT_QUEUE_CONTAINER)
    parser.add_argument("--model-container", default=DEFAULT_MODEL_CONTAINER)
    parser.add_argument("--observability-namespace", default=DEFAULT_OBSERVABILITY_NAMESPACE)
    parser.add_argument("--observability-configmap", default=DEFAULT_OBSERVABILITY_CONFIGMAP)

    parser.add_argument("--cpus", type=comma_strings, default=list(CPU_CANDIDATES))
    parser.add_argument("--fixed-memory", default=FIXED_MEMORY)
    parser.add_argument(
        "--client-concurrencies",
        type=comma_ints,
        default=list(CLIENT_CONCURRENCY_CANDIDATES),
    )

    parser.add_argument("--warmup-seconds", type=positive_int, default=WARMUP_SECONDS)
    parser.add_argument("--measure-seconds", type=positive_int, default=MEASURE_SECONDS)
    parser.add_argument("--scrape-lag-seconds", type=positive_int, default=SCRAPE_LAG_SECONDS)
    parser.add_argument("--cooldown-seconds", type=positive_int, default=COOLDOWN_SECONDS)
    parser.add_argument("--prom-step-seconds", type=positive_int, default=PROM_STEP_SECONDS)
    parser.add_argument("--cpu-rate-window", default=CPU_RATE_WINDOW)
    parser.add_argument("--ready-timeout-seconds", type=positive_int, default=600)
    parser.add_argument("--hey-timeout-padding-seconds", type=positive_int, default=60)
    parser.add_argument("--preflight-attempts", type=positive_int, default=PREFLIGHT_ATTEMPTS)
    parser.add_argument(
        "--preflight-retry-seconds",
        type=positive_int,
        default=PREFLIGHT_RETRY_SECONDS,
    )

    parser.add_argument("--slo-p95-seconds", type=positive_float, default=SLO_P95_SECONDS)
    parser.add_argument("--baseline-repeats", type=positive_int, default=BASELINE_REPEATS)
    parser.add_argument("--exploration-repeats", type=positive_int, default=EXPLORATION_REPEATS)
    parser.add_argument(
        "--memory-headroom-factor",
        type=positive_float,
        default=MEMORY_HEADROOM_FACTOR,
    )
    parser.add_argument("--rps-diff-warn-ratio", type=positive_float, default=RPS_DIFF_WARN_RATIO)
    parser.add_argument(
        "--max-cpu-throttling-avg",
        type=positive_float,
        default=MAX_CPU_THROTTLING_AVG,
        help="Maximum average CPU throttling ratio allowed for recommendations.",
    )
    parser.add_argument(
        "--max-cpu-throttling-max",
        type=positive_float,
        default=MAX_CPU_THROTTLING_MAX,
        help="Maximum peak CPU throttling ratio allowed for recommendations.",
    )
    parser.add_argument(
        "--max-cpu-utilization-ratio-avg",
        type=positive_float,
        default=MAX_CPU_UTILIZATION_RATIO_AVG,
        help="Maximum average CPU usage divided by CPU limit allowed for recommendations.",
    )
    parser.add_argument(
        "--refresh-observability",
        action="store_true",
        help="Patch an annotation to force a new Revision before profiling.",
    )
    parser.add_argument(
        "--configure-observability",
        action="store_true",
        help="Patch Knative request-metrics-export-interval, then force a new Revision.",
    )
    parser.add_argument("--request-metrics-export-interval", default="5s")
    parser.add_argument(
        "--no-apply",
        action="store_true",
        help="Do not patch or inspect Kubernetes resources; useful for local parser checks.",
    )
    return parser
