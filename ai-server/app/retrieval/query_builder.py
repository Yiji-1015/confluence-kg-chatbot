"""
검색용 질의를 만드는 헬퍼.

멀티턴에서 사용자는 앞 대화를 이어 "그건 며칠까지야?"처럼 묻는다. 이 문장만으로는
검색이 아무것도 못 찾는다. 지시어에는 주제어가 없기 때문이다. 답변 생성에는 대화
이력이 전달되지만 검색에는 반영되지 않아, 멀티턴에서 검색이 조용히 빗나간다.

직전 사용자 발화를 검색어 앞에 붙여 주제어를 복원한다. LLM으로 질의를 재작성하는
방식이 더 정확하지만 호출이 한 번 늘고 지연도 늘어난다. 붙이기만 해도 BM25는
주제어 토큰을, 임베딩은 주제 벡터를 되찾으므로 먼저 이쪽을 쓴다.
"""
from typing import Any, List, Optional


def build_search_query(query: str, history: Optional[List[Any]] = None, turns: int = 1) -> str:
    """
    직전 사용자 발화 turns개를 앞에 붙인 검색용 질의를 돌려준다.

    turns=0이면 원문 그대로 (기능 끄기). history 항목은 role/content를 가진 객체나
    dict 모두 받는다. 답변 생성에는 원본 query를 그대로 쓰고 검색어만 바꾼다.
    """
    if turns <= 0 or not history:
        return query

    previous = []
    for h in history:
        role = h.get("role") if isinstance(h, dict) else getattr(h, "role", None)
        content = h.get("content") if isinstance(h, dict) else getattr(h, "content", None)
        if role == "user" and content:
            previous.append(content.strip())

    if not previous:
        return query
    return " ".join(previous[-turns:] + [query])


if __name__ == "__main__":
    H = [
        {"role": "user", "content": "연차 신청 절차 알려줘"},
        {"role": "assistant", "content": "인사팀에 신청서를 제출하시면 됩니다."},
        {"role": "user", "content": "반차도 되나?"},
    ]
    assert build_search_query("그건 며칠까지야?", H) == "반차도 되나? 그건 며칠까지야?"
    assert build_search_query("그건?", H, turns=2) == "연차 신청 절차 알려줘 반차도 되나? 그건?"
    assert build_search_query("그건?", H, turns=0) == "그건?"
    assert build_search_query("연차 절차", None) == "연차 절차"
    assert build_search_query("연차 절차", []) == "연차 절차"
    # assistant 발화만 있으면 붙일 것이 없다
    assert build_search_query("그건?", [{"role": "assistant", "content": "네"}]) == "그건?"
    print("OK")
