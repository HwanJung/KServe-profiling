from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path

from .models import ProfileConfig
from .utils import check_call, kubectl_json


class KServeClient:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args

    def configure_observability(self) -> None:
        patch = {
            "data": {
                "request-metrics-export-interval": self.args.request_metrics_export_interval
            }
        }
        check_call(
            [
                "kubectl",
                "patch",
                "cm",
                self.args.observability_configmap,
                "-n",
                self.args.observability_namespace,
                "--type",
                "merge",
                "-p",
                json.dumps(patch),
            ],
            timeout=60,
        )

    def refresh_observability_revision(self) -> None:
        patch = {
            "metadata": {
                "annotations": {
                    "profiling.knative.dev/observability-refresh": str(int(time.time()))
                }
            }
        }
        check_call(
            [
                "kubectl",
                "patch",
                "inferenceservice",
                self.args.inferenceservice,
                "-n",
                self.args.namespace,
                "--type=merge",
                "-p",
                json.dumps(patch),
            ],
            timeout=60,
        )

    def apply_and_wait(self, config: ProfileConfig) -> None:
        if self.args.no_apply:
            return
        self.delete_inferenceservice()
        self.apply_config(config)
        self.wait_ready()

    def apply_config(self, config: ProfileConfig) -> None:
        manifest = self.build_candidate_manifest(config)
        temp_path = self.write_temp_manifest(manifest)
        try:
            check_call(
                [
                    "kubectl",
                    "apply",
                    "-f",
                    str(temp_path),
                ],
                timeout=60,
            )
        finally:
            temp_path.unlink(missing_ok=True)

    def delete_inferenceservice(self) -> None:
        check_call(
            [
                "kubectl",
                "delete",
                "inferenceservice",
                self.args.inferenceservice,
                "-n",
                self.args.namespace,
                "--ignore-not-found=true",
                "--wait=true",
            ],
            timeout=self.args.ready_timeout_seconds + 30,
        )

    def build_candidate_manifest(self, config: ProfileConfig) -> dict:
        manifest = kubectl_json(
            [
                "kubectl",
                "apply",
                "--dry-run=client",
                "-f",
                self.args.inferenceservice_manifest,
                "-o",
                "json",
            ],
            timeout=60,
        )
        if manifest.get("kind") == "List":
            items = manifest.get("items", [])
            manifest = next(
                (
                    item
                    for item in items
                    if item.get("kind") == "InferenceService"
                    and item.get("metadata", {}).get("name") == self.args.inferenceservice
                ),
                {},
            )
        if manifest.get("kind") != "InferenceService":
            raise RuntimeError(
                "inferenceservice manifest did not render an InferenceService: "
                f"{self.args.inferenceservice_manifest}"
            )

        metadata = manifest.setdefault("metadata", {})
        metadata["name"] = self.args.inferenceservice
        metadata["namespace"] = self.args.namespace
        for key in (
            "creationTimestamp",
            "generation",
            "resourceVersion",
            "uid",
        ):
            metadata.pop(key, None)
        annotations = metadata.get("annotations", {})
        if isinstance(annotations, dict):
            annotations.pop("kubectl.kubernetes.io/last-applied-configuration", None)

        predictor = manifest.setdefault("spec", {}).setdefault("predictor", {})
        predictor["minReplicas"] = 1
        predictor["maxReplicas"] = 1
        predictor["containerConcurrency"] = config.container_concurrency

        model = predictor.setdefault("model", {})
        resources = model.setdefault("resources", {})
        resources["requests"] = {"cpu": config.cpu, "memory": config.memory}
        resources["limits"] = {"cpu": config.cpu, "memory": config.memory}
        manifest.pop("status", None)
        return manifest

    def write_temp_manifest(self, manifest: dict) -> Path:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f"{self.args.inferenceservice}-",
            suffix=".json",
            delete=False,
            dir="/tmp",
        ) as f:
            json.dump(manifest, f)
            f.write("\n")
            return Path(f.name)

    def wait_ready(self) -> None:
        check_call(
            [
                "kubectl",
                "wait",
                "inferenceservice",
                self.args.inferenceservice,
                "-n",
                self.args.namespace,
                "--for=condition=Ready",
                f"--timeout={self.args.ready_timeout_seconds}s",
            ],
            timeout=self.args.ready_timeout_seconds + 30,
        )
        time.sleep(self.args.cooldown_seconds)

    def pod_restarts(self) -> dict[str, int]:
        if self.args.no_apply:
            return {}
        payload = self._pod_payload()
        restarts: dict[str, int] = {}
        for pod in payload.get("items", []):
            name = pod["metadata"]["name"]
            total = 0
            for status in pod.get("status", {}).get("containerStatuses", []):
                total += int(status.get("restartCount", 0))
            restarts[name] = total
        return restarts

    def oom_killed_pods(self) -> list[str]:
        if self.args.no_apply:
            return []
        pods: list[str] = []
        for pod in self._pod_payload().get("items", []):
            name = pod["metadata"]["name"]
            for status in pod.get("status", {}).get("containerStatuses", []):
                state = status.get("lastState", {}).get("terminated", {})
                if state.get("reason") == "OOMKilled":
                    pods.append(name)
                    break
        return sorted(set(pods))

    def _pod_payload(self) -> dict:
        return kubectl_json(
            [
                "kubectl",
                "get",
                "pods",
                "-n",
                self.args.namespace,
                "-l",
                f"serving.knative.dev/service={self.args.kn_service}",
                "-o",
                "json",
            ],
            timeout=60,
        )
