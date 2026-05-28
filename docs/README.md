# Documentation

`docs` 디렉터리는 KServe 실습 환경 재현, 모델 배포, 성능 측정, 코드 작성 규칙을 정리한다.
처음 보는 경우 아래 순서대로 읽으면 된다.

## 빠른 시작

| 순서 | 문서 | 용도 |
| --- | --- | --- |
| 1 | [CLI_COMMANDS.md](./CLI_COMMANDS.md) | 자주 쓰는 `kubectl`, `curl`, `hey`, profiling 명령 치트시트 |
| 2 | [KIND_KSERVE_SETUP.md](./KIND_KSERVE_SETUP.md) | kind 기반 KServe/Knative 환경 구성과 재적용 순서 |
| 3 | [MOBILENET_V3_LARGE.md](./MOBILENET_V3_LARGE.md) | MobileNetV3Large 모델 export, 배포, smoke test, profiling |
| 4 | [MOBILENET_V3_SMALL.md](./MOBILENET_V3_SMALL.md) | MobileNetV3Small 모델 export, 배포, smoke test, profiling |
| 5 | [METRICS_COLLECTION.md](./METRICS_COLLECTION.md) | Prometheus metric 구조와 profiling PromQL |
| 6 | [profiling-plan.md](./profiling-plan.md) | CPU/concurrency profiling 실험 기준과 추천값 산정 방식 |
| 7 | [PYTHON_COMMENT_CONVENTION.md](./PYTHON_COMMENT_CONVENTION.md) | Python 주석/docstring 작성 규칙 |

## 목적별 찾기

| 하고 싶은 일 | 보면 되는 문서 |
| --- | --- |
| 명령어만 빠르게 찾기 | [CLI_COMMANDS.md](./CLI_COMMANDS.md) |
| 로컬 kind 클러스터 상태를 재현하거나 점검하기 | [KIND_KSERVE_SETUP.md](./KIND_KSERVE_SETUP.md) |
| MobileNet 모델을 PVC에 export하고 배포하기 | [MOBILENET_V3_LARGE.md](./MOBILENET_V3_LARGE.md), [MOBILENET_V3_SMALL.md](./MOBILENET_V3_SMALL.md) |
| Prometheus에서 latency/RPS/CPU/memory를 조회하기 | [METRICS_COLLECTION.md](./METRICS_COLLECTION.md) |
| profiling 결과를 해석하거나 추천 기준을 바꾸기 | [profiling-plan.md](./profiling-plan.md) |
| Python 코드 주석 스타일을 맞추기 | [PYTHON_COMMENT_CONVENTION.md](./PYTHON_COMMENT_CONVENTION.md) |

## 문서 관리 기준

- 새 명령어는 먼저 [CLI_COMMANDS.md](./CLI_COMMANDS.md)에 추가한다.
- 특정 모델만의 절차는 해당 모델 문서에 둔다.
- 여러 모델에 공통인 metric, PromQL, scoring 기준은 모델 문서에 반복하지 않고 metric/profiling 문서로 연결한다.
- 클러스터 현재 상태가 바뀌면 [KIND_KSERVE_SETUP.md](./KIND_KSERVE_SETUP.md)의 작성 기준과 상태 표를 함께 갱신한다.
