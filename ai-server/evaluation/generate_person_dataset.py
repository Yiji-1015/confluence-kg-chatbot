"""Build a deterministic Person/Document graph evaluation snapshot from Elasticsearch."""

import argparse
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from pprint import pformat

from app.config import settings
from app.retrieval.es_client import get_es_client


DATASET_SIZE = 20
SENSITIVE_TERMS = ("비밀번호", "IP 목록", "피드백 세션", "인력 프로파일", "인력 기술정보", "멤버 연락처")
INVALID_PEOPLE = ("Unknown", "알 수 없음", "이전 사용자 (Deleted)", "(Deactivated)")


def _load_documents():
    response = get_es_client().search(
        index=settings.ELASTICSEARCH_INDEX,
        body={
            "size": 10_000,
            "_source": ["doc_id", "title", "author", "primary_contributor", "links", "path"],
            "query": {"match_all": {}},
            "collapse": {"field": "doc_id"},
            "sort": [{"doc_id": "asc"}],
        },
    )
    return [hit["_source"] for hit in response["hits"]["hits"]]


def _valid_person(name):
    return bool(name) and not any(term in name for term in INVALID_PEOPLE)


def _safe_document(document):
    text = f"{document.get('title', '')} {document.get('path', '')}"
    return (
        document.get("doc_id")
        and document.get("title")
        and _valid_person(document.get("author"))
        and _valid_person(document.get("primary_contributor"))
        and not any(term in text for term in SENSITIVE_TERMS)
    )


def _entity_ids(*values):
    return list(dict.fromkeys(values))


def _metadata(subtype, snapshot_at, documents, people, relations, path):
    return {
        "type": "person_relation",
        "subtype": subtype,
        "snapshot_at": snapshot_at,
        "hops": max(1, len(path) - 1),
        "expected_doc_ids": [document["doc_id"] for document in documents],
        "expected_entity_ids": _entity_ids(
            *(f"document:{document['doc_id']}" for document in documents),
            *(f"person:{person}" for person in people),
        ),
        "expected_relations": list(dict.fromkeys(relations)),
        "expected_path": path,
        "source_titles": [document["title"] for document in documents],
    }


def _ownership_items(documents, snapshot_at):
    candidates = []
    seen_pairs = set()
    for document in documents:
        author = document.get("author")
        contributor = document.get("primary_contributor")
        pair = (author, contributor)
        if not _safe_document(document) or author == contributor or pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        candidates.append(document)
        if len(candidates) == 8:
            break

    items = []
    for document in candidates:
        author = document["author"]
        contributor = document["primary_contributor"]
        doc_entity = f"document:{document['doc_id']}"
        path = [
            [f"person:{author}", "AUTHORED", doc_entity],
            [f"person:{contributor}", "TOP_CONTRIBUTOR", doc_entity],
        ]
        items.append({
            "input": f"'{document['title']}' 문서를 처음 작성한 사람과 현재 최다 기여자는 각각 누구야?",
            "expected_output": f"작성자는 {author}이고, 현재 최다 기여자는 {contributor}입니다.",
            "metadata": _metadata(
                "author_vs_top_contributor",
                snapshot_at,
                [document],
                [author, contributor],
                ["AUTHORED", "TOP_CONTRIBUTOR"],
                path,
            ),
        })
    return items


def _link_edges(documents):
    by_title = defaultdict(list)
    for document in documents:
        by_title[document.get("title")].append(document)

    edges = []
    seen = set()
    for source in documents:
        if not _safe_document(source):
            continue
        for title in sorted(set(source.get("links") or [])):
            matches = by_title.get(title, [])
            if len(matches) != 1:
                continue
            target = matches[0]
            key = (source["doc_id"], target["doc_id"])
            if key in seen or source["doc_id"] == target["doc_id"] or not _safe_document(target):
                continue
            seen.add(key)
            people = {
                source["author"],
                source["primary_contributor"],
                target["author"],
                target["primary_contributor"],
            }
            edges.append((len(people) == 1, source["doc_id"], target["doc_id"], source, target))
    return [(source, target) for _, _, _, source, target in sorted(edges)]


def _linked_ownership_items(edges, snapshot_at):
    items = []
    for source, target in edges[:8]:
        source_author = source["author"]
        target_contributor = target["primary_contributor"]
        source_entity = f"document:{source['doc_id']}"
        target_entity = f"document:{target['doc_id']}"
        path = [
            [f"person:{source_author}", "AUTHORED", source_entity],
            [source_entity, "LINKS_TO", target_entity],
            [f"person:{target_contributor}", "TOP_CONTRIBUTOR", target_entity],
        ]
        items.append({
            "input": (
                f"'{source['title']}'의 작성자는 누구고, 이 문서가 참조하는 "
                f"'{target['title']}'의 현재 최다 기여자는 누구야?"
            ),
            "expected_output": (
                f"'{source['title']}'의 작성자는 {source_author}이고, "
                f"'{target['title']}'의 현재 최다 기여자는 {target_contributor}입니다."
            ),
            "metadata": _metadata(
                "linked_document_ownership",
                snapshot_at,
                [source, target],
                [source_author, target_contributor],
                ["AUTHORED", "LINKS_TO", "TOP_CONTRIBUTOR"],
                path,
            ),
        })
    return items


def _comparison_items(edges, snapshot_at):
    same = [edge for edge in edges if edge[0]["primary_contributor"] == edge[1]["author"]]
    different = [edge for edge in edges if edge[0]["primary_contributor"] != edge[1]["author"]]
    selected = same[:2] + different[:2]
    items = []
    for source, target in selected:
        contributor = source["primary_contributor"]
        target_author = target["author"]
        is_same = contributor == target_author
        source_entity = f"document:{source['doc_id']}"
        target_entity = f"document:{target['doc_id']}"
        path = [
            [f"person:{contributor}", "TOP_CONTRIBUTOR", source_entity],
            [source_entity, "LINKS_TO", target_entity],
            [f"person:{target_author}", "AUTHORED", target_entity],
        ]
        answer = "같은 사람" if is_same else "서로 다른 사람"
        items.append({
            "input": (
                f"'{source['title']}'의 최다 기여자와 이 문서가 참조하는 "
                f"'{target['title']}'의 작성자는 같은 사람이야?"
            ),
            "expected_output": f"{answer}입니다. 각각 {contributor}, {target_author}입니다.",
            "metadata": _metadata(
                "linked_people_comparison",
                snapshot_at,
                [source, target],
                [contributor, target_author],
                ["TOP_CONTRIBUTOR", "LINKS_TO", "AUTHORED"],
                path,
            ),
        })
    return items


def _validate(items):
    assert len(items) == DATASET_SIZE, f"expected {DATASET_SIZE} items, got {len(items)}"
    ids = [item["id"] for item in items]
    assert len(ids) == len(set(ids)), "duplicate dataset item id"
    assert all(item["metadata"]["expected_path"] for item in items), "missing expected path"
    assert all(item["metadata"]["expected_entity_ids"] for item in items), "missing entity ids"


def generate():
    snapshot_at = datetime.now(timezone.utc).isoformat()
    documents = _load_documents()
    edges = _link_edges(documents)
    items = (
        _ownership_items(documents, snapshot_at)
        + _linked_ownership_items(edges, snapshot_at)
        + _comparison_items(edges, snapshot_at)
    )
    for index, item in enumerate(items, 1):
        item["id"] = f"qa-v3-person-{index:03d}"
    _validate(items)
    return snapshot_at, items


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="evaluation/person_dataset_items.py")
    args = parser.parse_args()
    snapshot_at, items = generate()
    content = (
        '"""Person/Document graph QA snapshot generated from Elasticsearch metadata.\n\n'
        "Regenerate after Confluence reindexing because TOP_CONTRIBUTOR can change.\n"
        '"""\n\n'
        f"SNAPSHOT_AT = {snapshot_at!r}\n\n"
        f"QA_DATASET_ITEMS = {pformat(items, sort_dicts=False, width=110)}\n"
    )
    Path(args.output).write_text(content, encoding="utf-8")
    print(f"wrote {len(items)} items to {args.output} ({snapshot_at})")


if __name__ == "__main__":
    main()
