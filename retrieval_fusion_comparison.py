"""
36문항 평가셋에 대해 4개 검색 방식을 비교한다.

공정성 설계:
- 질문 1개당 BM25 후보 50청크, kNN 후보 50청크를 "한 번만" 조회하고,
  4개 방식이 모두 그 동일한 스냅샷 위에서 재랭킹만 달리한다.
  (과거 compare_fusion.py는 RRF만 운영 함수를 다시 호출해 스냅샷이 달랐다)
- 최신성 가산점(RECENCY_BOOST_MAX)은 4개 방식 모두 끈 상태가 기본 비교다.
  결합 방식 자체의 차이만 보기 위함. 운영 RRF(가산점 ON)는 별도 방식으로 추가 계산한다.
"""
import sys, json, csv
sys.path.insert(0, '.')
from app.config import settings
from app.retrieval.es_client import (
    _SEARCH_SOURCE_FIELDS, _apply_recency_bonus, get_es_client, rrf_max_score,
)
from app.llm.litellm_client import embed_texts

CAND = settings.RETRIEVAL_CANDIDATE_SIZE   # 50
RRF_K = settings.RRF_K                     # 60
TOP_K = settings.RETRIEVAL_TOP_K           # 5

es = get_es_client()
IDX = settings.ELASTICSEARCH_INDEX
ds = json.load(open('ds36.json'))


def bm25_hits(q):
    return es.search(index=IDX, body={
        "query": {"bool": {"must": [{"multi_match": {
            "query": q, "fields": ["title^2.0", "text", "attachments^1.0"],
            "type": "best_fields"}}]}},
        "size": CAND, "_source": _SEARCH_SOURCE_FIELDS,
    })["hits"]["hits"]


def knn_hits(vec):
    return es.search(index=IDX, body={
        "knn": {"field": "text_vector", "query_vector": vec,
                "k": CAND, "num_candidates": max(CAND * 5, 50)},
        "size": CAND, "_source": _SEARCH_SOURCE_FIELDS,
    })["hits"]["hits"]


def docs_from_hits(hits):
    """hit 순서를 지키며 doc_id 중복 제거 -> 전체 문서 순위 리스트"""
    out, seen = [], set()
    for h in hits:
        d = h["_source"].get("doc_id")
        if d and d not in seen:
            seen.add(d); out.append(d)
    return out


def docs_from_scored(scored):
    scored = sorted(scored, key=lambda e: e["score"], reverse=True)
    out, seen = [], set()
    for e in scored:
        if e["doc_id"] and e["doc_id"] not in seen:
            seen.add(e["doc_id"]); out.append(e["doc_id"])
    return out


def merged(bm, kn):
    """chunk_id -> _source (bm25 우선 삽입 순서 유지, 운영 코드와 동일)"""
    m = {}
    for h in bm + kn:
        c = h["_source"].get("chunk_id")
        if c and c not in m:
            m[c] = h["_source"]
    return m


def minmax_scores(bm, kn, w_bm=4.0, w_kn=6.0):
    """운영 이전 방식: 각 리스트 0~1 정규화 후 가중평균(4:6), 없는 쪽은 0"""
    def norm(hits):
        if not hits: return {}
        s = [h["_score"] for h in hits]
        lo, hi = min(s), max(s); span = hi - lo
        return {h["_source"]["chunk_id"]: (1.0 if span == 0 else (h["_score"] - lo) / span)
                for h in hits}
    bn, kn_n = norm(bm), norm(kn)
    return {c: (w_bm * bn.get(c, 0.0) + w_kn * kn_n.get(c, 0.0)) / (w_bm + w_kn)
            for c in merged(bm, kn)}


def rrf_scores(bm, kn, k=RRF_K):
    out = {}
    for hits in (bm, kn):
        seen = set()
        rank = 0
        for h in hits:
            c = h["_source"].get("chunk_id")
            if not c or c in seen: continue
            seen.add(c); rank += 1
            out[c] = out.get(c, 0.0) + 1.0 / (k + rank)
    for c in merged(bm, kn):
        out.setdefault(c, 0.0)
    return out


def rank_of(doc_list, gold):
    return doc_list.index(gold) + 1 if gold and gold in doc_list else 0


vecs = embed_texts([d['question'] for d in ds])
rows = []

for d, vec in zip(ds, vecs):
    bm, kn = bm25_hits(d['question']), knn_hits(vec)
    src = merged(bm, kn)
    gold = d['page_id']

    docs_bm = docs_from_hits(bm)
    docs_kn = docs_from_hits(kn)

    mm = minmax_scores(bm, kn)
    rr = rrf_scores(bm, kn)
    mm_scored = [{"doc_id": s["doc_id"], "score": mm[c], "updated_at": s.get("updated_at")}
                 for c, s in src.items()]
    rr_scored = [{"doc_id": s["doc_id"], "score": rr[c], "updated_at": s.get("updated_at")}
                 for c, s in src.items()]
    docs_mm = docs_from_scored(mm_scored)
    docs_rr = docs_from_scored(rr_scored)

    # 운영 설정(RRF + 최신성 가산점 4%) 재현
    rr_prod = [dict(e) for e in rr_scored]
    _apply_recency_bonus(rr_prod, settings.RECENCY_BOOST_MAX)
    docs_rr_prod = docs_from_scored(rr_prod)

    # 정답 문서의 원시 근거: 각 리스트에서 정답 문서의 최상위 청크 위치와 점수
    def gold_eviden(hits):
        for i, h in enumerate(hits, 1):
            if h["_source"].get("doc_id") == gold:
                return i, round(h["_score"], 4)
        return 0, None
    bm_chunk_rank, bm_score = gold_eviden(bm)
    kn_chunk_rank, kn_score = gold_eviden(kn)

    rows.append({
        "id": d["id"], "source_id": d["source_id"], "category": d["category"],
        "answerability": d["answerability"], "retrieval_scoring": d["retrieval_scoring"],
        "question": d["question"], "page_id": gold, "document_title": d["document_title"],
        "rank_bm25": rank_of(docs_bm, gold), "rank_knn": rank_of(docs_kn, gold),
        "rank_minmax": rank_of(docs_mm, gold), "rank_rrf": rank_of(docs_rr, gold),
        "rank_rrf_prod": rank_of(docs_rr_prod, gold),
        "gold_bm25_chunk_rank": bm_chunk_rank, "gold_bm25_score": bm_score,
        "gold_knn_chunk_rank": kn_chunk_rank, "gold_knn_score": kn_score,
        "bm25_top_score": round(bm[0]["_score"], 4) if bm else None,
        "knn_top_score": round(kn[0]["_score"], 4) if kn else None,
        "top1_bm25": docs_bm[0] if docs_bm else "",
        "top1_knn": docs_kn[0] if docs_kn else "",
        "top1_minmax": docs_mm[0] if docs_mm else "",
        "top1_rrf": docs_rr[0] if docs_rr else "",
        "top5_rrf": " / ".join(docs_rr[:5]),
    })

with open('compare4_per_question.csv', 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
with open('compare4_per_question.json', 'w') as f:
    json.dump(rows, f, ensure_ascii=False, indent=2); f.write("\n")
print("saved compare4_per_question.{csv,json}", len(rows), "rows")
