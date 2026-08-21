"""
Elasticsearch에 색인된 실제 문서를 카테고리별로 샘플링해서, 각 문서 원문을 LLM에 주고
"이 내용에만 근거한 질문+답변"을 자동 생성한 뒤 dataset_items.py의 QA_DATASET_ITEMS에 이어붙인다.

질문을 사람이 지어내는 게 아니라, 문서 원문을 그대로 LLM에 주고 그 안에서만 답을 뽑게 하므로
기대답변이 실제 내용과 어긋날 위험이 적다 (완전히 없는 건 아니라서 이후 run_qa.py로 검증 필요).

실행:
    cd ai-server && .venv/bin/python -m evaluation.generate_dataset
"""
import json
import re

from app.retrieval.es_client import get_es_client
from app.config import settings
from app.llm.litellm_client import generate_chat_completion
from evaluation.dataset_items import QA_DATASET_ITEMS

# 카테고리별 목표 문항 수 (2026-08-21 재색인 시점 청크 분포 기준, 비중 대략 반영 + 소수 카테고리도 최소 커버)
CATEGORY_TARGETS = {
    "기획": 10,
    "Project": 8,
    "팀 회의록 (공개 운영 중)": 8,
    "LLOYDK에 오신 걸 환영합니다!": 6,
    "솔루션/개발": 6,
    "피앤씨": 3,
    "마케팅": 3,
    "커뮤니케이션 가이드": 2,
    "피드백 세션": 2,
    "기술 스택": 2,
    "프로젝트": 2,
    "엔지니어 역량": 1,
    "리더십": 1,
    "세일즈": 1,
}

GEN_MODEL = "gpt-4o-mini"
MAX_CONTEXT_CHARS = 1500

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _existing_doc_ids() -> set:
    ids = set()
    for item in QA_DATASET_ITEMS:
        ids.update(item.get("metadata", {}).get("expected_doc_ids", []))
    return ids


def _sample_docs_for_category(es, index: str, category: str, n: int, exclude_ids: set):
    """카테고리 안에서 서로 다른 doc_id를 n개 뽑고, 각 doc의 앞쪽 텍스트를 모아 반환."""
    res = es.search(index=index, body={
        "size": 300,
        "_source": ["doc_id", "title", "text", "chunk_index"],
        "query": {"term": {"category": category}},
        "sort": [{"doc_id": "asc"}, {"chunk_index": "asc"}],
    })

    by_doc = {}
    for hit in res["hits"]["hits"]:
        s = hit["_source"]
        doc_id = s["doc_id"]
        if doc_id in exclude_ids:
            continue
        if doc_id not in by_doc:
            by_doc[doc_id] = {"title": s["title"], "text": ""}
        if len(by_doc[doc_id]["text"]) < MAX_CONTEXT_CHARS:
            by_doc[doc_id]["text"] += ("\n" if by_doc[doc_id]["text"] else "") + s.get("text", "")

    picked = list(by_doc.items())[:n]
    return picked


def _generate_qa_for_doc(title: str, text: str):
    text = text[:MAX_CONTEXT_CHARS]
    prompt = (
        "아래는 사내 Confluence 문서 하나의 일부 내용입니다. 이 내용을 실제로 볼 법한 동료가 물어볼 만한, "
        "구체적이고 사실에 기반한 질문 하나와 그 정답을 만들어주세요.\n"
        "규칙:\n"
        "- 질문과 답은 반드시 주어진 텍스트 안의 내용만으로 답할 수 있어야 합니다. 텍스트에 없는 내용을 지어내지 마세요.\n"
        "- 표/양식 안내 문구뿐이라 구체적 사실이 없으면(예: 빈 템플릿), 그 문서가 '어떤 용도의 문서인지'를 묻는 질문으로 대신하세요.\n"
        "- 중요: 이 문서와 같은 종류(같은 팀 주간회의록, 같은 카테고리의 템플릿 등)의 다른 문서가 수십 개 더 있을 수 있습니다. "
        "'이 문서는', '이 회의는' 처럼 어떤 문서인지 특정 안 되는 표현만 쓰지 말고, 팀명/날짜/프로젝트명/고객사명 등 "
        "문서 제목이나 본문에 나온 구체적 단서를 질문 안에 자연스럽게 넣어서, 그 질문만 보고도 어느 문서를 말하는지 구분할 수 있게 하세요. "
        "(나쁜 예: '주간 미팅의 일시는 언제인가요?' / 좋은 예: '피앤씨팀 2월 23일자 주간미팅은 몇 시에 진행됐나요?')\n"
        "- 답은 1~2문장으로 간결하게.\n"
        "- 아래 JSON 형식으로만 출력하세요 (다른 텍스트 금지):\n"
        '{"question": "...", "answer": "..."}\n\n'
        f"[문서 제목: {title}]\n{text}"
    )
    raw = generate_chat_completion([{"role": "user", "content": prompt}], model=GEN_MODEL)
    match = _JSON_RE.search(raw)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        q, a = data.get("question", "").strip(), data.get("answer", "").strip()
        if not q or not a:
            return None
        return q, a
    except json.JSONDecodeError:
        return None


def main():
    es = get_es_client()
    index = settings.ELASTICSEARCH_INDEX
    exclude_ids = _existing_doc_ids()

    new_items = []
    next_id = len(QA_DATASET_ITEMS) + 1

    for category, n in CATEGORY_TARGETS.items():
        docs = _sample_docs_for_category(es, index, category, n, exclude_ids)
        print(f"[{category}] 후보 문서 {len(docs)}개 (목표 {n}개)")

        for doc_id, info in docs:
            qa = _generate_qa_for_doc(info["title"], info["text"])
            if not qa:
                print(f"  스킵 (생성 실패): {doc_id} {info['title']}")
                continue
            question, answer = qa
            item_id = f"qa-{next_id:03d}"
            next_id += 1
            new_items.append({
                "id": item_id,
                "input": question,
                "expected_output": answer,
                "metadata": {"expected_doc_ids": [doc_id], "category": category},
            })
            print(f"  {item_id} [{doc_id}] {info['title']}: {question}")

    print(f"\n총 {len(new_items)}개 문항 생성 완료. dataset_items.py에 반영합니다.")

    with open("evaluation/dataset_items.py", "r", encoding="utf-8") as f:
        content = f.read()

    insertion_lines = []
    for item in new_items:
        insertion_lines.append("    {")
        insertion_lines.append(f'        "id": {item["id"]!r},')
        insertion_lines.append(f'        "input": {item["input"]!r},')
        insertion_lines.append(f'        "expected_output": {item["expected_output"]!r},')
        insertion_lines.append(f'        "metadata": {item["metadata"]!r},')
        insertion_lines.append("    },")
    insertion_text = "\n".join(insertion_lines)

    idx = content.rstrip().rfind("]")
    updated = content[:idx] + insertion_text + "\n" + content[idx:]

    with open("evaluation/dataset_items.py", "w", encoding="utf-8") as f:
        f.write(updated)

    print("evaluation/dataset_items.py 업데이트 완료.")


if __name__ == "__main__":
    main()
