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
import os
import re
import sys

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
JUDGE_MODEL = "gpt-4o"
_SCORE_RE = re.compile(r"SCORE:\s*([0-9]*\.?[0-9]+)", re.IGNORECASE)
_REASON_RE = re.compile(r"REASON:\s*(.+)", re.IGNORECASE | re.DOTALL)


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
    query_vector = embed_texts([query])[0]
    results = search_hybrid(query_text=query, query_vector=query_vector)

    retrieved_doc_ids = [r.get("doc_id") for r in results]
    context_text = build_context_text(results)

    selected_model, _ = select_optimal_model(query=query)
    answer = generate_answer(query=query, context=context_text, model=selected_model)

    return {
        "answer": answer,
        "retrieved_doc_ids": retrieved_doc_ids,
        "context": context_text,
    }


def retrieval_hit_evaluator(*, input, output, expected_output=None, metadata=None, **kwargs):
    expected_ids = (metadata or {}).get("expected_doc_ids")
    if not expected_ids:
        # out-of-domain 문항(기대 문서 없음)은 hit 채점 대상이 아님 (None 반환 시 점수 자체를 안 남김)
        return None

    retrieved = output.get("retrieved_doc_ids", []) if isinstance(output, dict) else []
    hit = any(doc_id in retrieved for doc_id in expected_ids)
    return Evaluation(
        name="retrieval_hit",
        value=1.0 if hit else 0.0,
        data_type="BOOLEAN",
        comment=f"expected={expected_ids} retrieved={retrieved}",
    )


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
    raw = generate_chat_completion([{"role": "user", "content": judge_prompt}], model=JUDGE_MODEL)
    score, reason = _parse_judge_response(raw)
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
    raw = generate_chat_completion([{"role": "user", "content": judge_prompt}], model=JUDGE_MODEL)
    score, reason = _parse_judge_response(raw)
    if score is None:
        return None
    return Evaluation(name="answer_correctness", value=score, comment=reason)


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
        name="confluence-rag-qa",
        task=rag_task,
        evaluators=[retrieval_hit_evaluator, faithfulness_evaluator, correctness_evaluator],
    )

    print(result.format())
    _print_diagnosis(result.item_results)

    if result.dataset_run_url:
        print(f"\nLangfuse에서 보기: {result.dataset_run_url}")

    client.flush()


if __name__ == "__main__":
    main()
