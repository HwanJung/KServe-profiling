# kind KServe 실습 환경 정리

작성 기준: 2026-05-11, 현재 kube-context `kind-knative-cluster`

## 1. 클러스터 개요

현재 로컬 kind 환경에는 `kind`, `knative-cluster` 두 클러스터가 있고, 실습에 사용 중인 컨텍스트는 `kind-knative-cluster`이다.

```bash
kubectl config current-context
kind get clusters
kubectl get nodes -o wide
```

현재 노드 상태:

| 항목 | 값 |
| --- | --- |
| 노드 | `knative-cluster-control-plane` |
| Kubernetes | `v1.33.1` |
| OS | `Debian GNU/Linux 12 (bookworm)` |
| Runtime | `containerd://2.1.1` |
| 내부 IP | `172.21.0.2` |

## 2. KServe 설치

KServe는 upstream 저장소의 quick install 스크립트로 설치했다.

```bash
git clone https://github.com/kserve/kserve.git
cd kserve
./hack/setup/quick-install/kserve-knative-mode-full-install-helm.sh
```

설치 후 Helm 릴리스 상태:

| 릴리스 | 네임스페이스 | 차트 | 앱 버전 |
| --- | --- | --- | --- |
| `cert-manager` | `cert-manager` | `cert-manager-v1.17.0` | `v1.17.0` |
| `istio-base` | `istio-system` | `base-1.27.1` | `1.27.1` |
| `istiod` | `istio-system` | `istiod-1.27.1` | `1.27.1` |
| `istio-ingressgateway` | `istio-system` | `gateway-1.27.1` | `1.27.1` |
| `knative-operator` | `knative-operator` | `knative-operator-1.21.1` | |
| `kserve-crd` | `kserve` | `kserve-crd-v0.17.0` | `v0.17.0` |
| `kserve-resources` | `kserve` | `kserve-resources-v0.17.0` | `v0.17.0` |
| `kserve-runtime-configs` | `kserve` | `kserve-runtime-configs-v0.17.0` | `v0.17.0` |
| `knative` | `observability` | `kube-prometheus-stack-83.7.0` | `v0.90.1` |

주요 네임스페이스:

```bash
kubectl get ns
```

- `cert-manager`
- `istio-system`
- `knative-operator`
- `knative-serving`
- `kserve`
- `kserve-test`
- `observability`

## 3. Knative Serving 설정

Knative Serving은 `KnativeServing` CR을 통해 관리한다.

적용 파일:

- `knativeserving-qpext.yaml`
- `qpext_image_patch.yaml`
- `config-observability.yaml`

초기 `knativeserving-qpext.yaml`:

```yaml
apiVersion: operator.knative.dev/v1beta1
kind: KnativeServing
metadata:
  name: knative-serving
  namespace: knative-serving
spec:
  config:
    deployment:
      queue-sidecar-image: kserve/qpext:latest
```

실제 현재 설정은 `kserve/qpext:v0.17.0`을 사용한다. `qpext_image_patch.yaml`은 `config-deployment` ConfigMap 패치 형태로 남아 있지만, 현재 클러스터와 동일하게 유지하려면 `KnativeServing` CR에도 같은 값을 넣는 것이 맞다.

```yaml
spec:
  config:
    deployment:
      queue-sidecar-image: kserve/qpext:v0.17.0
      registries-skipping-tag-resolving: nvcr.io,index.docker.io
    domain:
      example.com: ""
```

확인 명령:

```bash
kubectl get knativeserving -n knative-serving knative-serving -o yaml
kubectl get configmap -n knative-serving config-deployment -o yaml
```

현재 클러스터와 동일하게 재현하려면 `KnativeServing` CR 자체에도 `v0.17.0` 설정을 넣어야 한다.

```bash
kubectl patch knativeserving knative-serving \
  -n knative-serving \
  --type merge \
  -p '{"spec":{"config":{"deployment":{"queue-sidecar-image":"kserve/qpext:v0.17.0","registries-skipping-tag-resolving":"nvcr.io,index.docker.io"}}}}'
```

## 4. 관측성 설정

`config-observability.yaml`로 Knative Serving 관측성 설정을 적용했다.

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: config-observability
  namespace: knative-serving
data:
  metrics-protocol: prometheus
  request-metrics-protocol: http/protobuf
  request-metrics-endpoint: http://knative-kube-prometheus-st-prometheus.observability.svc:9090/api/v1/otlp/v1/metrics
  tracing-protocol: http/protobuf
  tracing-endpoint: http://jaeger-collector.observability.svc:4318/v1/traces
  tracing-sampling-rate: "1"
```

확인 명령:

```bash
kubectl get cm -n knative-serving config-observability -o yaml
kubectl get pods -n observability
```

현재 `observability` 네임스페이스에는 Prometheus, Grafana, Alertmanager, kube-state-metrics, node-exporter가 실행 중이다.

## 5. 모델 저장소 PVC

ResNet 모델은 `kserve-test` 네임스페이스의 PVC에 저장한다.

적용 파일:

- `pv-pvc.yaml`
- `model-store-pod.yaml`

`pv-pvc.yaml`:

```yaml
apiVersion: v1
kind: PersistentVolume
metadata:
  name: resnet-pv
spec:
  storageClassName: manual
  capacity:
    storage: 5Gi
  accessModes:
    - ReadWriteOnce
  hostPath:
    path: /var/local/kserve-resnet
    type: DirectoryOrCreate
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: resnet-pvc
  namespace: kserve-test
spec:
  storageClassName: manual
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 5Gi
```

현재 상태:

| 리소스 | 상태 | 용량 | StorageClass |
| --- | --- | --- | --- |
| `resnet-pv` | `Bound` | `5Gi` | `manual` |
| `kserve-test/resnet-pvc` | `Bound` | `5Gi` | `manual` |

PVC 확인:

```bash
kubectl get pv,pvc -A
```

모델 파일은 `model-store-pod`를 통해 PVC의 `/pv`에 넣었다.

현재 모델 경로:

```text
/pv/resnet/123/saved_model.pb
/pv/resnet/123/variables/variables.index
/pv/resnet/123/variables/variables.data-00000-of-00001
```

확인 명령:

```bash
kubectl exec -n kserve-test model-store-pod -- find /pv -maxdepth 4 -type f -o -type d
```

## 6. ResNet50 InferenceService

적용 파일:

- `resnet50-cpu2.yaml`

```yaml
apiVersion: serving.kserve.io/v1beta1
kind: InferenceService
metadata:
  name: resnet50-cpu2
  namespace: kserve-test
  annotations:
    serving.kserve.io/enable-metric-aggregation: "true"
    serving.kserve.io/enable-prometheus-scraping: "true"
spec:
  predictor:
    minReplicas: 1
    maxReplicas: 1
    containerConcurrency: 0
    model:
      modelFormat:
        name: tensorflow
      runtime: kserve-tensorflow-serving
      storageUri: "pvc://resnet-pvc/resnet/"
      resources:
        requests:
          cpu: "2"
          memory: "4Gi"
        limits:
          cpu: "2"
          memory: "8Gi"
```

현재 상태:

| 항목 | 값 |
| --- | --- |
| InferenceService | `kserve-test/resnet50-cpu2` |
| URL | `http://resnet50-cpu2.kserve-test.example.com` |
| Ready | `True` |
| Deployment mode | `Knative` |
| Runtime | `kserve-tensorflow-serving` |
| Model state | `Loaded` |
| Revision | `resnet50-cpu2-predictor-00001` |

확인 명령:

```bash
kubectl get inferenceservice -A
kubectl get ksvc -A
kubectl get pods -n kserve-test
```

## 7. Istio Ingress Gateway

현재 Istio Ingress Gateway 서비스:

```bash
kubectl get svc -n istio-system istio-ingressgateway
```

| 항목 | 값 |
| --- | --- |
| Type | `LoadBalancer` |
| External IP | `<pending>` |
| HTTP NodePort | `31527` |
| HTTPS NodePort | `30164` |

kind 환경에서는 `LoadBalancer`의 외부 IP가 기본적으로 할당되지 않는다. 로컬에서 요청을 보낼 때는 보통 port-forward를 사용한다.

```bash
kubectl port-forward -n istio-system svc/istio-ingressgateway 8080:80
```

그 다음 Host 헤더를 지정해 호출한다.

```bash
curl -v \
  -H "Host: resnet50-cpu2.kserve-test.example.com" \
  -H "Content-Type: application/json" \
  --data @input.json \
  http://localhost:8080/v1/models/resnet50-cpu2:predict
```

## 8. 부하 테스트 스크립트

`profiling-script.py`는 `hey`와 Prometheus API를 사용해 동시성별 성능을 측정한다.

주요 설정:

| 항목 | 값 |
| --- | --- |
| Prometheus | `http://localhost:9090` |
| Target | `http://localhost:8080/v1/models/resnet50-cpu2:predict` |
| Payload | `input.json` |
| 결과 파일 | `sweep_results.json` |
| 동시성 범위 | `1` - `10` |
| 테스트 시간 | `60s` |
| p95 SLO | `300ms` |

실행 전 포트포워딩:

```bash
kubectl port-forward -n istio-system svc/istio-ingressgateway 8080:80
kubectl port-forward -n observability svc/knative-kube-prometheus-st-prometheus 9090:9090
```

실행:

```bash
python3 profiling-script.py
```

현재 `sweep_results.json`에는 `concurrency=1` 실행 결과가 남아 있으나, Prometheus 메트릭 `request_predict_seconds_bucket`, `request_predict_seconds_count`를 찾지 못했고 일부 요청이 `404`, `connection refused`로 실패한 이력이 있다. 재측정할 때는 Ingress port-forward와 Host 헤더, Prometheus 메트릭 수집 상태를 먼저 확인해야 한다.

## 9. 전체 재적용 순서

새 kind 클러스터에서 같은 구성을 다시 만들 때의 순서:

```bash
# 1. KServe/Knative/Istio/cert-manager 설치
git clone https://github.com/kserve/kserve.git
cd kserve
./hack/setup/quick-install/kserve-knative-mode-full-install-helm.sh
cd ..

# 2. 테스트 네임스페이스 생성
kubectl create namespace kserve-test

# 3. Knative queue sidecar 및 observability 설정
kubectl apply -f knativeserving-qpext.yaml
kubectl patch knativeserving knative-serving \
  -n knative-serving \
  --type merge \
  -p '{"spec":{"config":{"deployment":{"queue-sidecar-image":"kserve/qpext:v0.17.0","registries-skipping-tag-resolving":"nvcr.io,index.docker.io"}}}}'
kubectl apply -f config-observability.yaml

# 4. 모델 저장소 준비
kubectl apply -f pv-pvc.yaml
kubectl apply -f model-store-pod.yaml

# 5. PVC에 TensorFlow SavedModel 배치
kubectl exec -n kserve-test -it model-store-pod -- bash

# 6. InferenceService 배포
kubectl apply -f resnet50-cpu2.yaml
```

## 10. 상태 점검 명령 모음

```bash
kubectl get ns
helm list -A
kubectl get pods -A
kubectl get knativeserving -n knative-serving knative-serving
kubectl get inferenceservice -A
kubectl get ksvc -A
kubectl get pv,pvc -A
kubectl get svc -n istio-system istio-ingressgateway
```
