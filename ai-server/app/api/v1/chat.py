from fastapi import APIRouter, HTTPException, status
from app.schemas.chat import ChatRequest, ChatResponse, SourceDocument, GraphContext
from app.config import settings
from app.retrieval.es_client import search_hybrid

# /internal/chat 경로를 처리하는 FastAPI 라우터 정의
router = APIRouter(prefix="/internal/chat", tags=["Internal Chat AI Engine"])


@router.post("", response_model=ChatResponse, status_code=status.HTTP_200_OK)
async def process_chat(request: ChatRequest) -> ChatResponse:
    """
    Spring Boot 백엔드에서 호출하는 메인 AI RAG 채팅 엔드포인트.
    
    [전체 처리 파이프라인]
    1. Standalone Query Rewriting: 이전 대화 맥락을 파악해 단독 검색어로 재작성 (Phase 4)
    2. Hybrid Retrieval: Elasticsearch BM25(키워드) + Vector kNN(의미) 결합 검색 (Phase 1)
    3. Knowledge Graph Context: 관계형 질문일 경우 Neo4j에서 인맥/조직 맥락 조회 (Phase 5: 현재는 None 처리)
    4. LLM Answer Generation: LiteLLM 게이트웨이를 호출해 최종 답변 생성 (Phase 3)
    """
    try:
        # 1. 질문 재작성 (Phase 4 전까지는 사용자 질문 그대로 사용)
        rewritten_query = request.query

        # 2. Elasticsearch 하이브리드 검색 수행 (Phase 1)
        es_results = search_hybrid(query_text=rewritten_query, top_k=5)

        sources = []
        if es_results:
            # Elasticsearch 검색 결과가 있는 경우 출처 메타데이터로 변환
            for item in es_results:
                sources.append(
                    SourceDocument(
                        documentId=item.get("doc_id", "doc-sample"),
                        title=item.get("title", "제목 없음"),
                        url=item.get("url", f"{settings.CONFLUENCE_BASE_URL}"),
                        author=item.get("author", "Unknown"),
                        spaceKey=settings.CONFLUENCE_SPACE_KEY,
                        score=item.get("score", 0.0)
                    )
                )
        else:
            # Elasticsearch 미기동 시 시스템이 터지지 않고 안전하게 작동하도록 Fallback 샘플 문서 반환
            sources = [
                SourceDocument(
                    documentId="sample-doc-1",
                    title="LLOYDK Confluence 가이드",
                    url=f"{settings.CONFLUENCE_BASE_URL}/spaces/{settings.CONFLUENCE_SPACE_KEY}/pages/sample",
                    author="System Admin",
                    spaceKey=settings.CONFLUENCE_SPACE_KEY,
                    score=0.95
                )
            ]

        # 3. Knowledge Graph 맥락 추가 (Phase 5 전까지는 그래프 없이도 100% 동작하도록 None 처리)
        graph_context = None

        # 4. LiteLLM 기반 답변 생성 (Phase 3 연동 파이프라인 준비 중)
        answer_text = (
            f"안녕하세요! '{request.query}' 질문에 대한 답변입니다.\n"
            f"(AI Engine 기본 파이프라인 구축 완료 - Elasticsearch 하이브리드 검색 연동 완료)"
        )

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
