"""
Confluence 문서를 수집 -> 파싱 -> 청킹 -> 임베딩 -> Elasticsearch 색인까지
Phase 1 Retrieval Core 전체 파이프라인을 한 번에 실행하는 스크립트.

변경되지 않은 문서는 건너뛰는 증분 색인을 기본으로 한다 (Confluence의 updated_at과
Elasticsearch에 이미 저장된 updated_at을 비교). --force로 강제 전체 재색인 가능.

전체 스페이스를 대상으로 할 때는 Confluence에서 삭제된 문서를 색인에서도 지운다.
일부만 수집하는 실행(--limit/--category)에서는 수집 범위 밖 문서가 전부 삭제 대상으로
잡히므로 이 단계를 건너뛴다.

실행 예:
    cd ai-server && source .venv/bin/activate
    python -m scripts.ingest --limit 10                 # 문서 10개만 테스트 색인
    python -m scripts.ingest --category "솔루션/개발"     # 카테고리(대분류) 단위로 색인
    python -m scripts.ingest                             # 스페이스 전체 색인 (변경분만 + 삭제 정리)
    python -m scripts.ingest --force                     # 스페이스 전체 강제 재색인
    python -m scripts.ingest --no-prune                  # 삭제 정리 없이 색인만
"""
import argparse
from app.core.confluence_client import (
    fetch_all_page_ids,
    fetch_confluence_pages,
    fetch_pages_by_ids,
    fetch_pages_with_category,
    filter_pages_by_category,
)
from app.parser.confluence_parser import parse_confluence_html, split_text_into_chunks
from app.llm.litellm_client import embed_texts
from app.retrieval.es_client import (
    create_confluence_index,
    index_document_chunks,
    delete_documents_by_ids,
    get_all_indexed_doc_ids,
    get_indexed_updated_ats,
)


def prune_deleted_documents() -> int:
    """
    Confluence에서 삭제된 문서를 Elasticsearch에서도 지운다.

    Confluence의 전체 문서 id와 색인된 전체 doc_id를 비교해, 색인에만 남아 있는 것을
    지운다. 이걸 하지 않으면 원본이 사라진 뒤에도 챗봇이 그 문서를 근거로 답한다.

    본문이 없어 청크가 0개인 문서는 애초에 색인되지 않으므로 이 비교에 걸리지 않는다.

    주의: 전체 스페이스를 대상으로 할 때만 호출해야 한다. 일부만 수집한 상태로 비교하면
    수집하지 않은 문서가 전부 "삭제됨"으로 잡힌다. 호출부에서 막는다.
    """
    live_ids = set(fetch_all_page_ids())   # 실패하면 예외를 올린다 (부분 목록으로 지우지 않기 위해)
    if not live_ids:
        print("  Confluence 문서 id를 하나도 받지 못해 삭제 동기화를 건너뜁니다.")
        return 0

    indexed_ids = set(get_all_indexed_doc_ids())
    stale_ids = indexed_ids - live_ids

    print(f"  Confluence {len(live_ids)}건 / 색인 {len(indexed_ids)}건 -> 삭제 대상 {len(stale_ids)}건")
    if not stale_ids:
        return 0

    return delete_documents_by_ids(list(stale_ids))


def ingest(limit: int = None, batch_size: int = 50, category: str = None,
           force: bool = False, prune: bool = True) -> None:
    # 일부만 수집하는 실행에서는 삭제 동기화를 하면 안 된다. 수집 범위 밖의 문서가
    # 전부 삭제 대상으로 잡히기 때문이다.
    full_space_run = limit is None and category is None

    print("[1/6] Confluence 문서 수집 중...")

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

    print("[2/6] 삭제된 문서 정리 중...")
    if not prune:
        print("  --no-prune 지정됨 -> 건너뜀")
    elif not full_space_run:
        print("  부분 색인(--limit/--category)이라 건너뜀 (수집 범위 밖 문서를 삭제할 위험)")
    else:
        try:
            removed = prune_deleted_documents()
            if removed:
                print(f"  -> 삭제된 문서의 청크 {removed}개 제거")
        except Exception as e:
            print(f"  [경고] 삭제 동기화 실패, 색인은 계속합니다: {e}")

    print("[3/6] 증분 색인 대상 판별 중...")
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

    print("[4/6] 문서 파싱/청킹 중...")
    all_chunks = []
    empty_docs = 0
    for page in target_pages:
        parsed = parse_confluence_html(
            page["html_body"],
            metadata={
                "space_key": page["space_key"],
                "author": page["author"],
                "url": page["url"],
                "category": category_map.get(page["id"], ""),
                "path": path_map.get(page["id"], ""),
                "updated_at": page.get("last_updated"),
            },
        )

        chunks = split_text_into_chunks(
            doc_id=page["id"],
            title=page["title"],
            text=parsed["cleaned_text"],
            metadata=parsed["metadata"],
        )
        if not chunks:
            # 본문이 없는 문서(예: Confluence DB 매크로만 있는 페이지)는 청크가 0개다.
            # ES에 아무것도 안 남으니 다음 색인에서도 계속 대상으로 잡힌다. 정상이지만,
            # 이 숫자가 갑자기 늘면 파서가 깨진 신호이므로 눈에 보이게 찍어둔다.
            empty_docs += 1
        all_chunks.extend(chunks)

    empty_note = f" (본문이 없어 청크 0개인 문서 {empty_docs}개)" if empty_docs else ""
    print(f"  -> {len(all_chunks)}개 청크 생성 완료{empty_note}")

    print("[5/6] 인덱스 준비 및 임베딩 생성 중...")
    create_confluence_index()

    # 재색인 시 문서 길이가 줄어들면 이전 버전의 뒷쪽 청크(예: chunk_5)가 새 버전에는
    # 존재하지 않아 그대로 남아 고아 청크가 될 수 있다. 새로 색인하기 전에 대상 문서의
    # 기존 청크를 먼저 지워서 항상 최신 상태만 남긴다.
    #
    # 청크가 아니라 "재색인 대상 문서" 전체를 기준으로 지운다. 본문이 지워져 청크가
    # 0개가 된 문서는 all_chunks에 없어서, 청크 기준으로 지우면 옛 본문이 그대로 남는다.
    target_doc_ids = [page["id"] for page in target_pages]
    delete_documents_by_ids(target_doc_ids)

    if not all_chunks:
        print("  대상 문서가 모두 본문이 없어, 기존 청크만 정리하고 종료합니다.")
        return

    # BM25는 title 필드에 별도 가중치(title^2.0)를 이미 주고 있으므로 저장용 text는 그대로 두고,
    # 벡터 임베딩에만 제목을 함께 넣어 청크가 문서 제목 맥락을 벡터 공간에서도 유지하게 한다.
    texts_for_embedding = [f"{chunk['title']}\n{chunk['text']}" for chunk in all_chunks]
    vectors = []
    for i in range(0, len(texts_for_embedding), batch_size):
        batch = texts_for_embedding[i:i + batch_size]
        vectors.extend(embed_texts(batch))
        print(f"  임베딩 진행: {min(i + batch_size, len(texts_for_embedding))}/{len(texts_for_embedding)}")

    print("[6/6] Elasticsearch 색인 중...")
    indexed_count = index_document_chunks(all_chunks, vectors=vectors)
    print(f"  -> {indexed_count}개 청크 색인 완료")


if __name__ == "__main__":
    arg_parser = argparse.ArgumentParser(description="Confluence -> Elasticsearch 색인 파이프라인")
    arg_parser.add_argument("--limit", type=int, default=None, help="수집할 문서 수 제한 (테스트용, 미지정 시 전체)")
    arg_parser.add_argument("--batch-size", type=int, default=50, help="임베딩 API 호출 배치 크기")
    arg_parser.add_argument("--category", type=str, default=None, help="대분류 카테고리 제목으로 필터링 (예: '솔루션/개발')")
    arg_parser.add_argument("--force", action="store_true", help="변경 여부와 무관하게 전부 강제 재색인")
    arg_parser.add_argument("--no-prune", action="store_true",
                            help="Confluence에서 삭제된 문서를 색인에서 지우는 단계를 건너뜀")
    args = arg_parser.parse_args()

    ingest(limit=args.limit, batch_size=args.batch_size, category=args.category,
           force=args.force, prune=not args.no_prune)
