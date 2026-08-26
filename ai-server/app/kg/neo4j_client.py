"""
Neo4j Knowledge Graph 지식 그래프 DB 클라이언트 모듈

[역할 및 원칙]
1. Confluence 메타데이터(문서, 인물, 카테고리) 기반의 결정론적(Deterministic) 그래프 스키마 구축
2. 노드: Person, Document, Category
3. 관계:
   - (Person)-[:AUTHORED]->(Document)
   - (Person)-[:TOP_CONTRIBUTOR]->(Document)
   - (Document)-[:LINKS_TO]->(Document)
   - (Document)-[:BELONGS_TO]->(Category)
4. 관계형 질의(인물 담당/기여, 문서 간 연결) 시 1~2 hop 서브그래프를 추출하여 LLM Context에 병합
"""
import asyncio
import logging
from typing import List, Dict, Any, Optional, Set
from neo4j import GraphDatabase, Driver
from app.config import settings

logger = logging.getLogger(__name__)

# 싱글톤 Neo4j 드라이버 인스턴스 캐시
_driver: Optional[Driver] = None


def get_neo4j_driver() -> Driver:
    """
    Neo4j GraphDatabase Driver 인스턴스를 반환합니다 (연결 풀 관리).
    """
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(
            settings.NEO4J_URI,
            auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
            max_connection_lifetime=3600,
            max_connection_pool_size=50,
            connection_acquisition_timeout=10.0,
        )
    return _driver


def close_neo4j_driver() -> None:
    """
    애플리케이션 종료 시 Neo4j 연결 풀을 정상 종료합니다.
    """
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None
        logger.info("[Neo4j] 드라이버 연결 종료 완료")


def verify_connectivity() -> bool:
    """
    Neo4j 서버와의 연결 상태를 검증합니다.
    """
    try:
        driver = get_neo4j_driver()
        driver.verify_connectivity()
        return True
    except Exception as e:
        logger.error(f"[Neo4j] 연결 실패: {e}")
        return False


def init_kg_schema() -> None:
    """
    지식 그래프 제약조건(Constraints) 및 인덱스(Indexes)를 초기화합니다.
    """
    driver = get_neo4j_driver()
    queries = [
        "CREATE CONSTRAINT doc_id_unique IF NOT EXISTS FOR (d:Document) REQUIRE d.doc_id IS UNIQUE",
        "CREATE CONSTRAINT person_name_unique IF NOT EXISTS FOR (p:Person) REQUIRE p.name IS UNIQUE",
        "CREATE CONSTRAINT category_name_unique IF NOT EXISTS FOR (c:Category) REQUIRE c.name IS UNIQUE",
        "CREATE INDEX doc_title_index IF NOT EXISTS FOR (d:Document) ON (d.title)",
    ]
    with driver.session() as session:
        for q in queries:
            session.run(q)
    logger.info("[Neo4j] 지식 그래프 스키마(제약조건/인덱스) 초기화 완료")


def ingest_graph_data(documents: List[Dict[str, Any]], batch_size: int = 200) -> Dict[str, int]:
    """
    Elasticsearch 또는 파서에서 추출한 문서 메타데이터 목록을 Neo4j에 일괄 적재합니다.

    [적재 대상]
    - Document 노드 (doc_id, title, url, path, space_key, updated_at)
    - Category 노드 & [:BELONGS_TO] 관계
    - Person 노드 & [:AUTHORED], [:TOP_CONTRIBUTOR] 관계
    - Document 간 [:LINKS_TO] 관계
    """
    if not documents:
        return {"documents": 0, "persons": 0, "categories": 0, "relationships": 0}

    init_kg_schema()
    driver = get_neo4j_driver()

    # 정제된 데이터 배치 준비
    clean_docs = []
    for doc in documents:
        doc_id = str(doc.get("doc_id", "")).strip()
        if not doc_id:
            continue
        clean_docs.append({
            "doc_id": doc_id,
            "title": doc.get("title", ""),
            "url": doc.get("url", ""),
            "path": doc.get("path", ""),
            "space_key": doc.get("space_key", ""),
            "category": doc.get("category", "") or "",
            "author": doc.get("author", "") or "",
            "primary_contributor": doc.get("primary_contributor", "") or "",
            "links": [str(link).strip() for link in (doc.get("links") or []) if link and not str(link).startswith("http")],
            "updated_at": str(doc.get("updated_at", "")) if doc.get("updated_at") else "",
        })

    with driver.session() as session:
        # 1. Document & Category & BELONGS_TO 일괄 생성
        doc_cypher = """
        UNWIND $batch AS doc
        MERGE (d:Document {doc_id: doc.doc_id})
        SET d.title = doc.title,
            d.url = doc.url,
            d.path = doc.path,
            d.space_key = doc.space_key,
            d.updated_at = doc.updated_at
        WITH d, doc
        WHERE doc.category <> ''
        MERGE (c:Category {name: doc.category})
        MERGE (d)-[:BELONGS_TO]->(c)
        """
        for i in range(0, len(clean_docs), batch_size):
            batch = clean_docs[i:i + batch_size]
            session.run(doc_cypher, batch=batch)

        # 2. Person & AUTHORED 일괄 생성
        author_cypher = """
        UNWIND $batch AS doc
        MATCH (d:Document {doc_id: doc.doc_id})
        WHERE doc.author <> '' AND doc.author <> 'Unknown'
        MERGE (p:Person {name: doc.author})
        MERGE (p)-[:AUTHORED]->(d)
        """
        for i in range(0, len(clean_docs), batch_size):
            batch = clean_docs[i:i + batch_size]
            session.run(author_cypher, batch=batch)

        # 3. Person & TOP_CONTRIBUTOR 일괄 생성
        contributor_cypher = """
        UNWIND $batch AS doc
        MATCH (d:Document {doc_id: doc.doc_id})
        WHERE doc.primary_contributor <> '' AND doc.primary_contributor <> 'Unknown'
        MERGE (p:Person {name: doc.primary_contributor})
        MERGE (p)-[:TOP_CONTRIBUTOR]->(d)
        """
        for i in range(0, len(clean_docs), batch_size):
            batch = clean_docs[i:i + batch_size]
            session.run(contributor_cypher, batch=batch)

        # 4. Document 간 LINKS_TO 일괄 생성 (title 매칭)
        links_cypher = """
        UNWIND $batch AS doc
        MATCH (source:Document {doc_id: doc.doc_id})
        UNWIND doc.links AS target_title
        MATCH (target:Document {title: target_title})
        WHERE source <> target
        MERGE (source)-[:LINKS_TO]->(target)
        """
        for i in range(0, len(clean_docs), batch_size):
            batch = clean_docs[i:i + batch_size]
            session.run(links_cypher, batch=batch)

    stats = get_graph_stats()
    logger.info(f"[Neo4j] 지식 그래프 적재 완료: {stats}")
    return stats


def get_graph_stats() -> Dict[str, int]:
    """
    Neo4j에 적재된 노드 및 관계 총계 통계를 반환합니다.
    """
    driver = get_neo4j_driver()
    stats = {}
    with driver.session() as session:
        # 노드 수
        res = session.run("MATCH (d:Document) RETURN count(d) AS count")
        stats["documents"] = res.single()["count"]

        res = session.run("MATCH (p:Person) RETURN count(p) AS count")
        stats["persons"] = res.single()["count"]

        res = session.run("MATCH (c:Category) RETURN count(c) AS count")
        stats["categories"] = res.single()["count"]

        # 관계 수
        res = session.run("MATCH ()-[r:AUTHORED]->() RETURN count(r) AS count")
        stats["authored_edges"] = res.single()["count"]

        res = session.run("MATCH ()-[r:TOP_CONTRIBUTOR]->() RETURN count(r) AS count")
        stats["top_contributor_edges"] = res.single()["count"]

        res = session.run("MATCH ()-[r:LINKS_TO]->() RETURN count(r) AS count")
        stats["links_edges"] = res.single()["count"]

        res = session.run("MATCH ()-[r:BELONGS_TO]->() RETURN count(r) AS count")
        stats["belongs_edges"] = res.single()["count"]

    return stats


def get_all_person_names() -> List[str]:
    """
    등록된 모든 Person 이름 목록을 반환합니다 (질문 키워드 매칭용).
    """
    driver = get_neo4j_driver()
    with driver.session() as session:
        res = session.run("MATCH (p:Person) RETURN p.name AS name ORDER BY size(p.name) DESC")
        return [record["name"] for record in res if record["name"]]


def search_graph_context(
    query: str,
    doc_ids: Optional[List[str]] = None,
    limit: int = 10
) -> Dict[str, Any]:
    """
    사용자 질문(query) 또는 Hybrid 검색된 문서 ID(doc_ids)를 기반으로 1~2 hop 서브그래프를 탐색합니다.

    [탐색 시나리오]
    1. doc_ids가 주어진 경우: 해당 문서들의 작성자, 최다 기여자, 연결 문서(LINKS_TO) 탐색
    2. query에 특정 Person 이름이나 문서 제목이 언급된 경우: 해당 인물이 작성/기여한 문서 또는 관련 링크 탐색

    [반환값]
    {
        "entities": [{"type": "Person", "name": "..."}, ...],
        "relations": [{"source": "...", "type": "AUTHORED", "target": "..."}, ...],
        "formatted_context": "### 🌐 지식 그래프 관계 정보\n- ..."
    }
    """
    driver = get_neo4j_driver()
    entities: List[Dict[str, Any]] = []
    relations: List[Dict[str, Any]] = []
    seen_entities: Set[str] = set()
    seen_relations: Set[str] = set()
    context_lines: List[str] = []

    target_doc_ids = [str(d).strip() for d in (doc_ids or []) if d]

    # 질문에서 인물 이름 탐색 (예: "홍길동", "위승민")
    mentioned_people = []
    if query:
        all_people = get_all_person_names()
        for person_name in all_people:
            # 괄호 포함 이름('홍길동(Gildong)')인 경우 앞부분 한글 이름('홍길동')도 매칭
            simple_name = person_name.split("(")[0].strip() if "(" in person_name else person_name
            if (len(simple_name) >= 2 and simple_name in query) or (person_name in query):
                mentioned_people.append(person_name)

    with driver.session() as session:
        # 1. 대상 문서(doc_ids) 기준 1~2 hop 탐색
        if target_doc_ids:
            doc_query = """
            MATCH (d:Document)
            WHERE d.doc_id IN $doc_ids
            OPTIONAL MATCH (author:Person)-[r1:AUTHORED]->(d)
            OPTIONAL MATCH (contrib:Person)-[r2:TOP_CONTRIBUTOR]->(d)
            OPTIONAL MATCH (d)-[r3:LINKS_TO]->(linked:Document)
            OPTIONAL MATCH (linked_author:Person)-[:AUTHORED]->(linked)
            OPTIONAL MATCH (linked_contrib:Person)-[:TOP_CONTRIBUTOR]->(linked)
            RETURN d.doc_id AS doc_id, d.title AS title,
                   author.name AS author, contrib.name AS contributor,
                   collect(DISTINCT {
                       title: linked.title,
                       doc_id: linked.doc_id,
                       author: linked_author.name,
                       contributor: linked_contrib.name
                   }) AS linked_docs
            LIMIT $limit
            """
            results = session.run(doc_query, doc_ids=target_doc_ids, limit=limit)
            for record in results:
                title = record["title"]
                doc_id = record["doc_id"]
                author = record["author"]
                contributor = record["contributor"]
                linked_docs = record["linked_docs"]

                doc_key = f"Document:{doc_id}"
                if doc_key not in seen_entities:
                    seen_entities.add(doc_key)
                    entities.append({"type": "Document", "id": doc_id, "title": title})

                meta_parts = []
                if author:
                    meta_parts.append(f"최초 작성자: {author}")
                    p_key = f"Person:{author}"
                    if p_key not in seen_entities:
                        seen_entities.add(p_key)
                        entities.append({"type": "Person", "name": author})
                    rel_key = f"{author}-AUTHORED->{doc_id}"
                    if rel_key not in seen_relations:
                        seen_relations.add(rel_key)
                        relations.append({"source": author, "type": "AUTHORED", "target": title})

                if contributor:
                    meta_parts.append(f"최다 기여자: {contributor}")
                    p_key = f"Person:{contributor}"
                    if p_key not in seen_entities:
                        seen_entities.add(p_key)
                        entities.append({"type": "Person", "name": contributor})
                    rel_key = f"{contributor}-TOP_CONTRIBUTOR->{doc_id}"
                    if rel_key not in seen_relations:
                        seen_relations.add(rel_key)
                        relations.append({"source": contributor, "type": "TOP_CONTRIBUTOR", "target": title})

                meta_str = f" ({', '.join(meta_parts)})" if meta_parts else ""
                context_lines.append(f"- 문서 '{title}'{meta_str}")

                # 연결 문서 정보
                for ldoc in linked_docs:
                    ltitle = ldoc.get("title")
                    if ltitle:
                        ldoc_id = ldoc.get("doc_id")
                        lauthor = ldoc.get("author")
                        lcontrib = ldoc.get("contributor")

                        lparts = []
                        if lauthor:
                            lparts.append(f"작성자: {lauthor}")
                        if lcontrib:
                            lparts.append(f"최다 기여자: {lcontrib}")
                        l_meta = f" ({', '.join(lparts)})" if lparts else ""

                        context_lines.append(f"  └─ 참조 연결(LINKS_TO) ➔ '{ltitle}'{l_meta}")

                        rel_key = f"{doc_id}-LINKS_TO->{ldoc_id}"
                        if rel_key not in seen_relations:
                            seen_relations.add(rel_key)
                            relations.append({"source": title, "type": "LINKS_TO", "target": ltitle})

        # 2. 질문에 인물 이름이 언급된 경우의 인물 중심 탐색
        if mentioned_people:
            person_query = """
            MATCH (p:Person)
            WHERE p.name IN $people
            OPTIONAL MATCH (p)-[:AUTHORED]->(auth_doc:Document)
            OPTIONAL MATCH (p)-[:TOP_CONTRIBUTOR]->(contrib_doc:Document)
            RETURN p.name AS name,
                   collect(DISTINCT auth_doc.title) AS authored_titles,
                   collect(DISTINCT contrib_doc.title) AS contributed_titles
            LIMIT $limit
            """
            results = session.run(person_query, people=mentioned_people, limit=limit)
            for record in results:
                name = record["name"]
                auth_titles = [t for t in record["authored_titles"] if t]
                contrib_titles = [t for t in record["contributed_titles"] if t]

                p_key = f"Person:{name}"
                if p_key not in seen_entities:
                    seen_entities.add(p_key)
                    entities.append({"type": "Person", "name": name})

                p_info = []
                if auth_titles:
                    p_info.append(f"최초 작성한 문서: {', '.join(auth_titles[:5])}")
                if contrib_titles:
                    p_info.append(f"최다 기여한 문서: {', '.join(contrib_titles[:5])}")

                if p_info:
                    context_lines.append(f"- 인물 '{name}': {' / '.join(p_info)}")

    formatted_context = ""
    if context_lines:
        formatted_context = "### 🌐 지식 그래프 관계 정보 (Knowledge Graph Context)\n" + "\n".join(context_lines)

    return {
        "entities": entities,
        "relations": relations,
        "formatted_context": formatted_context,
    }


async def search_graph_context_async(
    query: str,
    doc_ids: Optional[List[str]] = None,
    limit: int = 10
) -> Dict[str, Any]:
    """
    [비동기] search_graph_context를 asyncio.to_thread로 실행하여 FastAPI 논블로킹 처리를 지원합니다.
    """
    return await asyncio.to_thread(search_graph_context, query, doc_ids, limit)
