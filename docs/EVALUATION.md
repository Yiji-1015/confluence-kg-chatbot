# 평가 계층 기준

> 대상: `ai-server/evaluation/` — `run_qa.py`(실행·채점), `generate_dataset.py`(평가셋 생성),
> `dataset_items.py`(v2 스냅샷), `generate_paraphrase_dataset.py`·`paraphrase_dataset_items.py`(v4 대조군),
> `compare_fusion.py`(결합 방식 비교).
>
> 검색 계층은 `RETRIEVAL.md`, 변경 이력은 `CHANGELOG.md`.

---

# 1. 실행 구조

## 1.1 전체 흐름

Langfuse의 Dataset 기능 위에서 돈다. 데이터셋 문항 하나마다 `rag_task`를 돌려 결과를 만들고,
그 결과를 채점기 6개에 각각 넘긴다.

```
Langfuse Dataset (confluence-rag-qa-v2, 45문항)
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
dataset = client.get_dataset("confluence-rag-qa-v2")
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
client.flush()
```

실행: `cd ai-server && .venv/bin/python -m evaluation.run_qa`

## 1.2 `rag_task` — 평가 대상 파이프라인

```python
def rag_task(*, item, **kwargs):
    query = item.input
    try:
        query_vector = embed_texts([query])[0]
        results = search_hybrid(query_text=query, query_vector=query_vector)
        selected_model, _ = select_optimal_model(query=query)
        context_text = build_context_text(results)
        answer = generate_answer(query=query, context=context_text, model=selected_model)
    except Exception as exc:
        _FAILURES[f"rag_task(검색/생성): {type(exc).__name__}"] += 1
        raise
    ...
```

**실서비스(`api/v1/chat.py`)와 같은 함수를 호출한다.** 평가 전용 파이프라인을 따로 두지 않았다.
`search_hybrid`, `select_optimal_model`, `build_context_text`, `generate_answer` 네 개가 공통이다.

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

예: `qa-rrf_k60-top5-cand50-chars3000-recency0.04-temp0-judge_solar-0914-1530`

검색 결과나 점수를 바꾸는 설정값이 전부 들어간다. 끝의 시각은 같은 설정을 여러 번 돌릴 때
이름이 겹치지 않게 하기 위한 것이다.

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
| `known_gap` | 겨냥한 문서가 본문 없이 청크 0개라 **Elasticsearch에 존재하지 않는다.** 검색이 못 찾는 것이 정상이므로 채점하면 구조적으로 0점이 되어 지표를 왜곡한다 |
| out-of-domain | `expected_doc_ids` 자체가 없다 (사내 문서에 답이 없는 질문) |

제외된 문항은 `answer_correctness`로 "모른다고 답하는가", "링크 안내로 답하는가"를 본다.
45문항 중 **38문항**이 검색 지표 채점 대상이다 (= `factoid` 38건).

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
실측(2026-09-01): 필드 결합 방식을 바꿔가며 재보면 `hit@5`는 전혀 움직이지 않고 MRR만 갈렸다.

| 방식 | hit@5 | MRR |
|---|---|---|
| `best_fields` | 0.974 | 0.908 |
| `tie_breaker=0.3` | 0.974 | 0.908 |
| `most_fields` | 0.974 | 0.890 |

컨텍스트는 점수 순으로 이어 붙으므로 앞 순위일수록 답변에 강하게 작용한다.

## 2.2 자체 구현 생성 지표

두 지표 모두 판정 프롬프트를 만들어 `_judge()`에 넘기고, 응답에서 점수를 파싱한다.

### `answer_faithfulness`

`answer`가 `context`에 있는 내용에만 근거하는지 본다. 정답 라벨이 필요 없다.

판정 프롬프트 요지:
- 컨텍스트에 없는 내용을 지어냈으면 낮은 점수
- **컨텍스트가 비어 있고 답변도 "모른다"고 했으면 1.0** (out-of-domain 문항에서 정직한 거절을 감점하지 않기 위한 규칙)
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
except ImportError:
    print("[RAGAS] 미설치 - 건너뜁니다.")
```

- `ragas`는 langchain/langgraph/datasets를 통째로 끌고 오므로 **서빙 이미지에 넣지 않고**
  `requirements-eval.txt`로 분리했다. 미설치면 빈 dict를 돌려주고 나머지 4개 지표로 계속 진행한다.
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
전혀 보지 못한다.** 실측(2026-09-01): 결합 방식을 바꾸자 상위 5개 구성이 38건 중 33건에서
달라졌는데도 `hit`은 완전히 동일했다.

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

**두 겹으로 막는다.**

| 방어 | 잡는 것 |
|---|---|
| `_FAILURES` 사유별 집계 | 코드가 아는 실패 (호출 실패, 파싱 실패, rag_task 예외) |
| `scored` vs `expected` 개수 대조 | 예상하지 못한 경로로 빠진 문항 |

누락이 없을 때도 **"채점 누락 없음"을 명시적으로 출력한다.** 침묵을 정상으로 오해하지 않게 하기 위해서다.

### 이 장치가 만들어진 계기 (2026-09-02)

recency를 켜고 재측정하던 중 판정 모델 호출에서 429(요청 한도 초과)가 5건 발생했다.
재시도가 없어 해당 문항들은 `correctness=None`으로 빠졌고, **45문항이 아니라 40문항 평균이
출력됐다.** 실행은 정상 종료되고 점수도 그럴듯해서 로그를 보지 않으면 알 수 없었다.

원인은 평가가 판정 모델을 짧은 시간에 몰아치기 때문이다. 자체 판정기 2종에 RAGAS의 내부
병렬 호출이 겹치면서 분당 한도를 넘겼다.

재발 방지는 **게이트웨이 한 곳에서** 처리했다. 호출부마다 재시도를 넣지 않고
`litellm/config.yaml`의 `router_settings`에 `num_retries: 3`, `retry_after: 5`를 뒀다.

이 문제로 recency 활성화 후의 첫 두 측정값은 **누락 때문인지 recency 때문인지 구분할 수 없어
폐기했다.** 세 번째 실행에서 429가 사라져 비교 가능한 값을 얻었다.

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
    verdict = "판단 보류 (out-of-domain/known-gap 항목이거나 점수 부족)"
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
- [qa-v2-021] hit=0.0 faithfulness=0.95 correctness=0.2 -> 검색 실패 (정답 문서를 못 찾음)
```

### 실제 적용 결과

실패 3건을 열어본 결과 **2건은 지표 적용 오류**(본문 없는 문서를 검색 지표로 채점),
**1건은 데이터셋 라벨 오류**(실제로 존재하는 문서를 out-of-domain으로 표기)였다.
모델이 아니라 측정 쪽 문제였다.

---

# 4. 평가 데이터셋

## 4.1 v2 — `confluence-rag-qa-v2` (45문항)

`generate_dataset.py`가 Elasticsearch에서 카테고리별 대표 문서를 샘플링하고 `gpt-4o`로 생성했다.

| 유형 (`metadata.type`) | 건수 | 검증 목적 |
|---|---|---|
| `factoid` | **38** | 단일 문서 기반 사실 질의 |
| `out_of_domain` | **5** | 사내 문서에 없는 질문에 정직하게 거절하는가 |
| `known_gap` | **2** | 본문 없는(DB 매크로) 문서를 링크 안내로 답하는가 |
| 멀티턴 | **0** | 없음 |

각 문항의 `metadata`에 `expected_doc_ids`, `doc_title`, `category`가 들어 있다.
(위 숫자는 2026-09-14에 `dataset_items.py`를 직접 세어 확인했다.)

> ⚠️ **`generate_dataset.py`의 모듈 docstring이 실제 데이터셋과 다르다.**
> "Factoid 35개 / Out-of-Domain 6개 / Known-Gap 3개 / **Multi-turn 4개**"로 적혀 있으나
> 실제로는 38 / 5 / 2이고 **멀티턴 문항은 하나도 없다.** 생성기를 고쳐가며 돌리는 동안
> 주석이 따라가지 못한 것으로 보인다. 이 주석을 근거로 "멀티턴도 평가한다"고 말하면 틀린다.

## 4.2 v2의 편향 (2026-09-14 확인)

생성 프롬프트에 다음 지시가 있다.

> *"문서 제목({title}), 상위 경로({path}), 팀명, 프로젝트명, 날짜 등 이 문서를 고유하게
> 식별할 수 있는 구체적인 단서 키워드를 질문 안에 반드시 자연스럽게 포함하세요"*

질문이 문서를 특정하지 못해 채점이 불가능해지는 것을 막으려는 의도였고 그 목적에는 맞다.
부작용으로 질문이 정답 문서의 제목을 그대로 담게 됐다.

| | 값 |
|---|---|
| 제목 단어가 질문에 100% 포함된 문항 | **27 / 38 (71%)** |
| 절반 이상 포함 | 34 / 38 |
| 평균 포함률 | **84.7%** |

키워드 검색에 유리한 조건이므로 **이 평가셋으로는 하이브리드 검색의 이득을 측정할 수 없다.**

## 4.3 v4 — paraphrase 대조군 (38문항)

`generate_paraphrase_dataset.py`가 v2의 채점 문항을 **같은 정답 문서를 겨냥한 채로** 다시 쓴다.
제목·경로·팀명·프로젝트명을 쓰지 말고 동의어와 구어체로 묻게 한다.

- 제목 단어 누출 **84.7% → 6.4%**
- 정답 문서를 고정하므로 **질문 표현 외의 변수가 통제된다**
- 스냅샷(`paraphrase_dataset_items.py`)으로 고정한다. 재생성하면 표현이 달라져 비교가 깨진다
- 검색 지표 전용이라 `expected_output`을 담지 않는다 (`source_id`로 v2에서 찾을 수 있다)

| | v2 원본 | v4 paraphrase |
|---|---|---|
| 예시 | LLOYDK의 **'종합건강검진 제도'** 문서에 따르면 2026년부터 시행되는 연 1회 종합검진 제도는 어떻게 운영되나요? | 2026년부터 시작되는 연 1회 건강검진은 어떻게 진행되나요? |

## 4.4 `compare_fusion.py` — 결합 방식 비교

두 평가셋 × 결합 방식 6종을 `hit@5` / `MRR`로 잰다. 답변 생성과 LLM 판정을 거치지 않으므로
빠르고 값이 흔들리지 않는다(동일 조건 3회 실행에서 소수점까지 동일).

비교 대상: 하이브리드(운영 코드 `search_hybrid` 그대로) / BM25 단독 / kNN 단독 / 슬롯 배분 3종.

실행: `docker exec -i rag-ai-server python -m evaluation.compare_fusion`

---

# 5. 측정값

## 5.1 검색 지표 (2026-09-14 / 583문서 · 3,211청크 / 38문항)

| 방식 | v2 원본 hit@5 / MRR | v4 paraphrase hit@5 / MRR |
|---|---|---|
| 하이브리드 (RRF) | 0.974 / **0.908** | **0.763** / **0.560** |
| BM25 단독 | 0.947 / 0.864 | 0.605 / 0.390 |
| kNN 단독 | 0.921 / 0.812 | 0.605 / 0.501 |

## 5.2 생성 지표 (2026-09-08 / 45문항 / 판정 `solar`)

| 지표 | 값 |
|---|---|
| `answer_correctness` | 0.970 |
| `ragas_context_precision` | 0.753 |

검색 엔진 변경(09-13·09-14) 이전 측정값이다. 재측정에 판정 모델 호출이 필요해 보류했다.

## 5.3 자체 지표와 표준 지표의 차이 (2026-09-01 기준선)

| 지표 | 값 |
|---|---|
| `answer_faithfulness` (자체) | **0.951** |
| `ragas_faithfulness` (표준) | **0.838** |
| 차이 | **0.113** |

**자체 지표가 0.113 후하게 채점하고 있었다.**
RAGAS는 답변을 문장 단위로 쪼개 각 문장이 컨텍스트에 근거하는지 따로 판정하는데,
자체 구현은 답변 전체를 한 번에 본다. 일부 문장이 근거 없어도 전체적으로 "근거함"으로 넘어간다.

## 5.4 알려진 채점 실패

`ragas_faithfulness`에서 `IncompleteOutputException`이 나던 문제는 **2026-09-14에 원인을
찾아 고쳤다.**

RAGAS의 `llm_factory`는 `max_tokens`를 넘기지 않으면 기본값 **1024**를 쓴다
(`InstructorModelArgs`). faithfulness는 답변을 **문장 단위로 쪼개 문장마다 판정 JSON을
만들기 때문에**, 답변이 길면 출력이 1024토큰을 넘겨 잘리고 instructor가 예외를 던진다.
그 문항은 점수 없이 빠져 평균이 40문항 평균이 된다.

실측: 문장 40개(2,510자) 답변에서 `max_tokens=1024`는 실패하고 `4096`은 성공했다.
같은 입력에서 한도만 바꾼 것이므로 다른 변수는 없다.

`context_precision`은 문서 5건만 판정해 출력이 짧아 걸리지 않는다.
**같은 판정 모델인데 faithfulness만 실패하던 이유가 이것이다.**

RAGAS 자체 docstring도 "Default max_tokens=1024 may not be sufficient /
If structured output is truncated, increase max_tokens further"라고 안내한다.

`RAGAS_JUDGE_MAX_TOKENS = 4096`으로 고정했다.

> 실패 건수가 2026-09-08의 1건에서 09-13 실행의 5건으로 늘었는데, RRF 전환으로
> `context_precision`이 0.753 → 0.849로 오르며 컨텍스트가 충실해지고 답변이 길어진 것이
> 원인으로 보인다. **검색이 좋아지면서 지표 수집이 깨진 셈이다.**

---

# 6. 한계

- **38문항 1회 측정이다.** ±0.02 수준의 차이는 노이즈와 구분되지 않는다.
- v4는 제목 단서를 빼면서 일부 문항이 모호해졌다(정답 문서가 유일하지 않을 수 있다).
  절대 수치는 과소평가되며 방식 간 비교에만 쓴다.
- v4 문장은 LLM이 다시 쓴 것이다. "제목을 모르고 묻는 경우"를 대신할 뿐 실사용 질문 분포를
  대표하지 않는다.
- 45문항이 전부 단일턴이라 **멀티턴 검색의 이득이 지표로 잡히지 않는다.**
  `run_qa`는 `search_hybrid`를 직접 호출해 대화 이력 반영 경로를 타지 않는다.
