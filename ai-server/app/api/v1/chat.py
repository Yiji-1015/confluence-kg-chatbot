import logging

from fastapi import APIRouter, HTTPException, status
from app.schemas.chat import ChatRequest, ChatResponse, SourceDocument
from app.config import settings
from app.retrieval.es_client import search_hybrid_async
from app.retrieval.query_builder import build_search_query
from app.llm.litellm_client import embed_texts_async, generate_answer_async
from app.llm.model_router import select_optimal_model
from app.llm.prompts import build_context_text

# Langfuse Observability 트레이싱 데코레이터 (OTel 기반)
try:
    from langfuse.decorators import observe, langfuse_context
except ImportError:
    def observe(*args, **kwargs):
        def decorator(f):
            return f
        return decorator
    langfuse_context = None

logger = logging.getLogger(__name__)

# /internal/chat 경로를 처리하는 FastAPI 라우터 정의
router = APIRouter(prefix="/internal/chat", tags=["Internal Chat AI Engine"])


@router.post("", response_model=ChatResponse, status_code=status.HTTP_200_OK)
@observe(name="confluence-rag-chat")
async def process_chat(request: ChatRequest) -> ChatResponse:
    """
    Spring Boot 백엔드에서 호출하는 메인 AI RAG 채팅 엔드포인트.
    """
    try:
        query = request.query.strip()

        # 1. 동적 모델 라우팅 (12자 미만 or 1000자 이상 시 gpt-4o, 일반은 deepseek-chat)
        selected_model, routing_reason = select_optimal_model(
            query=query,
            history=request.history,
            override_model=request.model
        )

        if langfuse_context:
            try:
                langfuse_context.update_current_trace(
                    session_id=request.sessionId,
                    input=request.query,
                    tags=["confluence-rag", "hybrid-search", selected_model],
                    metadata={
                        "selected_model": selected_model,
                        "routing_reason": routing_reason,
                        "query_length": len(query),
                        "history_turns": len(request.history or [])
                    }
                )
            except Exception:
                pass

        # 2. 검색용 질의 구성. 답변 생성에는 원본 query를 그대로 쓰고 검색어만 바꾼다.
        search_query = build_search_query(
            query, request.history, turns=settings.SEARCH_HISTORY_TURNS
        )

        # 3. 질문 임베딩 비동기 생성 (OpenAI text-embedding-3-small via LiteLLM)
        query_vectors = await embed_texts_async([search_query])
        query_vector = query_vectors[0] if query_vectors else None

        # 4. Elasticsearch 하이브리드 검색 비동기 수행 (BM25 + Vector kNN)
        es_results = await search_hybrid_async(
            query_text=search_query,
            query_vector=query_vector,
        )

        sources = [
            SourceDocument(
                documentId=str(item.get("doc_id", "")).strip() or "doc-sample",
                title=item.get("title", "제목 없음"),
                url=item.get("url", settings.CONFLUENCE_BASE_URL),
                author=item.get("author") or "Unknown",
                category=item.get("category") or item.get("space_key") or settings.CONFLUENCE_SPACE_KEY,
                score=item.get("score", 0.0)
            )
            for item in (es_results or [])
        ]

        # 4. Context 결합 (실서비스와 평가가 같은 포맷을 쓰도록 공용 헬퍼 사용)
        context_text = build_context_text(es_results)

        # 5. LiteLLM 기반 최종 답변 비동기 생성 (동적 라우팅된 모델 사용)
        answer_text = await generate_answer_async(
            query=query,
            context=context_text,
            model=selected_model,
            history=[{"role": h.role, "content": h.content} for h in (request.history or [])]
        )

        # Langfuse 트레이스 즉시 전송 (비동기 버퍼 flush)
        if langfuse_context:
            try:
                langfuse_context.flush()
            except Exception:
                pass

        return ChatResponse(
            sessionId=request.sessionId,
            answer=answer_text,
            sources=sources
        )

    except Exception:
        # 예외 원문(ES URL, 자격 힌트 등)이 응답으로 새지 않도록 로그에만 남긴다.
        logger.exception("[Chat] AI Engine 채팅 처리 실패 (sessionId: %s)", request.sessionId)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="AI Engine 채팅 처리에 실패했습니다."
        )
