"""
검색 결합 방식 5종을 같은 36문항에서 비교한다.
**검색 지표(Hit@5 / MRR)와 RAGAS 컨텍스트 지표 2종을 한 표에** 놓는다.

    방식    Hit@5   MRR   context_precision   context_recall

**왜 이 두 RAGAS 지표인가.** 둘 다 답변(`response`)을 받지 않는다. 필요한 것은
질문·정답 라벨·검색된 컨텍스트뿐이다. 즉 **답변을 한 번도 생성하지 않고** 잰다.
생성기를 안 거치므로 "이게 검색 차이인지 프롬프트 차이인지"가 섞이지 않는다.
검색 변수만 분리해서 보려는 이 비교의 목적에 정확히 맞는다.

  context_precision  가져온 문서 중 정답에 쓸모 있는 것이 상위에 왔는가
  context_recall     정답을 쓰기에 충분한 내용을 가져왔는가

**왜 Hit@5로는 부족한가.** Hit@5는 정답 문서가 top5에 들었는지만 본다. 함께 딸려온
나머지 네 문서가 전부 무관해도 만점이다. 그런데 컨텍스트에는 다섯이 다 들어간다.
`context_precision`이 그 사각지대를 메운다.

생성까지 태우는 나머지 RAGAS 3종(`faithfulness`, `answer_relevancy`,
`answer_correctness`)은 운영 설정 한 가지에 대해 `run_qa.py`가 잰다. 역할이 다르다.
  이 스크립트  "왜 이 검색 방식인가"      — 방식 비교
  run_qa       "그래서 최종 품질이 어떤가" — 운영 파이프라인 기준선

**공정성.** 질문당 BM25 후보 50청크 + kNN 후보 50청크를 한 번만 조회하고 모든 방식이
그 동일한 스냅샷 위에서 재랭킹만 달리한다. 컨텍스트 조립도 운영 코드와 같은 함수다.
채점 경로(`_ascore`)도 `run_qa`와 같은 것을 쓴다 — 판정 모델·max_tokens까지 동일하다.

실행:
    docker exec rag-ai-server python -m evaluation.compare_methods

먼저 3문항만 돌려 확인 (판정 호출이 확 줄어든다):
    docker exec -e COMPARE_LIMIT=3 rag-ai-server python -m evaluation.compare_methods
"""
import collections
import concurrent.futures
import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from app.config import settings
from app.llm.litellm_client import embed_texts

from evaluation.dataset_items_36 import load_raw
from evaluation.retrieval_methods import (
    METHOD_LABELS,
    METHODS,
    clear_cache,
    ranked_doc_ids,
    search_with_method,
)
from evaluation.run_qa import _FAILURES, _ascore, _ragas_metrics, disable_eval_spans

# 채점할 RAGAS 지표. 둘 다 `response`를 받지 않는다(생성 불필요).
RAGAS_METRICS = ["context_precision", "context_recall"]

TOP_K = settings.RETRIEVAL_TOP_K
LIMIT = int(os.environ.get("COMPARE_LIMIT", "0"))
MAX_CONCURRENCY = int(os.environ.get("EVAL_MAX_CONCURRENCY", "4"))

OUT_NAME = os.environ.get("COMPARE_OUT", "compare_methods_ragas")

# 산출물이 나갈 자리. 컨테이너의 /app는 컨테이너 레이어라 쓰기는 되지만 **재생성하면
# 사라진다.** 판정 모델을 수백 번 부른 결과를 그렇게 잃으면 안 되므로, compose가
# 호스트로 rw 마운트해 둔 /app/eval-results를 우선 쓴다.
# (/app/evaluation은 :ro 마운트라 거기에는 못 쓴다.)
_MOUNTED_OUT = Path("/app/eval-results")


def resolve_out_dir() -> Path:
    override = os.environ.get("COMPARE_OUT_DIR", "").strip()
    if override:
        return Path(override)
    if _MOUNTED_OUT.is_dir():
        return _MOUNTED_OUT
    return Path.cwd()


def check_writable(out_dir: Path) -> None:
    """
    **비싼 작업을 시작하기 전에** 쓸 수 있는지 확인한다.

    끝에서야 알면 판정 모델 수백 번을 이미 쓴 뒤다. 그 실패가 제일 아프다.
    """
    probe = out_dir / ".write_probe"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except Exception as exc:
        raise SystemExit(
            "\n산출물을 쓸 수 없어 시작하지 않습니다 (채점을 돌리기 전에 멈춥니다).\n"
            f"  경로: {out_dir}\n"
            f"  원인: {type(exc).__name__}: {exc}\n\n"
            "  컨테이너 안에서 돌린다면 docker-compose.app.yml에 이 마운트가 있어야 합니다:\n"
            "      - ./eval-results:/app/eval-results\n"
            "  추가한 뒤 컨테이너를 다시 만드세요:\n"
            "      docker compose -f docker-compose.yml -f docker-compose.app.yml up -d ai-server\n\n"
            "  다른 곳에 쓰려면: -e COMPARE_OUT_DIR=/tmp"
        )


def _rank(doc_ids, gold):
    """정답 문서의 순위(1부터). 없으면 0."""
    return doc_ids.index(gold) + 1 if gold and gold in doc_ids else 0


def retrieve_all(rows):
    """
    검색만 먼저 전부 끝낸다. LLM 판정은 그 다음이다.

    순서를 나눈 이유: 검색을 채점과 섞어 병렬로 돌리면 후보 캐시에 여러 스레드가 동시에
    들어가고, "모든 방식이 같은 스냅샷을 봤다"가 설계가 아니라 우연이 된다.
    여기서 직렬로 다 채워 두면 그 보장이 코드로 남는다.
    """
    print(f"[1/2] 검색 — {len(rows)}문항 x {len(METHODS)}방식")
    vectors = embed_texts([row["question"] for row in rows])

    tasks = []
    for row, vector in zip(rows, vectors):
        gold = (row.get("page_id") or "").strip()
        reference = (row.get("ground_truth_snippet") or "").strip()
        for method in METHODS:
            picked = search_with_method(method, row["question"], vector, top_k=TOP_K)
            full_rank = _rank(ranked_doc_ids(method, row["question"], vector), gold)
            top_rank = _rank([d["doc_id"] for d in picked], gold)
            tasks.append({
                "id": row["id"],
                "category": row.get("category"),
                "answerability": row.get("answerability"),
                "question": row["question"],
                "page_id": gold,
                "reference": reference,
                "method": method,
                "rank_full": full_rank,
                "rank_top": top_rank,
                "hit": 1.0 if top_rank else 0.0,
                "top_doc_ids": [d["doc_id"] for d in picked],
                "contexts": [d.get("text", "") for d in picked],
            })
        print(f"  {row['id']:>3}번 완료", end="\r")
    print(f"  검색 완료 — 조합 {len(tasks)}건        ")
    return tasks


def score_all(tasks):
    """RAGAS 채점. 조합 단위로 병렬 실행한다."""
    scorable = [t for t in tasks if t["reference"] and t["contexts"]]
    skipped = len(tasks) - len(scorable)
    print(f"\n[2/2] RAGAS 채점 — {len(scorable)}조합 x {len(RAGAS_METRICS)}지표"
          f"{f' (건너뜀 {skipped}건)' if skipped else ''}")

    def score(task):
        task["context_precision"] = _ascore(
            "context_precision",
            user_input=task["question"],
            reference=task["reference"],
            retrieved_contexts=task["contexts"])
        task["context_recall"] = _ascore(
            "context_recall",
            user_input=task["question"],
            retrieved_contexts=task["contexts"],
            reference=task["reference"])
        return task

    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as pool:
        for _ in pool.map(score, scorable):
            done += 1
            print(f"  {done}/{len(scorable)}", end="\r")
    print(f"  채점 완료 — {done}조합        ")
    return tasks


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _fmt(value):
    return f"{value:.3f}" if value is not None else "-"


def summarize(tasks, rows):
    """
    표를 찍는다. **모든 컬럼의 분모를 맞춘다.**

    Hit@5/MRR은 정답 문서 id가 있어야 계산된다. `page_id`가 빈 문항(not_found)은
    구조적으로 0점이라 검색 지표에서 뺀다. 그런데 RAGAS 지표만 36문항으로 재면
    한 표 안에서 컬럼마다 분모가 달라진다. 비교표에서 그건 읽는 사람을 속인다.
    그래서 **주 표는 page_id가 있는 문항으로 통일**하고, 36문항 값은 참고로 따로 찍는다.
    """
    gold_ids = {row["id"] for row in rows if (row.get("page_id") or "").strip()}
    n_gold, n_all = len(gold_ids), len(rows)

    by_method = collections.defaultdict(list)
    for task in tasks:
        by_method[task["method"]].append(task)

    print(f"\n{'=' * 78}")
    print(f"검색 방식 비교 — {n_gold}문항 (정답 문서 id가 있는 문항, 전체 {n_all})")
    print(f"{'=' * 78}")
    print(f"{'방식':<22}{'Hit@5':>8}{'MRR(top5)':>11}{'MRR(전체)':>11}"
          f"{'ctx_prec':>10}{'ctx_recall':>12}")

    summary = []
    for method in METHODS:
        scored = [t for t in by_method[method] if t["id"] in gold_ids]
        hit = _mean([t["hit"] for t in scored])
        mrr_top = _mean([1.0 / t["rank_top"] if t["rank_top"] else 0.0 for t in scored])
        mrr_full = _mean([1.0 / t["rank_full"] if t["rank_full"] else 0.0 for t in scored])
        precision = _mean([t.get("context_precision") for t in scored])
        recall = _mean([t.get("context_recall") for t in scored])
        print(f"{METHOD_LABELS[method]:<22}{_fmt(hit):>8}{_fmt(mrr_top):>11}"
              f"{_fmt(mrr_full):>11}{_fmt(precision):>10}{_fmt(recall):>12}")
        summary.append({
            "method": method, "label": METHOD_LABELS[method], "items": len(scored),
            "hit_at_5": hit, "mrr_top5": mrr_top, "mrr_full": mrr_full,
            "context_precision": precision, "context_recall": recall,
            "scored_precision": sum(1 for t in scored if t.get("context_precision") is not None),
            "scored_recall": sum(1 for t in scored if t.get("context_recall") is not None),
        })

    print(f"\n채점 건수 (누락이 있으면 그 방식의 평균은 다른 방식과 비교할 수 없다)")
    for entry in summary:
        print(f"  {entry['label']:<22} ctx_prec {entry['scored_precision']}/{entry['items']}"
              f"   ctx_recall {entry['scored_recall']}/{entry['items']}")

    print(f"\n참고 — 전체 {n_all}문항 기준 RAGAS "
          f"(not_found 문항은 정답 라벨이 '문서가 존재하지 않음'이라 구조적으로 낮다)")
    for method in METHODS:
        tasks_all = by_method[method]
        print(f"  {METHOD_LABELS[method]:<22}"
              f"ctx_prec {_fmt(_mean([t.get('context_precision') for t in tasks_all]))}"
              f"   ctx_recall {_fmt(_mean([t.get('context_recall') for t in tasks_all]))}")

    return summary, gold_ids


def head_to_head(tasks, gold_ids, baseline="rrf"):
    """
    평균 차이만으로는 36문항에서 아무 말도 못 한다. 판정 LLM은 실행마다 문항 단위로
    ±0.10씩 흔들리고 전체 평균으로는 ±0.03 수준이다(EVALUATION.md 6.1).
    그 폭 안의 차이는 노이즈와 구분되지 않는다.

    그래서 **문항 단위 승/패를 센다.** "평균이 0.02 높다"는 못 믿어도
    "36문항 중 16건에서 이겼다"는 셀 수 있는 사실이다.
    """
    lookup = {(t["method"], t["id"]): t for t in tasks}
    ids = sorted(gold_ids)

    print(f"\n{'=' * 78}")
    print(f"문항 단위 승/패 — {METHOD_LABELS[baseline]} 기준 (동점 판정 폭 0.05)")
    print(f"{'=' * 78}")

    results = []
    for metric in RAGAS_METRICS:
        print(f"\n[{metric}]")
        print(f"{'상대':<22}{'상대 승':>8}{METHOD_LABELS[baseline][:6] + ' 승':>10}"
              f"{'동점':>7}{'비교불가':>9}")
        for method in METHODS:
            if method == baseline:
                continue
            win = lose = tie = skip = 0
            for item_id in ids:
                a = lookup.get((method, item_id), {}).get(metric)
                b = lookup.get((baseline, item_id), {}).get(metric)
                if a is None or b is None:
                    skip += 1
                elif abs(a - b) <= 0.05:
                    tie += 1
                elif a > b:
                    win += 1
                else:
                    lose += 1
            print(f"{METHOD_LABELS[method]:<22}{win:>8}{lose:>10}{tie:>7}{skip:>9}")
            results.append({"metric": metric, "method": method, "baseline": baseline,
                            "win": win, "lose": lose, "tie": tie, "skipped": skip})
    return results


def save(tasks, summary, h2h, out_dir: Path):
    per_question = [{k: v for k, v in t.items() if k != "contexts"} for t in tasks]
    for row in per_question:
        row["top_doc_ids"] = " / ".join(row["top_doc_ids"])

    csv_path = out_dir / f"{OUT_NAME}.csv"
    json_path = out_dir / f"{OUT_NAME}.json"

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(per_question[0].keys()))
        writer.writeheader()
        writer.writerows(per_question)

    with json_path.open("w", encoding="utf-8") as f:
        json.dump({
            "measured_at": datetime.now().isoformat(timespec="seconds"),
            "conditions": {
                "index": settings.ELASTICSEARCH_INDEX,
                "top_k": TOP_K,
                "candidate_size": settings.RETRIEVAL_CANDIDATE_SIZE,
                "rrf_k": settings.RRF_K,
                "recency_boost_max": settings.RECENCY_BOOST_MAX,
                "judge_model": settings.JUDGE_MODEL,
                "metrics_source": "ragas.metrics.collections",
                "metrics": RAGAS_METRICS,
                "note": "context_precision/recall은 답변을 생성하지 않는다",
            },
            "summary": summary,
            "head_to_head": h2h,
            "per_question": per_question,
        }, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"\n저장: {csv_path}")
    print(f"      {json_path}")
    if out_dir == _MOUNTED_OUT:
        print("      (호스트의 eval-results/ 에 그대로 남습니다)")
    else:
        print("      ※ 컨테이너 안이라면 재생성 시 사라집니다. docker cp로 꺼내두세요.")


def main():
    rows = load_raw()
    if LIMIT:
        rows = rows[:LIMIT]
        print(f"※ COMPARE_LIMIT={LIMIT} — 앞 {LIMIT}문항만 돌립니다 (확인용)\n")

    out_dir = resolve_out_dir()
    check_writable(out_dir)

    print("=== 측정 조건 ===")
    print(f"인덱스   : {settings.ELASTICSEARCH_INDEX}")
    print(f"산출물   : {out_dir}")
    print(f"검색     : top_k={TOP_K} 후보={settings.RETRIEVAL_CANDIDATE_SIZE} "
          f"RRF_K={settings.RRF_K} 최신성={settings.RECENCY_BOOST_MAX:g}(rrf_recency에만)")
    print(f"판정     : {settings.JUDGE_MODEL}")
    print(f"지표     : {', '.join(RAGAS_METRICS)} (ragas.metrics.collections, 생성 불필요)")
    print()

    # 이 스크립트는 Langfuse Experiment가 아니다. 부모 trace가 없으니 채점 span을 만들어도
    # 붙을 곳이 없고, Langfuse가 설정 안 된 환경에서는 호출마다 인증 에러가 찍혀 표를
    # 밀어낸다. 결과는 CSV/JSON과 콘솔 표로 남는다.
    disable_eval_spans()

    metrics = _ragas_metrics()
    if "error" in metrics:
        raise SystemExit(
            f"\nRAGAS 초기화 실패로 중단합니다.\n  원인: {metrics['error']}\n"
            "  설치: docker exec rag-ai-server pip install -r /app/requirements-eval.txt")

    tasks = retrieve_all(rows)
    score_all(tasks)
    summary, gold_ids = summarize(tasks, rows)
    h2h = head_to_head(tasks, gold_ids)
    save(tasks, summary, h2h, out_dir)

    if _FAILURES:
        print("\n" + "!" * 60)
        print(f"경고: 채점 실패 {sum(_FAILURES.values())}건. 해당 조합은 평균에서 빠졌다.")
        for reason, count in _FAILURES.most_common():
            print(f"  - {reason}: {count}건")
        print("!" * 60)
    else:
        print("\n채점 누락 없음.")

    clear_cache()


if __name__ == "__main__":
    main()
