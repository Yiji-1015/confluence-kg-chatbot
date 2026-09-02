"""
AI 엔진 계측. Prometheus 지표와 Langfuse 트레이스를 같은 지점에서 남긴다.

두 도구는 답하는 질문이 다르다.
- Prometheus: "지금 어느 단계가 느린가"를 전체 요청에 대한 분포로 본다 (p95, 에러율).
- Langfuse: "이 요청 하나가 왜 느렸나"를 단계별로 펼쳐 본다.

같은 `stage()` 하나로 둘 다 남기므로 계측 지점이 어긋나지 않는다. 따로 두면 한쪽만
갱신되고 다른 쪽이 낡아 서로 다른 이야기를 하게 된다.
"""
import logging
import time
from contextlib import contextmanager

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import Response

# Langfuse v4. v2의 langfuse.decorators는 제거됐다. 미설치나 API 변경 시에도
# 서비스는 계속 동작해야 하므로 실패하면 무해한 대체물로 내려간다.
try:
    from langfuse import get_client, observe, propagate_attributes

    _LANGFUSE_OK = True
except Exception as _exc:  # pragma: no cover
    # 조용히 넘어가면 추적이 죽은 것을 아무도 모른다. 실제로 그렇게 한 번 놓쳤다.
    logging.getLogger(__name__).warning(
        "Langfuse 초기화 실패, 트레이싱 없이 계속합니다: %s", _exc
    )
    _LANGFUSE_OK = False

    def observe(*args, **kwargs):
        def decorator(f):
            return f
        return decorator


def langfuse_enabled() -> bool:
    return _LANGFUSE_OK


# RAG 단계는 소요 시간의 자릿수가 다르다. ES 검색은 수십 ms, LLM 생성은 수 초~수십 초다.
# 기본 버킷은 10초에서 끝나 LLM 지연을 전부 마지막 버킷에 몰아넣어 p95를 못 읽는다.
_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 60.0)

# 어느 단계가 병목인지 답하는 핵심 지표. stage 라벨로 embedding/search/generation을 가른다.
STAGE_SECONDS = Histogram(
    "rag_stage_duration_seconds",
    "RAG 파이프라인 단계별 소요 시간",
    ["stage"],
    buckets=_BUCKETS,
)

# 처리량과 에러율. status=error 비율이 곧 이 서비스의 에러율이다.
REQUESTS = Counter(
    "rag_requests_total",
    "AI 엔진 채팅 요청 수",
    ["status"],
)

# 검색이 0건을 돌려주면 답변은 반드시 실패한다. 지연이 아닌 "조용한 실패"를 잡는 지표다.
RETRIEVED = Histogram(
    "rag_retrieved_documents",
    "요청당 검색된 문서 수",
    buckets=(0, 1, 2, 3, 4, 5, 10),
)

# 모델 라우팅 분포. 비싼 모델로 쏠리면 비용과 지연이 함께 오른다.
MODEL_SELECTED = Counter(
    "rag_model_selected_total",
    "동적 라우팅으로 선택된 모델",
    ["model"],
)

# 단계 실패를 따로 센다. 전체 실패만 보면 어디서 깨졌는지 알 수 없다.
STAGE_ERRORS = Counter(
    "rag_stage_errors_total",
    "RAG 단계별 예외 발생 수",
    ["stage", "exception"],
)


@contextmanager
def stage(name: str, **trace_data):
    """
    RAG 단계 하나를 계측한다. Prometheus 히스토그램과 Langfuse span을 함께 남긴다.

    예외가 나면 소요 시간과 실패를 모두 기록하고 그대로 올려보낸다. 삼키면 상위에서
    같은 예외를 또 처리하게 되고, 지연 기록이 빠져 "실패한 요청은 빨랐다"고 보이게 된다.
    """
    started = time.perf_counter()
    span_cm = None
    span = None
    if _LANGFUSE_OK:
        try:
            span_cm = get_client().start_as_current_observation(name=f"rag.{name}")
            span = span_cm.__enter__()
        except Exception:
            span_cm = None

    try:
        yield span
    except Exception as exc:
        STAGE_ERRORS.labels(stage=name, exception=type(exc).__name__).inc()
        raise
    finally:
        elapsed = time.perf_counter() - started
        STAGE_SECONDS.labels(stage=name).observe(elapsed)
        if span_cm is not None:
            try:
                if trace_data:
                    span.update(metadata=trace_data)
                span_cm.__exit__(None, None, None)
            except Exception:
                pass


@contextmanager
def trace_attributes(session_id=None, tags=None, metadata=None):
    """트레이스 전체에 세션/태그를 붙인다. Langfuse가 없으면 아무것도 하지 않는다."""
    if not _LANGFUSE_OK:
        yield
        return
    try:
        with propagate_attributes(session_id=session_id, tags=tags, metadata=metadata):
            yield
    except Exception:
        yield


def observe_rag(name: str):
    """요청 전체를 하나의 트레이스로 묶는 데코레이터."""
    return observe(name=name)


def record_request(status: str) -> None:
    REQUESTS.labels(status=status).inc()


def record_retrieved(count: int) -> None:
    RETRIEVED.observe(count)


def record_model(model: str) -> None:
    MODEL_SELECTED.labels(model=model).inc()


def flush_traces() -> None:
    if _LANGFUSE_OK:
        try:
            get_client().flush()
        except Exception:
            pass


def metrics_response() -> Response:
    """Prometheus 스크레이프 응답. prometheus_client가 프로세스/GC 지표도 함께 낸다."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
