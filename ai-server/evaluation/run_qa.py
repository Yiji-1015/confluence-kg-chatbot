"""
Langfuse Dataset(confluence-rag-qa-v2)에 대해 실제 RAG 파이프라인(검색+생성)을 돌리고
3가지 점수를 매겨서 Langfuse에 기록한다.

- retrieval_hit: ES 하이브리드 검색이 정답 문서를 top_k 안에 찾았는가 (검색 문제 진단)
- answer_faithfulness: 답변이 "검색된 컨텍스트"에 근거하는가 (LLM 판단 무시/환각 진단)
- answer_correctness: 답변이 기대 답변과 실제로 맞는가 (최종 품질)

세 점수를 조합하면 오답 원인을 구분할 수 있다:
  retrieval_hit=0                                -> 검색 실패
  retrieval_hit=1, faithfulness 낮음              -> 컨텍스트는 맞는데 LLM이 무시/환각
  retrieval_hit=1, faithfulness 높음, correctness 낮음 -> 컨텍스트도 맞고 충실한데 이해/추론이 틀림

실행:
    cd ai-server && .venv/bin/python -m evaluation.run_qa
"""
import asyncio
import collections
import concurrent.futures
import os
import re
import sys
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from app.config import settings

if settings.LANGFUSE_PUBLIC_KEY:
    os.environ["LANGFUSE_PUBLIC_KEY"] = settings.LANGFUSE_PUBLIC_KEY
if settings.LANGFUSE_SECRET_KEY:
    os.environ["LANGFUSE_SECRET_KEY"] = settings.LANGFUSE_SECRET_KEY
if settings.LANGFUSE_HOST:
    os.environ["LANGFUSE_HOST"] = settings.LANGFUSE_HOST

from langfuse import get_client
from langfuse.experiment import Evaluation

from app.retrieval.es_client import search_hybrid
from app.llm.litellm_client import embed_texts, generate_answer, generate_chat_completion
from app.llm.model_router import select_optimal_model
from app.llm.prompts import build_context_text

DATASET_NAME = "confluence-rag-qa-v2"
JUDGE_MODEL = settings.JUDGE_MODEL


def _run_name() -> str:
    """
    Langfuse의 실행(run) 이름. 이름이 고정이면 목록에서 어떤 설정의 실행인지 구분할 수 없어
    기억이나 코드 주석에 의존하게 된다. 설정값을 이름에 담아 목록만 봐도 읽히게 한다.
    끝의 시각은 같은 설정을 여러 번 돌릴 때(재현성 확인) 이름이 겹치지 않게 하기 위함이다.
    """
    return (
        f"qa-bm25_{settings.HYBRID_BM25_WEIGHT:g}"
        f"-knn_{settings.HYBRID_KNN_WEIGHT:g}"
        f"-top{settings.RETRIEVAL_TOP_K}"
        f"-cand{settings.RETRIEVAL_CANDIDATE_SIZE}"
        f"-chars{settings.DOC_CONTEXT_MAX_CHARS}"
        f"-recency{settings.RECENCY_BOOST_MAX:g}"
        f"-temp{settings.LLM_TEMPERATURE:g}"
        f"-judge_{settings.JUDGE_MODEL}"
        f"-{datetime.now():%m%d-%H%M}"
    )
_SCORE_RE = re.compile(r"SCORE:\s*([0-9]*\.?[0-9]+)", re.IGNORECASE)
_REASON_RE = re.compile(r"REASON:\s*(.+)", re.IGNORECASE | re.DOTALL)


# 채점 실패 집계. 실패하면 그 문항은 점수를 안 남기고 평균에서 조용히 빠지므로,
# 몇 건이 빠졌는지 끝에 반드시 찍어야 결과를 믿을 수 있는지 판단할 수 있다.
_FAILURES = collections.Counter()


def _judge(prompt: str, metric: str):
    """판정 모델을 호출하고 점수를 파싱한다. 실패 시 (None, 사유)를 돌려주고 집계한다."""
    try:
        raw = generate_chat_completion([{"role": "user", "content": prompt}], model=JUDGE_MODEL)
    except Exception as exc:
        _FAILURES[f"{metric}: 호출 실패({type(exc).__name__})"] += 1
        return None, None
    score, reason = _parse_judge_response(raw)
    if score is None:
        _FAILURES[f"{metric}: 형식 파싱 실패"] += 1
    return score, reason


def _parse_judge_response(raw: str):
    score_match = _SCORE_RE.search(raw)
    reason_match = _REASON_RE.search(raw)
    score = float(score_match.group(1)) if score_match else None
    if score is not None:
        score = max(0.0, min(1.0, score))
    reason = reason_match.group(1).strip() if reason_match else raw.strip()[:200]
    return score, reason


def rag_task(*, item, **kwargs):
    """
    실서비스 경로(api/v1/chat.py)와 같은 검색·컨텍스트 조립·모델 라우팅을 그대로 태운다.
    여기가 실서비스와 어긋나면, 평가 점수는 실제로 돌아가지 않는 파이프라인을 측정하게 된다.
    """
    query = item.input
    try:
        query_vector = embed_texts([query])[0]
        results = search_hybrid(query_text=query, query_vector=query_vector)
        selected_model, _ = select_optimal_model(query=query)
        context_text = build_context_text(results)
        answer = generate_answer(query=query, context=context_text, model=selected_model)
    except Exception as exc:
        # 여기서 죽으면 채점기는 아예 호출되지 않아 채점기 집계에 안 잡힌다.
        # 별도로 세지 않으면 "전부 실패했는데 누락 없음"이라고 보고하게 된다.
        _FAILURES[f"rag_task(검색/생성): {type(exc).__name__}"] += 1
        raise

    retrieved_doc_ids = [r.get("doc_id") for r in results]

    return {
        "answer": answer,
        "retrieved_doc_ids": retrieved_doc_ids,
        "context": context_text,
        # RAGAS는 합쳐진 문자열이 아니라 문서 단위 리스트를 받는다
        "retrieved_contexts": [r.get("text", "") for r in results],
    }


def _scorable_expected_ids(metadata):
    """
    검색 지표(hit/MRR)로 채점할 문항인지 판단하고, 맞다면 기대 문서 id를 돌려준다.

    - known_gap: 겨냥한 문서가 본문 없이 청크 0개라 ES에 존재하지 않는다. 검색이 못 찾는 게
      정상이므로 채점하면 구조적으로 0점이 되어 지표를 왜곡한다 (이 문항의 목적은
      correctness로 "링크 안내로 답하는가"를 보는 것).
    - out_of_domain: 기대 문서 자체가 없다.
    """
    meta = metadata or {}
    if meta.get("known_gap"):
        return None
    return meta.get("expected_doc_ids") or None


def _retrieved_ids(output):
    return output.get("retrieved_doc_ids", []) if isinstance(output, dict) else []


def retrieval_hit_evaluator(*, input, output, expected_output=None, metadata=None, **kwargs):
    meta = metadata or {}

    expected_ids = _scorable_expected_ids(meta)
    if not expected_ids:
        return None

    retrieved = _retrieved_ids(output)
    hit = any(doc_id in retrieved for doc_id in expected_ids)
    return Evaluation(
        name="retrieval_hit",
        value=1.0 if hit else 0.0,
        data_type="BOOLEAN",
        comment=f"expected={expected_ids} retrieved={retrieved}",
    )


def retrieval_mrr_evaluator(*, input, output, expected_output=None, metadata=None, **kwargs):
    """
    정답 문서가 "몇 번째로" 검색됐는지를 점수화한다 (1등 1.0 / 2등 0.5 / 3등 0.33 / 못 찾음 0).

    retrieval_hit은 상위 top_k 안에 있기만 하면 1점이라, 정답이 1등이든 5등이든 구분하지
    못한다. 실제로 결합 방식을 바꿔가며 재보면 hit은 전부 같은데 MRR만 갈렸다
    (2026-09-01 실측: hit은 세 방식 모두 0.974, MRR은 0.890~0.908).
    컨텍스트는 점수 순으로 이어 붙으므로 앞 순위일수록 답변에 강하게 작용한다.
    """
    expected_ids = _scorable_expected_ids(metadata)
    if not expected_ids:
        return None

    retrieved = _retrieved_ids(output)
    rank = next((i for i, doc_id in enumerate(retrieved, start=1) if doc_id in expected_ids), None)
    return Evaluation(
        name="retrieval_mrr",
        value=1.0 / rank if rank else 0.0,
        comment=f"rank={rank or 'miss'} expected={expected_ids} retrieved={retrieved}",
    )


_RAGAS = {}


def _ragas_metrics():
    """
    RAGAS 지표를 지연 생성한다. 미설치면 빈 dict를 돌려주고 나머지 지표로 계속 진행한다.
    (ragas는 langchain/langgraph/datasets를 통째로 끌고 오므로 서빙 이미지에 넣지 않고
     requirements-eval.txt로 분리했다.)

    판정 LLM은 직접 구현한 지표와 동일하게 LiteLLM 게이트웨이의 JUDGE_MODEL을 쓴다.
    같은 모델로 채점해야 두 지표를 공정하게 비교할 수 있다.
    """
    if "loaded" in _RAGAS:
        return _RAGAS
    _RAGAS["loaded"] = True
    try:
        from openai import AsyncOpenAI
        from ragas.llms import llm_factory
        from ragas.metrics.collections import ContextPrecisionWithoutReference, Faithfulness

        client = AsyncOpenAI(base_url=f"{settings.LITELLM_BASE_URL}/v1", api_key="litellm-local")
        judge = llm_factory(model=settings.JUDGE_MODEL, provider="openai", client=client)
        _RAGAS["faithfulness"] = Faithfulness(llm=judge)
        _RAGAS["context_precision"] = ContextPrecisionWithoutReference(llm=judge)
        print(f"[RAGAS] 지표 활성화 (판정 모델: {settings.JUDGE_MODEL})")
    except ImportError:
        print("[RAGAS] 미설치 - 건너뜁니다. 쓰려면: pip install -r requirements-eval.txt")
    return _RAGAS


def _ragas_score(key: str, output):
    """RAGAS 지표 하나를 채점해 0~1 값을 돌려준다. 실패 시 None (해당 문항만 건너뜀)."""
    metric = _ragas_metrics().get(key)
    if metric is None or not isinstance(output, dict):
        return None

    contexts = output.get("retrieved_contexts") or []
    if not contexts:
        return None

    def call():
        return asyncio.run(metric.ascore(
            user_input=output.get("question", ""),
            response=output.get("answer", ""),
            retrieved_contexts=contexts,
        ))

    try:
        # Langfuse는 평가기를 이벤트 루프 안에서 실행하므로 asyncio.run()이 거부된다.
        # 루프가 이미 돌고 있으면 별도 스레드에서 새 루프를 열어 실행한다.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            result = call()
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                result = pool.submit(call).result()
        return float(result.value)
    except Exception as exc:
        _FAILURES[f"ragas_{key}: {type(exc).__name__}"] += 1
        return None


def ragas_faithfulness_evaluator(*, input, output, expected_output=None, metadata=None, **kwargs):
    """
    RAGAS 표준 faithfulness. 직접 구현한 answer_faithfulness와 나란히 기록해서,
    자체 판정 기준이 표준 지표와 어긋나지 않는지 교차 검증한다.
    """
    if isinstance(output, dict):
        output.setdefault("question", input)
    value = _ragas_score("faithfulness", output)
    if value is None:
        return None
    return Evaluation(name="ragas_faithfulness", value=value)


def ragas_context_precision_evaluator(*, input, output, expected_output=None, metadata=None, **kwargs):
    """
    검색된 문서들이 실제로 답변에 쓸모 있었는지를 순위 가중으로 채점한다 (정답 라벨 불필요).

    retrieval_hit/MRR은 "정답으로 라벨링한 문서 1개"만 보기 때문에, 함께 딸려온
    나머지 문서들의 품질을 전혀 못 본다. 실제로 결합 방식을 바꾸면 상위 5개 구성이
    38건 중 33건에서 달라지는데도 hit은 동일했다 (2026-09-01 실측).
    이 지표가 그 사각지대를 메운다.
    """
    if isinstance(output, dict):
        output.setdefault("question", input)
    value = _ragas_score("context_precision", output)
    if value is None:
        return None
    return Evaluation(name="ragas_context_precision", value=value)


def faithfulness_evaluator(*, input, output, expected_output=None, metadata=None, **kwargs):
    answer = output.get("answer", "") if isinstance(output, dict) else str(output)
    context = output.get("context", "") if isinstance(output, dict) else ""

    judge_prompt = (
        "다음 [답변]이 [컨텍스트]에 명시된 내용에만 근거하고 있는지 평가하세요.\n"
        "컨텍스트에 없는 내용을 답변이 지어냈다면 낮은 점수를 주세요.\n"
        "컨텍스트가 비어있거나 '찾지 못했습니다'인데 답변도 모른다고 했다면 1.0을 주세요.\n\n"
        f"[컨텍스트]\n{context}\n\n[답변]\n{answer}\n\n"
        "0.0(전혀 근거 없음) ~ 1.0(완전히 근거함) 사이 점수를 아래 형식으로만 출력하세요:\n"
        "SCORE: <숫자>\nREASON: <한 줄 이유>"
    )
    score, reason = _judge(judge_prompt, "answer_faithfulness")
    if score is None:
        return None
    return Evaluation(name="answer_faithfulness", value=score, comment=reason)


def correctness_evaluator(*, input, output, expected_output=None, metadata=None, **kwargs):
    if not expected_output:
        return None

    answer = output.get("answer", "") if isinstance(output, dict) else str(output)
    judge_prompt = (
        "[기대 답변]과 [실제 답변]을 비교해서 실제 답변이 기대 답변의 핵심 정보를 정확히 담고 있는지 평가하세요.\n"
        "표현 방식이 달라도 핵심 사실이 맞으면 높은 점수를 주세요.\n\n"
        f"[질문]\n{input}\n\n[기대 답변]\n{expected_output}\n\n[실제 답변]\n{answer}\n\n"
        "0.0(완전히 틀림) ~ 1.0(정확히 일치) 사이 점수를 아래 형식으로만 출력하세요:\n"
        "SCORE: <숫자>\nREASON: <한 줄 이유>"
    )
    score, reason = _judge(judge_prompt, "answer_correctness")
    if score is None:
        return None
    return Evaluation(name="answer_correctness", value=score, comment=reason)


def _print_warnings(scored: int = None, expected: int = None):
    """
    채점에서 빠진 문항이 있으면 크게 알린다. 평균만 보면 알 수 없기 때문이다.

    실패 집계뿐 아니라 "실제로 채점된 문항 수"도 대조한다. 집계는 우리가 아는
    실패만 세므로, 예상 못 한 경로로 빠진 문항은 개수 대조로만 잡힌다.
    """
    if scored is not None and expected is not None and scored != expected:
        _FAILURES[f"문항 수 불일치: {expected}건 중 {scored}건만 채점됨"] += 1

    if not _FAILURES:
        print("\n채점 누락 없음 - 모든 문항이 정상 채점됐습니다.")
        return

    total = sum(_FAILURES.values())
    print("\n" + "!" * 60)
    print(f"경고: 채점 실패 {total}건. 해당 문항은 평균 계산에서 빠졌으므로")
    print("      이 실행의 점수를 다른 실행과 비교하면 안 됩니다.")
    for reason, count in _FAILURES.most_common():
        print(f"  - {reason}: {count}건")
    print("!" * 60)


def _print_diagnosis(item_results):
    print("\n=== 실패 원인 진단 ===")
    for r in item_results:
        scores = {e.name: e.value for e in r.evaluations}
        hit = scores.get("retrieval_hit")
        faith = scores.get("answer_faithfulness")
        correct = scores.get("answer_correctness")

        if correct is not None and correct >= 0.7:
            continue  # 정답 처리된 항목은 스킵

        item_id = getattr(r.item, "id", None) or str(getattr(r.item, "input", ""))[:30]
        if hit == 0.0:
            verdict = "검색 실패 (정답 문서를 못 찾음)"
        elif hit == 1.0 and faith is not None and faith < 0.6:
            verdict = "생성 실패 (문서는 찾았는데 LLM이 근거 없이 답함)"
        elif hit == 1.0 and faith is not None and faith >= 0.6:
            verdict = "생성 실패 (문서도 맞고 충실한데 이해/추론이 틀림)"
        else:
            verdict = "판단 보류 (out-of-domain/known-gap 항목이거나 점수 부족)"

        print(f"- [{item_id}] hit={hit} faithfulness={faith} correctness={correct} -> {verdict}")


def main():
    client = get_client()
    dataset = client.get_dataset(DATASET_NAME)

    result = dataset.run_experiment(
        name=_run_name(),
        task=rag_task,
        evaluators=[retrieval_hit_evaluator, retrieval_mrr_evaluator,
                    faithfulness_evaluator, correctness_evaluator,
                    ragas_faithfulness_evaluator, ragas_context_precision_evaluator],
    )

    print(result.format())
    _print_warnings(scored=len(result.item_results), expected=len(dataset.items))
    _print_diagnosis(result.item_results)

    if result.dataset_run_url:
        print(f"\nLangfuse에서 보기: {result.dataset_run_url}")

    client.flush()


if __name__ == "__main__":
    main()
