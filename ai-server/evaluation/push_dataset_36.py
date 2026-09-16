"""
36문항 평가셋을 Langfuse Dataset으로 업로드(업서트)한다.

실행:
    docker exec rag-ai-server python -m evaluation.push_dataset_36

아이템 id를 `qa36-001` 처럼 고정해 두었으므로 여러 번 돌려도 아이템이 늘지 않고
같은 자리에 덮어쓴다. 데이터셋이 36건을 넘으면 다른 평가셋이 섞여 들어간 것이다.
"""
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# 환경변수 설정과 연결 확인을 맡는 모듈. `run_qa`가 아니라 여기를 거친다 —
# 문항을 업로드하는 데 Elasticsearch나 LLM 모듈이 딸려 올 이유가 없다.
from evaluation.langfuse_client import connect
from evaluation.dataset_items_36 import DATASET_NAME_DEFAULT, dataset_path, load_items

DATASET_NAME = os.environ.get("EVAL_DATASET_NAME", DATASET_NAME_DEFAULT)


def main():
    # 업서트하기 전에 연결을 확인한다. Langfuse는 이벤트를 비동기로 보내므로,
    # 키가 틀려도 create_dataset_item()은 그 자리에서 실패하지 않는다. 36줄의
    # "upserted ..."가 다 찍히고 맨 끝 개수 대조에서야 어긋난다.
    client = connect()
    items = load_items()

    client.create_dataset(
        name=DATASET_NAME,
        description=(
            "Confluence RAG 36문항 평가셋 (색인 확인된 정답 문서 18개). "
            f"원본: {dataset_path().name}. "
            "input=question, expected_output=ground_truth_snippet, "
            "metadata.expected_doc_ids=page_id."
        ),
    )

    for item in items:
        client.create_dataset_item(
            dataset_name=DATASET_NAME,
            id=item["id"],
            input=item["input"],
            expected_output=item["expected_output"],
            metadata=item["metadata"],
        )
        print(f"upserted {item['id']}: {item['input'][:40]}")

    client.flush()

    # 업로드 직후 서버에서 다시 읽어 개수를 확인한다. 업서트가 조용히 실패하면
    # 실행은 성공한 것처럼 끝나고 Experiment만 문항 수가 모자라게 돈다.
    remote = client.get_dataset(DATASET_NAME)
    print(f"\n'{DATASET_NAME}' 업로드 완료 — 로컬 {len(items)}건 / 서버 {len(remote.items)}건")
    if len(remote.items) != len(items):
        raise SystemExit(
            f"경고: 서버 아이템 수({len(remote.items)})가 평가셋({len(items)})과 다릅니다. "
            "과거 아이템이 남아 있는지 Langfuse UI에서 확인하세요."
        )


if __name__ == "__main__":
    main()
