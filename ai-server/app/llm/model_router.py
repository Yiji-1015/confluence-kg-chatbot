from typing import List, Optional, Tuple
from app.schemas.chat import MessageRole


def select_optimal_model(
    query: str,
    history: Optional[List[MessageRole]] = None,
    override_model: Optional[str] = None
) -> Tuple[str, str]:
    """
    사용자 질문의 길이와 멀티턴 컨텍스트 복잡도에 따라 최적의 LLM 모델을 동적으로 결정하는 라우팅 함수.

    [라우팅 규칙]
    1. override_model이 명시된 경우: 해당 모델 우선 적용
    2. 질문 길이 < 12자 (단어형/모호한 질문): 'gpt-4o' (정밀한 의도 파악 및 추론)
    3. 총 텍스트 길이 >= 1000자 (멀티턴 히스토리 + 질문 합산): 'gpt-4o' (복잡한 장문 컨텍스트 분석)
    4. 일반 표준 질문: 'deepseek-chat' (초고속 및 90% 이상 비용 절감)

    Returns:
        (선택된 모델명, 라우팅 사유)
    """
    if override_model:
        return override_model, f"manual_override ({override_model})"

    cleaned_query = query.strip()
    query_len = len(cleaned_query)

    # 전체 컨텍스트 길이 계산 (질문 + 이전 대화 히스토리 본문)
    history_len = sum(len(msg.content) for msg in (history or []))
    total_len = query_len + history_len

    # 규칙 1: 12글자 미만 (모호하거나 짧은 질문)
    # 짧은 질문일수록 단서가 적어 의도 파악에 실패하는 사례가 실사용에서 관찰됐다.
    # 비용 최적화가 아니라 "짧아서 못 알아듣는" 실패를 줄이려고 상위 모델로 올린다.
    if query_len < 12:
        return "gpt-4o", f"short_query ({query_len} chars < 12, intent clarification)"

    # 규칙 2: 1000자 이상 (복잡한 멀티턴/장문 질문)
    if total_len >= 1000:
        return "gpt-4o", f"long_context ({total_len} chars >= 1000, deep reasoning)"

    # 규칙 3: 표준 가성비 모델
    return "deepseek-chat", f"standard_query (query: {query_len} chars, total: {total_len} chars)"
