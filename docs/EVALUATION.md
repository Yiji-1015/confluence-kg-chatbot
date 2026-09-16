# 평가 계층 기준

> 대상: `ai-server/evaluation/` — `run_qa.py`(실행·채점),
> `dataset_items_36.py`(평가셋 → Langfuse item 변환), `push_dataset_36.py`(업로드).
>
> 검색 방식 비교(BM25 / kNN / min-max / RRF)는 저장소 루트의 `retrieval_fusion_comparison.py`와
> `EVAL_RESULTS_20260916.md`가 담당한다. 이 문서는 **Langfuse Experiment 경로**를 다룬다.
>
> 검색 계층은 `RETRIEVAL.md`, 변경 이력은 `CHANGELOG.md`.

---

# 1. 실행 구조

## 1.1 전체 흐름

Langfuse의 Dataset 기능 위에서 돈다. 데이터셋 문항 하나마다 `rag_task`를 돌려 결과를 만들고,
그 결과를 채점기 6개에 각각 넘긴다.

```
Langfuse Dataset (confluence-rag-qa-36-indexed, 36문항)
        │
        │  문항마다
        ▼
    rag_task(item)  ── 검색 + 생성을 실제로 수행
        │
        │  {answer, retrieved_doc_ids, context, retrieved_contexts}
        ▼
    채점기 6개가 각각 이 dict를 받아 점수 하나씩 반환
        │
        ▼
    Langfuse에 run 단위로 기록 + 콘솔에 누락 경고·원인 진단 출력
```

`main()`:

```python
dataset = client.get_dataset(DATASET_NAME)
expected_counts = _preflight(dataset)          # RAGAS 없으면 여기서 중단

run_name = _run_name()
result = dataset.run_experiment(
    name=run_name,
    run_name=run_name,                          # 아래 1.3 참고
    description=...,
    task=rag_task,
    evaluators=[retrieval_hit_evaluator, retrieval_mrr_evaluator,
                faithfulness_evaluator, correctness_evaluator,
                ragas_faithfulness_evaluator, ragas_context_precision_evaluator],
    max_concurrency=MAX_CONCURRENCY,
    metadata={"rrf_k": ..., "top_k": ..., "judge_model": ...},
)
print(result.format())
_print_metric_table(result.item_results, expected_counts)
_print_warnings(scored=len(result.item_results), expected=len(dataset.items))
_print_diagnosis(result.item_results)
client.flush()
```

실행:

```bash
# 평가 의존성. 이미지에 없으므로 컨테이너를 새로 만들 때마다 필요하다.
docker exec rag-ai-server pip install -r /app/requirements-eval.txt
docker exec rag-ai-server python -m evaluation.push_dataset_36
docker exec -e EVAL_RUN_NAME=<이름> rag-ai-server python -m evaluation.run_qa
```

`max_concurrency`는 기본 4다. 판정 모델을 짧은 시간에 몰아치면 429가 나고, 재시도에 실패한
문항이 점수 없이 빠져 평균이 왜곡된다(5.4절).

## 1.2 `rag_task` — 평가 대상 파이프라인

```python
def rag_task(*, item, **kwargs):
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
        _FAILURES[f"rag_task(검색/생성): {type(exc).__name__}"] += 1
        raise
    ...
```

**실서비스(`api/v1/chat.py`)와 같은 함수를 호출한다.** 평가 전용 파이프라인을 따로 두지 않았다.
`search_hybrid`, `select_optimal_model`, `build_context_text`, `generate_answer` 네 개가 공통이다.

단계를 감싸는 `stage()`도 실서비스와 같은 것(`app.observability`)이다. 그래서 Langfuse
trace 하나를 열면 `rag.embedding → rag.search → rag.context_build → rag.generation`이
시간 순으로 펼쳐지고, **검색 결과가 컨텍스트를 거쳐 생성으로 들어갔는지를 눈으로 확인**할 수 있다.
계측을 평가용으로 따로 만들면 실서비스 trace와 모양이 달라져 비교가 안 된다.

예외를 잡아 집계한 뒤 **다시 raise한다.** 여기서 죽으면 채점기가 아예 호출되지 않아
채점기 쪽 실패 집계에 잡히지 않기 때문이다. 따로 세지 않으면 "전부 실패했는데 누락 없음"으로
보고하게 된다.

**반환값 4종** — 채점기마다 필요한 형태가 다르다.

| 키 | 형태 | 쓰는 채점기 |
|---|---|---|
| `answer` | 문자열 | `answer_faithfulness`, `answer_correctness`, `ragas_faithfulness` |
| `retrieved_doc_ids` | 문서 id 리스트 (순위 순) | `retrieval_hit`, `retrieval_mrr` |
| `context` | **합쳐진 문자열** | `answer_faithfulness` (자체 구현) |
| `retrieved_contexts` | **문서 단위 리스트** | RAGAS 지표 2종 |

`context`와 `retrieved_contexts`는 같은 내용의 다른 형태다.
RAGAS는 문서 단위 리스트를 요구하고, 자체 판정 프롬프트는 한 덩어리 문자열을 쓴다.

## 1.3 `_run_name()` — 실행 이름에 설정값을 담는다

```python
f"qa-rrf_k{RRF_K}"
f"-top{RETRIEVAL_TOP_K}-cand{RETRIEVAL_CANDIDATE_SIZE}"
f"-chars{DOC_CONTEXT_MAX_CHARS}-recency{RECENCY_BOOST_MAX:g}"
f"-temp{LLM_TEMPERATURE:g}-judge_{JUDGE_MODEL}"
f"-{datetime.now():%m%d-%H%M}"
```

예: `qa-rrf_k60-top5-cand50-chars3000-recency0.04-temp0-judge_solar-judge-0916-1657`

검색 결과나 점수를 바꾸는 설정값이 전부 들어간다. 끝의 시각은 같은 설정을 여러 번 돌릴 때
이름이 겹치지 않게 하기 위한 것이다.

`EVAL_RUN_NAME`을 주면 그 이름을 그대로 쓴다. 발표에서 특정 실행을 지목해야 할 때 쓴다.

**`run_experiment(name=...)`만 주면 안 된다.** langfuse 4.x는 `run_name`을 생략하면
`name` 뒤에 ISO 타임스탬프를 붙여 실제 run 이름을 만든다. 그러면 지정한 이름과 Langfuse UI에
보이는 이름이 달라진다. `name`과 `run_name`에 같은 값을 넘겨 고정한다.

---

# 2. 채점기 6종

| 이름 | 층위 | 값의 범위 | 정답 라벨 | 판정 방식 |
|---|---|---|---|---|
| `retrieval_hit` | 검색 | 0 또는 1 | 필요 | 규칙 비교 |
| `retrieval_mrr` | 검색 | 0 ~ 1 | 필요 | 규칙 비교 |
| `answer_faithfulness` | 생성 (자체) | 0 ~ 1 | 불필요 | LLM 판정 |
| `answer_correctness` | 생성 (자체) | 0 ~ 1 | **필요** | LLM 판정 |
| `ragas_faithfulness` | 생성 (표준) | 0 ~ 1 | 불필요 | LLM 판정 |
| `ragas_context_precision` | 생성 (표준) | 0 ~ 1 | 불필요 | LLM 판정 |

채점기는 점수 대신 `None`을 돌려줄 수 있다. `None`이면 그 문항은 그 지표의 평균에서 빠진다.
(채점 대상이 아닌 문항, 또는 판정 호출이 실패한 경우)

## 2.1 검색 지표

### 채점 대상 선별 — `_scorable_expected_ids()`

```python
meta = metadata or {}
if meta.get("known_gap"):
    return None
return meta.get("expected_doc_ids") or None
```

두 종류를 검색 지표 채점에서 제외한다.

| 유형 | 제외 이유 |
|---|---|
| `known_gap` | 겨냥한 문서가 본문 없이 청크 0개라 **Elasticsearch에 존재하지 않는다.** 검색이 못 찾는 것이 정상이므로 채점하면 구조적으로 0점이 되어 지표를 왜곡한다. 36문항 셋에는 해당 문항이 없다(플래그를 세우지 않는다) |
| `not_found` | `expected_doc_ids` 자체가 없다 (사내 문서에 답이 없는 질문). 36문항 중 3건 — #19, #21, #36 |

제외된 문항은 `answer_correctness`로 "모른다고 답하는가"를 본다.
36문항 중 **33문항**이 검색 지표 채점 대상이다.

**검색 채점 제외와 답변 채점 제외는 다르다.** 제외되는 것은 `retrieval_hit` / `retrieval_mrr`
두 지표뿐이고, 나머지 4개 지표는 36문항 전부 채점한다. `unsupported`(정답 문서는 있지만
문서에 답이 없는 문항, #20·#35)는 **검색 지표 채점 대상이다.** 문서를 찾는 것까지는 맞고,
"찾은 뒤에 답하지 말아야 한다"는 답변 축에서 `answer_correctness`가 본다.

### `retrieval_hit` (hit@5)

```python
retrieved = output.get("retrieved_doc_ids", [])
hit = any(doc_id in retrieved for doc_id in expected_ids)
value = 1.0 if hit else 0.0
```

정답 문서가 `RETRIEVAL_TOP_K`(=5) 안에 **들어 있기만 하면** 1점이다. 순위는 보지 않는다.

### `retrieval_mrr`

```python
rank = next((i for i, doc_id in enumerate(retrieved, start=1)
             if doc_id in expected_ids), None)
value = 1.0 / rank if rank else 0.0
```

1위 `1.0` / 2위 `0.5` / 3위 `0.333` / 5위 `0.2` / 못 찾음 `0`.

**왜 둘 다 필요한가** — `hit@5`는 정답이 1위든 5위든 같은 1점이라 순위 변화를 감지하지 못한다.
두 지표는 **서로 반대 방향으로 갈릴 수 있다.** 같은 36문항에서 결합 방식을 바꿔 잰 결과
(`EVAL_RESULTS_20260916.md` 1절):

| 방식 | Hit@5 | MRR |
|---|---|---|
| BM25 단독 | 24/33 (0.727) | **0.615** |
| RRF (현재 방식) | **28/33 (0.848)** | 0.589 |

RRF가 Hit@5는 4문항 앞서는데 MRR은 뒤진다. **"RRF가 모든 지표에서 낫다"고 말할 수 없다.**
결합의 이득은 순위 정밀도가 아니라 회수(recall) 쪽에 있다. 한 지표만 봤다면 이 구분을
못 했을 것이다.

컨텍스트는 점수 순으로 이어 붙으므로 앞 순위일수록 답변에 강하게 작용한다.

## 2.2 자체 구현 생성 지표

두 지표 모두 판정 프롬프트를 만들어 `_judge()`에 넘기고, 응답에서 점수를 파싱한다.

### `answer_faithfulness`

`answer`가 `context`에 있는 내용에만 근거하는지 본다. 정답 라벨이 필요 없다.

판정 프롬프트 요지:
- 컨텍스트에 없는 내용을 지어냈으면 낮은 점수
- **컨텍스트가 비어 있고 답변도 "모른다"고 했으면 1.0** (`not_found` 문항에서 정직한 거절을 감점하지 않기 위한 규칙)
- 출력 형식 `SCORE: <숫자>` / `REASON: <한 줄>`

### `answer_correctness`

`answer`를 `expected_output`과 비교한다. **`expected_output`이 없으면 `None`을 돌려준다.**

판정 프롬프트 요지:
- 표현 방식이 달라도 핵심 사실이 맞으면 높은 점수
- 질문·기대 답변·실제 답변 셋을 함께 제시

## 2.3 RAGAS 표준 지표

### 지연 로딩 — `_ragas_metrics()`

```python
if "loaded" in _RAGAS:
    return _RAGAS
_RAGAS["loaded"] = True
try:
    from ragas.metrics.collections import ContextPrecisionWithoutReference, Faithfulness
    client = AsyncOpenAI(base_url=f"{LITELLM_BASE_URL}/v1", api_key="litellm-local")
    judge = llm_factory(model=JUDGE_MODEL, provider="openai", client=client)
    _RAGAS["faithfulness"] = Faithfulness(llm=judge)
    _RAGAS["context_precision"] = ContextPrecisionWithoutReference(llm=judge)
except Exception as exc:
    _RAGAS["error"] = f"{type(exc).__name__}: {exc}"
    print(f"[RAGAS] 초기화 실패 - {_RAGAS['error']}")
```

- `ragas`는 langchain/langgraph/datasets를 통째로 끌고 오므로 **서빙 이미지에 넣지 않고**
  `requirements-eval.txt`로 분리했다. 컨테이너를 새로 만들면 사라지므로 평가 전에 매번 설치한다.
- **`ImportError`가 아니라 `Exception`을 잡는다.** ragas가 설치돼 있어도 의존성 버전 충돌로
  import가 깨지는 경우가 있었고(`langchain-community` 0.4에서 제거된 모듈 참조),
  `ImportError`만 잡으면 그건 잡히지 않고 실행 전체가 죽는다.
- **미설치를 조용히 넘기지 않는다.** 예전에는 빈 dict를 돌려주고 나머지 4개 지표로 계속
  진행했는데, 그러면 RAGAS 두 지표가 전부 `None`이 된 채로 실행이 정상 종료되고 4개 지표
  평균만 그럴듯하게 찍힌다. 지금은 `_preflight()`가 실행 **전에** 이 함수를 먼저 불러
  상태를 확인하고, 실패하면 설치 명령을 안내하며 아예 시작하지 않는다.
- **판정 LLM을 자체 지표와 같은 것(`JUDGE_MODEL`)으로 맞췄다.** LiteLLM 게이트웨이를 OpenAI 호환
  엔드포인트로 물려서 RAGAS가 같은 모델을 쓰게 한다. 모델이 다르면 두 지표를 비교할 수 없다.

### 이벤트 루프 처리 — `_ragas_score()`

RAGAS 지표는 `async` API(`metric.ascore()`)만 제공하는데, Langfuse는 채점기를 이미 돌고 있는
이벤트 루프 안에서 호출한다. 그 안에서 `asyncio.run()`을 부르면 거부된다.

```python
try:
    asyncio.get_running_loop()
except RuntimeError:
    result = call()                      # 루프 없음 -> 그냥 실행
else:
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(call).result()   # 루프 있음 -> 별도 스레드에서 새 루프
```

`retrieved_contexts`가 비어 있으면 채점하지 않고 `None`을 돌려준다.

### `ragas_context_precision`

검색된 문서들이 **실제로 답변에 쓸모 있었는지**를 순위 가중으로 채점한다. 정답 라벨이 필요 없다.

`hit`과 `MRR`은 정답으로 라벨링한 문서 1개만 보므로, **함께 딸려온 나머지 문서들의 품질을
전혀 보지 못한다.** top5 중 4건이 무관한 문서여도 정답 1건이 1위면 `hit`도 `MRR`도 만점이다.

실측(2026-09-16, 5.3절): `boundary` 축은 `hit` 0.800인데 `ragas_context_precision`은 0.437이다.
정답 문서는 대체로 찾지만 **함께 딸려온 나머지 4건이 답변에 쓸모없었다**는 뜻이며,
검색 지표 두 개로는 드러나지 않는다.

---

# 3. 판정 모델과 실패 처리

## 3.1 `_judge()` — 판정 모델 호출

```python
def _judge(prompt: str, metric: str):
    try:
        raw = generate_chat_completion([{"role": "user", "content": prompt}], model=JUDGE_MODEL)
    except Exception as exc:
        _FAILURES[f"{metric}: 호출 실패({type(exc).__name__})"] += 1
        return None, None
    score, reason = _parse_judge_response(raw)
    if score is None:
        _FAILURES[f"{metric}: 형식 파싱 실패"] += 1
    return score, reason
```

호출 실패와 형식 파싱 실패를 **사유별로 구분해서** 센다.

### 응답 파싱 — `_parse_judge_response()`

```python
_SCORE_RE  = re.compile(r"SCORE:\s*([0-9]*\.?[0-9]+)", re.IGNORECASE)
_REASON_RE = re.compile(r"REASON:\s*(.+)", re.IGNORECASE | re.DOTALL)
...
score = max(0.0, min(1.0, score))          # 0~1로 강제
reason = reason_match.group(1).strip() if reason_match else raw.strip()[:200]
```

판정 모델이 범위를 벗어난 값을 내도 0~1로 자른다.
`REASON`을 못 찾으면 원문 앞 200자를 사유로 남긴다(왜 파싱이 깨졌는지 보려고).

## 3.2 판정 모델 선택

| 역할 | 모델 |
|---|---|
| 답변 생성 | `deepseek-chat` 또는 `gpt-4o` (`select_optimal_model`이 질문 길이로 라우팅) |
| **판정** | **`solar`** (`settings.JUDGE_MODEL`) |

판정 모델은 답변 생성에 쓰이는 두 모델 어느 쪽도 아니다. 같은 모델이 답하고 채점하면
자기 답변을 후하게 볼 수 있다.

## 3.3 채점 누락 탐지 — `_print_warnings()`

```python
if scored is not None and expected is not None and scored != expected:
    _FAILURES[f"문항 수 불일치: {expected}건 중 {scored}건만 채점됨"] += 1

if not _FAILURES:
    print("\n채점 누락 없음 - 모든 문항이 정상 채점됐습니다.")
    return

total = sum(_FAILURES.values())
print(f"경고: 채점 실패 {total}건. 해당 문항은 평균 계산에서 빠졌으므로")
print("      이 실행의 점수를 다른 실행과 비교하면 안 됩니다.")
for reason, count in _FAILURES.most_common():
    print(f"  - {reason}: {count}건")
```

**세 겹으로 막는다.**

| 방어 | 잡는 것 |
|---|---|
| `_FAILURES` 사유별 집계 | 코드가 아는 실패 (호출 실패, 파싱 실패, rag_task 예외, RAGAS 미설치) |
| `_print_metric_table()` 지표별 대조 | 지표 하나만 조용히 덜 채점된 경우 |
| `scored` vs `expected` 개수 대조 | 예상하지 못한 경로로 빠진 문항 |

`_print_metric_table()`은 지표마다 **평균 / 채점 / 대상 / 누락**을 한 줄로 찍는다.
"대상"은 데이터셋에서 미리 센 값이다(`_expected_scorable_counts()`). 평균만 보면 그게
36문항 평균인지 30문항 평균인지 알 수 없기 때문에, 분모를 항상 같이 출력한다.

```
지표                              평균      채점      대상      누락
retrieval_hit                0.818      33      33       0
```

누락이 없을 때도 **"채점 누락 없음"을 명시적으로 출력한다.** 침묵을 정상으로 오해하지 않게 하기 위해서다.

### 이 장치가 만들어진 계기 (2026-09-02)

recency를 켜고 재측정하던 중 판정 모델 호출에서 429(요청 한도 초과)가 5건 발생했다.
재시도가 없어 해당 문항들은 `correctness=None`으로 빠졌고, **전체 문항이 아니라 5문항이 빠진
평균이 출력됐다.** 실행은 정상 종료되고 점수도 그럴듯해서 로그를 보지 않으면 알 수 없었다.
(당시는 45문항 평가셋을 쓰던 때다. 그때의 점수는 조건이 달라 지금 수치와 비교할 수 없으므로
이 문서에 남기지 않는다. 남는 것은 **장치가 만들어진 경위**뿐이다.)

원인은 평가가 판정 모델을 짧은 시간에 몰아치기 때문이다. 자체 판정기 2종에 RAGAS의 내부
병렬 호출이 겹치면서 분당 한도를 넘겼다.

재발 방지는 **게이트웨이 한 곳에서** 처리했다. 호출부마다 재시도를 넣지 않고
`litellm/config.yaml`의 `router_settings`에 `num_retries: 3`, `retry_after: 5`를 뒀다.

이 문제로 recency 활성화 후의 첫 두 측정값은 **누락 때문인지 recency 때문인지 구분할 수 없어
폐기했다.** 세 번째 실행에서 429가 사라져 비교 가능한 값을 얻었다.

같은 이유로 `max_concurrency`를 4로 둔다(1.1절). 한 번에 몰아치지 않으면 애초에 429가 덜 난다.

## 3.4 실패 원인 진단 — `_print_diagnosis()`

```python
if correct is not None and correct >= 0.7:
    continue                                    # 정답 처리된 문항은 건너뜀

if hit == 0.0:
    verdict = "검색 실패 (정답 문서를 못 찾음)"
elif hit == 1.0 and faith < 0.6:
    verdict = "생성 실패 (문서는 찾았는데 LLM이 근거 없이 답함)"
elif hit == 1.0 and faith >= 0.6:
    verdict = "생성 실패 (문서도 맞고 충실한데 이해/추론이 틀림)"
else:
    verdict = "판단 보류 (not_found/unsupported 항목이거나 점수 부족)"
```

세 지표의 조합으로 고쳐야 할 단계를 가른다.

| `hit` | `faithfulness` | `correctness` | 진단 | 손댈 곳 |
|---|---|---|---|---|
| 0 | — | 낮음 | 검색 실패 | 검색 (가중치·후보 수·분석기) |
| 1 | < 0.6 | 낮음 | 컨텍스트 무시 | 프롬프트 |
| 1 | ≥ 0.6 | 낮음 | 이해·추론 실패 | 모델 |

출력 예:

```
=== 실패 원인 진단 ===
- [qa36-033] hit=0.0 faithfulness=1.0 correctness=0.0 -> 검색 실패 (정답 문서를 못 찾음)
- [qa36-034] hit=1.0 faithfulness=1.0 correctness=0.0 -> 생성 실패 (문서도 맞고 충실한데 이해/추론이 틀림)
```

### 이 분기의 한계 (2026-09-16 확인)

이번 실행에서 실패 8건 중 7건이 `faithfulness=1.0`이라 전부 "이해·추론 실패"로 분류됐다.
그런데 실제 원인을 열어보니 **모델의 추론 문제가 아니라 컨텍스트 조립 문제**였다.
첨부파일명이 프롬프트에 들어가지 않아 답할 근거 자체가 없었다(5.4절).

분기가 보는 것은 `hit` / `faithfulness` / `correctness` 셋뿐이다. "문서는 맞는데 그 문서의
**어떤 필드가** 컨텍스트에 안 실렸다"는 경우를 구분하지 못한다. 자동 분류를 그대로 믿지 말고
실패 문항의 실제 컨텍스트를 확인해야 한다.

---

# 4. 평가 데이터셋

## 4.1 `confluence-rag-qa-36-indexed` (36문항)

원본은 저장소 루트의 `confluence_retrieval_eval_36_indexed.json` **하나뿐이다.**
`dataset_items_36.py`가 이 파일을 읽어 키 이름만 Langfuse item 형태로 바꾸고,
`push_dataset_36.py`가 업로드한다. 파이썬 스냅샷으로 옮겨 적지 않는다. 두 벌이 되면
질문이나 정답 라벨이 한쪽에서만 바뀌어도 알아채지 못한다.

| 원본 필드 | Langfuse item | 쓰는 채점기 |
|---|---|---|
| `question` | `input` | 전부 |
| `ground_truth_snippet` | `expected_output` | `answer_correctness` |
| `page_id` | `metadata.expected_doc_ids` | `retrieval_hit`, `retrieval_mrr` |

item id는 `qa36-001` 형식으로 고정한다. 여러 번 업로드해도 아이템이 늘지 않고 덮어쓴다.

**구성** (선별 근거는 `confluence_retrieval_eval_36_indexed_notes.md`)

| 축 | 분포 |
|---|---|
| `category` | semantic 8 / keyword 7 / boundary 7 / conversational 6 / attachment 6 / table 2 |
| `answerability` | answerable 28 / not_found 3 / answerable_negative 3 / unsupported 2 |
| 정답 문서 | 18종 (전부 색인 확인됨) |

## 4.2 `expected_output`의 성질 — 인용할 때 주의

`ground_truth_snippet`은 36문항 전부 채워져 있어서 `answer_correctness`는 36건 모두 채점된다.
다만 **문항 유형에 따라 그 값이 재는 것이 다르다.**

| 유형 | n | `ground_truth_snippet`의 내용 | correctness가 재는 것 |
|---|--:|---|---|
| `answerable` | 28 | 문서 본문에서 뽑은 사실 | 사실이 맞는가 |
| `answerable_negative` | 3 | 금지·제한 사실 | 사실이 맞는가 |
| `not_found` | 3 | "…문서가 존재하지 않음" | 정직하게 거절했는가 |
| `unsupported` | 2 | "…연차 일수는 답변하지 않는다" | 답하지 말아야 할 때 참았는가 |

뒤의 두 유형(5문항)은 사실 서술이 아니라 **동작 서술**이다. 그래서 전체 평균 0.839는
"사실 정확도 0.800"이 아니라 "사실 정확도와 응답 정책 준수를 섞은 값"이다.
발표에서 단일 수치로 인용하려면 이 점을 함께 말해야 한다.

## 4.3 평가셋의 출처

**사람이 처음부터 손으로 쓴 평가셋이 아니다.** 원본 55문항 중 50개는 LLM 자동 생성물이고
`attachment` 5개만 색인에서 실제 문서·첨부명을 확인해 작성했다. 이후 실패 문항을 열어보며
라벨 오류를 수정한 기록이 있다. **"자동 생성 후 검증·수정"이 정확한 표현이며,
"수동 검증 평가셋"이라고 하면 과장이다.**

---

# 5. 측정값

## 5.1 실행 조건

| 항목 | 값 |
|---|---|
| Dataset | `confluence-rag-qa-36-indexed` (36문항) |
| 인덱스 | `confluence-current` (3,493청크) |
| 검색 | RRF K=60, top_k=5, BM25·kNN 후보 각 50, 최신성 가산점 4% (운영 설정 그대로) |
| 생성 | `select_optimal_model` 라우팅, temperature 0 |
| 판정 | `solar-judge` (자체 지표·RAGAS 공통) |

같은 데이터셋에 run 두 개가 있다. **두 run의 차이는 `build_context_text()` 한 곳뿐이다.**

| run 이름 | 내용 |
|---|---|
| `ey-interview-36-rrf-k60-top5-20260916` | 기준선 |
| `ey-interview-36-rrf-k60-top5-20260916-attachfix` | **현재 값.** 첨부파일명을 컨텍스트에 넣도록 고친 뒤 재측정 |

두 run 모두 36 item / Error 0 / **채점 누락 0**이다.
데이터셋을 나누지 않고 같은 데이터셋에 넣었다. Langfuse는 **같은 데이터셋 안의 run만**
한 표에 나란히 놓고 비교하기 때문이다.

## 5.2 여섯 지표

| 지표 | 기준선 | **현재(attachfix)** | 차이 | 채점 문항 |
|---|--:|--:|--:|---|
| `retrieval_hit` | 0.818 | **0.818** | +0.000 | 33 / 33 |
| `retrieval_mrr` | 0.553 | **0.553** | +0.000 | 33 / 33 |
| `answer_faithfulness` (자체) | 0.992 | **0.985** | -0.007 | 36 / 36 |
| `answer_correctness` | 0.800 | **0.839** | **+0.039** | 36 / 36 |
| `ragas_faithfulness` (표준) | 0.879 | **0.868** | -0.011 | 36 / 36 |
| `ragas_context_precision` | 0.679 | **0.680** | +0.001 | 36 / 36 |

검색 지표의 분모가 33인 것은 정답 `page_id`가 없는 `not_found` 3문항을 뺐기 때문이다(2.1절).

**검색 지표가 두 run에서 완전히 같다.** 컨텍스트 조립만 바꿨으니 검색 결과는 바뀌면 안 되고,
실제로 소수점까지 동일하다. 변경 범위가 의도대로였다는 확인이다.

**검색 지표가 별도 측정과도 일치한다.** `retrieval_hit` 0.818 / `retrieval_mrr` 0.553은
`EVAL_RESULTS_20260916.md`가 `retrieval_fusion_comparison.py`로 따로 잰
"RRF + 최신성(운영 설정)" 행과 같은 값이다. 경로가 완전히 다른 두 측정이 같은 값을 냈으므로,
**Langfuse Experiment가 평가 전용 로직이 아니라 운영 `search_hybrid`를 그대로 탄 것이 확인된다.**

## 5.3 category별 (현재 run)

| category | n | hit | mrr | faith(자체) | correctness | ragas_faith | ragas_ctx_prec |
|---|--:|--:|--:|--:|--:|--:|--:|
| semantic | 8 | 0.750 | 0.490 | 0.956 | 0.688 | 0.875 | 0.529 |
| keyword | 7 | 0.857 | 0.607 | 0.986 | 0.986 | 0.798 | 0.738 |
| boundary | 7 | 0.800 | 0.357 | 1.000 | 0.957 | 0.948 | 0.437 |
| conversational | 6 | 0.833 | 0.492 | 0.983 | 0.917 | 0.990 | 0.856 |
| attachment | 6 | 0.800 | 0.667 | 1.000 | 0.600 | 0.689 | 0.811 |
| table | 2 | 1.000 | 1.000 | 1.000 | 1.000 | 0.971 | 1.000 |

축별 문항 수가 2~8이다. **방향 지표로만 쓰고 절대값을 인용하지 않는다.**

`answerability`별 `answer_correctness`: answerable 0.836(28) / answerable_negative 0.967(3) /
not_found 0.967(3) / unsupported 0.500(2).

`ragas_context_precision`이 `boundary` 축에서 0.437로 낮다. `hit`은 0.800인데도 그렇다.
정답 문서는 대체로 찾지만 **함께 딸려온 나머지 4건이 답변에 쓸모없었다**는 뜻이며,
검색 지표 두 개로는 드러나지 않는 부분이다.

## 5.4 첨부파일명 수정 — 원인과 효과

### 발견

기준선 run에서 `attachment` 축만 `answer_correctness` 0.367로 무너졌다. `hit`은 0.800으로
정상이었다. **검색은 되는데 답변이 안 되는** 형태였다.

- 검색은 첨부명을 본다. BM25 필드에 `attachments^1.0`이 있어 첨부파일명으로 문서를 찾아낸다.
- **생성은 첨부명을 못 봤다.** `build_context_text()`가 렌더링하는 것이 `title` / `path` /
  `text` 셋뿐이어서, `search_hybrid`가 함께 돌려주는 `attachments`가 프롬프트에 들어가지 않았다.

실측 확인 (#34 "제휴병원 할인 혜택 안내문 파일로 받을 수 있어?"):

| | 값 |
|---|---|
| 검색 결과의 `attachments` | `25년 10월 기업공지 제휴병원 혜택 안내_세이프닥` 포함 (정답) |
| 컨텍스트에 그 파일명이 있었는가 | **없음** |
| 당시 답변 | "파일 수령 가능 여부는 문서에 명시되어 있지 않습니다" |

### 수정

`build_context_text()`가 첨부파일명을 문서 제목 바로 아래 줄에 넣는다.
**"파일명만 확인 가능, 본문은 색인되지 않음"을 함께 적는다.** 첨부파일의 본문은 색인되지 않으므로,
이를 알려주지 않으면 파일명만 보고 내용을 지어낼 수 있다.

### 효과

| | 기준선 | attachfix |
|---|--:|--:|
| `attachment` 축 correctness (n=6) | 0.367 | **0.600** |
| #25 "LG유플러스 운영자 매뉴얼 최신 버전 있어?" | 0.20 | **0.80** |
| #34 "제휴병원 할인 혜택 안내문 파일로 받을 수 있어?" | 0.00 | **0.80** |

**전체 correctness 상승분(+0.039)은 전부 이 두 문항에서 나왔다.**
(0.60 + 0.80) / 36 = +0.0389. 나머지 문항의 ±0.10 변동 6건은 정확히 상쇄된다
(+0.10 ×3, -0.10 ×3). 그 6건이 첨부명 추가로 컨텍스트가 바뀌어 흔들린 것인지 판정 모델의
실행 간 변동인지는 **구분할 수 없다.** 이 폭을 개선이나 퇴행으로 읽지 않는다.

`answer_faithfulness`(-0.007)와 `ragas_faithfulness`(-0.011)는 소폭 내렸다. 36문항에서
한 문항이 안 되는 폭이라 **변화라고 말할 수 없다.**

### 고쳐지지 않은 것

- **#33** (0.00 유지) — 첨부 문제가 아니라 최신성 가산점 문제다. 5.5절 표 참고.
- **#35** (0.00 유지) — `unsupported` 문항이다. 첨부 본문이 색인되지 않아 **답하지 말아야
  하는데** 같은 페이지의 다른 본문을 근거로 연차 소멸 기한을 상세히 답한다. 컨텍스트에
  "본문은 색인되지 않음"을 적어도 이 동작은 바뀌지 않았다. 검색·컨텍스트가 아니라
  **응답 정책(시스템 프롬프트) 문제**이며 이번 변경 범위가 아니다.

## 5.5 실패 문항 (현재 run, correctness < 0.7, 6건)

| id | category | hit | mrr | faith | corr | 진단 |
|--:|---|--:|--:|--:|--:|---|
| 8 | semantic | 0.0 | 0.00 | 0.9 | 0.0 | 검색 실패 — 정답이 **6위**, top5 바로 밖 |
| 10 | semantic | 1.0 | 0.33 | 1.0 | 0.0 | 문서는 찾았으나 이해·추론 실패 |
| 11 | semantic | 0.0 | 0.00 | 0.95 | 0.5 | 검색 실패 — 후보 풀 밖 |
| 16 | conversational | 1.0 | 0.50 | 1.0 | 0.6 | 부분 정답 |
| 33 | attachment | 0.0 | 0.00 | 1.0 | 0.0 | **최신성 가산점 때문** (아래) |
| 35 | attachment | 1.0 | 1.00 | 1.0 | 0.0 | 응답 정책 (5.4절) |

기준선의 8건에서 #25·#34가 빠졌다.

`answer_faithfulness`가 6건 중 4건 1.0이다. **컨텍스트를 무시하거나 지어내서 틀린 것이
아니라, 컨텍스트에 답이 없는데 그 안에서 성실하게 답한 결과다.** 자체 faithfulness만 보면
0.985로 문제가 없어 보이지만 correctness는 0.839다. 두 지표를 함께 봐야 하는 이유다.

### 검색 실패 3건의 원인이 서로 다르다 (2026-09-16 실측)

`RECENCY_BOOST_MAX`를 켜고 끄면서 같은 질문을 다시 검색해 확인했다.

| id | 운영 설정(가산점 ON) | 가산점 OFF | 후보 확대(top50) | 원인 |
|--:|---|---|---|---|
| 8 | miss | miss | **6위** | top5 바로 밖. 29청크 대형 문서의 청크 희석 |
| 11 | miss | miss | 없음 | **1차 후보 회수 실패.** 결합 방식 문제가 아니다 |
| 33 | miss | **3위** | 7위 | **최신성 가산점이 밀어냈다** |

**#33은 검색 방식의 실패가 아니라 튜닝 파라미터의 부작용이다.**
`EVAL_RESULTS_20260916.md` 1절이 "최신성 가산점은 이 평가셋에서 이득이 아니다
(Hit@5 28 → 27)"라고 적은 그 1문항이 #33이다. 이번 실행으로 어떤 문항인지 특정됐다.

가산점을 끄면 이 1문항을 되찾지만, 그것만으로 "꺼야 한다"고 말하기엔 33문항 중 1문항이라
근거가 얇다. **"가산점이 도움이 됐다"고 말할 수 없다**까지가 지금 말할 수 있는 전부다.

---

# 6. 한계와 남은 일

## 6.1 한계

- **36문항 1회 측정이다.** 두 run 비교에서 본 것처럼 **±0.10짜리 문항 변동이 6건** 생긴다.
  전체 평균으로는 ±0.03 수준이며, 그 폭의 차이는 노이즈와 구분되지 않는다.
- 축별 문항이 2~8건이라 **category별 절대값은 인용할 수 없다.** 방향만 본다.
- 정답 문서가 18종에 몰려 있다. 문서 단위 편향이 지표에 그대로 남는다.
- `answer_correctness` 0.839는 사실 정확도와 응답 정책 준수를 섞은 값이다(4.2절).
- 36문항이 전부 단일턴이라 **멀티턴 검색의 이득이 지표로 잡히지 않는다.**
  `run_qa`는 `search_hybrid`를 직접 호출해 `build_search_query`의 대화 이력 반영 경로를
  타지 않는다. 실서비스와 이 한 지점만 다르다.
- 제목 단어를 쓰지 않는 질문을 사람이 직접 쓴 대조군이 없다. 2026-09-16에 v4 paraphrase를
  삭제한 뒤로 "하이브리드가 제목 없는 질문에서 버틴다"는 주장의 근거가 없다. **말할 수 없다.**

## 6.2 남은 일

| 우선순위 | 할 일 | 근거 |
|---|---|---|
| 1 | `unsupported` 문항의 응답 정책 (#35) | 5.4절. 답하면 안 될 때 답한다. 첨부 본문 미색인을 컨텍스트에 적어도 안 바뀐다 |
| 2 | `RECENCY_BOOST_MAX` 유지 여부 결정 | 5.5절. 끄면 #33을 되찾지만 근거가 1문항뿐 |
| 3 | #11의 1차 후보 회수 개선 (후보 50 밖) | 5.5절. 결합 방식으로는 못 고친다 |
| 4 | #8의 대형 문서 청크 희석 (29청크) | 5.5절. 청킹 전략 과제 |
| 5 | 제목 단어 없는 질문을 사람이 작성 | 6.1절 마지막 항목 |
