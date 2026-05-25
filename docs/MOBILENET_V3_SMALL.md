# MobileNetV3Small KServe Setup

This guide mirrors the MobileNetV3Large workflow so MobileNetV3Small can be
profiled with the same KServe and profiling script.

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

Run the existing profiling script with MobileNetV3Small service names. This is
the same profiling path as MobileNetV3Large; output files are saved under
`results/mobilenet-v3-small/`.

```bash
python scripts/profiling-script.py \
  --target-url http://localhost:8080/v2/models/mobilenet-v3-small/infer \
  --host-header mobilenet-v3-small.kserve-test.example.com \
  --inferenceservice mobilenet-v3-small \
  --kn-service mobilenet-v3-small-predictor \
  --inferenceservice-manifest k8s/manifests/mobilenet-v3-small.yaml \
  --cpus 500m,1,2
```

The generated files are:

```text
results/mobilenet-v3-small/profiling_runs.json
results/mobilenet-v3-small/profiling_summary.json
```
