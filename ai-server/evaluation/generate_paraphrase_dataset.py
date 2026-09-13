"""
[Paraphrase 평가셋 생성기 — confluence-rag-qa-v4-para]

confluence-rag-qa-v2의 채점 문항을 "문서 제목 단어를 쓰지 않고" 다시 쓴다.
정답 문서(expected_doc_ids)는 그대로 두므로, 질문 표현 외의 변수가 통제된다.

왜 필요한가:
v2의 생성 프롬프트는 "이 문서를 고유하게 식별할 수 있는 구체적인 단서 키워드를
질문 안에 반드시 포함하라"고 지시한다. 그 결과 채점 문항 38개 중 27개(71%)가
문서 제목 단어를 100% 담게 됐고, 평균 누출률이 84.7%였다.
이 조건은 BM25의 홈그라운드라 하이브리드 검색의 이득이 측정되지 않는다.

실행:
    docker exec -i rag-ai-server python -m evaluation.generate_paraphrase_dataset

출력은 stdout이며, paraphrase_dataset_items.py로 저장해 스냅샷으로 관리한다.
재생성하면 문항 표현이 달라지므로 측정값을 비교하려면 스냅샷을 고정해야 한다.
"""
import pprint
import re
import sys

from app.llm.litellm_client import generate_chat_completion
from evaluation.dataset_items import QA_DATASET_ITEMS

REWRITE_MODEL = "gpt-4o"


def _tokens(text):
    return {t for t in re.split(r"[^0-9A-Za-z가-힣]+", text or "") if len(t) >= 2}


def _scorable(item):
    """검색 지표로 채점 가능한 문항만 고른다 (run_qa._scorable_expected_ids와 같은 기준)."""
    meta = item.get("metadata") or {}
    if meta.get("known_gap"):
        return False
    return bool(meta.get("expected_doc_ids") and meta.get("doc_title"))


def _rewrite_prompt(item):
    meta = item["metadata"]
    return f"""아래는 사내 문서 검색 챗봇의 평가 질문입니다. 이 질문을 다시 써 주세요.

[원래 질문]
{item['input']}

[기대 답변]
{item['expected_output']}

[이 질문이 겨냥하는 문서]
- 제목: {meta.get('doc_title', '')}
- 경로: {meta.get('path') or meta.get('category', '')}

[다시 쓰기 규칙]
1. **문서 제목과 경로에 나오는 단어를 절대 쓰지 마세요.** 회사명, 팀명, 문서명, 프로젝트명도 쓰지 마세요.
2. 대신 그 내용을 **동의어나 풀어쓴 표현**으로 물어보세요. (예: "연차 규정" -> "쉬는 날 며칠이나 되는지")
3. 실제 직원이 챗봇에 물어보듯 **자연스러운 구어체**로 쓰세요.
4. 기대 답변은 그대로 나올 수 있어야 합니다. 묻는 내용 자체는 바꾸지 마세요.
5. 질문 한 문장만 출력하세요. 설명, 따옴표, 마크다운 금지."""


def main():
    targets = [it for it in QA_DATASET_ITEMS if _scorable(it)]
    print(f"# 대상 {len(targets)}문항 / 전체 {len(QA_DATASET_ITEMS)}", file=sys.stderr)

    items, leaks = [], []
    for i, src in enumerate(targets, 1):
        meta = src["metadata"]
        try:
            raw = generate_chat_completion(
                [{"role": "user", "content": _rewrite_prompt(src)}],
                model=REWRITE_MODEL,
            )
        except Exception as exc:
            print(f"# [{i}] 생성 실패, 건너뜀: {exc}", file=sys.stderr)
            continue

        question = raw.strip().strip('"').strip("'").split("\n")[0]
        title_tokens = _tokens(meta.get("doc_title"))
        leak = len(title_tokens & _tokens(question)) / len(title_tokens) if title_tokens else 0.0
        leaks.append(leak)

        items.append({
            "id": f"qa-v4-para-{i:03d}",
            "input": question,
            "metadata": {
                "source_id": src.get("id"),
                "expected_doc_ids": meta["expected_doc_ids"],
                "doc_title": meta.get("doc_title"),
                "category": meta.get("category"),
                "title_leak": round(leak, 3),
                "original_input": src["input"],
            },
        })
        print(f"# [{i:2}/{len(targets)}] 누출 {leak:>4.0%} | {question[:70]}", file=sys.stderr)

    print(f"\n# 생성 {len(items)}문항, 평균 제목단어 누출률 "
          f"{sum(leaks) / len(leaks):.1%}", file=sys.stderr)

    print("PARAPHRASE_QA_ITEMS = ", end="")
    pprint.pprint(items, indent=4, width=110, sort_dicts=False)


if __name__ == "__main__":
    main()
