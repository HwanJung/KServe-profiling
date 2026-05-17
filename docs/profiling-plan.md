# KServe Predictor Pod Profiling Plan

## Goal

- 목적은 single predictor pod에서 `p95 <= 500ms`와 CPU 안정성 기준을 만족하면서 `RPS / CPU limit`이 가장 높은 CPU와 `containerConcurrency` 조합을 찾고, memory request/limit은 profiling run의 peak memory 기반으로 산정하는 것이다.
- 최종 추천 후보는 탐색 단계의 반복 측정을 모두 통과한 후보 중에서만 선택한다.
- latency/RPS의 표준 지표는 Knative queue-proxy request metric이다. 현재 클러스터에서는 `kserve/qpext`가 queue-proxy 구현체지만, qpext 전용 scrape metric은 표준 지표로 쓰지 않는다.

## Metrics Setup

- request latency/RPS는 queue-proxy가 OTLP HTTP/protobuf로 Prometheus에 push하는 metric을 사용한다.
- Prometheus scrape interval은 profiling 절차에서 변경하지 않고 기존 클러스터 설정을 유지한다.
- profiling 중 Knative request metric export interval은 `5s`로 설정한다.

```bash
kubectl patch cm config-observability \
  -n knative-serving \
  --type merge \
  -p '{"data":{"request-metrics-export-interval":"5s"}}'
```

- 기존 Revision Pod에는 설정이 바로 반영되지 않으므로 InferenceService에 annotation을 변경해 새 Revision을 만든다.

```bash
kubectl patch isvc mobilenet-v3-large \
  -n kserve-test \
  --type merge \
  -p "{\"metadata\":{\"annotations\":{\"profiling.knative.dev/observability-refresh\":\"$(date +%s)\"}}}"
```

- 새 predictor Pod의 `OBSERVABILITY_CONFIG`에서 `requestMetrics.exportInterval`이 `5000000000` 또는 `5s`에 해당하는 값인지 확인한다.
- CPU usage/throttling과 memory peak는 kubelet/cAdvisor metric을 사용한다. Metrics API와 `kubectl top`에는 의존하지 않는다.

## Metrics Reference

Profiling에 사용하는 PromQL은 [METRICS_COLLECTION.md](./METRICS_COLLECTION.md)의 `Profiling PromQL` 섹션에 둔다. 이 plan은 실험 절차와 추천 기준만 정의한다.

## Measurement Settings

| 항목 | 값 |
| --- | --- |
| Prometheus scrape interval | existing cluster setting, currently `5s` |
| OTLP push interval | `5s` |
| Measurement duration | `120s` |
| Request aggregation window | measurement duration, default `120s` |
| CPU rate window | `1m` |
| Max avg CPU throttling ratio | `0.10` |
| Max CPU throttling ratio | `0.20` |
| Max avg CPU utilization ratio | `0.80` |
| Memory peak | `query_range` sample max |

## Experiment Procedure

1. Prometheus와 request metric 설정을 확인한다.
   - `Metrics Setup` 섹션의 설정을 적용하고 새 Revision 반영 여부를 확인한다.
   - Prometheus에서 latency, RPS, CPU, throttling, pod memory metric이 조회되는지 확인한다.

2. 고정 실험 조건을 설정한다.
   - 대상은 `kserve-test/mobilenet-v3-large` single replica로 둔다.
   - memory는 sweep하지 않고 충분히 큰 고정값을 사용한다. 기본값은 `4Gi`다.
   - CPU 후보는 `1`, `2`, `4`만 사용한다.
   - 각 run은 `30s` warmup 후 `120s` measurement로 실행한다.
   - warmup 구간은 latency, RPS, CPU, memory 집계에서 제외한다.
   - request latency/RPS는 measurement 종료 후 scrape lag를 둔 뒤 종료 시각 기준 instant query로 한 번 계산한다.
   - CPU usage/throttling과 memory는 measurement 구간에 대해 `query_range`로 조회하고 평균/최댓값 또는 peak를 계산한다.

3. CPU별 baseline을 측정한다.
   - 각 CPU 후보에 대해 `containerConcurrency=0`으로 실행한다.
   - client concurrency를 단계적으로 증가시키며 측정한다.
   - 각 client concurrency에서 p95 latency, Prometheus RPS, `hey` RPS, CPU usage, CPU throttling ratio, pod memory peak, restart/OOMKilled 여부를 기록한다.

4. CPU별 SLO 통과 최대 client concurrency를 찾는다.
   - `p95 <= 500ms`를 만족하는 최대 client concurrency를 `best_c`로 기록한다.
   - client concurrency 후보의 최댓값까지 모두 SLO를 통과하면 saturation point를 찾지 못한 것이므로 client concurrency 범위를 확장한다.
   - saturation point를 찾지 못한 run은 최종 추천에 쓰지 않는다.

5. `containerConcurrency` 후보를 생성한다.
   - 각 CPU별 `best_c`에서 `floor(best_c / 2)`, `best_c`, `ceil(best_c * 1.5)`를 후보로 만든다.
   - 중복 후보는 제거한다.
   - `1` 미만 값은 제외한다.
   - `0`은 unlimited 의미라서 튜닝 후보군에서는 제외하고, 필요하면 baseline run에서만 별도로 다룬다.

6. 탐색 run을 실행한다.
   - CPU 후보 `1`, `2`, `4`와 CPU별 `containerConcurrency` 후보 조합을 실행한다.
   - 각 조합은 2회 반복 측정한다.
   - invalid run 기준은 `Scoring` 섹션을 따른다.

7. 탐색 결과를 필터링한다.
   - 탐색 통과 기준은 `Scoring` 섹션을 따른다.
   - SLO를 통과해도 CPU throttling이나 CPU 사용률 기준을 넘는 후보는 추천 후보에서 제외한다.
   - `prom_rps_avg`와 `hey_rps` 차이가 큰 후보는 원인을 확인하기 전까지 추천 후보로 확정하지 않는다.

8. 통과 후보를 점수화한다.
   - 탐색을 통과한 후보만 selection 대상으로 삼는다.
   - primary score는 2회 반복 측정의 평균 `prom_rps_avg / cpu_limit`로 계산한다.
   - tie-breaker는 `Scoring` 섹션을 따른다.

9. 최종 후보를 선정한다.
   - 별도 상위 후보 반복 검증 run은 수행하지 않는다.
   - 탐색 단계의 2회 반복 측정 결과를 집계해 최종 후보를 선정한다.
   - RPS, CPU usage, CPU throttling ratio는 평균값을 사용한다.
   - p95 latency는 run 전체 측정 구간 p95를 기록한다.
   - SLO 통과 여부는 반복 run들의 worst run-level p95로 판단한다.

10. memory request/limit을 산정한다.
    - 최종 후보의 탐색 run별 `memory_peak_bytes` 최댓값을 `max_memory_peak_bytes`로 둔다.
    - `max_memory_peak_bytes * 1.3`으로 headroom을 적용한다.
    - 결과를 Gi 단위로 올림해 추천 memory request/limit으로 사용한다.

11. 산정된 memory로 최종 재검증한다.
    - CPU와 `containerConcurrency`는 최종 후보 값을 사용한다.
    - memory request/limit은 산정된 값을 사용한다.
    - 1회 재실행해 OOMKilled 없음, restart 증가 없음, p95 `<= 500ms`, CPU 안정성 기준 통과, RPS 급락 없음 여부를 확인한다.
    - 실패하면 memory headroom을 늘려 재검증한다.

12. 최종 결과를 정리한다.
    - 기록 항목은 `Output` 섹션을 따른다.

## Scoring

- 탐색 통과 기준: 2회 반복 측정이 모두 valid run이고 worst run-level p95 <= `500ms`이며 CPU 안정성 기준을 만족한다.
- CPU 안정성 기준:
  - 평균 CPU throttling ratio <= `0.10`
  - 최대 CPU throttling ratio <= `0.20`
  - 평균 CPU 사용률 / CPU limit <= `0.80`
- SLO를 통과하더라도 평균 CPU throttling ratio가 `0.10`을 넘거나 최대 CPU throttling ratio가 `0.20`을 넘는 후보는 추천에서 제외한다. 예를 들어 MobilenetV3Large의 `cpu=1` 후보처럼 p95가 `500ms` 이하라도 throttling이 `0.76` 이상이면 운영 안정형 추천 후보가 아니다.
- selection 대상: 탐색 통과 후보만 포함한다.
- primary score: 2회 반복 측정의 평균 `prom_rps_avg / cpu_limit`
- tie-breaker: 더 낮은 산정 memory limit, 더 낮은 `containerConcurrency`, 더 낮은 worst run-level p95, 더 낮은 CPU throttling ratio 순서로 적용한다.
- HTTP non-2xx, `hey` failure, pod restart 증가, OOMKilled, latency/RPS metric 누락, Prometheus query empty/NaN은 invalid run으로 기록한다.
- memory peak metric 누락 또는 Prometheus query empty/NaN은 memory 산정 불가로 기록하고 최종 추천 후보에서 제외한다.
- `prom_rps_avg`와 `hey_rps` 차이가 크면 warning으로 남기고 원인을 확인하기 전까지 추천 후보로 확정하지 않는다.

## Output

- 추천 CPU request/limit
- 추천 memory request/limit과 산정 근거: `max_memory_peak_bytes`, headroom factor, rounded value
- 추천 `containerConcurrency`
- SLO 통과 최대 client concurrency
- 평균 RPS, `RPS / CPU limit`, worst run-level p95
- 평균/최대 CPU usage, CPU throttling ratio, run별 memory peak
- CPU 안정성 기준과 기준 위반으로 제외된 후보의 사유
- `prom_rps_avg`와 `hey_rps` 차이
- 제외 후보별 실패 사유

## Assumptions

- 대상은 `kserve-test/mobilenet-v3-large` single replica InferenceService이다.
- replica autoscaling 최적화는 범위에서 제외한다.
- 모델 runtime 내부 metric과 qpext scrape metric은 이번 profiling의 표준 지표로 사용하지 않는다.
