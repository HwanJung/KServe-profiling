# CLI Commands

이 문서는 `kserve-practice` 작업에서 자주 쓰는 CLI 명령을 목적별로 정리한다.
명령은 프로젝트 루트(`/home/hwan/projects/kserve-practice`)에서 실행하는 기준이다.

## 빠른 목차

| 섹션 | 내용 |
| --- | --- |
| [1. Cluster Context](#1-cluster-context) | kube context, kind cluster, namespace 확인 |
| [2. KServe And Knative Setup Checks](#2-kserve-and-knative-setup-checks) | Knative/KServe 설정 확인과 patch |
| [3. Model Storage](#3-model-storage) | PV/PVC, model store/export pod |
| [4. Export Models](#4-export-models) | ONNX model export |
| [5. Deploy InferenceServices](#5-deploy-inferenceservices) | InferenceService 배포 |
| [6. Port Forwarding](#6-port-forwarding) | ingress, Prometheus, Grafana port-forward |
| [7. Smoke Tests](#7-smoke-tests) | `curl`, `hey` 요청 테스트 |
| [8. Profiling CLI](#8-profiling-cli) | profiling script 실행 |
| [9. Prometheus Queries](#9-prometheus-queries) | Prometheus API 조회 |
| [10. Result Inspection](#10-result-inspection) | profiling 결과 확인 |
| [11. Cleanup](#11-cleanup) | 리소스 삭제 |

## 자주 쓰는 순서

```bash
kubectl port-forward -n istio-system svc/istio-ingressgateway 8080:80
kubectl port-forward -n observability svc/knative-kube-prometheus-st-prometheus 9090:9090
kubectl apply -f k8s/manifests/mobilenet-v3-large.yaml
kubectl wait -n kserve-test --for=condition=Ready inferenceservice/mobilenet-v3-large --timeout=600s
python scripts/profiling-script.py --cluster local --model mobilenet-v3-large
```

## 1. Cluster Context

현재 Kubernetes context와 kind 클러스터를 확인한다.

```bash
kubectl config current-context
kind get clusters
kubectl get nodes -o wide
```

주요 namespace를 확인한다.

```bash
kubectl get ns
```

KServe, Knative, 관측성 관련 리소스 상태를 빠르게 확인한다.

```bash
kubectl get pods -n kserve
kubectl get pods -n knative-serving
kubectl get pods -n istio-system
kubectl get pods -n observability
kubectl get inferenceservice -A
kubectl get ksvc -A
```

## 2. KServe And Knative Setup Checks

Knative Serving 설정을 확인한다.

```bash
kubectl get knativeserving -n knative-serving knative-serving -o yaml
kubectl get configmap -n knative-serving config-deployment -o yaml
kubectl get cm -n knative-serving config-observability -o yaml
```

`queue-proxy`를 KServe qpext 이미지로 맞춘다.

```bash
kubectl patch knativeserving knative-serving \
  -n knative-serving \
  --type merge \
  -p '{"spec":{"config":{"deployment":{"queue-sidecar-image":"kserve/qpext:v0.17.0","registries-skipping-tag-resolving":"nvcr.io,index.docker.io"}}}}'
```

Profiling용 request metric export interval을 `5s`로 설정한다.

```bash
kubectl patch cm config-observability \
  -n knative-serving \
  --type merge \
  -p '{"data":{"request-metrics-export-interval":"5s"}}'
```

기존 Revision Pod에 관측성 설정을 다시 반영하려면 InferenceService annotation을 변경해 새 Revision을 만든다.

```bash
kubectl patch isvc mobilenet-v3-large \
  -n kserve-test \
  --type merge \
  -p "{\"metadata\":{\"annotations\":{\"profiling.knative.dev/observability-refresh\":\"$(date +%s)\"}}}"
```

## 3. Model Storage

PV/PVC와 모델 저장소 Pod를 만든다.

```bash
kubectl apply -f k8s/manifests/pv-pvc.yaml
kubectl apply -f k8s/manifests/model-store-pod.yaml
kubectl wait -n kserve-test --for=condition=Ready pod/model-store-pod --timeout=300s
```

PV/PVC 상태를 확인한다.

```bash
kubectl get pv,pvc -A
```

PVC 안의 모델 파일을 확인한다.

```bash
kubectl exec -n kserve-test model-store-pod -- \
  find /pv -maxdepth 4 -type f -o -type d
```

모델 export 전용 Pod를 만든다.

```bash
kubectl apply -f k8s/manifests/model-export-pod.yaml
kubectl wait -n kserve-test --for=condition=Ready pod/model-export-pod --timeout=300s
```

## 4. Export Models

MobileNetV3Large export dependency를 설치하고 export script를 실행한다.

```bash
kubectl exec -n kserve-test model-export-pod -- \
  pip install tf2onnx onnx onnxruntime

kubectl cp scripts/export-mobilenet-v3-large.py \
  kserve-test/model-export-pod:/tmp/export-mobilenet-v3-large.py

kubectl exec -n kserve-test model-export-pod -- \
  python /tmp/export-mobilenet-v3-large.py
```

MobileNetV3Small을 export한다.

```bash
kubectl cp scripts/export-mobilenet-v3-small.py \
  kserve-test/model-export-pod:/tmp/export-mobilenet-v3-small.py

kubectl exec -n kserve-test model-export-pod -- \
  python /tmp/export-mobilenet-v3-small.py
```

ResNet50을 export한다.

```bash
kubectl cp scripts/export-resnet50.py \
  kserve-test/model-export-pod:/tmp/export-resnet50.py

kubectl exec -n kserve-test model-export-pod -- \
  python /tmp/export-resnet50.py
```

export script는 로컬에서도 help를 확인할 수 있다.

```bash
python scripts/export-mobilenet-v3-large.py --help
python scripts/export-mobilenet-v3-small.py --help
python scripts/export-resnet50.py --help
```

## 5. Deploy InferenceServices

MobileNetV3Large를 배포하고 Ready 상태를 기다린다.

```bash
kubectl apply -f k8s/manifests/mobilenet-v3-large.yaml
kubectl wait -n kserve-test \
  --for=condition=Ready inferenceservice/mobilenet-v3-large \
  --timeout=600s
```

MobileNetV3Small을 배포한다.

```bash
kubectl apply -f k8s/manifests/mobilenet-v3-small.yaml
kubectl wait -n kserve-test \
  --for=condition=Ready inferenceservice/mobilenet-v3-small \
  --timeout=600s
```

ResNet50을 배포한다.

```bash
kubectl apply -f k8s/manifests/resnet50.yaml
kubectl wait -n kserve-test \
  --for=condition=Ready inferenceservice/resnet50 \
  --timeout=600s
```

배포 상태와 URL을 확인한다.

```bash
kubectl get isvc -n kserve-test
kubectl describe isvc -n kserve-test mobilenet-v3-large
kubectl get pods -n kserve-test
```

## 6. Port Forwarding

KServe ingress gateway를 로컬 `8080`으로 연결한다.

```bash
kubectl port-forward -n istio-system svc/istio-ingressgateway 8080:80
```

Prometheus를 로컬 `9090`으로 연결한다.

```bash
kubectl port-forward -n observability svc/knative-kube-prometheus-st-prometheus 9090:9090
```

Grafana가 필요하면 로컬 `3000`으로 연결한다.

```bash
kubectl port-forward -n observability svc/knative-grafana 3000:80
```

## 7. Smoke Tests

MobileNetV3Large v2 infer 요청을 보낸다.

```bash
curl -v \
  -H "Host: mobilenet-v3-large.kserve-test.example.com" \
  -H "Content-Type: application/json" \
  -d @config/input.json \
  http://localhost:8080/v2/models/mobilenet-v3-large/infer
```

MobileNetV3Small 요청을 보낸다.

```bash
curl -v \
  -H "Host: mobilenet-v3-small.kserve-test.example.com" \
  -H "Content-Type: application/json" \
  -d @config/input.json \
  http://localhost:8080/v2/models/mobilenet-v3-small/infer
```

ResNet50 요청을 보낸다.

```bash
curl -v \
  -H "Host: resnet50.kserve-test.example.com" \
  -H "Content-Type: application/json" \
  -d @config/input.json \
  http://localhost:8080/v2/models/resnet50/infer
```

`hey`로 단일 요청 preflight를 직접 확인한다.

```bash
hey -n 1 -c 1 \
  -m POST \
  -host mobilenet-v3-large.kserve-test.example.com \
  -H "Content-Type: application/json" \
  -D config/input.json \
  http://localhost:8080/v2/models/mobilenet-v3-large/infer
```

## 8. Profiling CLI

기본 profiling을 실행한다. 기본 출력은 `results/<model>/` 아래에 저장된다.

```bash
python scripts/profiling-script.py \
  --cluster local \
  --model mobilenet-v3-large
```

MobileNetV3Small profiling을 실행한다.

```bash
python scripts/profiling-script.py \
  --cluster local \
  --model mobilenet-v3-small
```

ResNet50 profiling을 실행한다.

```bash
python scripts/profiling-script.py \
  --cluster local \
  --model resnet50
```

CPU 후보와 client concurrency 후보를 직접 지정한다.

```bash
python scripts/profiling-script.py \
  --cluster local \
  --model mobilenet-v3-large \
  --cpus 500m,1,2 \
  --client-concurrencies 1,2,4,8,16,32
```

Profiling 전에 request metric 설정을 적용하고 새 Revision을 만든다.

```bash
python scripts/profiling-script.py \
  --cluster local \
  --model mobilenet-v3-large \
  --configure-observability
```

관측성 설정은 그대로 두고 새 Revision만 만들려면 `--refresh-observability`를 사용한다.

```bash
python scripts/profiling-script.py \
  --cluster local \
  --model mobilenet-v3-large \
  --refresh-observability
```

출력 위치를 바꾼다.

```bash
python scripts/profiling-script.py \
  --cluster local \
  --model mobilenet-v3-large \
  --output-dir results/manual-run
```

결과 파일을 명시적으로 지정한다.

```bash
python scripts/profiling-script.py \
  --cluster local \
  --model mobilenet-v3-large \
  --results-file results/profiling_runs.json \
  --summary-file results/profiling_summary.json
```

CLI help를 확인한다.

```bash
python scripts/profiling-script.py --help
```

## 9. Prometheus Queries

Prometheus API에 직접 PromQL을 던진다.

```bash
curl -G http://localhost:9090/api/v1/query \
  --data-urlencode 'query=sum(rate(http_server_request_duration_seconds_count{k8s_namespace_name="kserve-test",kn_service_name="mobilenet-v3-large-predictor",container_name="queue-proxy",http_request_method="POST"}[120s]))'
```

p95 latency를 조회한다.

```bash
curl -G http://localhost:9090/api/v1/query \
  --data-urlencode 'query=histogram_quantile(0.95,sum by (le)(rate(http_server_request_duration_seconds_bucket{k8s_namespace_name="kserve-test",kn_service_name="mobilenet-v3-large-predictor",container_name="queue-proxy",http_request_method="POST"}[120s])))'
```

model server CPU usage를 조회한다.

```bash
curl -G http://localhost:9090/api/v1/query \
  --data-urlencode 'query=sum(rate(container_cpu_usage_seconds_total{namespace="kserve-test",container="kserve-container"}[1m]))'
```

pod memory working set을 조회한다.

```bash
curl -G http://localhost:9090/api/v1/query \
  --data-urlencode 'query=sum(container_memory_working_set_bytes{namespace="kserve-test",pod=~"mobilenet-v3-large-predictor-.*",container!="",container!="POD"})'
```

## 10. Result Inspection

Profiling run 원본과 요약 결과를 확인한다.

```bash
cat results/mobilenet-v3-large/profiling_runs.json
cat results/mobilenet-v3-large/profiling_summary.json
```

`jq`가 있으면 추천 config만 확인한다.

```bash
jq '.recommendation' results/mobilenet-v3-large/profiling_summary.json
```

실패한 run만 확인한다.

```bash
jq '.[] | select(.valid == false)' results/mobilenet-v3-large/profiling_runs.json
```

## 11. Cleanup

InferenceService를 삭제한다.

```bash
kubectl delete inferenceservice -n kserve-test mobilenet-v3-large
kubectl delete inferenceservice -n kserve-test mobilenet-v3-small
kubectl delete inferenceservice -n kserve-test resnet50
```

모델 저장소 Pod와 export Pod를 삭제한다.

```bash
kubectl delete pod -n kserve-test model-store-pod --ignore-not-found=true
kubectl delete pod -n kserve-test model-export-pod --ignore-not-found=true
```

PV/PVC까지 삭제한다.

```bash
kubectl delete -f k8s/manifests/pv-pvc.yaml
```

kind 클러스터를 삭제한다.

```bash
kind delete cluster --name knative-cluster
```

## 12. Related Docs

- [kind KServe 실습 환경 정리](./KIND_KSERVE_SETUP.md)
- [KServe Metrics Collection](./METRICS_COLLECTION.md)
- [KServe Predictor Pod Profiling Plan](./profiling-plan.md)
- [MobileNetV3Large KServe Setup](./MOBILENET_V3_LARGE.md)
- [MobileNetV3Small KServe Setup](./MOBILENET_V3_SMALL.md)
