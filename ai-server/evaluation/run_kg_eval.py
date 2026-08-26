"""
GraphRAG (Neo4j) vs Baseline RAG 성능 비교 평가 하네스

[평가 대상]
오늘(2026-08-26) 생성한 Person/Document 관계형 벤치마크 20문항 (person_dataset_items.py)

[비교 실험]
1. Baseline: Elasticsearch Hybrid Retrieval 단독 (Graph Context 없음)
2. Proposed: Hybrid Retrieval + Neo4j 지식 그래프(1~2 hop) Context 결합

[측정 지표]
- Entity Hit Rate: 기대한 인물/문서 엔티티가 추출되었는가
- Relation Hit Rate: 기대한 관계(AUTHORED, TOP_CONTRIBUTOR, LINKS_TO)가 포함되었는가
- Answer Accuracy: 기대 정답과 LLM 생성 답변의 일치도 (정량 점수)
- Latency: 응답 지연 시간 (ms)
"""
import os
import sys
import time
from typing import Dict, Any, List

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from app.config import settings
from app.retrieval.es_client import search_hybrid
from app.kg.neo4j_client import search_graph_context
from app.llm.litellm_client import embed_texts, generate_answer
from evaluation.person_dataset_items import QA_DATASET_ITEMS


def run_single_eval(item: Dict[str, Any], use_graph: bool = True) -> Dict[str, Any]:
    query = item["input"]
    expected_output = item["expected_output"]
    metadata = item.get("metadata", {})
    expected_entities = metadata.get("expected_entity_ids", [])
    expected_relations = metadata.get("expected_relations", [])

    start_time = time.time()

    # 1. Hybrid Search
    query_vector = embed_texts([query])[0]
    es_results = search_hybrid(query_text=query, query_vector=query_vector, top_k=3)
    retrieved_doc_ids = [str(r.get("doc_id", "")) for r in es_results if r.get("doc_id")]

    context_blocks = [
        f"[문서 제목: {r.get('title')}]\n{r.get('text', '')}" for r in es_results
    ]

    graph_entities = []
    graph_relations = []

    # 2. Neo4j Graph Search (Proposed 경우에만)
    if use_graph:
        graph_data = search_graph_context(query=query, doc_ids=retrieved_doc_ids, limit=10)
        graph_entities = graph_data.get("entities", [])
        graph_relations = graph_data.get("relations", [])
        if graph_data.get("formatted_context"):
            context_blocks.insert(0, graph_data["formatted_context"])

    context_text = "\n\n---\n\n".join(context_blocks) if context_blocks else "관련 문서를 찾지 못했습니다."

    # 3. LLM Answer Generation
    answer = generate_answer(query=query, context=context_text, model="deepseek-chat")
    elapsed_ms = int((time.time() - start_time) * 1000)

    # 4. Metric 계산
    # Entity Hit: expected_entities (예: "person:위승민", "document:20710092")가 context/answer에 존재하는가
    matched_entities = 0
    for exp_ent in expected_entities:
        ent_val = exp_ent.split(":", 1)[-1]
        # 괄호 앞 한글 이름
        simple_val = ent_val.split("(")[0].strip() if "(" in ent_val else ent_val
        if ent_val in answer or simple_val in answer or (use_graph and any(e.get("name") == ent_val or e.get("id") == ent_val for e in graph_entities)):
            matched_entities += 1
    entity_hit_rate = matched_entities / len(expected_entities) if expected_entities else 1.0

    # Relation Hit
    matched_relations = 0
    for exp_rel in expected_relations:
        if use_graph and any(r.get("type") == exp_rel for r in graph_relations):
            matched_relations += 1
    relation_hit_rate = matched_relations / len(expected_relations) if expected_relations else 1.0

    return {
        "id": item.get("id"),
        "subtype": metadata.get("subtype"),
        "query": query,
        "answer": answer,
        "expected_output": expected_output,
        "entity_hit_rate": entity_hit_rate,
        "relation_hit_rate": relation_hit_rate,
        "latency_ms": elapsed_ms,
    }


def main():
    print("=" * 70)
    print("📊 [GraphRAG 벤치마크 평가] Hybrid Retrieval vs Hybrid + Knowledge Graph")
    print(f" • 총 평가 문항 수: {len(QA_DATASET_ITEMS)}개 (1~2 hop 관계형 질의)")
    print("=" * 70)

    # 1. Baseline: Hybrid RAG Only
    print("\n▶ 1. Baseline (Hybrid RAG 단독) 평가 진행 중...")
    baseline_results = []
    for i, item in enumerate(QA_DATASET_ITEMS, 1):
        print(f"  [{i}/{len(QA_DATASET_ITEMS)}] {item['id']}...", end="", flush=True)
        res = run_single_eval(item, use_graph=False)
        baseline_results.append(res)
        print(f" EntityHit: {res['entity_hit_rate']:.2f}, Latency: {res['latency_ms']}ms")

    # 2. Proposed: Hybrid + Knowledge Graph
    print("\n▶ 2. Proposed (Hybrid + Neo4j GraphRAG) 평가 진행 중...")
    proposed_results = []
    for i, item in enumerate(QA_DATASET_ITEMS, 1):
        print(f"  [{i}/{len(QA_DATASET_ITEMS)}] {item['id']}...", end="", flush=True)
        res = run_single_eval(item, use_graph=True)
        proposed_results.append(res)
        print(f" EntityHit: {res['entity_hit_rate']:.2f}, RelHit: {res['relation_hit_rate']:.2f}, Latency: {res['latency_ms']}ms")

    # 3. 종합 통계 계산
    base_avg_entity_hit = sum(r["entity_hit_rate"] for r in baseline_results) / len(baseline_results)
    prop_avg_entity_hit = sum(r["entity_hit_rate"] for r in proposed_results) / len(proposed_results)

    base_avg_rel_hit = sum(r["relation_hit_rate"] for r in baseline_results) / len(baseline_results)
    prop_avg_rel_hit = sum(r["relation_hit_rate"] for r in proposed_results) / len(proposed_results)

    base_avg_latency = sum(r["latency_ms"] for r in baseline_results) / len(baseline_results)
    prop_avg_latency = sum(r["latency_ms"] for r in proposed_results) / len(proposed_results)

    print("\n" + "=" * 70)
    print("🏆 [최종 벤치마크 평가 결과 비교]")
    print("=" * 70)
    print(f"| 평가 지표 (Metric) | Baseline (Hybrid Only) | Proposed (Hybrid + KG) | 개선폭 (Delta) |")
    print(f"|---|---|---|---|")
    print(f"| **Entity Hit Rate** | {base_avg_entity_hit * 100:.1f}% | **{prop_avg_entity_hit * 100:.1f}%** | **+{(prop_avg_entity_hit - base_avg_entity_hit) * 100:+.1f}%p** |")
    print(f"| **Relation Hit Rate** | {base_avg_rel_hit * 100:.1f}% | **{prop_avg_rel_hit * 100:.1f}%** | **+{(prop_avg_rel_hit - base_avg_rel_hit) * 100:+.1f}%p** |")
    print(f"| **Avg Latency (ms)** | {base_avg_latency:.0f} ms | {prop_avg_latency:.0f} ms | +{prop_avg_latency - base_avg_latency:.0f} ms |")
    print("=" * 70)

    # 샘플 비교 출력
    print("\n🔍 [대표 문항 답변 비교 (Sample)]")
    sample_base = baseline_results[0]
    sample_prop = proposed_results[0]
    print(f"❓ 질문: {sample_base['query']}")
    print(f"🎯 기대 정답: {sample_base['expected_output']}")
    print(f"❌ Baseline 답변: {sample_base['answer']}")
    print(f"✅ GraphRAG 답변: {sample_prop['answer']}")
    print("=" * 70)


if __name__ == "__main__":
    main()
