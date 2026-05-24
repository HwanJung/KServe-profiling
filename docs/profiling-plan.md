# KServe Predictor Pod Profiling Plan

## Goal

- 목적은 single predictor pod에서 `p95 <= 500ms`와 CPU 안정성 기준을 만족하면서 가장 효율이 좋은 Pod spec을 찾는 것이다. CPU와 `containerConcurrency`는 한 Pod 안에서 효율적으로 처리할 수 있는 지점까지만 키우고, 그 이후의 전체 처리량 증가는 replica/autoscaling으로 확장하는 것을 전제로 한다.
- primary efficiency는 `RPS / CPU limit`이다. client concurrency를 올렸을 때 RPS가 concurrency 증가율에 충분히 따라오지 못하면 같은 Pod를 더 밀어 넣는 대신 더 낮은 `containerConcurrency`를 선택한다.
- 최종 추천 후보는 검증 단계의 반복 측정을 모두 통과한 후보 중에서만 선택한다.
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
| Max CPU throttling ratio | `0.10` |
| Memory peak | `query_range` sample max |
| Client concurrency coarse search | `1, 2, 4, 8, ...` |
| Client concurrency refinement | 효율 경계 주변 linear 또는 binary refinement |
| Min marginal RPS efficiency | `0.70` |

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
   - client concurrency는 먼저 `1, 2, 4, 8, ...`처럼 exponential search로 증가시켜 SLO 실패 또는 RPS 효율 저하가 처음 나타나는 상한을 찾는다.
   - 각 client concurrency에서 p95 latency, Prometheus RPS, `hey` RPS, CPU usage, CPU throttling ratio, pod memory peak, restart/OOMKilled 여부를 기록한다.

4. CPU별 효율 경계 구간을 찾는다.
   - `p95 > 500ms`, HTTP/`hey` failure, pod restart/OOMKilled, 또는 marginal RPS efficiency가 기준 미만으로 떨어지는 client concurrency를 `bad_c`로 기록한다.
   - 직전의 valid client concurrency를 `last_good_c`로 기록한다.
   - marginal RPS efficiency는 `RPS 증가율 / client concurrency 증가율`로 계산한다.
   - 예를 들어 client concurrency가 `4 -> 8`로 `100%` 증가했는데 RPS가 `30%`만 증가하면 marginal RPS efficiency는 `0.30`이다.
   - marginal RPS efficiency가 `0.70` 미만이면 한 Pod 안에서 concurrency를 더 올리는 효율이 낮다고 보고, 그 직전 값을 `stable_c` 후보로 둔다.
   - client concurrency 후보의 최댓값까지 모두 SLO와 efficiency 기준을 통과하면 효율 경계를 찾지 못한 것이므로 client concurrency 범위를 확장한다.
   - 효율 경계를 찾지 못한 run은 최종 추천에 쓰지 않는다.

5. CPU별 client concurrency 경계를 보강 측정한다.
   - `last_good_c`와 `bad_c` 사이가 `8` 이하이면 모든 정수 값을 linear refinement로 측정한다.
   - 구간이 `8`보다 크면 binary refinement로 `p95 <= 500ms`와 marginal RPS efficiency 경계를 좁힌 뒤, 최종 경계 주변의 정수값을 다시 측정한다.
   - latency/RPS는 noisy할 수 있으므로 최종 경계 주변 값은 최소 2회 반복 측정한다.
   - 반복 측정을 모두 통과한 최대 client concurrency를 `stable_c`로 기록한다.
   - 운영 기준은 경계값 자체가 아니라 `stable_c` 이하의 효율 유지 구간에서 선택한다.

6. `containerConcurrency`를 설정한다.
   - baseline은 `containerConcurrency=0`에서 측정했으므로 각 CPU별 기본 추천값은 `containerConcurrency=stable_c`로 둔다.
   - 운영 안정성을 더 보수적으로 잡아야 하면 `stable_c`보다 한 단계 낮은 refinement 측정값을 보수 추천값으로 둔다.
   - `stable_c`보다 큰 값은 한 Pod 안에서 효율이 낮아지는 구간에 가까우므로 별도 후보로 확장하지 않는다.
   - `0`은 unlimited 의미라서 운영 추천값으로 쓰지 않고, baseline run에서만 사용한다.

7. 검증 run을 실행한다.
   - CPU 후보 `1`, `2`, `4`와 각 CPU별 추천 `containerConcurrency` 값을 실행한다.
   - 각 조합은 2회 반복 측정한다.
   - invalid run 기준은 `Scoring` 섹션을 따른다.

8. 검증 결과를 필터링한다.
   - 검증 통과 기준은 `Scoring` 섹션을 따른다.
   - SLO를 통과해도 CPU throttling 기준을 넘는 후보는 추천 후보에서 제외한다.
   - `prom_rps_avg`와 `hey_rps` 차이가 큰 후보는 원인을 확인하기 전까지 추천 후보로 확정하지 않는다.

9. 통과 후보를 점수화한다.
   - 검증을 통과한 후보만 selection 대상으로 삼는다.
   - primary score는 2회 반복 측정의 평균 `prom_rps_avg / cpu_limit`로 계산한다.
   - tie-breaker는 `Scoring` 섹션을 따른다.

10. 최종 후보를 선정한다.
   - 별도 상위 후보 반복 검증 run은 수행하지 않는다.
   - 검증 단계의 2회 반복 측정 결과를 집계해 최종 후보를 선정한다.
   - RPS, CPU usage, CPU throttling ratio는 평균값을 사용한다.
   - p95 latency는 run 전체 측정 구간 p95를 기록한다.
   - SLO 통과 여부는 반복 run들의 worst run-level p95로 판단한다.

11. memory request/limit을 산정한다.
    - 최종 후보의 검증 run별 `memory_peak_bytes` 최댓값을 `max_memory_peak_bytes`로 둔다.
    - `max_memory_peak_bytes * 1.3`으로 headroom을 적용한다.
    - 결과를 Gi 단위로 올림해 추천 memory request/limit으로 사용한다.

12. 산정된 memory로 최종 재검증한다.
    - CPU와 `containerConcurrency`는 최종 후보 값을 사용한다.
    - memory request/limit은 산정된 값을 사용한다.
    - 1회 재실행해 OOMKilled 없음, restart 증가 없음, p95 `<= 500ms`, CPU 안정성 기준 통과, RPS 급락 없음 여부를 확인한다.
    - 실패하면 memory headroom을 늘려 재검증한다.

13. 최종 결과를 정리한다.
    - 기록 항목은 `Output` 섹션을 따른다.

## Scoring

- 검증 통과 기준: 2회 반복 측정이 모두 valid run이고 worst run-level p95 <= `500ms`이며 CPU 안정성 기준을 만족한다.
- CPU 안정성 기준:
  - 평균 CPU throttling ratio <= `0.10`
  - 최대 CPU throttling ratio <= `0.10`
- CPU 사용률은 결과에 기록하지만 추천 후보 제외 기준으로 쓰지 않는다. CPU throttling이 CPU limit에 실제로 막힌 직접 신호이기 때문이다.
- SLO를 통과하더라도 평균 또는 최대 CPU throttling ratio가 `0.10`을 넘는 후보는 추천에서 제외한다. 예를 들어 MobilenetV3Large의 `cpu=1` 후보처럼 p95가 `500ms` 이하라도 throttling이 `0.76` 이상이면 운영 안정형 추천 후보가 아니다.
- selection 대상: 검증 통과 후보만 포함한다.
- primary score: 2회 반복 측정의 평균 `prom_rps_avg / cpu_limit`
- tie-breaker: 더 낮은 산정 memory limit, 더 낮은 `containerConcurrency`, 더 낮은 worst run-level p95, 더 낮은 CPU throttling ratio 순서로 적용한다.
- HTTP non-2xx, `hey` failure, pod restart 증가, OOMKilled, latency/RPS metric 누락, Prometheus query empty/NaN은 invalid run으로 기록한다.
- memory peak metric 누락 또는 Prometheus query empty/NaN은 memory 산정 불가로 기록하고 최종 추천 후보에서 제외한다.
- `prom_rps_avg`와 `hey_rps` 차이가 크면 warning으로 남기고 원인을 확인하기 전까지 추천 후보로 확정하지 않는다.

## Output

- 추천 CPU request/limit
- 추천 memory request/limit과 산정 근거: `max_memory_peak_bytes`, headroom factor, rounded value
- 추천 `containerConcurrency`
- `last_good_c`, `bad_c`, `stable_c`, refinement 방식
- 평균 RPS, `RPS / CPU limit`, worst run-level p95
- 평균/최대 CPU usage, CPU throttling ratio, run별 memory peak
- CPU throttling 기준과 기준 위반으로 제외된 후보의 사유
- `prom_rps_avg`와 `hey_rps` 차이
- 제외 후보별 실패 사유

## Assumptions

- 대상은 `kserve-test/mobilenet-v3-large` single replica InferenceService이다.
- replica autoscaling 최적화는 범위에서 제외한다.
- 모델 runtime 내부 metric과 qpext scrape metric은 이번 profiling의 표준 지표로 사용하지 않는다.
