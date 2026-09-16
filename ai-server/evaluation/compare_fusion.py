"""
[검색 결합 방식 비교 — 읽기 전용]

같은 문항 집합에 대해 결합 방식만 바꿔가며 검색 지표(hit@5, MRR)를 잰다.
답변 생성과 LLM 판정을 거치지 않으므로 빠르고 값이 흔들리지 않는다.
검색 지표는 정답 문서 id만 있으면 계산되기 때문이다.

비교 대상:
- 하이브리드: 운영 코드 `search_hybrid` 그대로 (RRF, K=60)
- min-max 4:6: 이전 방식 (각 리스트를 0~1로 정규화한 뒤 가중평균). 2026-09-14에 RRF로 교체
- BM25 단독 / kNN 단독: 한쪽 리스트만 문서 단위로 정리
- 슬롯 배분: 점수를 섞지 않고 각 리스트에서 정해진 수만큼 가져오는 방식

문항 집합:
- v2 원본 (`dataset_items`): 질문의 84.7%가 문서 제목 단어를 포함한다
  (v4 paraphrase 대조군은 2026-09-16에 제거했다. LLM이 LLM 질문을 재작성한 것이라
   "실제 사용자 질문의 표본"으로 제시하기 어렵다고 판단했다.)

실행:
    docker exec -i rag-ai-server python -m evaluation.compare_fusion
"""
from app.config import settings
from app.retrieval.es_client import (
    _SEARCH_SOURCE_FIELDS,
    _apply_recency_bonus,
    get_es_client,
    search_hybrid,
)
from app.llm.litellm_client import embed_texts
from evaluation.dataset_items import QA_DATASET_ITEMS

TOP_K = settings.RETRIEVAL_TOP_K
CANDIDATE_SIZE = settings.RETRIEVAL_CANDIDATE_SIZE


def _scorable_expected_ids(metadata):
    """run_qa._scorable_expected_ids와 같은 기준."""
    meta = metadata or {}
    if meta.get("known_gap"):
        return None
    return meta.get("expected_doc_ids") or None


def _distinct_docs(hits):
    """hit 순서를 지키면서 중복 doc_id를 걷어내고 상위 TOP_K개를 돌려준다."""
    picked, seen = [], set()
    for hit in hits:
        doc_id = hit["_source"].get("doc_id")
        if doc_id and doc_id not in seen:
            seen.add(doc_id)
            picked.append(doc_id)
    return picked[:TOP_K]


def _bm25_hits(es, index, query):
    return es.search(index=index, body={
        "query": {"bool": {"must": [{"multi_match": {
            "query": query,
            "fields": ["title^2.0", "text"],
            "type": "best_fields",
        }}]}},
        "size": CANDIDATE_SIZE,
        "_source": _SEARCH_SOURCE_FIELDS,
    })["hits"]["hits"]


def _knn_hits(es, index, vector):
    return es.search(index=index, body={
        "knn": {
            "field": "text_vector",
            "query_vector": vector,
            "k": CANDIDATE_SIZE,
            "num_candidates": max(CANDIDATE_SIZE * 5, 50),
        },
        "size": CANDIDATE_SIZE,
        "_source": _SEARCH_SOURCE_FIELDS,
    })["hits"]["hits"]


def _slot(n_knn, n_bm25):
    """
    점수를 섞지 않고 각 리스트에서 정해진 수만큼 가져오는 방식.
    한쪽에만 걸린 문서도 자리가 보장되지만, 두 리스트의 순위를 견줄 수 없다.
    """
    def pick(bm25_docs, knn_docs, **_):
        chosen, seen = [], set()
        for source, quota in ((knn_docs, n_knn), (bm25_docs, n_bm25)):
            taken = 0
            for doc_id in source:
                if taken >= quota:
                    break
                if doc_id in seen:
                    continue
                seen.add(doc_id)
                chosen.append(doc_id)
                taken += 1
        return chosen[:TOP_K]
    return pick


def _minmax_fusion(bm25_hits, knn_hits, w_bm25=4.0, w_knn=6.0):
    """2026-09-14 이전의 결합 방식. RRF와 비교하기 위해 남겨둔다."""
    def norm(hits):
        if not hits:
            return {}
        scores = [h["_score"] for h in hits]
        lo, hi = min(scores), max(scores)
        span = hi - lo
        return {h["_source"]["chunk_id"]: (1.0 if span == 0 else (h["_score"] - lo) / span)
                for h in hits}

    bn, kn = norm(bm25_hits), norm(knn_hits)
    merged = {}
    for hit in bm25_hits + knn_hits:
        merged.setdefault(hit["_source"]["chunk_id"], hit["_source"])
    scored = [{**src, "score": (w_bm25 * bn.get(c, 0.0) + w_knn * kn.get(c, 0.0)) / (w_bm25 + w_knn)}
              for c, src in merged.items()]
    _apply_recency_bonus(scored, settings.RECENCY_BOOST_MAX)
    scored.sort(key=lambda e: e["score"], reverse=True)
    picked, seen = [], set()
    for e in scored:
        if e["doc_id"] in seen:
            continue
        seen.add(e["doc_id"])
        picked.append(e["doc_id"])
        if len(picked) >= TOP_K:
            break
    return picked


STRATEGIES = {
    "하이브리드 RRF (운영)": lambda *, query, vector, **_: [
        e["doc_id"] for e in search_hybrid(query, vector, top_k=TOP_K)
    ],
    "이전 방식 min-max 4:6": lambda *, bm25_hits, knn_hits, **_: _minmax_fusion(bm25_hits, knn_hits),
    "BM25 단독": lambda *, bm25_docs, **_: bm25_docs[:TOP_K],
    "kNN 단독": lambda *, knn_docs, **_: knn_docs[:TOP_K],
    "슬롯 kNN3+BM25 2": _slot(3, 2),
    "슬롯 kNN4+BM25 1": _slot(4, 1),
    "슬롯 kNN2+BM25 3": _slot(2, 3),
}


def _mrr(retrieved, expected_ids):
    rank = next((i for i, d in enumerate(retrieved, 1) if d in expected_ids), None)
    return 1.0 / rank if rank else 0.0


def evaluate(items, label):
    pairs = []
    for item in items:
        expected = _scorable_expected_ids(item.get("metadata"))
        if expected:
            pairs.append((item["input"], expected))

    es = get_es_client()
    index = settings.ELASTICSEARCH_INDEX
    vectors = embed_texts([q for q, _ in pairs])

    totals = {name: [0.0, 0.0] for name in STRATEGIES}
    head_to_head = {"hybrid": 0, "bm25": 0, "tie": 0}

    for (query, expected), vector in zip(pairs, vectors):
        bm25_raw = _bm25_hits(es, index, query)
        knn_raw = _knn_hits(es, index, vector)
        bm25_docs = _distinct_docs(bm25_raw)
        knn_docs = _distinct_docs(knn_raw)

        results = {}
        for name, strategy in STRATEGIES.items():
            retrieved = strategy(query=query, vector=vector,
                                 bm25_docs=bm25_docs, knn_docs=knn_docs,
                                 bm25_hits=bm25_raw, knn_hits=knn_raw)
            results[name] = retrieved
            totals[name][0] += 1.0 if any(d in retrieved for d in expected) else 0.0
            totals[name][1] += _mrr(retrieved, expected)

        hybrid_mrr = _mrr(results["하이브리드 RRF (운영)"], expected)
        bm25_mrr = _mrr(results["BM25 단독"], expected)
        if hybrid_mrr > bm25_mrr:
            head_to_head["hybrid"] += 1
        elif bm25_mrr > hybrid_mrr:
            head_to_head["bm25"] += 1
        else:
            head_to_head["tie"] += 1

    n = len(pairs)
    print(f"\n=== {label} (n={n}) ===")
    print(f"  {'방식':<20} {'hit@5':>8} {'MRR':>8}")
    print("  " + "-" * 38)
    for name, (hit, mrr) in totals.items():
        print(f"  {name:<20} {hit / n:>8.3f} {mrr / n:>8.3f}")
    print(f"  문항별 승부(MRR): 하이브리드 {head_to_head['hybrid']}승 / "
          f"BM25 {head_to_head['bm25']}승 / 동점 {head_to_head['tie']}")


if __name__ == "__main__":
    evaluate(QA_DATASET_ITEMS, "v2 원본 — 제목 단어 84.7% 포함")
