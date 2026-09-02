import os
import logging
from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI, Response, status
from app.config import settings

logger = logging.getLogger(__name__)

# Langfuse 환경변수 주입 (Python SDK 자동 인식용)
if settings.LANGFUSE_PUBLIC_KEY:
    os.environ["LANGFUSE_PUBLIC_KEY"] = settings.LANGFUSE_PUBLIC_KEY
if settings.LANGFUSE_SECRET_KEY:
    os.environ["LANGFUSE_SECRET_KEY"] = settings.LANGFUSE_SECRET_KEY
if settings.LANGFUSE_HOST:
    os.environ["LANGFUSE_HOST"] = settings.LANGFUSE_HOST
    os.environ["LANGFUSE_BASEURL"] = settings.LANGFUSE_HOST

from app.api.v1.chat import router as chat_router
from app.observability import metrics_response
from app.llm.litellm_client import embed_texts_async
from app.retrieval.es_client import get_es_client, search_hybrid_async


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI 생명주기 관리자 (서버 시작 시 자동 Warmup 실행)
    - LiteLLM/OpenAI 임베딩 커넥션 풀 예열
    - Elasticsearch 하이브리드 검색 및 인덱스 캐시 예열
    - 사용자의 '첫 질문' 콜드 스타트 지연(Cold Start Latency)을 사전에 제거
    """
    print("[Warmup] AI Engine 커넥션 풀 및 캐시 예열 시작...")
    try:
        # 1. 임베딩 엔진(LiteLLM -> OpenAI) 예열
        vectors = await embed_texts_async(["warmup"])
        query_vector = vectors[0] if vectors else None

        # 2. Elasticsearch 하이브리드 검색 엔진(BM25 + kNN) 캐시 예열
        if query_vector:
            await search_hybrid_async(
                query_text="warmup",
                query_vector=query_vector,
                top_k=1
            )

        print("[Warmup] ✅ AI Engine 예열 완료! (LiteLLM, Elasticsearch 소켓 및 캐시 활성화됨)")
    except Exception as e:
        print(f"[Warmup Warning] 예열 중 오류 발생 (기본 서비스는 계속 동작합니다): {e}")

    yield

    print("[Shutdown] AI Engine 정상 종료")


# FastAPI 웹 애플리케이션 생성 및 설명 설정
app = FastAPI(
    title=settings.APP_NAME,
    description="Confluence RAG Chatbot - Python FastAPI AI 엔진 서비스",
    version="1.0.0",
    debug=settings.DEBUG,
    lifespan=lifespan
)

# API 라우터 등록 (/internal/chat 메인 채팅 엔드포인트)
app.include_router(chat_router)


@app.get("/internal/health", tags=["Health Check"])
async def health_check(response: Response):
    """
    실제 의존성 연결을 확인하는 헬스체크.

    이전에는 설정 문자열만 돌려줘서 Elasticsearch나 LiteLLM이 죽어도 healthy를 반환했다.
    그 상태로는 "떴다"와 "쓸 수 있다"가 구분되지 않아, 컨테이너는 정상인데 모든 요청이
    실패하는 상황을 healthcheck가 놓친다. 의존성 하나라도 끊기면 503을 돌려준다.
    """
    checks = {}

    try:
        es = get_es_client()
        checks["elasticsearch"] = "up" if es.ping() else "down"
    except Exception:
        checks["elasticsearch"] = "down"

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{settings.LITELLM_BASE_URL}/v1/models")
            checks["litellm"] = "up" if r.status_code == 200 else "down"
    except Exception:
        checks["litellm"] = "down"

    healthy = all(v == "up" for v in checks.values())
    if not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "healthy" if healthy else "degraded",
        "app_name": settings.APP_NAME,
        "dependencies": checks,
    }


@app.get("/metrics", tags=["Observability"], include_in_schema=False)
async def metrics():
    """Prometheus 스크레이프 엔드포인트 (RAG 단계별 지연, 에러율, 프로세스 지표)."""
    return metrics_response()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
