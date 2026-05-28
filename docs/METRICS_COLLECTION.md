# KServe Metrics Collection

이 문서는 현재 kind KServe 클러스터에서 Prometheus가 수집하는 추론 요청 메트릭 형태를 정리한다.

## 빠른 목차

| 섹션 | 내용 |
| --- | --- |
| [구성](#구성) | Prometheus, InferenceService, queue-proxy 기준 정보 |
| [관찰된 요청 메트릭](#관찰된-요청-메트릭) | 사용 가능한 latency/RPS metric |
| [주요 라벨 구조](#주요-라벨-구조) | PromQL filter에 필요한 label |
| [Profiling PromQL](#profiling-promql) | request, CPU, memory query |
| [조회 명령](#조회-명령) | `curl` 기반 Prometheus API 예시 |
| [참고 사항](#참고-사항) | metric 해석 시 주의점 |

## 구성

| 항목 | 값 |
| --- | --- |
| Prometheus namespace | `observability` |
| Prometheus service | `knative-kube-prometheus-st-prometheus` |
| Prometheus local URL | `http://localhost:9090` |
| InferenceService namespace | `kserve-test` |
| InferenceService | `mobilenet-v3-large` |
| Knative service | `mobilenet-v3-large-predictor` |
| Revision example | `mobilenet-v3-large-predictor-00001` |
| 요청 메트릭 발생 컨테이너 | `queue-proxy` |

Prometheus 포트포워딩:

```bash
kubectl port-forward -n observability svc/knative-kube-prometheus-st-prometheus 9090:9090
```

## 관찰된 요청 메트릭

기존에 예상했던 `request_predict_seconds_bucket`, `request_predict_seconds_count`는 현재 클러스터에서 수집되지 않는다.

현재 추론 요청 성능 측정에 사용할 수 있는 메트릭은 Knative queue-proxy에서 노출되는 OpenTelemetry HTTP server metric이다.

| 용도 | 메트릭 이름 |
| --- | --- |
| p95 latency histogram | `http_server_request_duration_seconds_bucket` |
| request count / RPS | `http_server_request_duration_seconds_count` |
| total latency sum | `http_server_request_duration_seconds_sum` |
| request body size histogram | `http_server_request_body_size_bytes_bucket` |
| request body count | `http_server_request_body_size_bytes_count` |

현재 `profiling-script.py`는 요청 latency/RPS에 아래 두 메트릭을 사용한다.

```promql
http_server_request_duration_seconds_bucket
http_server_request_duration_seconds_count
```

CPU, throttling, memory 산정에는 kubelet/cAdvisor 계열 metric을 사용한다.

```promql
container_cpu_usage_seconds_total
container_cpu_cfs_throttled_periods_total
container_cpu_cfs_periods_total
container_memory_working_set_bytes
```

## 주요 라벨 구조

`http_server_request_duration_seconds_count`의 현재 라벨 예시:

```text
container_name="queue-proxy"
http_request_method="POST"
http_response_status_code="200"
instance="mobilenet-v3-large-predictor-00001-deployment-648b7d6656-6b627"
job="mobilenet-v3-large-predictor"
k8s_namespace_name="kserve-test"
k8s_pod_name="mobilenet-v3-large-predictor-00001-deployment-648b7d6656-6b627"
kn_configuration_name="mobilenet-v3-large-predictor"
kn_revision_name="mobilenet-v3-large-predictor-00001"
kn_route_tag="kn:disabled"
kn_service_name="mobilenet-v3-large-predictor"
network_protocol_name="http"
network_protocol_version="1.1"
server_address="10.244.0.29"
server_port="8012"
service_instance_id="mobilenet-v3-large-predictor-00001-deployment-648b7d6656-6b627"
service_name="mobilenet-v3-large-predictor"
service_version="unknown"
url_scheme="http"
```

중요한 점:

- KServe/Knative 요청 메트릭에는 `namespace`가 아니라 `k8s_namespace_name` 라벨이 붙는다.
- 서비스 필터는 `kn_service_name="mobilenet-v3-large-predictor"` 또는 `job="mobilenet-v3-large-predictor"`로 잡을 수 있다.
- 실제 추론 요청 메트릭은 `queue-proxy` 컨테이너에서 관측된다.
- `http_server_request_duration_seconds_bucket`에는 histogram bucket 라벨인 `le`가 추가된다.
- 현재 관찰된 요청은 `http_request_method="POST"`, `http_response_status_code="200"` 형태다.

## Profiling PromQL

Profiling metric 설정:

| 항목 | 값 |
| --- | --- |
| Prometheus scrape interval | existing cluster setting, currently `5s` |
| OTLP push interval | `5s` |
| Measurement duration | `120s` |
| Request aggregation window | measurement duration, default `120s` |
| CPU rate window | `1m` |
| Memory peak | `query_range` sample max |

Prometheus scrape interval은 profiling 절차에서 변경하지 않고 기존 클러스터 설정을 유지한다. 현재 scrape 상태에서는 `30s` request aggregation window가 너무 짧아 샘플 부족으로 빈 결과가 나올 수 있었다. Profiling에서는 warmup을 제외한 measurement duration 전체를 request aggregation window로 사용한다.

### Request Metrics

Request latency/RPS는 측정 종료 시각에 Prometheus instant query를 한 번 실행해 산정한다. 이때 `rate(...[<measurement_duration>])`의 range selector는 warmup을 제외한 실제 측정 구간 길이와 맞춘다. 기본 측정 시간이 `120s`이면 request query도 `[120s]`를 사용한다.

이 방식은 `query_range`로 rolling p95를 여러 번 계산한 뒤 최댓값을 고르는 방식보다 run 전체의 대표 latency/RPS에 가깝다. 단, Prometheus는 scrape된 sample만 계산에 사용하므로 측정 종료 후 scrape lag를 둔 다음 query한다.

p95 latency:

```promql
histogram_quantile(
  0.95,
  sum by (le) (
    rate(http_server_request_duration_seconds_bucket{
      k8s_namespace_name="kserve-test",
      kn_service_name="mobilenet-v3-large-predictor",
      container_name="queue-proxy",
      http_request_method="POST"
    }[120s])
  )
)
```

RPS:

```promql
sum(
  rate(http_server_request_duration_seconds_count{
    k8s_namespace_name="kserve-test",
    kn_service_name="mobilenet-v3-large-predictor",
    container_name="queue-proxy",
    http_request_method="POST"
  }[120s])
)
```

측정 시간이 바뀌면 request query의 range selector도 같은 값으로 바꾼다.

```promql
rate(http_server_request_duration_seconds_count{...}[<measurement_duration>])
```

### CPU Metrics

CPU usage와 throttling은 측정 구간 중 평균/최댓값을 함께 기록하기 위해 `query_range`로 조회한다. 각 sample은 `rate(...[1m])`로 계산하고, run의 측정 시작/종료 시각을 `query_range`의 `start`/`end`로 사용한다.

CPU usage:

```promql
sum(
  rate(container_cpu_usage_seconds_total{
    namespace="kserve-test",
    container="kserve-container"
  }[1m])
)
```

CPU throttling ratio:

```promql
sum(rate(container_cpu_cfs_throttled_periods_total{
  namespace="kserve-test",
  container="kserve-container"
}[1m]))
/
sum(rate(container_cpu_cfs_periods_total{
  namespace="kserve-test",
  container="kserve-container"
}[1m]))
```

### Pod Memory Metrics

Memory request/limit 산정에는 model server 컨테이너만 보지 않고 predictor pod 전체 working set을 사용한다. `container!="", container!="POD"` 필터는 pause/infra container series를 제외하고, 같은 predictor pod 안의 model server와 queue-proxy sidecar를 함께 합산하기 위한 것이다.

Pod memory working set:

```promql
sum(
  container_memory_working_set_bytes{
    namespace="kserve-test",
    pod=~"mobilenet-v3-large-predictor-.*",
    container!="",
    container!="POD"
  }
)
```

Pod memory peak during one `120s` measurement window:

```promql
max_over_time(
  (
    sum(
      container_memory_working_set_bytes{
        namespace="kserve-test",
        pod=~"mobilenet-v3-large-predictor-.*",
        container!="",
        container!="POD"
      }
    )
  )[120s:]
)
```

Pod memory peak for a known measurement duration:

```promql
max_over_time(
  (
    sum(
      container_memory_working_set_bytes{
        namespace="kserve-test",
        pod=~"mobilenet-v3-large-predictor-.*",
        container!="",
        container!="POD"
      }
    )
  )[<measurement_duration>:]
)
```

Run-scoped peak from Prometheus HTTP API `query_range`:

```promql
sum(
  container_memory_working_set_bytes{
    namespace="kserve-test",
    pod=~"mobilenet-v3-large-predictor-.*",
    container!="",
    container!="POD"
  }
)
```

`query_range`를 사용할 때는 위 run-scoped query를 run의 측정 시작/종료 시각으로 조회하고, 반환된 sample의 최댓값을 해당 run의 `memory_peak_bytes`로 기록한다. 이렇게 하면 warmup 구간을 제외한 실제 측정 구간 peak만 계산할 수 있다.

Container-level memory working set breakdown:

```promql
sum by (pod, container) (
  container_memory_working_set_bytes{
    namespace="kserve-test",
    pod=~"mobilenet-v3-large-predictor-.*",
    container!="",
    container!="POD"
  }
)
```

## 조회 명령

전체 메트릭 이름 조회:

```bash
curl -sS 'http://localhost:9090/api/v1/label/__name__/values'
```

HTTP request 계열 메트릭 이름 필터:

```bash
python3 -c "import json, urllib.request; data=json.load(urllib.request.urlopen('http://localhost:9090/api/v1/label/__name__/values'))['data']; print('\n'.join(n for n in data if 'http_server_request' in n))"
```

현재 요청 count metric 라벨 확인:

```bash
python3 -c "import json, urllib.parse, urllib.request; q='http_server_request_duration_seconds_count{k8s_namespace_name=\"kserve-test\",kn_service_name=\"mobilenet-v3-large-predictor\",container_name=\"queue-proxy\",http_request_method=\"POST\"}'; u='http://localhost:9090/api/v1/query?'+urllib.parse.urlencode({'query':q}); print(json.dumps(json.load(urllib.request.urlopen(u)), indent=2))"
```

PromQL 결과 확인:

```bash
python3 -c "import json, urllib.parse, urllib.request; q='sum(rate(http_server_request_duration_seconds_count{k8s_namespace_name=\"kserve-test\",kn_service_name=\"mobilenet-v3-large-predictor\",container_name=\"queue-proxy\",http_request_method=\"POST\"}[120s]))'; u='http://localhost:9090/api/v1/query?'+urllib.parse.urlencode({'query':q}); print(json.dumps(json.load(urllib.request.urlopen(u)), indent=2))"
```

## 참고 사항

- 요청이 없는 idle 상태에서는 RPS가 `0`이고, p95 latency는 `NaN`으로 반환될 수 있다.
- request latency/RPS는 측정 종료 시각 기준 instant query로 계산한다.
- CPU usage, CPU throttling, memory peak는 Prometheus range query 결과에서 `NaN`을 제외하고 계산한다.
- `http_response_status_code`를 필터에 넣으면 성공 응답만 볼 수 있지만, 실패 응답을 포함한 전체 트래픽 측정에는 제외하는 편이 낫다.
- `server_port="8012"`는 queue-proxy의 server-side 관측 지점이고, `http_client_request_duration_seconds_*`에서는 backend 호출 대상인 `server_port="8080"`이 보일 수 있다.
