from fastapi import APIRouter, HTTPException, status
from app.schemas.chat import ChatRequest, ChatResponse, SourceDocument, GraphContext
from app.config import settings
from app.retrieval.es_client import search_hybrid
from app.llm.litellm_client import embed_texts, generate_answer
from app.llm.model_router import select_optimal_model

# Langfuse Observability 트레이싱 데코레이터
try:
    from langfuse.decorators import observe, langfuse_context
except ImportError:
    # langfuse 미설치 시 no-op 데코레이터
    def observe(*args, **kwargs):
        def decorator(f):
            return f
        return decorator
    langfuse_context = None

# /internal/chat 경로를 처리하는 FastAPI 라우터 정의
router = APIRouter(prefix="/internal/chat", tags=["Internal Chat AI Engine"])


@router.post("", response_model=ChatResponse, status_code=status.HTTP_200_OK)
@observe(name="confluence-rag-chat")
async def process_chat(request: ChatRequest) -> ChatResponse:
    """
    Spring Boot 백엔드에서 호출하는 메인 AI RAG 채팅 엔드포인트.
    """
    try:
        # 1. 질문 재작성 (Phase 4 전까지는 사용자 질문 그대로 사용)
        rewritten_query = request.query.strip()

        # 2. 동적 모델 라우팅 (12자 미만 or 1000자 이상 시 gpt-4o, 일반은 deepseek-chat)
        selected_model, routing_reason = select_optimal_model(
            query=rewritten_query,
            history=request.history,
            override_model=request.model
        )

        if langfuse_context:
            langfuse_context.update_current_trace(
                session_id=request.sessionId,
                input=request.query,
                tags=["confluence-rag", "hybrid-search", selected_model],
                metadata={
                    "selected_model": selected_model,
                    "routing_reason": routing_reason,
                    "query_length": len(rewritten_query),
                    "history_turns": len(request.history or [])
                }
            )

        # 3. 질문 임베딩 생성 (OpenAI text-embedding-3-small via LiteLLM)
        query_vectors = embed_texts([rewritten_query])
        query_vector = query_vectors[0] if query_vectors else None

        # 4. Elasticsearch 하이브리드 검색 수행 (BM25 + Vector kNN)
        es_results = search_hybrid(
            query_text=rewritten_query,
            query_vector=query_vector,
            top_k=3
        )

        sources = []
        context_blocks = []

        if es_results:
            for item in es_results:
                sources.append(
                    SourceDocument(
                        documentId=item.get("doc_id", "doc-sample"),
                        title=item.get("title", "제목 없음"),
                        url=item.get("url", settings.CONFLUENCE_BASE_URL),
                        author=item.get("author") or item.get("primary_contributor") or "Unknown",
                        spaceKey=item.get("category") or item.get("space_key") or settings.CONFLUENCE_SPACE_KEY,
                        score=item.get("score", 0.0)
                    )
                )
                # LLM 프롬프트에 들어갈 Context 블록 조립
                path_info = f" (경로: {item['path']})" if item.get("path") else ""
                context_blocks.append(f"[문서 제목: {item.get('title')}{path_info}]\n{item.get('text', '')}")

        # 5. Context 결합
        if context_blocks:
            context_text = "\n\n---\n\n".join(context_blocks)
        else:
            context_text = "관련된 사내 Confluence 문서를 찾지 못했습니다."

        # 6. LiteLLM 기반 최종 답변 생성 (동적 라우팅된 모델 사용)
        answer_text = generate_answer(
            query=rewritten_query,
            context=context_text,
            model=selected_model
        )

        # 7. Knowledge Graph 맥락 (Phase 5 전까지는 None)
        graph_context = None

        # Langfuse 트레이스 즉시 전송 (비동기 버퍼 flush)
        if langfuse_context:
            try:
                langfuse_context.flush()
            except Exception:
                pass

        return ChatResponse(
            sessionId=request.sessionId,
            rewrittenQuery=rewritten_query,
            answer=answer_text,
            sources=sources,
            graphContext=graph_context
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"AI Engine 채팅 처리 실패: {str(e)}"
        )

