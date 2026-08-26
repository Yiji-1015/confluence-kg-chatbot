"""
Confluence 문서 메타데이터 기반 Neo4j 지식 그래프 적재 스크립트

[실행 방법]
    docker exec -it kg-ai-server python -m scripts.ingest_kg
    또는 로컬:
    cd ai-server && python -m scripts.ingest_kg
"""
import argparse
import logging
from app.config import settings
from app.retrieval.es_client import get_es_client
from app.kg.neo4j_client import ingest_graph_data, get_graph_stats, verify_connectivity

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def fetch_unique_documents_from_es(limit: int = None):
    """
    Elasticsearch에서 중복을 제거한 고유 Confluence 문서 메타데이터 목록을 조회합니다.
    """
    es = get_es_client()
    query_body = {
        "size": 10_000,
        "_source": [
            "doc_id", "title", "author", "primary_contributor",
            "links", "path", "category", "space_key", "url", "updated_at"
        ],
        "query": {"match_all": {}},
        "collapse": {"field": "doc_id"},
        "sort": [{"doc_id": "asc"}]
    }

    response = es.search(index=settings.ELASTICSEARCH_INDEX, body=query_body)
    hits = response.get("hits", {}).get("hits", [])
    documents = [h["_source"] for h in hits if "_source" in h]

    if limit:
        documents = documents[:limit]

    return documents


def main():
    parser = argparse.ArgumentParser(description="Confluence 메타데이터를 Neo4j 지식 그래프에 적재합니다.")
    parser.add_argument("--limit", type=int, default=None, help="적재할 최대 문서 수 제한")
    args = parser.parse_args()

    print("=" * 60)
    print("🚀 [1/3] Neo4j 지식 그래프(Knowledge Graph) 적재 시작")
    print("=" * 60)

    if not verify_connectivity():
        print("❌ Neo4j 서버에 연결할 수 없습니다. 컨테이너 상태 및 설정을 확인하세요.")
        return

    print("📊 [2/3] Elasticsearch에서 고유 문서 메타데이터 수집 중...")
    documents = fetch_unique_documents_from_es(limit=args.limit)
    print(f"  -> {len(documents)}개 고유 문서 메타데이터 추출 완료")

    if not documents:
        print("❌ 적재할 문서 메타데이터가 없습니다.")
        return

    print("⚙️ [3/3] Neo4j 스키마 초기화 및 노드/관계 일괄 적재 중 (UNWIND Batch)...")
    stats = ingest_graph_data(documents)

    print("\n" + "=" * 60)
    print("✅ 지식 그래프(Knowledge Graph) 적재 성공!")
    print("=" * 60)
    print(f" • Document 노드 수: {stats.get('documents', 0):,}개")
    print(f" • Person 노드 수:   {stats.get('persons', 0):,}명")
    print(f" • Category 노드 수: {stats.get('categories', 0):,}개")
    print(" • 관계 엣지:")
    print(f"   - [:AUTHORED] (최초 작성):       {stats.get('authored_edges', 0):,}건")
    print(f"   - [:TOP_CONTRIBUTOR] (최다 기여): {stats.get('top_contributor_edges', 0):,}건")
    print(f"   - [:LINKS_TO] (문서 간 링크):      {stats.get('links_edges', 0):,}건")
    print(f"   - [:BELONGS_TO] (카테고리 소속):   {stats.get('belongs_edges', 0):,}건")
    print("=" * 60)


if __name__ == "__main__":
    main()
