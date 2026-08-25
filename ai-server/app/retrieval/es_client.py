import asyncio
from elasticsearch import Elasticsearch, helpers
from typing import List, Dict, Any, Optional
from app.config import settings


def get_es_client() -> Elasticsearch:
    """
    Elasticsearch 커넥션 클라이언트 객체를 생성하여 반환합니다.
    ELASTICSEARCH.md 기준: HTTP TLS + 인증이 활성화된 클러스터에 접속한다.
    (로컬 환경의 OpenSSL 3.x 자체 서명 CA 검증 에러 방지를 위해 verify_certs=False 적용)
    """
    return Elasticsearch(
        settings.ELASTICSEARCH_URL,
        basic_auth=(settings.ELASTICSEARCH_USER, settings.ELASTICSEARCH_PASSWORD),
        ca_certs=settings.ELASTICSEARCH_CA_CERT,
        verify_certs=False,
        ssl_show_warn=False,
        # 기본 10s 타임아웃으로는 벡터 필드가 포함된 대량 bulk 색인(수백~수천 문서)이
        # 자주 Connection timed out으로 실패한다 (2026-08-21 전체 재색인 시 확인됨).
        request_timeout=60,
        max_retries=3,
        retry_on_timeout=True,
    )


def create_confluence_index(index_name: Optional[str] = None) -> bool:
    """
    Confluence RAG용 Elasticsearch 인덱스 및 매핑을 생성하는 함수.

    [인덱스 매핑 설정]
    1. Nori 한국어 분석기 (nori_analyzer): title, text 필드의 키워드 검색(BM25)용
    2. Dense Vector (text_vector): 1536차원 OpenAI text-embedding-3-small 임베딩 벡터 코사인 유사도 검색용
    """
    target_index = index_name or settings.ELASTICSEARCH_INDEX
    es = get_es_client()

    try:
        # 인덱스가 이미 존재할 경우 무분별한 재생성을 방지하고 통과
        if es.indices.exists(index=target_index):
            return True

        # Nori 형태소 분석기 및 1536차원 벡터 필드 매핑 정의 (ELASTICSEARCH.md 준수)
        mapping = {
            "settings": {
                "analysis": {
                    "analyzer": {
                        "nori_analyzer": {
                            "type": "custom",
                            "tokenizer": "nori_tokenizer"
                        }
                    }
                }
            },
            "mappings": {
                "properties": {
                    "chunk_id": {"type": "keyword"},  # Elasticsearch _id로 사용되는 고유 청크 키
                    "doc_id": {"type": "keyword"},    # 원본 Confluence 문서 ID (삭제 동기화용)
                    "title": {"type": "text", "analyzer": "nori_analyzer"},  # 문서 제목 (BM25 키워드 검색)
                    "text": {"type": "text", "analyzer": "nori_analyzer"},   # 청크 본문 (BM25 키워드 검색)
                    "space_key": {"type": "keyword"}, # Confluence Space 식별자
                    "author": {"type": "keyword"},   # 작성자 메타데이터
                    "url": {"type": "keyword"},      # Confluence 문서 원본 URL
                    "category": {"type": "keyword"}, # 대분류 카테고리 (Confluence 조상 페이지 기준, 예: "솔루션/개발")
                    "path": {"type": "keyword"},     # 전체 계층 경로 (예: "기획 / PoC / SPRINT_Palantir")
                    "links": {"type": "keyword"},    # 본문 내 언급된 참조 링크 목록
                    "updated_at": {"type": "date"},  # Confluence 문서 최종 수정 시각 (증분 재색인 비교 기준)
                    "primary_contributor": {"type": "keyword"},  # 버전 히스토리 기준 최다 수정자
                    "chunk_index": {"type": "integer"},
                    "total_chunks": {"type": "integer"},
                    "text_vector": {                 # OpenAI text-embedding-3-small 1536차원 임베딩 벡터 필드
                        "type": "dense_vector",
                        "dims": 1536,
                        "index": True,
                        "similarity": "cosine"       # 코사인 유사도 기반 벡터 연산
                    }
                }
            }
        }

        es.indices.create(index=target_index, body=mapping)
        print(f"[Elasticsearch] 인덱스 '{target_index}' 생성 완료 (Nori 분석기 + 1536차원 Vector 매핑)")
        return True

    except Exception as e:
        print(f"[Elasticsearch Error] 인덱스 생성 중 오류 발생: {e}")
        return False


def index_document_chunks(
    chunks: List[Dict[str, Any]],
    vectors: Optional[List[List[float]]] = None,
    index_name: Optional[str] = None
) -> int:
    """
    문서 청크들과 임베딩 벡터들을 Elasticsearch에 대량 색인(Bulk Indexing)하는 함수.
    
    [핵심 포인트]
    - chunk_id를 Elasticsearch의 Primary Key(_id)로 지정하므로,
      문서가 수정되어 다시 색인될 때 기존 청크를 중복 저장하지 않고 자동으로 덮어쓰기(Upsert)합니다.
    """
    if not chunks:
        return 0

    target_index = index_name or settings.ELASTICSEARCH_INDEX
    es = get_es_client()

    actions = []
    for idx, chunk in enumerate(chunks):
        doc_body = {
            "chunk_id": chunk["chunk_id"],
            "doc_id": chunk["doc_id"],
            "title": chunk["title"],
            "text": chunk["text"],
            "space_key": chunk.get("metadata", {}).get("space_key", settings.CONFLUENCE_SPACE_KEY),
            "author": chunk.get("metadata", {}).get("author", "Unknown"),
            "url": chunk.get("metadata", {}).get("url", ""),
            "category": chunk.get("metadata", {}).get("category", ""),
            "path": chunk.get("metadata", {}).get("path", ""),
            "links": chunk.get("metadata", {}).get("links", []),
            "updated_at": chunk.get("metadata", {}).get("updated_at") or None,
            "primary_contributor": chunk.get("metadata", {}).get("primary_contributor", "알 수 없음"),
            "chunk_index": chunk["chunk_index"],
            "total_chunks": chunk["total_chunks"]
        }

        # OpenAI text-embedding-3-small 1536차원 임베딩 벡터가 함께 전달된 경우 추가
        if vectors and idx < len(vectors):
            doc_body["text_vector"] = vectors[idx]

        action = {
            "_index": target_index,
            "_id": chunk["chunk_id"],  # 자동 덮어쓰기(Upsert)용 PK 지정
            "_source": doc_body
        }
        actions.append(action)

    try:
        # 벡터 필드가 커서 기본 chunk_size=500이면 요청 하나가 너무 커져 타임아웃나기 쉽다.
        # 배치를 작게 쪼개고, 클라이언트 request_timeout(60s)과 별개로 재시도도 켜둔다.
        success_count, errors = helpers.bulk(es, actions, chunk_size=200, raise_on_error=False)
        if errors:
            print(f"[Elasticsearch Warning] {len(errors)}개 청크 색인 실패 (예: {errors[0]})")
        print(f"[Elasticsearch] 총 {success_count}개 청크 bulk 색인 성공")
        return success_count
    except Exception as e:
        print(f"[Elasticsearch Error] Bulk 색인 중 오류 발생: {e}")
        return 0


# BM25:kNN 결합 가중치. 두 점수 모두 0~1로 정규화한 뒤 이 비율로 합산한다.
_BM25_WEIGHT = 3.0
_KNN_WEIGHT = 7.0

# 문서 하나의 청크를 이어붙일 때 최대 글자 수 (컨텍스트가 무한정 커지는 것 방지)
_MAX_DOC_TEXT_CHARS = 4000


def _normalize_scores(hits: List[Dict[str, Any]]) -> Dict[str, float]:
    """
    ES hit 리스트의 _score를 chunk_id 기준 0~1 min-max 정규화해서 반환하는 헬퍼 함수.
    BM25 원시 점수(수십 단위)와 kNN 코사인 유사도(0~1)는 스케일이 전혀 달라서,
    정규화 없이 그대로 더하면 BM25가 항상 압도해버린다 (2026-08-21 실측으로 확인됨).
    """
    if not hits:
        return {}

    scores = [h["_score"] for h in hits]
    lo, hi = min(scores), max(scores)
    span = hi - lo

    normalized = {}
    for h in hits:
        chunk_id = h["_source"].get("chunk_id")
        normalized[chunk_id] = 1.0 if span == 0 else (h["_score"] - lo) / span
    return normalized


def _fetch_full_doc_text(es: Elasticsearch, index: str, doc_id: str, max_chars: int = _MAX_DOC_TEXT_CHARS) -> str:
    """
    같은 문서의 청크 하나만 검색에 걸리면, 정작 답이 있는 다른 청크가 컨텍스트에서
    빠질 수 있다 (예: "무엇을 할 수 있는가" 청크 대신 "설치 방법" 청크만 뽑히는 경우).
    검색으로 문서가 한 번 hit하면, 그 문서의 청크를 chunk_index 순으로 이어붙여서
    최대한 그 문서 전체 맥락을 컨텍스트에 포함시킨다.
    """
    res = es.search(index=index, body={
        "size": 50,
        "_source": ["text"],
        "query": {"term": {"doc_id": doc_id}},
        "sort": [{"chunk_index": "asc"}],
    })

    parts = []
    total_len = 0
    for hit in res.get("hits", {}).get("hits", []):
        chunk_text = hit["_source"].get("text", "")
        if total_len >= max_chars:
            break
        parts.append(chunk_text)
        total_len += len(chunk_text)

    return "\n".join(parts)


def search_hybrid(
    query_text: str,
    query_vector: Optional[List[float]] = None,
    top_k: int = 5,
    space_key: Optional[str] = None,
    index_name: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    BM25 키워드 검색과 Vector kNN 의미 검색을 결합한 하이브리드 검색 함수.

    [검색 설계 및 가중치]
    1. BM25 키워드 검색: Nori 분석기로 title과 text 필드 매칭
       - title^2.0: 사내 업무 문서 특성상 제목 매칭이 매우 중요하므로 문서 제목에 2.0배 가중치 부여
    2. Vector kNN 의미 검색: 1536차원 질문 임베딩 벡터(query_vector)와 text_vector의 코사인 유사도 매칭
    3. 점수 결합: BM25와 kNN을 같은 요청에 넣고 raw score를 그냥 더하면 BM25(수십 단위)가
       kNN(0~1)을 압도해서 사실상 BM25 단독 검색이 되어버린다. 그래서 두 쿼리를 따로 실행해
       chunk_id 기준으로 각각 0~1 min-max 정규화한 뒤 BM25:kNN = 3:2 가중합으로 재랭킹한다.
       (ES basic 라이선스는 retriever.rrf를 지원하지 않아 RRF 대신 이 방식을 사용)
    """
    target_index = index_name or settings.ELASTICSEARCH_INDEX
    es = get_es_client()

    # BM25/kNN 각각 top_k보다 넉넉한 후보 풀을 가져와야 재랭킹 시 놓치는 문서가 줄어든다
    candidate_size = max(top_k * 5, 20)

    # 1. BM25 키워드 매칭 쿼리 (제목 가중치 2배 부여)
    bm25_query = {
        "bool": {
            "must": [
                {
                    "multi_match": {
                        "query": query_text,
                        "fields": ["title^2.0", "text"], # 제목(title) 매칭 시 점수 2배 가산
                        "type": "best_fields"
                    }
                }
            ]
        }
    }

    # 필요시 특정 Confluence Space만 필터링하도록 지원
    if space_key:
        bm25_query["bool"]["filter"] = [{"term": {"space_key": space_key}}]

    bm25_hits: List[Dict[str, Any]] = []
    knn_hits: List[Dict[str, Any]] = []

    try:
        bm25_res = es.search(index=target_index, body={"query": bm25_query, "size": candidate_size})
        bm25_hits = bm25_res.get("hits", {}).get("hits", [])

        if query_vector:
            knn_query: Dict[str, Any] = {
                "field": "text_vector",
                "query_vector": query_vector,
                "k": candidate_size,
                "num_candidates": max(candidate_size * 5, 50)
            }
            if space_key:
                knn_query["filter"] = {"term": {"space_key": space_key}}
            knn_res = es.search(index=target_index, body={"knn": knn_query, "size": candidate_size})
            knn_hits = knn_res.get("hits", {}).get("hits", [])

    except Exception as e:
        print(f"[Elasticsearch Error] 하이브리드 검색 수행 중 오류 발생: {e}")
        return []

    # 2. 각 리스트를 chunk_id 기준 0~1로 정규화
    bm25_norm = _normalize_scores(bm25_hits)
    knn_norm = _normalize_scores(knn_hits)

    # 3. chunk_id 기준으로 두 결과를 합치고 3:2 가중합으로 재랭킹
    merged_sources: Dict[str, Dict[str, Any]] = {}
    for hit in bm25_hits + knn_hits:
        chunk_id = hit["_source"].get("chunk_id")
        if chunk_id and chunk_id not in merged_sources:
            merged_sources[chunk_id] = hit["_source"]

    scored: List[Dict[str, Any]] = []
    for chunk_id, source in merged_sources.items():
        combined_score = (
            _BM25_WEIGHT * bm25_norm.get(chunk_id, 0.0)
            + _KNN_WEIGHT * knn_norm.get(chunk_id, 0.0)
        ) / (_BM25_WEIGHT + _KNN_WEIGHT)
        scored.append({
            "chunk_id": source.get("chunk_id"),
            "doc_id": source.get("doc_id"),
            "title": source.get("title"),
            "text": source.get("text"),
            "url": source.get("url"),
            "author": source.get("author"),
            "category": source.get("category"),
            "path": source.get("path"),
            "space_key": source.get("space_key"),
            "primary_contributor": source.get("primary_contributor"),
            "score": combined_score
        })

    scored.sort(key=lambda x: x["score"], reverse=True)

    # 4. top_k "청크"가 아니라 top_k "서로 다른 문서"를 뽑는다. 같은 문서의 청크 여러 개가
    #    상위권을 독점하면 다른 관련 문서가 밀려나고, 정작 필요한 부분이 담긴 청크는
    #    top_k 밖으로 빠질 수 있기 때문이다 (2026-08-21 실측: MCP 문서 관련 질문에서
    #    top_k=3 전부 "설치 방법" 청크만 뽑히고 "기능 목록" 청크는 못 들어온 사례 확인).
    picked: List[Dict[str, Any]] = []
    seen_doc_ids = set()
    for entry in scored:
        if entry["doc_id"] in seen_doc_ids:
            continue
        seen_doc_ids.add(entry["doc_id"])
        picked.append(entry)
        if len(picked) >= top_k:
            break

    # 5. 뽑힌 문서마다 청크를 이어붙여서, 대표로 매칭된 청크 하나가 아니라
    #    문서 전체 맥락을 컨텍스트로 채운다.
    for entry in picked:
        entry["text"] = _fetch_full_doc_text(es, target_index, entry["doc_id"])

    return picked


async def search_hybrid_async(
    query_text: str,
    query_vector: Optional[List[float]] = None,
    top_k: int = 5,
    space_key: Optional[str] = None,
    index_name: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    [비동기] 하이브리드 검색 함수.
    Elasticsearch 동기 검색을 asyncio.to_thread로 감싸 메인 이벤트 루프 블로킹 없이 실행합니다.
    """
    return await asyncio.to_thread(
        search_hybrid,
        query_text=query_text,
        query_vector=query_vector,
        top_k=top_k,
        space_key=space_key,
        index_name=index_name
    )


def delete_documents_by_ids(doc_ids: List[str], index_name: Optional[str] = None) -> int:
    """
    Confluence에서 삭제된 문서 ID(doc_id)들을 Elasticsearch에서도 일괄 삭제하는 동기화 함수.
    """
    if not doc_ids:
        return 0

    target_index = index_name or settings.ELASTICSEARCH_INDEX
    es = get_es_client()

    query = {
        "query": {
            "terms": {
                "doc_id": doc_ids
            }
        }
    }

    try:
        res = es.delete_by_query(index=target_index, body=query)
        deleted_count = res.get("deleted", 0)
        print(f"[Elasticsearch] Confluence에서 삭제된 문서 {deleted_count}개 청크 ES에서 제거 완료")
        return deleted_count
    except Exception as e:
        print(f"[Elasticsearch Error] 삭제 동기화 처리 실패: {e}")
        return 0


def get_indexed_updated_ats(doc_ids: List[str], index_name: Optional[str] = None) -> Dict[str, str]:
    """
    [증분 색인용] 이미 색인된 문서들의 저장된 updated_at 값을 doc_id 기준으로 모아서 반환하는 함수.

    Confluence에서 가져온 최신 updated_at과 비교해서, 값이 같으면 재색인을 건너뛰고
    다르면(또는 아직 색인된 적 없으면) 다시 색인하는 방식으로 임베딩 비용을 아낀다.
    한 문서는 여러 청크로 쪼개져 저장되지만 updated_at은 문서 단위로 동일하므로,
    doc_id별 대표값 하나씩만 모으면 된다 (collapse).
    """
    if not doc_ids:
        return {}

    target_index = index_name or settings.ELASTICSEARCH_INDEX
    es = get_es_client()

    query = {
        "size": 0,
        "query": {"terms": {"doc_id": doc_ids}},
        "aggs": {
            "by_doc": {
                "terms": {"field": "doc_id", "size": len(doc_ids)},
                "aggs": {"latest_updated_at": {"max": {"field": "updated_at"}}}
            }
        }
    }

    try:
        if not es.indices.exists(index=target_index):
            return {}

        res = es.search(index=target_index, body=query)
        buckets = res.get("aggregations", {}).get("by_doc", {}).get("buckets", [])
        return {
            bucket["key"]: bucket["latest_updated_at"].get("value_as_string", "")
            for bucket in buckets
        }
    except Exception as e:
        print(f"[Elasticsearch Error] 기존 updated_at 조회 실패: {e}")
        return {}
