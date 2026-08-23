"""
QA_DATASET_ITEMS를 Langfuse Dataset으로 업로드(업서트)한다.

실행:
    cd ai-server && .venv/bin/python -m evaluation.push_dataset
"""
from app.config import settings
import os

if settings.LANGFUSE_PUBLIC_KEY:
    os.environ["LANGFUSE_PUBLIC_KEY"] = settings.LANGFUSE_PUBLIC_KEY
if settings.LANGFUSE_SECRET_KEY:
    os.environ["LANGFUSE_SECRET_KEY"] = settings.LANGFUSE_SECRET_KEY
if settings.LANGFUSE_HOST:
    os.environ["LANGFUSE_HOST"] = settings.LANGFUSE_HOST

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from langfuse import get_client
from evaluation.dataset_items import QA_DATASET_ITEMS

DATASET_NAME = "confluence-rag-qa-v2"


def main():
    client = get_client()

    client.create_dataset(
        name=DATASET_NAME,
        description="Confluence RAG 챗봇 QA용 대표 질문 세트 (검색 hit / 충실도 / 정답 일치도 채점)",
    )

    for item in QA_DATASET_ITEMS:
        # id를 고정해서 재실행해도 같은 아이템이 upsert되도록 함
        client.create_dataset_item(
            dataset_name=DATASET_NAME,
            id=item["id"],
            input=item["input"],
            expected_output=item["expected_output"],
            metadata=item["metadata"],
        )
        print(f"upserted {item['id']}: {item['input'][:40]}")

    client.flush()
    print(f"\n총 {len(QA_DATASET_ITEMS)}개 아이템을 '{DATASET_NAME}' 데이터셋에 업로드 완료")


if __name__ == "__main__":
    main()
