"""
확정된 36문항 평가셋을 Langfuse Dataset item 형태로 읽어온다.

원본은 저장소 루트의 `confluence_retrieval_eval_36_indexed.json` 하나뿐이다.
여기서 파이썬 스냅샷으로 다시 옮겨 적지 않는다. 옮겨 적는 순간 두 벌이 되고,
질문이나 정답 라벨이 한쪽에서만 바뀌어도 알아채지 못한다.

하는 일은 **키 이름 맞추기뿐**이다. 질문(`question`)과 정답 라벨
(`ground_truth_snippet`, `page_id`)의 값은 한 글자도 건드리지 않는다.

| 원본 필드 | Langfuse item |
|---|---|
| `question` | `input` |
| `ground_truth_snippet` | `expected_output` (answer_correctness 채점 기준) |
| `page_id` | `metadata.expected_doc_ids` (retrieval_hit / retrieval_mrr 채점 기준) |

`page_id`가 빈 문자열인 문항(not_found 3건)은 `expected_doc_ids`가 빈 리스트가 되고,
`run_qa._scorable_expected_ids()`가 그 문항을 검색 지표 채점 대상에서 뺀다.
정답 문서 자체가 없는 질문이라 hit/MRR을 매기면 구조적으로 0점이 되기 때문이다.
답변 지표(faithfulness / correctness)는 이 문항들도 그대로 채점한다.
"""
import json
from pathlib import Path
from typing import Any, Dict, List

DATASET_FILE_NAME = "confluence_retrieval_eval_36_indexed.json"

# Langfuse Dataset 이름. 과거 45문항 셋(confluence-rag-qa-v2)과 섞이지 않도록 따로 둔다.
DATASET_NAME_DEFAULT = "confluence-rag-qa-36-indexed"

# 컨테이너에서는 compose가 /app 아래로 읽기 전용 마운트하고,
# 호스트에서 직접 돌릴 때는 저장소 루트에서 찾는다.
_CANDIDATE_PATHS = [
    Path("/app") / DATASET_FILE_NAME,
    Path(__file__).resolve().parent.parent.parent / DATASET_FILE_NAME,
]

EXPECTED_ITEM_COUNT = 36


def dataset_path() -> Path:
    for path in _CANDIDATE_PATHS:
        if path.is_file():
            return path
    raise FileNotFoundError(
        f"{DATASET_FILE_NAME}을 찾지 못했습니다. 확인한 경로: "
        + ", ".join(str(p) for p in _CANDIDATE_PATHS)
    )


def load_raw() -> List[Dict[str, Any]]:
    """원본 JSON을 그대로 돌려준다."""
    with dataset_path().open(encoding="utf-8") as f:
        raw = json.load(f)

    # 문항 수가 달라졌다면 다른 평가셋을 읽고 있다는 뜻이다. 조용히 진행하면
    # "36문항 평가"라고 보고하면서 실제로는 다른 셋을 측정하게 된다.
    if len(raw) != EXPECTED_ITEM_COUNT:
        raise ValueError(
            f"{dataset_path()}의 문항 수가 {len(raw)}건입니다 "
            f"(기대: {EXPECTED_ITEM_COUNT}건). 평가셋이 바뀌었는지 확인하세요."
        )
    return raw


def to_langfuse_item(row: Dict[str, Any]) -> Dict[str, Any]:
    page_id = (row.get("page_id") or "").strip()
    return {
        # id를 고정해야 재실행 때 새 아이템이 생기지 않고 같은 아이템에 덮어쓴다.
        "id": f"qa36-{int(row['id']):03d}",
        "input": row["question"],
        "expected_output": row["ground_truth_snippet"],
        "metadata": {
            "expected_doc_ids": [page_id] if page_id else [],
            "source_id": row.get("source_id"),
            "category": row.get("category"),
            "document_title": row.get("document_title"),
            # answerable / answerable_negative / not_found / unsupported
            "answerability": row.get("answerability"),
            # positive / negative — 원본 평가셋이 검색 채점 방향을 표기한 값
            "retrieval_scoring": row.get("retrieval_scoring"),
            "dataset_file": DATASET_FILE_NAME,
        },
    }


def load_items() -> List[Dict[str, Any]]:
    return [to_langfuse_item(row) for row in load_raw()]


if __name__ == "__main__":
    items = load_items()
    scorable = sum(1 for i in items if i["metadata"]["expected_doc_ids"])
    with_reference = sum(1 for i in items if (i["expected_output"] or "").strip())
    print(f"파일: {dataset_path()}")
    print(f"문항: {len(items)}건")
    print(f"검색 지표 채점 대상(expected_doc_ids 있음): {scorable}건")
    print(f"answer_correctness 채점 대상(expected_output 있음): {with_reference}건")
