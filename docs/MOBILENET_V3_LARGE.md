# MobileNetV3Large KServe Setup

This guide adds MobileNetV3Large beside the existing ResNet50 service so both
models can be compared with the same KServe and profiling workflow.

## 빠른 목차

| 섹션 | 내용 |
| --- | --- |
| [1. Export the ONNX Model](#1-export-the-onnx-model) | 모델 export pod 준비와 ONNX 생성 |
| [2. Deploy the InferenceService](#2-deploy-the-inferenceservice) | KServe InferenceService 배포 |
| [3. Smoke Test](#3-smoke-test) | `curl` 요청 테스트 |
| [4. Profile MobileNetV3Large](#4-profile-mobilenetv3large) | profiling script 실행과 결과 파일 |

## 1. Export the ONNX Model

Create a temporary model export pod that mounts the existing model PVC.

```bash
kubectl apply -f k8s/manifests/model-export-pod.yaml
kubectl wait -n kserve-test --for=condition=Ready pod/model-export-pod --timeout=300s
```

Copy and run the export script.

```bash
kubectl exec -n kserve-test model-export-pod -- \
  pip install tf2onnx onnx onnxruntime

kubectl cp scripts/export-mobilenet-v3-large.py \
  kserve-test/model-export-pod:/tmp/export-mobilenet-v3-large.py

kubectl exec -n kserve-test model-export-pod -- \
  python /tmp/export-mobilenet-v3-large.py
```

Check that KServe can see the ONNX model file.

```bash
kubectl exec -n kserve-test model-export-pod -- \
  find /pv/mobilenet-v3-large -maxdepth 2 -type f -o -type d
```

Expected structure:

```text
/pv/mobilenet-v3-large/model.onnx
```

## 2. Deploy the InferenceService

```bash
kubectl apply -f k8s/manifests/mobilenet-v3-large.yaml
kubectl wait -n kserve-test \
  --for=condition=Ready inferenceservice/mobilenet-v3-large \
  --timeout=600s
```

Initial resource settings are intentionally lighter than ResNet50:

```text
cpu request/limit: 1
memory request: 1Gi
memory limit: 2Gi
containerConcurrency: 0
```

## 3. Smoke Test

Use the same `224x224x3` payload shape as the ResNet50 test.

```bash
curl -v \
  -H "Host: mobilenet-v3-large.kserve-test.example.com" \
  -H "Content-Type: application/json" \
  -d @config/input.json \
  http://localhost:8080/v2/models/mobilenet-v3-large/infer
```

## 4. Profile MobileNetV3Large

Run the profiling script with the built-in local cluster and model profile. By
default, output files are saved under `results/mobilenet-v3-large/`.

```bash
python scripts/profiling-script.py \
  --cluster local \
  --model mobilenet-v3-large
```

The generated files are:

```text
results/mobilenet-v3-large/profiling_runs.json
results/mobilenet-v3-large/profiling_summary.json
```

To use a custom location, pass `--output-dir`. To preserve the old flat file
layout, pass `--results-file` and `--summary-file` explicitly.

## Related Docs

- [CLI command reference](./CLI_COMMANDS.md)
- [Metrics collection](./METRICS_COLLECTION.md)
- [Profiling plan](./profiling-plan.md)
