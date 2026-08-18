from elasticsearch import Elasticsearch, helpers
from typing import List, Dict, Any, Optional
from app.config import settings


def get_es_client() -> Elasticsearch:
    """
    Elasticsearch 커넥션 클라이언트 객체를 생성하여 반환합니다.
    """
    return Elasticsearch(settings.ELASTICSEARCH_URL)


def create_confluence_index(index_name: Optional[str] = None) -> bool:
    """
    Confluence RAG용 Elasticsearch 인덱스 및 매핑을 생성하는 함수.
    
    [인덱스 매핑 설정]
    1. Nori 한국어 분석기 (nori_analyzer): title, text 필드의 키워드 검색(BM25)용
    2. Dense Vector (text_vector): 1024차원 BGE-M3 임베딩 벡터 코사인 유사도 검색용
    """
    target_index = index_name or settings.ELASTICSEARCH_INDEX
    es = get_es_client()

    try:
        # 인덱스가 이미 존재할 경우 무분별한 재생성을 방지하고 통과
        if es.indices.exists(index=target_index):
            return True

        # Nori 형태소 분석기 및 1024차원 벡터 필드 매핑 정의 (ELASTICSEARCH.md 준수)
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
                    "chunk_index": {"type": "integer"},
                    "total_chunks": {"type": "integer"},
                    "text_vector": {                 # BGE-M3 1024차원 임베딩 벡터 필드
                        "type": "dense_vector",
                        "dims": 1024,
                        "index": True,
                        "similarity": "cosine"       # 코사인 유사도 기반 벡터 연산
                    }
                }
            }
        }

        es.indices.create(index=target_index, body=mapping)
        print(f"[Elasticsearch] 인덱스 '{target_index}' 생성 완료 (Nori 분석기 + 1024차원 Vector 매핑)")
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
            "chunk_index": chunk["chunk_index"],
            "total_chunks": chunk["total_chunks"]
        }

        # BGE-M3 1024차원 임베딩 벡터가 함께 전달된 경우 추가
        if vectors and idx < len(vectors):
            doc_body["text_vector"] = vectors[idx]

        action = {
            "_index": target_index,
            "_id": chunk["chunk_id"],  # 자동 덮어쓰기(Upsert)용 PK 지정
            "_source": doc_body
        }
        actions.append(action)

    try:
        success_count, _ = helpers.bulk(es, actions)
        print(f"[Elasticsearch] 총 {success_count}개 청크 bulk 색인 성공")
        return success_count
    except Exception as e:
        print(f"[Elasticsearch Error] Bulk 색인 중 오류 발생: {e}")
        return 0


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
    2. Vector kNN 의미 검색: 1024차원 질문 임베딩 벡터(query_vector)와 text_vector의 코사인 유사도 매칭
    3. 스케일 결합: 정확한 키워드 우선 + 유의어 의미 검색 보조의 순수 점수 결합으로 최상위 top_k 문서 추출
    """
    target_index = index_name or settings.ELASTICSEARCH_INDEX
    es = get_es_client()

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

    query_body: Dict[str, Any] = {
        "query": bm25_query,
        "size": top_k
    }

    # 2. BGE-M3 질문 임베딩 벡터가 존재할 경우 Vector kNN 쿼리 동시 결합
    if query_vector:
        query_body["knn"] = {
            "field": "text_vector",
            "query_vector": query_vector,
            "k": top_k,
            "num_candidates": 50
        }

    results = []
    try:
        res = es.search(index=target_index, body=query_body)
        for hit in res.get("hits", {}).get("hits", []):
            source = hit["_source"]
            results.append({
                "chunk_id": source.get("chunk_id"),
                "doc_id": source.get("doc_id"),
                "title": source.get("title"),
                "text": source.get("text"),
                "url": source.get("url"),
                "author": source.get("author"),
                "score": float(hit.get("_score", 0.0))
            })

    except Exception as e:
        print(f"[Elasticsearch Error] 하이브리드 검색 수행 중 오류 발생: {e}")

    return results


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
