"""
같은 후보 위에서 검색 결합 방식만 갈아끼운다. 평가에서 "검색 방식이 최종 답변 품질에
얼마나 영향을 주는가"를 재기 위한 모듈이다.

**왜 필요한가.** `retrieval_hit`/`MRR`은 정답 문서가 top5에 들었는지만 본다. 함께 딸려온
나머지 네 문서가 지저분해도 만점이다. 그런데 컨텍스트에는 다섯 문서가 다 들어가고,
답변은 그 다섯을 보고 만들어진다. 검색 지표가 높다고 답변이 좋다는 보장이 없다.
그래서 방식마다 **끝까지(생성까지) 태워서** RAGAS로 잰다.

**공정성.** 질문 하나당 BM25 후보 50청크와 kNN 후보 50청크를 **한 번만** 조회하고
(`_CANDIDATE_CACHE`), 모든 방식이 그 동일한 스냅샷 위에서 재랭킹만 달리한다.
방식마다 따로 조회하면 스냅샷이 달라져 "결합 방식의 차이"가 아닌 것이 섞인다.

재랭킹 이후 경로(top_k 문서 선별 → 문서 전체 청크 재조립)는 `search_hybrid`와
**같은 함수**를 쓴다. 컨텍스트 조립이 방식마다 다르면 비교가 성립하지 않는다.

방식:
    bm25         BM25 단독 (키워드)
    knn          kNN 단독 (의미)
    minmax       min-max 정규화 후 가중평균 4:6 (운영 이전 방식)
    rrf          RRF (현재 결합 방식), 최신성 가산점 OFF
    rrf_recency  RRF + 최신성 가산점 (운영 설정 그대로)

기본 비교는 최신성 가산점을 **모두 끈** 상태다. 결합 방식 자체의 차이만 보기 위함이고,
운영 설정은 `rrf_recency` 한 행으로 따로 본다.
"""
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings
from app.retrieval.es_client import (
    _BM25_FIELDS,
    _SEARCH_SOURCE_FIELDS,
    _apply_recency_bonus,
    _fetch_full_doc_texts,
    _rrf_scores,
    get_es_client,
)

# 표에 찍을 이름. 코드 상수와 발표 자료의 표기를 한 곳에서 맞춘다.
METHOD_LABELS = {
    "bm25": "BM25 단독",
    "knn": "kNN 단독",
    "minmax": "min-max 4:6",
    "rrf": "RRF",
    "rrf_recency": "RRF + 최신성 (운영)",
}
METHODS = list(METHOD_LABELS)

# min-max 가중치. 운영 이전 설정을 그대로 쓴다 (BM25 4 : kNN 6).
MINMAX_W_BM25 = 4.0
MINMAX_W_KNN = 6.0

# 질문 텍스트 -> (bm25_hits, knn_hits). 한 실행 안에서 방식들이 같은 후보를 보게 한다.
# ES 검색은 결정적이라 다시 불러도 같은 결과지만, 캐시해 두면 "같은 스냅샷"이
# 설계로 보장되고 호출도 방식 수만큼 줄어든다.
_CANDIDATE_CACHE: Dict[str, Tuple[List[Dict], List[Dict]]] = {}


def clear_cache() -> None:
    """방식 하나를 다 돌고 다음 방식으로 넘어갈 때는 비우지 않는다. 실행 전체가 끝난 뒤에만."""
    _CANDIDATE_CACHE.clear()


def _candidates(query_text: str, query_vector: Optional[List[float]]):
    """BM25/kNN 후보를 조회한다 (질문당 1회). `search_hybrid`의 1단계와 같은 쿼리다."""
    if query_text in _CANDIDATE_CACHE:
        return _CANDIDATE_CACHE[query_text]

    es = get_es_client()
    index = settings.ELASTICSEARCH_INDEX
    size = settings.RETRIEVAL_CANDIDATE_SIZE

    bm25_res = es.search(index=index, body={
        "query": {"bool": {"must": [{"multi_match": {
            "query": query_text, "fields": _BM25_FIELDS, "type": "best_fields"}}]}},
        "size": size,
        "_source": _SEARCH_SOURCE_FIELDS,
    })
    bm25_hits = bm25_res.get("hits", {}).get("hits", [])

    knn_hits: List[Dict[str, Any]] = []
    if query_vector:
        knn_res = es.search(index=index, body={
            "knn": {"field": "text_vector", "query_vector": query_vector,
                    "k": size, "num_candidates": max(size * 5, 50)},
            "size": size,
            "_source": _SEARCH_SOURCE_FIELDS,
        })
        knn_hits = knn_res.get("hits", {}).get("hits", [])

    _CANDIDATE_CACHE[query_text] = (bm25_hits, knn_hits)
    return bm25_hits, knn_hits


def _merge_sources(hit_lists) -> Dict[str, Dict[str, Any]]:
    """chunk_id -> _source. 먼저 들어온 쪽의 것을 유지한다(운영 코드와 같은 순서)."""
    merged: Dict[str, Dict[str, Any]] = {}
    for hits in hit_lists:
        for hit in hits:
            chunk_id = hit["_source"].get("chunk_id")
            if chunk_id and chunk_id not in merged:
                merged[chunk_id] = hit["_source"]
    return merged


def _minmax_norm(hits: List[Dict[str, Any]]) -> Dict[str, float]:
    """리스트 하나를 0~1로 정규화한다. 폭이 0이면(전부 동점) 전부 1.0."""
    if not hits:
        return {}
    scores = [hit["_score"] for hit in hits]
    low, high = min(scores), max(scores)
    span = high - low
    return {hit["_source"]["chunk_id"]: (1.0 if span == 0 else (hit["_score"] - low) / span)
            for hit in hits}


def fuse(method: str, bm25_hits, knn_hits):
    """
    방식에 따라 (후보 source 맵, chunk_id -> 점수)를 돌려준다.
    ES 왕복 없이 순수 계산만 하므로 셀프체크로 검증할 수 있다.

    단독 방식(bm25/knn)은 **자기 후보만** 본다. 상대 리스트의 청크를 0점으로 섞어도
    정렬 결과는 같지만, 단독 방식의 후보 풀에 상대 검색 결과가 들어 있는 것 자체가
    "단독"이라는 이름과 맞지 않는다.
    """
    if method == "bm25":
        sources = _merge_sources([bm25_hits])
        return sources, {hit["_source"]["chunk_id"]: hit["_score"] for hit in bm25_hits
                         if hit["_source"].get("chunk_id")}

    if method == "knn":
        sources = _merge_sources([knn_hits])
        return sources, {hit["_source"]["chunk_id"]: hit["_score"] for hit in knn_hits
                         if hit["_source"].get("chunk_id")}

    sources = _merge_sources([bm25_hits, knn_hits])

    if method == "minmax":
        bm25_norm, knn_norm = _minmax_norm(bm25_hits), _minmax_norm(knn_hits)
        total = MINMAX_W_BM25 + MINMAX_W_KNN
        return sources, {
            chunk_id: (MINMAX_W_BM25 * bm25_norm.get(chunk_id, 0.0)
                       + MINMAX_W_KNN * knn_norm.get(chunk_id, 0.0)) / total
            for chunk_id in sources
        }

    if method in ("rrf", "rrf_recency"):
        bm25_rrf, knn_rrf = _rrf_scores(bm25_hits), _rrf_scores(knn_hits)
        return sources, {chunk_id: bm25_rrf.get(chunk_id, 0.0) + knn_rrf.get(chunk_id, 0.0)
                         for chunk_id in sources}

    raise ValueError(f"알 수 없는 검색 방식: {method!r} (가능: {', '.join(METHODS)})")


def search_with_method(
    method: str,
    query_text: str,
    query_vector: Optional[List[float]] = None,
    top_k: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    `search_hybrid`와 **같은 모양의 결과**를 돌려준다. 그래서 `build_context_text()`와
    평가 채점기가 코드 변경 없이 그대로 받는다.

    재랭킹만 방식별로 다르고, 그 뒤 두 단계는 운영 코드와 같은 함수를 쓴다.
      4) top_k "서로 다른 문서" 선별
      5) 그 문서들의 청크를 chunk_index 순으로 이어붙여 문서 전체 맥락으로 교체
    """
    if method not in METHOD_LABELS:
        raise ValueError(f"알 수 없는 검색 방식: {method!r} (가능: {', '.join(METHODS)})")

    top_k = top_k or settings.RETRIEVAL_TOP_K
    bm25_hits, knn_hits = _candidates(query_text, query_vector)
    sources, scores = fuse(method, bm25_hits, knn_hits)

    scored = [{
        "chunk_id": source.get("chunk_id"),
        "doc_id": source.get("doc_id"),
        "title": source.get("title"),
        "text": source.get("text"),
        "url": source.get("url"),
        "author": source.get("author"),
        "category": source.get("category"),
        "path": source.get("path"),
        "space_key": source.get("space_key"),
        "updated_at": source.get("updated_at"),
        "attachments": source.get("attachments") or [],
        "score": scores.get(chunk_id, 0.0),
    } for chunk_id, source in sources.items()]

    # 최신성 가산점은 운영 설정 재현용 한 방식에만 건다. 나머지는 결합 방식 자체만 본다.
    if method == "rrf_recency":
        _apply_recency_bonus(scored, settings.RECENCY_BOOST_MAX)

    scored.sort(key=lambda entry: entry["score"], reverse=True)

    picked: List[Dict[str, Any]] = []
    seen: set = set()
    for entry in scored:
        if entry["doc_id"] in seen:
            continue
        seen.add(entry["doc_id"])
        picked.append(entry)
        if len(picked) >= top_k:
            break

    doc_texts = _fetch_full_doc_texts(
        get_es_client(), settings.ELASTICSEARCH_INDEX, [e["doc_id"] for e in picked])
    for entry in picked:
        entry["text"] = doc_texts.get(entry["doc_id"]) or entry["text"]

    return picked


def _self_check() -> None:
    """ES 없이 결합 계산만 확인한다. 실행: python -m evaluation.retrieval_methods"""
    def hit(chunk_id, doc_id, score):
        return {"_score": score, "_source": {"chunk_id": chunk_id, "doc_id": doc_id}}

    # c1은 BM25 1위·kNN 3위, c3은 kNN 1위인데 BM25 후보 밖.
    bm25 = [hit("c1", "d1", 30.0), hit("c2", "d2", 20.0), hit("c4", "d4", 10.0)]
    knn = [hit("c3", "d3", 0.90), hit("c2", "d2", 0.85), hit("c1", "d1", 0.80)]

    def order(method):
        sources, scores = fuse(method, bm25, knn)
        return [c for c, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)]

    assert order("bm25") == ["c1", "c2", "c4"], order("bm25")
    assert order("knn") == ["c3", "c2", "c1"], order("knn")

    # RRF의 핵심: 양쪽 리스트에 든 청크(c1, c2)가 한쪽에만 든 청크(c3, c4)보다 위로 온다.
    # c3는 kNN 1위인데도 BM25 후보 밖이라 밀린다. "두 검색이 모두 인정한 것"이 이기는 설계다.
    rrf = order("rrf")
    assert set(rrf[:2]) == {"c1", "c2"}, rrf
    assert set(rrf[2:]) == {"c3", "c4"}, rrf

    # c1(1위+3위)과 c2(2위+2위)는 거의 동점인데 c1이 미세하게 앞선다.
    # 1/(k+순위)가 볼록이라 순위가 벌어진 쪽의 합이 더 크기 때문이다 (1/61+1/63 > 2/62).
    # 직관과 어긋나는 지점이라 수치로 박아둔다.
    _, rrf_scores_map = fuse("rrf", bm25, knn)
    k = settings.RRF_K
    assert abs(rrf_scores_map["c1"] - (1 / (k + 1) + 1 / (k + 3))) < 1e-12
    assert abs(rrf_scores_map["c2"] - (2 / (k + 2))) < 1e-12
    assert rrf_scores_map["c1"] > rrf_scores_map["c2"], (rrf_scores_map["c1"], rrf_scores_map["c2"])

    # 단독 방식은 상대 리스트의 청크를 후보로 갖지 않는다
    assert "c3" not in order("bm25")
    assert "c4" not in order("knn")

    # min-max는 순위가 아니라 "그 리스트 안에서의 점수 위치"를 본다. kNN 가중치가 크므로
    # (4:6) kNN에서 1위인 c3가 BM25 1위인 c1보다 위로 온다. RRF와 갈리는 지점이다.
    _, minmax_map = fuse("minmax", bm25, knn)
    assert minmax_map["c3"] > minmax_map["c1"], (minmax_map["c3"], minmax_map["c1"])
    assert set(minmax_map) == {"c1", "c2", "c3", "c4"}, sorted(minmax_map)

    try:
        fuse("없는방식", bm25, knn)
    except ValueError:
        pass
    else:
        raise AssertionError("알 수 없는 방식인데 통과했다")

    print("retrieval_methods 셀프체크 통과")
    for name in METHODS:
        print(f"  {name:<12} {METHOD_LABELS[name]}")


if __name__ == "__main__":
    _self_check()
