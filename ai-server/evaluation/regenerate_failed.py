"""
검색 실패(retrieval_hit=0)로 나온 자동생성 문항들을, 질문에 문서를 특정할 단서를 넣도록
개선된 프롬프트(generate_dataset._generate_qa_for_doc)로 다시 생성해서 같은 id에 덮어쓴다.

실행:
    cd ai-server && .venv/bin/python -m evaluation.regenerate_failed
"""
from app.retrieval.es_client import get_es_client
from app.config import settings
from evaluation.dataset_items import QA_DATASET_ITEMS
from evaluation.generate_dataset import _generate_qa_for_doc, MAX_CONTEXT_CHARS

# 2026-08-21 5:5 가중치 재검증 실행에서 retrieval_hit=0으로 나온, 질문이 모호했던 자동생성 문항들
FAILED_IDS = [
    "qa-017", "qa-018", "qa-020", "qa-021", "qa-025", "qa-026", "qa-028",
    "qa-032", "qa-035", "qa-036", "qa-037", "qa-039", "qa-040", "qa-041",
    "qa-050", "qa-053", "qa-059", "qa-060", "qa-061", "qa-063", "qa-067",
]


def _fetch_doc_text(es, index: str, doc_id: str) -> str:
    res = es.search(index=index, body={
        "size": 20,
        "_source": ["text"],
        "query": {"term": {"doc_id": doc_id}},
        "sort": [{"chunk_index": "asc"}],
    })
    text = ""
    for hit in res["hits"]["hits"]:
        if len(text) >= MAX_CONTEXT_CHARS:
            break
        text += ("\n" if text else "") + hit["_source"].get("text", "")
    return text


def _serialize_items(items) -> str:
    lines = [
        '"""',
        "Langfuse QA 데이터셋 아이템 정의.",
        "",
        "각 아이템은 실제 confluence-openai-v1 인덱스에 들어있는 문서를 근거로 작성했다.",
        "metadata.expected_doc_ids로 \"검색이 정답 문서를 찾았는지\"와",
        "\"답변 생성이 맞았는지\"를 분리해서 채점할 수 있게 한다.",
        "",
        "- expected_doc_ids가 채워진 항목: 특정 문서에서 답이 나와야 하는 일반 질문",
        "- expected_doc_ids가 빈 리스트인 항목: 관련 문서가 없어야 하는 질문 (환각 방지 검증)",
        "- metadata.known_gap=True: 파서가 Confluence 임베디드 database 매크로를 못 읽어와서",
        "  본문이 링크만 남은 문서를 겨냥한 질문 (검색/생성과 무관한 \"수집 단계\" 갭 진단용)",
        '"""',
        "",
        "QA_DATASET_ITEMS = [",
    ]
    for item in items:
        lines.append("    {")
        lines.append(f'        "id": {item["id"]!r},')
        lines.append(f'        "input": {item["input"]!r},')
        lines.append(f'        "expected_output": {item["expected_output"]!r},')
        lines.append(f'        "metadata": {item["metadata"]!r},')
        lines.append("    },")
    lines.append("]")
    return "\n".join(lines) + "\n"


def main():
    es = get_es_client()
    index = settings.ELASTICSEARCH_INDEX

    items = [dict(i) for i in QA_DATASET_ITEMS]
    by_id = {i["id"]: i for i in items}

    for item_id in FAILED_IDS:
        item = by_id.get(item_id)
        if not item:
            print(f"스킵 (없는 id): {item_id}")
            continue

        doc_id = item["metadata"]["expected_doc_ids"][0]
        text = _fetch_doc_text(es, index, doc_id)
        if not text:
            print(f"스킵 (문서 원문 없음): {item_id} / {doc_id}")
            continue

        # 제목은 원래 질문 생성 때 안 남겨뒀으므로 doc_id로 재조회
        title_res = es.search(index=index, body={
            "size": 1, "_source": ["title"], "query": {"term": {"doc_id": doc_id}}
        })
        title = title_res["hits"]["hits"][0]["_source"]["title"] if title_res["hits"]["hits"] else ""

        qa = _generate_qa_for_doc(title, text)
        if not qa:
            print(f"스킵 (생성 실패): {item_id} / {doc_id}")
            continue

        question, answer = qa
        print(f"{item_id} [{doc_id}] {title}")
        print(f"  이전: {item['input']}")
        print(f"  이후: {question}")
        item["input"] = question
        item["expected_output"] = answer

    with open("evaluation/dataset_items.py", "w", encoding="utf-8") as f:
        f.write(_serialize_items(items))

    print("\nevaluation/dataset_items.py 재작성 완료.")


if __name__ == "__main__":
    main()
