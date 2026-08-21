import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings

# Langfuse 환경변수 주입 (Python SDK 자동 인식용)
# router보다 먼저 실행되어야 함: chat_router가 import되면서 get_client()가 호출되므로
# 이 주입이 늦으면 SDK가 키 없이 초기화되어 버림
if settings.LANGFUSE_PUBLIC_KEY:
    os.environ["LANGFUSE_PUBLIC_KEY"] = settings.LANGFUSE_PUBLIC_KEY
if settings.LANGFUSE_SECRET_KEY:
    os.environ["LANGFUSE_SECRET_KEY"] = settings.LANGFUSE_SECRET_KEY
if settings.LANGFUSE_HOST:
    os.environ["LANGFUSE_HOST"] = settings.LANGFUSE_HOST
    os.environ["LANGFUSE_BASEURL"] = settings.LANGFUSE_HOST

from app.api.v1.chat import router as chat_router

# FastAPI 웹 애플리케이션 생성 및 설명 설정
app = FastAPI(
    title=settings.APP_NAME,
    description="Confluence Knowledge Graph Chatbot - Python FastAPI AI 엔진 서비스",
    version="1.0.0",
    debug=settings.DEBUG
)

# CORS 미들웨어 설정 (Spring Boot 백엔드 및 로컬 테스트 통신 허용)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 실무 운영 환경에서는 특정 도메인으로 제한 가능
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API 라우터 등록 (/internal/chat 메인 채팅 엔드포인트)
app.include_router(chat_router)


@app.get("/internal/health", tags=["Health Check"])
async def health_check():
    """
    Docker 및 Spring Boot 백엔드 헬스체크용 엔드포인트.
    AI 엔진의 정상 구동 여부와 주요 연동 서비스(LiteLLM, ES, Neo4j)의 설정 URL을 반환합니다.
    """
    return {
        "status": "healthy",
        "app_name": settings.APP_NAME,
        "litellm_url": settings.LITELLM_BASE_URL,
        "elasticsearch_url": settings.ELASTICSEARCH_URL,
        "neo4j_uri": settings.NEO4J_URI
    }


if __name__ == "__main__":
    import uvicorn
    # 로컬 개발용 서버 직접 실행 (포트 8000)
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
