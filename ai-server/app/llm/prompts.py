"""LLM 호출에 쓰이는 프롬프트/컨텍스트 조립 모음."""
from typing import Any, Dict, List

NO_CONTEXT_TEXT = "관련된 사내 Confluence 문서를 찾지 못했습니다."


def build_context_text(results: List[Dict[str, Any]]) -> str:
    """
    검색 결과(search_hybrid의 반환값)를 LLM 프롬프트에 넣을 [Context] 문자열로 조립한다.

    실서비스(api/v1/chat.py)와 평가(evaluation/run_qa.py)가 각자 이 포맷을 만들다가
    서로 달라지면, 평가 점수가 실서비스와 다른 프롬프트를 측정하게 된다.
    그래서 조립은 여기 한 곳에서만 한다.
    """
    if not results:
        return NO_CONTEXT_TEXT

    blocks = []
    for item in results:
        path_info = f" (경로: {item['path']})" if item.get("path") else ""
        blocks.append(f"[문서 제목: {item.get('title')}{path_info}]\n{item.get('text', '')}")
    return "\n\n---\n\n".join(blocks)


RAG_SYSTEM_PROMPT = (
    "당신은 사내 Confluence 문서를 기반으로 답변하는 전문 AI 어시스턴트입니다.\n"
    "1. 사내 규정, 시스템, 프로젝트 등 업무 지식에 관한 질문은 반드시 주어진 [Context]에 근거하여 사실에 기반해 정확하게 답변하세요. Context에 없는 내용은 추측하지 말고 솔직하게 모른다고 답하세요.\n"
    "2. 사용자가 이전 대화 내용(예: '방금 내가 뭐라고 했지?', '앞서 말한 내용 요약해줘' 등)이나 인사/일상 대화를 건넨 경우에는, 전달된 대화 히스토리(history)를 바탕으로 자연스럽고 친절하게 응답하세요.\n"
    "3. [Context]가 실제 내용 없이 Confluence 데이터베이스 임베드 링크(예: '.../database/12345' 형태의 URL)만 있는 경우에는, 그 상세 행 데이터까지는 확인할 수 없다고 안내하며 해당 링크를 직접 확인하도록 안내하세요."
)


if __name__ == "__main__":
    # 실서비스와 평가가 공유하는 포맷이라 깨지면 양쪽이 같이 틀어진다.
    # 실행: cd ai-server && python -m app.llm.prompts
    assert build_context_text([]) == NO_CONTEXT_TEXT
    _out = build_context_text([
        {"title": "가이드", "path": "기획 / PoC", "text": "본문"},
        {"title": "경로없음", "text": "B"},
    ])
    assert _out == "[문서 제목: 가이드 (경로: 기획 / PoC)]\n본문\n\n---\n\n[문서 제목: 경로없음]\nB"
    print("prompts self-check OK")
