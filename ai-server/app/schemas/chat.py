from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any


class MessageRole(BaseModel):
    """대화 히스토리의 개별 메시지 역할 및 내용 모델"""
    role: str = Field(..., description="메시지 작성 주체: 'user' 또는 'assistant'")
    content: str = Field(..., description="메시지 텍스트 내용")


class ChatRequest(BaseModel):
    """
    Spring Boot 백엔드에서 Python AI 서버로 전송하는 메인 채팅 요청 모델 (POST /internal/chat)
    """
    sessionId: str = Field(..., description="사용자 대화 세션 고유 식별자")
    query: str = Field(..., description="사용자가 입력한 원본 질문 텍스트")
    history: Optional[List[MessageRole]] = Field(default=[], description="최근 멀티턴 대화 히스토리 목록")
    model: Optional[str] = Field(default=None, description="선택적 LLM 모델 오버라이드 명칭")


class SourceDocument(BaseModel):
    """
    검색 결과로 반환되는 참고 Confluence 출처 문서 메타데이터 모델
    """
    documentId: str = Field(..., description="Confluence 원본 문서 ID")
    title: str = Field(..., description="Confluence 문서 제목")
    url: str = Field(..., description="Confluence 원본 문서 웹 링크 URL")
    author: Optional[str] = Field(default=None, description="문서 작성자 또는 최종 수정자 이름")
    category: Optional[str] = Field(default=None, description="문서 대분류 카테고리 (Confluence 조상 페이지 기준, 없으면 Space 키로 대체)")
    score: Optional[float] = Field(default=None, description="하이브리드 검색 유사도/정확도 점수")


class GraphContext(BaseModel):
    """
    Phase 5 지식 그래프(Neo4j) 탐색 결과 맥락 모델
    """
    entities: Optional[List[Dict[str, Any]]] = Field(default=[], description="추출된 그래프 엔티티 노드 목록")
    relations: Optional[List[Dict[str, Any]]] = Field(default=[], description="탐색된 엔티티 간 관계 엣지 목록")


class ChatResponse(BaseModel):
    """
    Python AI 서버가 Spring Boot 백엔드로 돌려주는 최종 응답 데이터 모델
    """
    sessionId: str = Field(..., description="대화 세션 식별자")
    rewrittenQuery: Optional[str] = Field(default=None, description="검색용으로 재작성된 단독 질문 (Stand-alone Query)")
    answer: str = Field(..., description="LLM이 최종 생성한 답변 텍스트")
    sources: List[SourceDocument] = Field(default=[], description="검색에 참고된 Confluence 출처 문서 목록")
    graphContext: Optional[GraphContext] = Field(default=None, description="지식 그래프 맥락 (Phase 5 이전에는 None)")
