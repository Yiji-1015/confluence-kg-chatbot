"""
Langfuse Dataset(36문항)에 대해 실제 RAG 파이프라인(검색+생성)을 돌리고
**RAGAS 표준 지표 5종**으로 채점해서 Langfuse Experiment에 기록한다.

채점기는 전부 `ragas` 패키지가 제공하는 구현을 그대로 쓴다. 이 파일에는 점수를 만드는
로직이 없다. 프롬프트도, 파싱도, 가중치도 여기서 정의하지 않는다. 하는 일은
"파이프라인 출력 -> RAGAS 입력 필드"로 이름을 맞춰 넘기는 것뿐이다.

  ragas.metrics.collections.Faithfulness        -> faithfulness
  ragas.metrics.collections.AnswerRelevancy     -> answer_relevancy
  ragas.metrics.collections.ContextPrecision    -> context_precision
  ragas.metrics.collections.ContextRecall       -> context_recall
  ragas.metrics.collections.AnswerCorrectness   -> answer_correctness

Langfuse에 남는 점수 이름도 RAGAS 인스턴스의 `.name`을 그대로 쓴다. 우리가 지어낸
이름을 붙이면 같은 이름의 다른 계산을 보고 표준 지표라고 읽게 된다.

지표가 보는 곳:
  faithfulness       답변이 "검색된 컨텍스트"에 근거하는가        (환각 탐지)
  answer_relevancy   답변이 "질문"에 대답하고 있는가              (동문서답 탐지)
  context_precision  검색된 문서 중 정답에 쓸모 있는 것이 상위인가 (검색 정밀도)
  context_recall     정답을 구성하는 내용이 컨텍스트에 다 있는가   (검색 재현율)
  answer_correctness 답변이 정답(reference)과 사실로 일치하는가    (최종 품질)

실행:
    docker exec rag-ai-server python -m evaluation.run_qa
    docker exec -e EVAL_RUN_NAME=... rag-ai-server python -m evaluation.run_qa

ragas는 서빙에 필요 없어 별도 설치다:
    docker exec rag-ai-server pip install -r /app/requirements-eval.txt
"""
import asyncio
import collections
import concurrent.futures
import contextlib
import contextvars
import os
import sys
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from app.config import settings

# langfuse 환경변수 설정과 연결 확인은 이 모듈이 맡는다 (import 시점에 환경변수가 채워진다).
from evaluation.langfuse_client import connect, get_client, load_dataset

from langfuse.experiment import Evaluation

from app.llm.litellm_client import embed_texts, generate_answer
from app.llm.model_router import select_optimal_model
from app.llm.prompts import build_context_text
from app.observability import stage
from app.retrieval.es_client import search_hybrid
from evaluation.dataset_items_36 import DATASET_NAME_DEFAULT

DATASET_NAME = os.environ.get("EVAL_DATASET_NAME", DATASET_NAME_DEFAULT)

# RAGAS 판정 호출의 출력 한도. 기본값 1024는 긴 답변에서 구조화 출력이 잘린다.
# faithfulness는 답변을 문장 단위로 쪼개 문장마다 판정 JSON을 만들기 때문에 답변이
# 길면 1024를 넘겨 IncompleteOutputException으로 죽고, 그 문항이 평균에서 빠진다.
# 실측(2026-09-14): 문장 40개(2,510자) 답변에서 1024는 실패, 4096은 성공.
# 지표 계산이 아니라 판정 LLM의 호출 한도라서 여기서 정한다.
RAGAS_JUDGE_MAX_TOKENS = 4096

# 동시 실행 수. 판정 모델을 짧은 시간에 몰아치면 429가 나고, 재시도에 실패한 문항이
# 점수 없이 빠져 평균이 왜곡된다 (2026-09-02 실측: 45문항 중 5문항 누락).
MAX_CONCURRENCY = int(os.environ.get("EVAL_MAX_CONCURRENCY", "4"))

# RAGAS가 판정 모델을 부르는 호출까지 trace에 남길지. `langfuse.openai`를 import하면
# OpenAI SDK가 전역으로 계측되어 판정 호출 하나하나가 generation으로 붙는다
# (프롬프트·응답·토큰·비용까지 보인다).
#
# **기본값은 끔.** 이 계측은 `instructor`(RAGAS가 구조화 출력에 쓴다)가 같은 메서드를
# 패치하는 자리와 겹친다. 두 패치가 함께 도는 것을 실제 엔드포인트로 확인하기 전까지
# 기본으로 켜지 않는다. 채점이 깨지는 쪽이 trace가 덜 자세한 쪽보다 나쁘다.
# 끈 상태에서도 판정 호출은 LiteLLM의 success_callback으로 Langfuse에 남는다.
# 다만 실험 trace 아래가 아니라 별도 trace로 뜬다.
#
# 켜려면: docker exec -e EVAL_TRACE_JUDGE=1 rag-ai-server python -m evaluation.run_qa
TRACE_JUDGE_CALLS = os.environ.get("EVAL_TRACE_JUDGE", "").strip().lower() in ("1", "true", "yes")

# 이 실행에서 기록해야 하는 지표. 끝에 "지표별로 몇 건이 채점됐는지"를 대조하는 데 쓴다.
# 순서와 이름 모두 RAGAS 인스턴스의 `.name`과 같다.
METRIC_NAMES = [
    "faithfulness",
    "answer_relevancy",
    "context_precision",
    "context_recall",
    "answer_correctness",
]


def _run_name() -> str:
    """
    Langfuse의 실행(run) 이름. 이름이 고정이면 목록에서 어떤 설정의 실행인지 구분할 수 없어
    기억이나 코드 주석에 의존하게 된다. 설정값을 이름에 담아 목록만 봐도 읽히게 한다.
    끝의 시각은 같은 설정을 여러 번 돌릴 때(재현성 확인) 이름이 겹치지 않게 하기 위함이다.

    EVAL_RUN_NAME을 주면 그 이름을 그대로 쓴다 (발표용으로 이름을 고정해야 할 때).
    """
    override = os.environ.get("EVAL_RUN_NAME", "").strip()
    if override:
        return override
    return (
        f"ragas5-rrf_k{settings.RRF_K}"
        f"-top{settings.RETRIEVAL_TOP_K}"
        f"-cand{settings.RETRIEVAL_CANDIDATE_SIZE}"
        f"-chars{settings.DOC_CONTEXT_MAX_CHARS}"
        f"-recency{settings.RECENCY_BOOST_MAX:g}"
        f"-temp{settings.LLM_TEMPERATURE:g}"
        f"-judge_{settings.JUDGE_MODEL}"
        f"-{datetime.now():%m%d-%H%M}"
    )


# 채점 실패 집계. 실패하면 그 문항은 점수를 안 남기고 평균에서 조용히 빠지므로,
# 몇 건이 빠졌는지 끝에 반드시 찍어야 결과를 믿을 수 있는지 판단할 수 있다.
_FAILURES = collections.Counter()


def rag_task(*, item, **kwargs):
    """
    실서비스 경로(api/v1/chat.py)와 같은 검색·컨텍스트 조립·모델 라우팅을 그대로 태운다.
    여기가 실서비스와 어긋나면, 평가 점수는 실제로 돌아가지 않는 파이프라인을 측정하게 된다.

    각 단계를 실서비스와 같은 `stage()`로 감싼다. 그래야 Langfuse trace에서
    rag.embedding -> rag.search -> rag.context_build -> rag.generation 이 한 줄로 펼쳐지고,
    "검색 -> 컨텍스트 -> 생성"이 실제로 이어졌는지를 trace 하나로 확인할 수 있다.
    """
    query = item.input
    try:
        with stage("embedding", chars=len(query)):
            query_vector = embed_texts([query])[0]

        with stage("search", query=query):
            results = search_hybrid(query_text=query, query_vector=query_vector)

        selected_model, routing_reason = select_optimal_model(query=query)

        with stage("context_build", documents=len(results)):
            context_text = build_context_text(results)

        with stage("generation", model=selected_model, context_chars=len(context_text)):
            answer = generate_answer(query=query, context=context_text, model=selected_model)
    except Exception as exc:
        # 여기서 죽으면 채점기는 아예 호출되지 않아 채점기 집계에 안 잡힌다.
        # 별도로 세지 않으면 "전부 실패했는데 누락 없음"이라고 보고하게 된다.
        _FAILURES[f"rag_task(검색/생성): {type(exc).__name__}"] += 1
        raise

    return {
        "answer": answer,
        "retrieved_doc_ids": [r.get("doc_id") for r in results],
        "context": context_text,
        # RAGAS는 합쳐진 문자열이 아니라 문서 단위 리스트를 받는다.
        # 한 덩어리로 넘기면 context_precision/recall이 "문서 1건"만 보고 채점한다.
        "retrieved_contexts": [r.get("text", "") for r in results],
        "selected_model": selected_model,
        "routing_reason": routing_reason,
    }


_RAGAS = {}


def _ragas_metrics():
    """
    RAGAS 지표 5종을 지연 생성한다. 미설치/초기화 실패면 `error`만 담아 돌려준다.
    (ragas는 langchain/langgraph/datasets를 통째로 끌고 오므로 서빙 이미지에 넣지 않고
     requirements-eval.txt로 분리했다.)

    판정 LLM과 임베딩 모두 LiteLLM 게이트웨이를 통해 부른다. 게이트웨이를 거쳐야
    실서비스와 같은 재시도·폴백 정책과 Langfuse 콜백이 그대로 적용된다.

    - llm        : JUDGE_MODEL (생성 모델과 다른 계열 — self-preference bias 제거)
    - embeddings : DEFAULT_EMBEDDING_MODEL (answer_relevancy, answer_correctness가 쓴다)

    지표 생성자에는 llm/embeddings 말고 아무 값도 넘기지 않는다. strictness,
    weights(0.75/0.25), beta 같은 값을 우리가 건드리면 그 순간 "RAGAS 점수"가 아니라
    "우리가 조정한 점수"가 된다. 전부 라이브러리 기본값을 쓴다.
    """
    if "loaded" in _RAGAS:
        return _RAGAS
    _RAGAS["loaded"] = True
    try:
        if TRACE_JUDGE_CALLS:
            # import 자체가 OpenAI SDK를 전역 계측한다. 클래스는 openai.AsyncOpenAI 그대로다.
            from langfuse.openai import AsyncOpenAI
        else:
            from openai import AsyncOpenAI
        from ragas.embeddings import OpenAIEmbeddings
        from ragas.llms import llm_factory
        from ragas.metrics.collections import (
            AnswerCorrectness,
            AnswerRelevancy,
            ContextPrecision,
            ContextRecall,
            Faithfulness,
        )

        client = AsyncOpenAI(base_url=f"{settings.LITELLM_BASE_URL}/v1", api_key="litellm-local")
        judge = llm_factory(model=settings.JUDGE_MODEL, provider="openai", client=client,
                            max_tokens=RAGAS_JUDGE_MAX_TOKENS)
        embeddings = OpenAIEmbeddings(client=client, model=settings.DEFAULT_EMBEDDING_MODEL)

        for metric in (
            Faithfulness(llm=judge),
            AnswerRelevancy(llm=judge, embeddings=embeddings),
            ContextPrecision(llm=judge),
            ContextRecall(llm=judge),
            AnswerCorrectness(llm=judge, embeddings=embeddings),
        ):
            # 점수 이름은 RAGAS가 정한 것을 그대로 쓴다.
            _RAGAS[metric.name] = metric

        print(f"[RAGAS] 지표 {len(METRIC_NAMES)}종 활성화 "
              f"(판정 {settings.JUDGE_MODEL} / 임베딩 {settings.DEFAULT_EMBEDDING_MODEL})")
        if TRACE_JUDGE_CALLS:
            print("[RAGAS] 판정 호출 계측 켜짐 (EVAL_TRACE_JUDGE) — "
                  "판정 프롬프트·응답이 trace에 남는다")
    except Exception as exc:
        _RAGAS["error"] = f"{type(exc).__name__}: {exc}"
        print(f"[RAGAS] 초기화 실패 - {_RAGAS['error']}")
    return _RAGAS


# span 생성이 한 번 실패하면 그 뒤로는 시도하지 않는다.
# Langfuse가 설정되지 않은 채로 돌리면(예: 비교 스크립트) 호출마다 SDK가
# "Authentication error ..."를 찍는다. 36문항 x 5방식 x 2지표면 수백 줄이 쏟아져
# 정작 봐야 할 표와 경고가 스크롤 밖으로 밀린다. 한 번만 알리고 끈다.
_SPANS = {"enabled": True}


def disable_eval_spans() -> None:
    """채점 span을 끈다. Langfuse Experiment 밖에서 채점기를 재사용할 때 쓴다."""
    _SPANS["enabled"] = False


@contextlib.contextmanager
def _eval_span(name: str, fields):
    """
    채점 한 건을 Langfuse span으로 감싼다. Langfuse가 없거나 span 생성이 실패해도
    **채점은 그대로 진행한다.** 관측이 평가를 막으면 안 된다.

    입력 전문은 남기지 않는다. 컨텍스트가 문서 여러 건이라 trace가 본문으로 뒤덮이고,
    같은 내용이 이미 `rag.context_build`에 있다. 길이와 건수만 남겨 어느 문항이
    무거웠는지 가늠할 수 있게 한다.
    """
    if not _SPANS["enabled"]:
        yield None
        return

    contexts = fields.get("retrieved_contexts") or []
    metadata = {
        "metric": name,
        "retrieved_contexts": len(contexts),
        "context_chars": sum(len(c) for c in contexts),
        "response_chars": len(fields.get("response") or ""),
        "reference_chars": len(fields.get("reference") or ""),
    }
    span_cm = None
    span = None
    try:
        # as_type="evaluator" — Langfuse가 채점 구간으로 알아본다. 기본값 "span"으로 두면
        # rag.search 같은 파이프라인 단계와 같은 모양이라 trace에서 구분되지 않는다.
        span_cm = get_client().start_as_current_observation(
            name=f"eval.{name}", as_type="evaluator")
        span = span_cm.__enter__()
    except Exception as exc:
        span_cm = None
        _SPANS["enabled"] = False
        print(f"[eval span] 생성 실패로 이후 채점은 span 없이 진행합니다: "
              f"{type(exc).__name__}: {exc}")

    try:
        yield span
    finally:
        if span_cm is not None:
            try:
                span.update(metadata=metadata)
                span_cm.__exit__(None, None, None)
            except Exception:
                pass


def _ascore(name: str, **fields):
    """
    RAGAS 지표 하나를 채점해 0~1 값을 돌려준다. 실패 시 None (해당 문항만 건너뜀).
    `fields`는 각 지표의 `ascore()` 시그니처에 그대로 실린다.

    채점 구간을 `eval.<지표>` span으로 감싸 Langfuse trace에 남긴다. 그래야 실험 item
    하나를 열었을 때 검색·생성 아래에 채점 5건이 이어 붙고, **어느 지표가 몇 초를 썼는지,
    어디서 실패했는지**가 한 trace에서 읽힌다. 점수만 남기면 값은 보이지만 그 값이
    어떻게 나왔는지는 안 보인다.
    """
    metric = _ragas_metrics().get(name)
    if metric is None:
        # 조용히 건너뛰지 않는다. 몇 건이 왜 빠졌는지 끝에 사유별로 찍힌다.
        _FAILURES[f"{name}: RAGAS 미설치/초기화 실패로 채점 불가"] += 1
        return None

    def call():
        with _eval_span(name, fields):
            return asyncio.run(metric.ascore(**fields))

    try:
        # Langfuse는 평가기를 이벤트 루프 안에서 실행하므로 asyncio.run()이 거부된다.
        # 루프가 이미 돌고 있으면 별도 스레드에서 새 루프를 열어 실행한다.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            result = call()
        else:
            # `pool.submit(call)`로 그냥 넘기면 새 스레드는 현재 컨텍스트를 물려받지
            # 못한다. Langfuse(OpenTelemetry)의 "지금 열려 있는 span"은 contextvar에
            # 들어 있어서, 전파하지 않으면 eval span이 실험 item 아래가 아니라
            # **부모 없는 별도 trace로 떨어진다.** 점수는 정상 기록되므로 UI를 열어
            # 보기 전까지 알아채지 못한다.
            # copy_context()로 떠서 스레드 안에서 그 컨텍스트로 실행한다
            # (실측 확인: 전파 없음 -> 부모 없음 / 전파 -> experiment-item 아래).
            ctx = contextvars.copy_context()
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                result = pool.submit(ctx.run, call).result()
        return float(result.value)
    except Exception as exc:
        _FAILURES[f"{name}: {type(exc).__name__}"] += 1
        return None


def _answer(output) -> str:
    return output.get("answer", "") if isinstance(output, dict) else str(output)


def _contexts(output, name: str):
    """문서 단위 컨텍스트. 0건이면 컨텍스트 기반 지표는 채점할 수 없다."""
    contexts = output.get("retrieved_contexts") or [] if isinstance(output, dict) else []
    if not contexts:
        _FAILURES[f"{name}: 검색 결과 0건이라 채점 불가"] += 1
        return None
    return contexts


def _reference(expected_output, name: str):
    """정답 라벨. 없으면 reference를 요구하는 지표는 채점할 수 없다."""
    reference = (expected_output or "").strip() if isinstance(expected_output, str) else ""
    if not reference:
        _FAILURES[f"{name}: expected_output(reference) 없음"] += 1
        return None
    return reference


def faithfulness_evaluator(*, input, output, expected_output=None, metadata=None, **kwargs):
    """답변의 각 주장이 검색된 컨텍스트로 뒷받침되는 비율. 환각을 잡는다."""
    contexts = _contexts(output, "faithfulness")
    if contexts is None:
        return None
    value = _ascore("faithfulness",
                    user_input=input,
                    response=_answer(output),
                    retrieved_contexts=contexts)
    if value is None:
        return None
    return Evaluation(name="faithfulness", value=value)


def answer_relevancy_evaluator(*, input, output, expected_output=None, metadata=None, **kwargs):
    """
    답변에서 질문을 역생성해 원 질문과의 임베딩 유사도를 잰다.
    faithfulness가 높아도 동문서답일 수 있다. 그 경우를 여기서만 잡는다.
    """
    value = _ascore("answer_relevancy", user_input=input, response=_answer(output))
    if value is None:
        return None
    return Evaluation(name="answer_relevancy", value=value)


def context_precision_evaluator(*, input, output, expected_output=None, metadata=None, **kwargs):
    """
    검색된 문서 중 정답(reference)에 쓸모 있는 것이 상위에 왔는지를 순위 가중으로 잰다.
    컨텍스트는 점수 순으로 이어 붙으므로 앞 순위일수록 답변에 강하게 작용한다.
    """
    contexts = _contexts(output, "context_precision")
    reference = _reference(expected_output, "context_precision")
    if contexts is None or reference is None:
        return None
    value = _ascore("context_precision",
                    user_input=input,
                    reference=reference,
                    retrieved_contexts=contexts)
    if value is None:
        return None
    return Evaluation(name="context_precision", value=value)


def context_recall_evaluator(*, input, output, expected_output=None, metadata=None, **kwargs):
    """
    정답(reference)을 문장 단위로 쪼개, 각 문장이 검색된 컨텍스트에 실제로 있는지 본다.
    "정답 문서를 찾았는가"가 아니라 "정답을 쓰기에 충분한 내용을 가져왔는가"를 잰다.
    """
    contexts = _contexts(output, "context_recall")
    reference = _reference(expected_output, "context_recall")
    if contexts is None or reference is None:
        return None
    value = _ascore("context_recall",
                    user_input=input,
                    retrieved_contexts=contexts,
                    reference=reference)
    if value is None:
        return None
    return Evaluation(name="context_recall", value=value)


def answer_correctness_evaluator(*, input, output, expected_output=None, metadata=None, **kwargs):
    """
    답변과 정답을 사실 단위로 대조한 F1(가중 0.75)과 임베딩 의미 유사도(0.25)의 합.
    가중치는 RAGAS 기본값이며 여기서 바꾸지 않는다.
    """
    reference = _reference(expected_output, "answer_correctness")
    if reference is None:
        return None
    value = _ascore("answer_correctness",
                    user_input=input,
                    response=_answer(output),
                    reference=reference)
    if value is None:
        return None
    return Evaluation(name="answer_correctness", value=value)


EVALUATORS = [
    faithfulness_evaluator,
    answer_relevancy_evaluator,
    context_precision_evaluator,
    context_recall_evaluator,
    answer_correctness_evaluator,
]


def _expected_scorable_counts(dataset_items):
    """
    지표별로 "채점됐어야 하는 문항 수"를 데이터셋에서 미리 센다.
    실제 채점 건수와 대조해야 빠진 건수를 알 수 있다.

    reference(=expected_output)를 요구하는 세 지표는 정답 라벨이 있는 문항만 대상이다.
    """
    total = len(dataset_items)
    with_reference = sum(1 for i in dataset_items if (i.expected_output or ""))
    return {
        "faithfulness": total,
        "answer_relevancy": total,
        "context_precision": with_reference,
        "context_recall": with_reference,
        "answer_correctness": with_reference,
    }


def _print_metric_table(item_results, expected_counts):
    """지표별 평균 / 채점 건수 / 누락 건수. 평균만 보면 몇 건 평균인지 알 수 없다."""
    scored = collections.defaultdict(list)
    for r in item_results:
        for e in r.evaluations:
            if e.value is not None:
                scored[e.name].append(float(e.value))

    print("\n=== RAGAS 지표별 채점 결과 ===")
    print(f"{'지표':<24}{'평균':>8}{'채점':>8}{'대상':>8}{'누락':>8}")
    for name in METRIC_NAMES:
        values = scored.get(name, [])
        expected = expected_counts.get(name, 0)
        missing = expected - len(values)
        avg = f"{sum(values) / len(values):.3f}" if values else "-"
        print(f"{name:<24}{avg:>8}{len(values):>8}{expected:>8}{missing:>8}")
        if missing:
            _FAILURES[f"{name}: 대상 {expected}건 중 {len(values)}건만 채점됨"] += 1
    return scored


def _print_warnings(scored: int = None, expected: int = None):
    """
    채점에서 빠진 문항이 있으면 크게 알린다. 평균만 보면 알 수 없기 때문이다.

    실패 집계뿐 아니라 "실제로 채점된 문항 수"도 대조한다. 집계는 우리가 아는
    실패만 세므로, 예상 못 한 경로로 빠진 문항은 개수 대조로만 잡힌다.
    """
    if scored is not None and expected is not None and scored != expected:
        _FAILURES[f"문항 수 불일치: {expected}건 중 {scored}건만 실행됨"] += 1

    if not _FAILURES:
        print("\n채점 누락 없음 - 모든 문항이 정상 채점됐습니다.")
        return

    total = sum(_FAILURES.values())
    print("\n" + "!" * 60)
    print(f"경고: 채점 실패/누락 {total}건. 해당 문항은 평균 계산에서 빠졌으므로")
    print("      이 실행의 점수를 다른 실행과 비교하면 안 됩니다.")
    for reason, count in _FAILURES.most_common():
        print(f"  - {reason}: {count}건")
    print("!" * 60)


def _print_diagnosis(item_results):
    """
    낮은 점수의 원인을 검색/생성으로 가른다. 판정은 전부 RAGAS 5종 값만 보고 한다.

      context_recall 낮음                       -> 검색이 정답 근거를 못 가져왔다
      recall 높음 + faithfulness 낮음           -> 근거는 있는데 LLM이 무시하고 지어냈다
      recall/faithfulness 높음 + relevancy 낮음 -> 근거대로 답했지만 질문에 안 맞는 말이다
      나머지                                     -> 근거도 맞고 충실한데 사실 대조에서 틀렸다
    """
    print("\n=== 실패 원인 진단 (answer_correctness < 0.7) ===")
    for r in item_results:
        scores = {e.name: e.value for e in r.evaluations}
        correctness = scores.get("answer_correctness")
        recall = scores.get("context_recall")
        faith = scores.get("faithfulness")
        relevancy = scores.get("answer_relevancy")

        if correctness is None or correctness >= 0.7:
            continue

        item_id = getattr(r.item, "id", None) or str(getattr(r.item, "input", ""))[:30]
        if recall is not None and recall < 0.5:
            verdict = "검색 실패 (정답 근거가 컨텍스트에 없음)"
        elif faith is not None and faith < 0.6:
            verdict = "생성 실패 (근거는 있는데 LLM이 근거 없이 답함)"
        elif relevancy is not None and relevancy < 0.6:
            verdict = "생성 실패 (충실하지만 질문에 대답하지 않음)"
        else:
            verdict = "생성 실패 (근거도 맞고 충실한데 사실이 틀림)"

        print(f"- [{item_id}] recall={recall} faithfulness={faith} "
              f"relevancy={relevancy} correctness={correctness} -> {verdict}")


def _preflight(dataset):
    """
    실행 전에 확인한다. 여기서 막지 않으면 지표 없이 빈 Experiment를 만들어 놓고도
    실행은 정상 종료되고, 나중에 Langfuse UI를 열어야 누락을 알게 된다.
    """
    print("=== 실행 전 확인 ===")
    print(f"Langfuse       : 연결됨 ({settings.LANGFUSE_HOST})")
    print(f"데이터셋       : {DATASET_NAME} ({len(dataset.items)}건)")
    print(f"실행(run) 이름 : {_run_name()}")
    print(f"검색 설정      : RRF_K={settings.RRF_K} top_k={settings.RETRIEVAL_TOP_K} "
          f"candidate={settings.RETRIEVAL_CANDIDATE_SIZE} recency={settings.RECENCY_BOOST_MAX:g}")
    print(f"판정 모델      : {settings.JUDGE_MODEL} (LiteLLM {settings.LITELLM_BASE_URL})")

    metrics = _ragas_metrics()
    if "error" in metrics:
        raise SystemExit(
            "\nRAGAS를 초기화하지 못해 실행을 중단합니다.\n"
            f"  원인: {metrics['error']}\n"
            "  설치: docker exec rag-ai-server pip install -r /app/requirements-eval.txt\n"
            "  (이 실행의 채점기는 전부 RAGAS입니다. 없으면 기록할 점수가 하나도 없습니다.)"
        )

    missing = [name for name in METRIC_NAMES if name not in metrics]
    if missing:
        raise SystemExit(
            f"\nRAGAS 지표 {missing}를 만들지 못해 실행을 중단합니다.\n"
            "  설치된 ragas 버전이 requirements-eval.txt와 다른지 확인하세요."
        )
    print(f"RAGAS          : 사용 가능 ({', '.join(METRIC_NAMES)})")

    expected = _expected_scorable_counts(dataset.items)
    print("\n지표별 채점 대상 문항 수:")
    for name in METRIC_NAMES:
        print(f"  - {name}: {expected[name]}건")
    print()
    return expected


def main():
    # 순서가 중요하다. 연결 -> 데이터셋 -> RAGAS 순으로 확인해야 실패 메시지가
    # 실제 원인을 가리킨다. 연결이 안 된 채로 데이터셋을 읽으면 "데이터셋 없음"처럼 보인다.
    client = connect()
    dataset = load_dataset(client, DATASET_NAME)
    expected_counts = _preflight(dataset)

    run_name = _run_name()
    result = dataset.run_experiment(
        name=run_name,
        # run_name을 따로 주지 않으면 langfuse 4.x가 이름 뒤에 ISO 타임스탬프를 붙인다.
        # 발표에서 지목할 이름이므로 정확히 이 이름으로 고정한다.
        run_name=run_name,
        description=(
            f"36문항 평가셋({DATASET_NAME}) / 운영 RRF 검색 파이프라인 "
            f"(RRF_K={settings.RRF_K}, top_k={settings.RETRIEVAL_TOP_K}, "
            f"BM25·kNN 후보 각 {settings.RETRIEVAL_CANDIDATE_SIZE}) / "
            f"RAGAS 표준 지표 5종, 판정 {settings.JUDGE_MODEL}"
        ),
        task=rag_task,
        evaluators=EVALUATORS,
        max_concurrency=MAX_CONCURRENCY,
        metadata={
            "rrf_k": settings.RRF_K,
            "top_k": settings.RETRIEVAL_TOP_K,
            "candidate_size": settings.RETRIEVAL_CANDIDATE_SIZE,
            "recency_boost_max": settings.RECENCY_BOOST_MAX,
            "temperature": settings.LLM_TEMPERATURE,
            "judge_model": settings.JUDGE_MODEL,
            "embedding_model": settings.DEFAULT_EMBEDDING_MODEL,
            "dataset_items": len(dataset.items),
            "metrics": METRIC_NAMES,
            "metrics_source": "ragas.metrics.collections",
        },
    )

    print(result.format())
    _print_metric_table(result.item_results, expected_counts)
    _print_warnings(scored=len(result.item_results), expected=len(dataset.items))
    _print_diagnosis(result.item_results)

    # 대표 trace 1건. 검색 -> 컨텍스트 -> 생성이 한 trace에 이어졌는지 UI에서 확인할 주소다.
    if result.item_results:
        first = result.item_results[0]
        print(f"\n대표 trace: item={getattr(first.item, 'id', '?')} trace_id={first.trace_id}")

    if result.dataset_run_url:
        print(f"\nLangfuse에서 보기: {result.dataset_run_url}")

    client.flush()


if __name__ == "__main__":
    main()
