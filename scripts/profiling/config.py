"""Profiling CLI 기본값과 인자 파싱 규칙을 정의한다."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path


# Output layout defaults.
DEFAULT_OUTPUT_DIR = "results"
DEFAULT_RESULTS_FILENAME = "profiling_runs.json"
DEFAULT_SUMMARY_FILENAME = "profiling_summary.json"

# Kubernetes/Knative label and container names that are usually shared by clusters.
DEFAULT_QUEUE_CONTAINER = "queue-proxy"
DEFAULT_MODEL_CONTAINER = "kserve-container"
DEFAULT_OBSERVABILITY_NAMESPACE = "knative-serving"
DEFAULT_OBSERVABILITY_CONFIGMAP = "config-observability"

# Default profile selectors and local port-forward endpoints.
DEFAULT_CLUSTER = "local"
DEFAULT_MODEL = "mobilenet-v3-large"
DEFAULT_HOST_DOMAIN = "example.com"
DEFAULT_INGRESS_URL = "http://localhost:8080"
DEFAULT_PROM_URL = "http://localhost:9090"
DEFAULT_NAMESPACE = "kserve-test"
DEFAULT_PAYLOAD_FILE = "config/input.json"

# Profiling search space defaults. Model profiles can narrow these.
CPU_CANDIDATES = ("1", "2", "4")
CLIENT_CONCURRENCY_CANDIDATES = (1, 2, 4, 8, 16, 32, 64)

# Profiling timing and measurement defaults.
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

# Recommendation thresholds and repeat counts.
SLO_P95_SECONDS = 0.500
BASELINE_REPEATS = 1
REFINEMENT_REPEATS = 2
VALIDATION_REPEATS = 2
RPS_DIFF_WARN_RATIO = 0.15
MAX_CPU_THROTTLING_AVG = 0.10
MAX_CPU_THROTTLING_MAX = 0.10
MIN_MARGINAL_RPS_EFFICIENCY = 0.70
REFINEMENT_LINEAR_THRESHOLD = 8


@dataclass(frozen=True)
class ClusterProfile:
    """Cluster-level settings shared by every model in the same environment."""

    namespace: str
    prom_url: str
    ingress_url: str
    host_domain: str
    kube_context: str | None = None
    observability_namespace: str = DEFAULT_OBSERVABILITY_NAMESPACE
    observability_configmap: str = DEFAULT_OBSERVABILITY_CONFIGMAP
    queue_container: str = DEFAULT_QUEUE_CONTAINER
    model_container: str = DEFAULT_MODEL_CONTAINER


@dataclass(frozen=True)
class ModelProfile:
    """Model-level settings that differ by InferenceService."""

    name: str
    inferenceservice: str | None = None
    manifest: str | None = None
    payload_file: str = DEFAULT_PAYLOAD_FILE
    cpus: tuple[str, ...] = CPU_CANDIDATES
    fixed_memory: str = FIXED_MEMORY
    client_concurrencies: tuple[int, ...] = CLIENT_CONCURRENCY_CANDIDATES


CLUSTER_PROFILES = {
    # Local kind/KServe setup using port-forwarded ingress and Prometheus.
    "local": ClusterProfile(
        namespace=DEFAULT_NAMESPACE,
        prom_url=DEFAULT_PROM_URL,
        ingress_url=DEFAULT_INGRESS_URL,
        host_domain=DEFAULT_HOST_DOMAIN,
    ),
    # Fill these values with the real cloud ingress, Prometheus, and context.
    "cloud": ClusterProfile(
        namespace=DEFAULT_NAMESPACE,
        prom_url=DEFAULT_PROM_URL,
        ingress_url=DEFAULT_INGRESS_URL,
        host_domain=DEFAULT_HOST_DOMAIN,
    ),
}

MODEL_PROFILES = {
    # MobileNet models use a smaller CPU sweep than the generic default.
    "mobilenet-v3-large": ModelProfile(
        name="mobilenet-v3-large",
        cpus=("500m", "1", "2"),
    ),
    "mobilenet-v3-small": ModelProfile(
        name="mobilenet-v3-small",
        cpus=("500m", "1", "2"),
    ),
    # ResNet keeps the generic CPU sweep unless overridden from CLI.
    "resnet50": ModelProfile(
        name="resnet50",
        cpus=CPU_CANDIDATES,
    ),
}


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
    """CLI 인자나 추론 URL에서 모델 이름을 가져온다."""
    if getattr(args, "model_name", None):
        return args.model_name

    match = re.search(r"/models/([^/:]+)", args.target_url)
    if match:
        return match.group(1)

    return args.inferenceservice


def default_manifest(model_name: str) -> str:
    return f"k8s/manifests/{model_name}.yaml"


def resolve_config(args: argparse.Namespace) -> argparse.Namespace:
    """cluster/model profile 기본값과 CLI override를 하나의 namespace로 합친다."""
    cluster = CLUSTER_PROFILES[args.cluster]
    model = MODEL_PROFILES[args.model]
    inferenceservice = args.inferenceservice or model.inferenceservice or model.name
    model_name = args.model_name or model.name
    namespace = args.namespace or cluster.namespace
    ingress_url = cluster.ingress_url.rstrip("/")

    args.kube_context = args.kube_context or cluster.kube_context
    args.namespace = namespace
    args.prom_url = args.prom_url or cluster.prom_url
    args.inferenceservice = inferenceservice
    args.kn_service = args.kn_service or f"{inferenceservice}-predictor"
    args.queue_container = args.queue_container or cluster.queue_container
    args.model_container = args.model_container or cluster.model_container
    args.observability_namespace = (
        args.observability_namespace or cluster.observability_namespace
    )
    args.observability_configmap = (
        args.observability_configmap or cluster.observability_configmap
    )
    args.inferenceservice_manifest = (
        args.inferenceservice_manifest or model.manifest or default_manifest(model.name)
    )
    args.payload_file = args.payload_file or model.payload_file
    args.cpus = args.cpus or list(model.cpus)
    args.fixed_memory = args.fixed_memory or model.fixed_memory
    args.client_concurrencies = args.client_concurrencies or list(model.client_concurrencies)
    args.target_url = args.target_url or f"{ingress_url}/v2/models/{model_name}/infer"
    args.host_header = (
        args.host_header or f"{inferenceservice}.{namespace}.{cluster.host_domain}"
    )
    args.model_name = derive_model_name(args)
    return normalize_output_paths(args)


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
        description="Profile KServe CPU/containerConcurrency candidates.",
    )
    parser.add_argument("--cluster", choices=sorted(CLUSTER_PROFILES), default=DEFAULT_CLUSTER)
    parser.add_argument("--model", choices=sorted(MODEL_PROFILES), default=DEFAULT_MODEL)
    parser.add_argument("--kube-context")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--results-file",
        help="Override the run-level JSON output path.",
    )
    parser.add_argument(
        "--summary-file",
        help="Override the summary JSON output path.",
    )

    parser.add_argument("--cpus", type=comma_strings)
    parser.add_argument("--fixed-memory")
    parser.add_argument(
        "--client-concurrencies",
        type=comma_ints,
    )
    parser.add_argument("--slo-p95-seconds", type=positive_float, default=SLO_P95_SECONDS)

    parser.add_argument("--prom-url", help=argparse.SUPPRESS)
    parser.add_argument("--target-url", help=argparse.SUPPRESS)
    parser.add_argument("--host-header", help=argparse.SUPPRESS)
    parser.add_argument("--payload-file", help=argparse.SUPPRESS)
    parser.add_argument("--model-name", help=argparse.SUPPRESS)
    parser.add_argument("--inferenceservice-manifest", help=argparse.SUPPRESS)
    parser.add_argument("--namespace", help=argparse.SUPPRESS)
    parser.add_argument("--inferenceservice", help=argparse.SUPPRESS)
    parser.add_argument("--kn-service", help=argparse.SUPPRESS)
    parser.add_argument("--queue-container", help=argparse.SUPPRESS)
    parser.add_argument("--model-container", help=argparse.SUPPRESS)
    parser.add_argument("--observability-namespace", help=argparse.SUPPRESS)
    parser.add_argument("--observability-configmap", help=argparse.SUPPRESS)

    parser.add_argument(
        "--warmup-seconds",
        type=positive_int,
        default=WARMUP_SECONDS,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--measure-seconds",
        type=positive_int,
        default=MEASURE_SECONDS,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--scrape-lag-seconds",
        type=positive_int,
        default=SCRAPE_LAG_SECONDS,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--cooldown-seconds",
        type=positive_int,
        default=COOLDOWN_SECONDS,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--prom-step-seconds",
        type=positive_int,
        default=PROM_STEP_SECONDS,
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--cpu-rate-window", default=CPU_RATE_WINDOW, help=argparse.SUPPRESS)
    parser.add_argument(
        "--ready-timeout-seconds",
        type=positive_int,
        default=600,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--hey-timeout-padding-seconds",
        type=positive_int,
        default=60,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--preflight-attempts",
        type=positive_int,
        default=PREFLIGHT_ATTEMPTS,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--preflight-retry-seconds",
        type=positive_int,
        default=PREFLIGHT_RETRY_SECONDS,
        help=argparse.SUPPRESS,
    )

    parser.add_argument(
        "--baseline-repeats",
        type=positive_int,
        default=BASELINE_REPEATS,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--refinement-repeats",
        type=positive_int,
        default=REFINEMENT_REPEATS,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--validation-repeats",
        type=positive_int,
        default=VALIDATION_REPEATS,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--memory-headroom-factor",
        type=positive_float,
        default=MEMORY_HEADROOM_FACTOR,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--rps-diff-warn-ratio",
        type=positive_float,
        default=RPS_DIFF_WARN_RATIO,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--max-cpu-throttling-avg",
        type=positive_float,
        default=MAX_CPU_THROTTLING_AVG,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--max-cpu-throttling-max",
        type=positive_float,
        default=MAX_CPU_THROTTLING_MAX,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--min-marginal-rps-efficiency",
        type=positive_float,
        default=MIN_MARGINAL_RPS_EFFICIENCY,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--refinement-linear-threshold",
        type=positive_int,
        default=REFINEMENT_LINEAR_THRESHOLD,
        help=argparse.SUPPRESS,
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
    parser.add_argument(
        "--request-metrics-export-interval",
        default="5s",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--no-apply",
        action="store_true",
        help="Do not patch or inspect Kubernetes resources; useful for local parser checks.",
    )
    return parser
