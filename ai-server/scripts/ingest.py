"""
Confluence 문서를 수집 -> 파싱 -> 청킹 -> 임베딩 -> Elasticsearch 색인까지
Phase 1 Retrieval Core 전체 파이프라인을 한 번에 실행하는 스크립트.

변경되지 않은 문서는 건너뛰는 증분 색인을 기본으로 한다 (Confluence의 updated_at과
Elasticsearch에 이미 저장된 updated_at을 비교). --force로 강제 전체 재색인 가능.

실행 예:
    cd ai-server && source .venv/bin/activate
    python -m scripts.ingest --limit 10                 # 문서 10개만 테스트 색인
    python -m scripts.ingest --category "솔루션/개발"     # 카테고리(대분류) 단위로 색인
    python -m scripts.ingest                             # 스페이스 전체 색인 (변경분만)
    python -m scripts.ingest --force                     # 스페이스 전체 강제 재색인
"""
import argparse
from app.core.confluence_client import (
    fetch_confluence_pages,
    fetch_pages_by_ids,
    fetch_pages_with_category,
    filter_pages_by_category,
    get_primary_contributor,
)
from app.parser.confluence_parser import parse_confluence_html, split_text_into_chunks
from app.llm.litellm_client import embed_texts
from app.retrieval.es_client import (
    create_confluence_index,
    index_document_chunks,
    delete_documents_by_ids,
    get_indexed_updated_ats,
)


def ingest(limit: int = None, batch_size: int = 50, category: str = None, force: bool = False) -> None:
    print("[1/5] Confluence 문서 수집 중...")

    # 카테고리(대분류, level_1) 및 전체 계층 경로(path) 정보 조회
    category_df = fetch_pages_with_category()
    category_map = dict(zip(category_df.get("id", []), category_df.get("level_1", [])))
    path_map = dict(zip(category_df.get("id", []), category_df.get("path", [])))

    if category:
        page_ids = filter_pages_by_category(category_df, {"level_1": category})
        print(f"  -> '{category}' 카테고리에서 {len(page_ids)}개 문서 발견")
        pages = fetch_pages_by_ids(page_ids)
    else:
        pages = fetch_confluence_pages()

    if limit:
        pages = pages[:limit]
    print(f"  -> {len(pages)}개 문서 수집 완료")

    if not pages:
        print("수집된 문서가 없어 종료합니다.")
        return

    print("[2/5] 증분 색인 대상 판별 중...")
    if force:
        target_pages = pages
        print(f"  --force 지정됨 -> {len(target_pages)}개 문서 전부 재색인")
    else:
        doc_ids = [page["id"] for page in pages]
        indexed_updated_ats = get_indexed_updated_ats(doc_ids)
        target_pages = [
            page for page in pages
            if indexed_updated_ats.get(page["id"]) != page.get("last_updated")
        ]
        skipped = len(pages) - len(target_pages)
        print(f"  -> {skipped}개 문서는 변경 없어 건너뜀, {len(target_pages)}개 문서 재색인 대상")

    if not target_pages:
        print("재색인할 문서가 없어 종료합니다.")
        return

    print("[3/5] 최다 수정자 조회 및 파싱/청킹 중...")
    all_chunks = []
    for page in target_pages:
        primary_contributor = get_primary_contributor(page["id"])
        parsed = parse_confluence_html(
            page["html_body"],
            metadata={
                "space_key": page["space_key"],
                "author": page["author"],
                "url": page["url"],
                "category": category_map.get(page["id"], ""),
                "path": path_map.get(page["id"], ""),
                "updated_at": page.get("last_updated"),
                "primary_contributor": primary_contributor,
            },
        )
        # 본문에서 추출된 참조 링크 목록 추가
        parsed["metadata"]["links"] = parsed.get("links", [])

        chunks = split_text_into_chunks(
            doc_id=page["id"],
            title=page["title"],
            text=parsed["cleaned_text"],
            metadata=parsed["metadata"],
        )
        all_chunks.extend(chunks)
    print(f"  -> {len(all_chunks)}개 청크 생성 완료")

    if not all_chunks:
        print("색인할 청크가 없어 종료합니다.")
        return

    print("[4/5] 인덱스 준비 및 임베딩 생성 중...")
    create_confluence_index()

    # 재색인 시 문서 길이가 줄어들면 이전 버전의 뒷쪽 청크(예: chunk_5)가 새 버전에는
    # 존재하지 않아 그대로 남아 고아 청크가 될 수 있다. 새로 색인하기 전에 대상 문서의
    # 기존 청크를 먼저 지워서 항상 최신 상태만 남긴다.
    target_doc_ids = list({chunk["doc_id"] for chunk in all_chunks})
    delete_documents_by_ids(target_doc_ids)

    # BM25는 title 필드에 별도 가중치(title^2.0)를 이미 주고 있으므로 저장용 text는 그대로 두고,
    # 벡터 임베딩에만 제목을 함께 넣어 청크가 문서 제목 맥락을 벡터 공간에서도 유지하게 한다.
    texts_for_embedding = [f"{chunk['title']}\n{chunk['text']}" for chunk in all_chunks]
    vectors = []
    for i in range(0, len(texts_for_embedding), batch_size):
        batch = texts_for_embedding[i:i + batch_size]
        vectors.extend(embed_texts(batch))
        print(f"  임베딩 진행: {min(i + batch_size, len(texts_for_embedding))}/{len(texts_for_embedding)}")

    print("[5/5] Elasticsearch 색인 중...")
    indexed_count = index_document_chunks(all_chunks, vectors=vectors)
    print(f"  -> {indexed_count}개 청크 색인 완료")


if __name__ == "__main__":
    arg_parser = argparse.ArgumentParser(description="Confluence -> Elasticsearch 색인 파이프라인")
    arg_parser.add_argument("--limit", type=int, default=None, help="수집할 문서 수 제한 (테스트용, 미지정 시 전체)")
    arg_parser.add_argument("--batch-size", type=int, default=50, help="임베딩 API 호출 배치 크기")
    arg_parser.add_argument("--category", type=str, default=None, help="대분류 카테고리 제목으로 필터링 (예: '솔루션/개발')")
    arg_parser.add_argument("--force", action="store_true", help="변경 여부와 무관하게 전부 강제 재색인")
    args = arg_parser.parse_args()

    ingest(limit=args.limit, batch_size=args.batch_size, category=args.category, force=args.force)
