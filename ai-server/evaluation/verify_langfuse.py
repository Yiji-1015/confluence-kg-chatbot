"""
평가를 돌리기 전에 **연결만** 확인한다. 36문항을 태우지 않는다.

`run_qa`는 36문항 x 지표 5종이라 판정 모델 호출이 수백 건이다. 연결이나 키가 틀렸을 때
그걸로 알아내면 시간과 비용을 버린다. 이 스크립트는 같은 경로를 **최소 호출로** 밟는다.

확인 순서 (앞이 틀리면 뒤는 볼 필요가 없어서 순서대로 멈춘다):

  1. Langfuse 설정      키가 있는지, 형식이 맞는지
  2. Langfuse 인증      auth_check() — 지역(region)이 틀리면 여기서 걸린다
  3. Langfuse 쓰기      trace 1건을 실제로 남기고 UI 주소를 찍는다
  4. 데이터셋          업로드됐는지, 문항 수가 맞는지
  5. LiteLLM 게이트웨이  판정 모델 1회 + 임베딩 1회 (RAGAS가 쓸 바로 그 경로)
  6. RAGAS             지표 5종이 생성되는지 (채점은 하지 않는다)

실행:
    docker exec rag-ai-server python -m evaluation.verify_langfuse

5번은 실제 API를 부르므로 아주 작은 비용이 든다. 건너뛰려면:
    docker exec -e VERIFY_SKIP_LLM=1 rag-ai-server python -m evaluation.verify_langfuse
"""
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from app.config import settings

from evaluation.langfuse_client import brief, check_connection, get_client
from evaluation.dataset_items_36 import DATASET_NAME_DEFAULT, EXPECTED_ITEM_COUNT
from evaluation.run_qa import METRIC_NAMES, _ragas_metrics

DATASET_NAME = os.environ.get("EVAL_DATASET_NAME", DATASET_NAME_DEFAULT)
SKIP_LLM = os.environ.get("VERIFY_SKIP_LLM", "").strip().lower() in ("1", "true", "yes")


def _mask(value: str) -> str:
    """키를 그대로 찍지 않는다. 어느 키인지 알아볼 만큼만 남긴다."""
    value = (value or "").strip()
    if not value:
        return "(비어 있음)"
    return f"{value[:10]}…{value[-4:]}" if len(value) > 18 else f"{value[:6]}…"


def step(n: int, title: str):
    print(f"\n[{n}/6] {title}")


def main():
    print("=== 평가 연결 확인 ===")

    # 1~2. 설정과 인증. check_connection()이 실패하면 SystemExit으로 멈추고
    #      원인별 안내를 찍는다. run_qa와 같은 함수를 부른다 — 여기서 통과하고
    #      본 실행에서 막히면 확인의 의미가 없다.
    step(1, "Langfuse 설정")
    print(f"  host       : {settings.LANGFUSE_HOST}")
    print(f"  public key : {_mask(settings.LANGFUSE_PUBLIC_KEY)}")
    print(f"  secret key : {_mask(settings.LANGFUSE_SECRET_KEY)}")

    client = get_client()

    step(2, "Langfuse 인증")
    check_connection(client)
    print("  OK — 키가 이 host에서 통합니다")

    # 3. 인증이 됐다고 쓰기가 되는 것은 아니다. trace를 실제로 하나 남겨 본다.
    step(3, "Langfuse 쓰기 (trace 1건)")
    # 인증이 됐다고 쓰기가 되는 것은 아니다. Langfuse는 이벤트를 비동기로 보내므로
    # flush()까지 해야 실제로 서버에 닿았는지 알 수 있다.
    with client.start_as_current_observation(name="verify.connection") as span:
        span.update(metadata={"purpose": "연결 확인", "dataset": DATASET_NAME})
        trace_id = client.get_current_trace_id()
        trace_url = client.get_trace_url()
    client.flush()
    print(f"  OK — trace_id={trace_id}")
    print(f"  UI에서 확인: {trace_url or settings.LANGFUSE_HOST}")

    # 4. 데이터셋. 없으면 run_qa가 시작하자마자 멈추므로 여기서 먼저 알려준다.
    step(4, f"데이터셋 '{DATASET_NAME}'")
    try:
        dataset = client.get_dataset(DATASET_NAME)
    except Exception as exc:
        raise SystemExit(
            f"  실패: {brief(exc)}\n"
            "  먼저 업로드하세요:\n"
            "    docker exec rag-ai-server python -m evaluation.push_dataset_36"
        )
    count = len(dataset.items)
    print(f"  OK — {count}건")
    if count != EXPECTED_ITEM_COUNT:
        print(f"  경고: {EXPECTED_ITEM_COUNT}건이어야 합니다. 과거 아이템이 남아 있는지 "
              "Langfuse UI에서 확인하세요.")

    # 5. 판정 모델과 임베딩. RAGAS가 쓸 바로 그 경로를 최소 호출로 밟는다.
    #    여기가 막히면 채점만 전부 None이 되고 실행은 정상 종료돼서 알아채기 어렵다.
    step(5, "LiteLLM 게이트웨이")
    if SKIP_LLM:
        print("  건너뜀 (VERIFY_SKIP_LLM)")
    else:
        from app.llm.litellm_client import embed_texts, generate_chat_completion

        print(f"  base_url   : {settings.LITELLM_BASE_URL}")
        try:
            reply = generate_chat_completion(
                [{"role": "user", "content": "연결 확인. '확인'이라고만 답하세요."}],
                model=settings.JUDGE_MODEL,
            )
        except Exception as exc:
            raise SystemExit(
                f"  실패: 판정 모델 {settings.JUDGE_MODEL} 호출 불가 — {brief(exc)}\n"
                "  UPSTAGE_API_KEY가 .env에 있는지, LiteLLM 컨테이너가 떠 있는지 확인하세요.\n"
                "  (solar-judge에는 의도적으로 폴백을 두지 않았습니다 — litellm/config.yaml)"
            )
        print(f"  판정 모델  : OK — {settings.JUDGE_MODEL} -> {reply.strip()[:40]!r}")

        try:
            vector = embed_texts(["연결 확인"])[0]
        except Exception as exc:
            raise SystemExit(
                f"  실패: 임베딩 {settings.DEFAULT_EMBEDDING_MODEL} 호출 불가 — {brief(exc)}\n"
                "  OPENAI_API_KEY가 .env에 있는지 확인하세요.\n"
                "  (answer_relevancy와 answer_correctness가 임베딩을 씁니다)"
            )
        print(f"  임베딩     : OK — {settings.DEFAULT_EMBEDDING_MODEL} -> {len(vector)}차원")

    # 6. RAGAS. 생성만 확인한다. 실제 채점은 run_qa가 한다.
    step(6, "RAGAS 지표 5종")
    metrics = _ragas_metrics()
    if "error" in metrics:
        raise SystemExit(
            f"  실패: {metrics['error']}\n"
            "  설치: docker exec rag-ai-server pip install -r /app/requirements-eval.txt"
        )
    missing = [name for name in METRIC_NAMES if name not in metrics]
    if missing:
        raise SystemExit(f"  실패: {missing}를 만들지 못했습니다. ragas 버전을 확인하세요.")
    for name in METRIC_NAMES:
        print(f"  {name:<20} {type(metrics[name]).__name__}")

    print("\n" + "=" * 52)
    print("전부 통과. 이제 평가를 돌릴 수 있습니다.")
    print("  docker exec -e EVAL_RUN_NAME=ragas5-baseline \\")
    print("    rag-ai-server python -m evaluation.run_qa")
    print("=" * 52)


if __name__ == "__main__":
    main()
