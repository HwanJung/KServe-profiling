# Python 주석 컨벤션

## 목적

Python 코드는 먼저 이름, 구조, 작은 함수만으로 읽히는 것을 목표로 한다. 주석은 코드만으로 드러나지 않는 의도, 제약, 운영 맥락을 보완할 때만 사용한다.

## 언어

- 주석과 docstring은 한글로 작성한다.
- 주석은 짧고 구체적으로 쓴다.
- KServe, Knative, InferenceService, Prometheus, profiling, SLO, RPS, p95, throttling처럼 프로젝트에서 이미 쓰는 도메인 용어는 그대로 사용한다.

## 모듈 Docstring

스크립트나 모듈의 목적이 명확히 독립적이면 모듈 docstring을 사용한다.

```python
"""MobileNetV3Small 모델을 KServe용 ONNX로 내보낸다."""
```

중요한 운영 맥락이 필요한 경우가 아니면 모듈 docstring은 한 문장으로 유지한다.

## 함수와 클래스 Docstring

함수나 클래스가 공개 API이거나, 여러 모듈에서 재사용되거나, 이름과 시그니처만으로 동작이 명확하지 않을 때 docstring을 추가한다.

```python
def derive_model_name(args: argparse.Namespace) -> str:
    """CLI 인자나 추론 URL에서 모델 이름을 가져온다."""
```

코드만으로 충분히 명확한 작은 private helper에는 docstring을 붙이지 않는다.

## 인라인 주석

인라인 주석은 의도, 제약, 운영 맥락을 설명할 때 사용한다. 바로 다음 코드가 이미 말하고 있는 내용을 반복하지 않는다.

좋은 예:

```python
# 서버가 관리하는 필드를 제거해 manifest를 다시 적용할 수 있게 한다.
metadata.pop("resourceVersion", None)
```

피할 예:

```python
# resourceVersion을 제거한다.
metadata.pop("resourceVersion", None)
```

## 주석 위치

- 설명하는 코드 바로 위에 주석을 둔다.
- 동작이나 의도를 설명하는 주석은 완전한 문장으로 쓴다.
- 코드가 바뀌면 주석도 함께 갱신한다.
- 짧은 상수 목록이나 표 형태의 구조를 보완하는 경우가 아니면 줄 끝 주석은 피한다.

## 주석을 달아야 하는 경우

다음 경우에는 주석을 단다.

- API 호출만으로 명확하지 않은 Kubernetes 또는 KServe 동작
- profiling 가정, 임계값, 측정 방식의 trade-off
- 도구, 클러스터, 의존성 동작에 대한 workaround
- 의도가 분명하지 않은 데이터 변환
- 삭제, patch, rollout 로직 앞의 안전 제약

다음 경우에는 주석을 피한다.

- 단순한 값 할당
- 명확한 반복문이나 조건문
- 변수명을 바꾸거나 helper를 추출하면 더 명확해지는 코드
- 문서나 commit message에 남기는 편이 나은 과거 이력 설명

## TODO 주석

TODO 주석은 구체적인 후속 작업이 있을 때만 사용한다.

```python
# TODO: Prometheus query 지연을 측정한 뒤 고정 scrape lag를 대체한다.
```

다음처럼 모호한 TODO는 피한다.

```python
# TODO: 개선한다.
```

## Shebangs

실행 가능한 스크립트는 모듈 docstring 앞에 shebang을 둘 수 있다.

```python
#!/usr/bin/env python3
"""KServe 모델 후보에 대한 profiling을 실행한다."""
```

## 포맷

- PEP 8 주석 간격을 따른다. 블록 주석은 `# `로 시작한다.
- 주석은 프로젝트의 일반적인 줄 길이 안에서 유지한다.
- 모듈 docstring과 import 사이에는 빈 줄 하나를 둔다.
- 장식용 comment banner는 사용하지 않는다.
