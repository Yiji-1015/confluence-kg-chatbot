"""LLM 호출에 쓰이는 프롬프트/컨텍스트 조립 모음."""
from typing import Any, Dict, List

NO_CONTEXT_TEXT = "관련된 사내 Confluence 문서를 찾지 못했습니다."


def build_context_text(results: List[Dict[str, Any]]) -> str:
    """
    검색 결과(search_hybrid의 반환값)를 LLM 프롬프트에 넣을 [Context] 문자열로 조립한다.

    실서비스(api/v1/chat.py)와 평가(evaluation/run_qa.py)가 각자 이 포맷을 만들다가
    서로 달라지면, 평가 점수가 실서비스와 다른 프롬프트를 측정하게 된다.
    그래서 조립은 여기 한 곳에서만 한다.

    첨부파일명을 반드시 포함한다. 검색은 BM25 필드 `attachments^1.0`으로 첨부명을 보지만,
    여기서 빼먹으면 **찾아낸 문서의 파일명을 LLM은 볼 수 없다.** "그 파일 있어?" 류의 질문에서
    문서는 1위로 찾아놓고 "확인할 수 없습니다"라고 답하게 된다
    (2026-09-16 실측: attachment 축 answer_correctness 0.367, hit은 0.800으로 정상.
     이 줄을 넣은 뒤 재측정에서 0.600으로 올랐다 — EVALUATION.md 5.4절).

    "파일명만"이라고 명시하는 이유는 첨부파일의 **본문이 색인되지 않기 때문**이다.
    이걸 알려주지 않으면 파일명만 보고 내용을 지어낸다.
    """
    if not results:
        return NO_CONTEXT_TEXT

    blocks = []
    for item in results:
        path_info = f" (경로: {item['path']})" if item.get("path") else ""
        header = f"[문서 제목: {item.get('title')}{path_info}]"
        attachments = [name for name in (item.get("attachments") or []) if name]
        if attachments:
            header += f"\n[첨부파일 — 파일명만 확인 가능, 본문은 색인되지 않음: {', '.join(attachments)}]"
        blocks.append(f"{header}\n{item.get('text', '')}")
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

    # 첨부파일명은 본문 앞에 별도 줄로 들어간다. 빠지면 "그 파일 있어?"에 답할 수 없다.
    _att = build_context_text([
        {"title": "취업규칙", "text": "본문", "attachments": ["20260325_취업규칙_V3", "별표1"]},
    ])
    assert _att == (
        "[문서 제목: 취업규칙]\n"
        "[첨부파일 — 파일명만 확인 가능, 본문은 색인되지 않음: 20260325_취업규칙_V3, 별표1]\n"
        "본문"
    ), _att
    # 빈 값이나 None은 걸러낸다 (색인 실패한 첨부가 빈 문자열로 들어온 적이 있다)
    assert build_context_text([{"title": "T", "text": "x", "attachments": []}]) == "[문서 제목: T]\nx"
    assert build_context_text([{"title": "T", "text": "x", "attachments": ["", None]}]) == "[문서 제목: T]\nx"
    print("prompts self-check OK")
