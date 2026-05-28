# MobileNetV3Small KServe Setup

This guide mirrors the MobileNetV3Large workflow so MobileNetV3Small can be
profiled with the same KServe and profiling script.

## 빠른 목차

| 섹션 | 내용 |
| --- | --- |
| [1. Export the ONNX Model](#1-export-the-onnx-model) | 모델 export pod 준비와 ONNX 생성 |
| [2. Deploy the InferenceService](#2-deploy-the-inferenceservice) | KServe InferenceService 배포 |
| [3. Smoke Test](#3-smoke-test) | `curl` 요청 테스트 |
| [4. Profile MobileNetV3Small](#4-profile-mobilenetv3small) | profiling script 실행과 결과 파일 |

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

kubectl cp scripts/export-mobilenet-v3-small.py \
  kserve-test/model-export-pod:/tmp/export-mobilenet-v3-small.py

kubectl exec -n kserve-test model-export-pod -- \
  python /tmp/export-mobilenet-v3-small.py
```

Check that KServe can see the ONNX model file.

```bash
kubectl exec -n kserve-test model-export-pod -- \
  find /pv/mobilenet-v3-small -maxdepth 2 -type f -o -type d
```

Expected structure:

```text
/pv/mobilenet-v3-small/model.onnx
```

## 2. Deploy the InferenceService

```bash
kubectl apply -f k8s/manifests/mobilenet-v3-small.yaml
kubectl wait -n kserve-test \
  --for=condition=Ready inferenceservice/mobilenet-v3-small \
  --timeout=600s
```

Initial resource settings match the MobileNetV3Large setup:

```text
cpu request/limit: 1
memory request: 1Gi
memory limit: 2Gi
containerConcurrency: 0
```

## 3. Smoke Test

Use the same `224x224x3` payload shape as the MobileNetV3Large test.

```bash
curl -v \
  -H "Host: mobilenet-v3-small.kserve-test.example.com" \
  -H "Content-Type: application/json" \
  -d @config/input.json \
  http://localhost:8080/v2/models/mobilenet-v3-small/infer
```

## 4. Profile MobileNetV3Small

Run the profiling script with the built-in local cluster and MobileNetV3Small
model profile. This is the same profiling path as MobileNetV3Large; output
files are saved under `results/mobilenet-v3-small/`.

```bash
python scripts/profiling-script.py \
  --cluster local \
  --model mobilenet-v3-small
```

The generated files are:

```text
results/mobilenet-v3-small/profiling_runs.json
results/mobilenet-v3-small/profiling_summary.json
```

## Related Docs

- [CLI command reference](./CLI_COMMANDS.md)
- [Metrics collection](./METRICS_COLLECTION.md)
- [Profiling plan](./profiling-plan.md)
