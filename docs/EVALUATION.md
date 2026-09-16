# 평가 계층 기준

> 대상: `ai-server/evaluation/` — `run_qa.py`(실행·채점),
> `dataset_items_36.py`(평가셋 → Langfuse item 변환), `push_dataset_36.py`(업로드),
> `langfuse_client.py`(연결), `verify_langfuse.py`(연결 확인),
> `retrieval_methods.py`·`compare_methods.py`(검색 방식 비교, **7장**).
>
> 측정이 두 갈래다. 묻는 것이 다르다.
>
> | | 묻는 것 | 어디 |
> |---|---|---|
> | 운영 파이프라인 기준선 | 그래서 최종 품질이 어떤가 | `run_qa.py` → Langfuse Experiment (1~5장) |
> | 검색 방식 비교 | 왜 이 검색 방식인가 | `compare_methods.py` (7장) |
>
> 순위만 보는 검색 비교(Hit@5 / MRR 단독)는 저장소 루트의
> `retrieval_fusion_comparison.py`와 `EVAL_RESULTS_20260916.md`에 있다.
>
> 검색 계층은 `RETRIEVAL.md`, 변경 이력은 `CHANGELOG.md`.

> **2026-09-16 전면 교체.** 채점기를 **RAGAS 표준 지표 5종으로 전부 바꿨다.**
> 이전의 6종(`retrieval_hit`, `retrieval_mrr`, `answer_faithfulness`, `answer_correctness`,
> `ragas_faithfulness`, `ragas_context_precision`)은 **삭제됐다.**
> 그 이유는 0장에, 폐기된 수치의 처리는 5장에 적었다.

---

# 0. 왜 갈아엎었나

## 0.1 무엇이 문제였나

이전 채점기 6종 중 **RAGAS 구현은 2종뿐이었다.** 나머지 4종은 이 저장소에서 직접 만든 것이다.

| 이전 이름 | 실제 구현 |
|---|---|
| `retrieval_hit` | 자체 — 파이썬 `any()` 비교 |
| `retrieval_mrr` | 자체 — `1/rank` |
| `answer_faithfulness` | 자체 — **판정 모델에게 "SCORE: 숫자"를 달라고 한 프롬프트 한 방** |
| `answer_correctness` | 자체 — **같은 방식의 프롬프트 한 방** |
| `ragas_faithfulness` | RAGAS `Faithfulness` |
| `ragas_context_precision` | RAGAS `ContextPrecisionWithoutReference` |

문제는 뒤의 두 자체 지표다. 구현이 이랬다.

```python
judge_prompt = ("... 0.0 ~ 1.0 사이 점수를 아래 형식으로만 출력하세요:\n"
                "SCORE: <숫자>\nREASON: <한 줄 이유>")
raw = generate_chat_completion([{"role": "user", "content": judge_prompt}], model=JUDGE_MODEL)
score = float(_SCORE_RE.search(raw).group(1))
score = max(0.0, min(1.0, score))
```

**이건 지표가 아니라 모델이 부른 숫자다.** 0.2와 0.4를 가르는 기준이 어디에도 없다.
답변을 주장 단위로 쪼개지도, 각 주장을 컨텍스트와 대조하지도, 정답과 사실 단위로 맞춰보지도
않는다. 모델에게 "알아서 점수 매겨줘"라고 하고 정규식으로 숫자만 긁어냈다. 같은 입력에 다른
숫자가 나와도 알 길이 없고, 범위를 벗어나면 조용히 0~1로 잘랐다.

## 0.2 왜 못 알아챘나

이름 때문이다.

RAGAS에는 `faithfulness`와 `answer_correctness`라는 지표가 **실제로 있다.** 자체 구현에
`answer_faithfulness` / `answer_correctness`라는 거의 같은 이름을 붙여 놓으니, Langfuse
대시보드에서 `answer_correctness 0.839`를 보면 표준 지표를 본 것처럼 읽힌다.

문서가 이걸 굳히는 데 한몫했다. 여섯 지표를 한 표에 나란히 놓고 자체 두 개에는
`(자체)`, RAGAS 두 개에는 `(표준)`이라고만 적었다. 각주 한 줄로 구분한 셈인데, 정작 표 전체는
"채점기 6종"이라는 한 덩어리로 제시됐다. 자체 지표를 표준 지표와 **"교차 검증"한다**고 쓴 대목은
더 나쁘다. 검증하는 쪽과 받는 쪽이 대등한 것처럼 읽히지만, 실제로는 정의 없는 숫자와
정의된 계산을 나란히 둔 것이다.

결정적으로, **인용되던 대표 숫자가 자체 지표 쪽이었다.** 첨부파일명 수정(2026-09-16)의 효과를
`answer_correctness 0.800 → 0.839`로 보고했는데 이 값은 RAGAS가 아니라 "SCORE: 숫자" 프롬프트의
출력이다. 개선 근거로 삼기에 부족하다.

## 0.3 남은 하나 — reference 없는 context_precision

`ragas_context_precision`은 RAGAS 구현이 맞지만 `ContextPrecisionWithoutReference`,
**정답 라벨을 안 보는 변형**이었다. 이건 "검색된 문서가 *생성된 답변*에 쓸모 있었나"를 잰다.
답변이 틀렸는데 그 틀린 답변에 들어맞는 문서를 가져왔으면 점수가 올라간다.
36문항 전부 `ground_truth_snippet`이 채워져 있는데도 그걸 안 쓰고 있었다.

지금은 정답 라벨을 쓰는 `ContextPrecision`(= `ContextPrecisionWithReference`)으로 바꿨다.

## 0.4 그래서 정한 규칙

**점수를 만드는 코드를 이 저장소에 두지 않는다.** `run_qa.py`에는 프롬프트도, 파싱도,
가중치도 없다. 하는 일은 파이프라인 출력을 RAGAS의 입력 필드 이름에 맞춰 넘기는 것뿐이다.
지표 생성자에는 `llm`과 `embeddings` 말고 아무것도 넘기지 않는다. `strictness`,
`weights`(0.75/0.25), `beta`를 우리가 건드리는 순간 그건 RAGAS 점수가 아니라 우리가 조정한
점수가 되고, 0.1절과 같은 일이 다시 일어난다.

Langfuse에 남는 점수 이름도 RAGAS 인스턴스의 `.name`을 그대로 쓴다.

---

# 1. 실행 구조

## 1.1 전체 흐름

Langfuse의 Dataset 기능 위에서 돈다. 데이터셋 문항 하나마다 `rag_task`를 돌려 결과를 만들고,
그 결과를 RAGAS 채점기 5개에 각각 넘긴다.

```
Langfuse Dataset (confluence-rag-qa-36-indexed, 36문항)
        │
        │  문항마다
        ▼
    rag_task(item)  ── 검색 + 생성을 실제로 수행
        │
        │  {answer, retrieved_contexts, ...} + item.expected_output
        ▼
    RAGAS 지표 5개가 각각 이 값을 받아 점수 하나씩 반환
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
    evaluators=EVALUATORS,                      # RAGAS 5종
    max_concurrency=MAX_CONCURRENCY,
    metadata={"rrf_k": ..., "top_k": ..., "judge_model": ...,
              "metrics_source": "ragas.metrics.collections"},
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

# 연결만 먼저 확인한다 (1.4절). 36문항을 태우기 전에 10초면 끝난다.
docker exec rag-ai-server python -m evaluation.verify_langfuse

docker exec rag-ai-server python -m evaluation.push_dataset_36
docker exec -e EVAL_RUN_NAME=<이름> rag-ai-server python -m evaluation.run_qa
```

`max_concurrency`는 기본 4다. 판정 모델을 짧은 시간에 몰아치면 429가 나고, 재시도에 실패한
문항이 점수 없이 빠져 평균이 왜곡된다(3.3절).

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
지표를 갈아엎으면서 이 함수는 건드리지 않았다. **측정 대상은 그대로 두고 자(尺)만 바꾼 것**이라,
새 수치가 이전과 다르면 그건 파이프라인이 아니라 채점 방식의 차이다.

단계를 감싸는 `stage()`도 실서비스와 같은 것(`app.observability`)이다. 그래서 Langfuse
trace 하나를 열면 `rag.embedding → rag.search → rag.context_build → rag.generation`이
시간 순으로 펼쳐지고, **검색 결과가 컨텍스트를 거쳐 생성으로 들어갔는지를 눈으로 확인**할 수 있다.

예외를 잡아 집계한 뒤 **다시 raise한다.** 여기서 죽으면 채점기가 아예 호출되지 않아
채점기 쪽 실패 집계에 잡히지 않기 때문이다. 따로 세지 않으면 "전부 실패했는데 누락 없음"으로
보고하게 된다.

**반환값** — 지표마다 필요한 형태가 다르다.

| 키 | 형태 | 쓰는 지표 |
|---|---|---|
| `answer` | 문자열 | `faithfulness`, `answer_relevancy`, `answer_correctness` |
| `retrieved_contexts` | **문서 단위 리스트** | `faithfulness`, `context_precision`, `context_recall` |
| `context` | 합쳐진 문자열 | 채점에 안 쓴다. trace에서 실제 프롬프트를 확인하는 용도 |
| `retrieved_doc_ids` | 문서 id 리스트 (순위 순) | 채점에 안 쓴다. 실패 문항을 열어볼 때 쓴다 |

정답 라벨(`reference`)은 반환값이 아니라 Langfuse가 채점기에 넘겨주는
`expected_output`에서 온다.

**`retrieved_contexts`를 리스트로 넘기는 것이 중요하다.** 한 덩어리 문자열로 넘기면
`context_precision`과 `context_recall`이 "문서 1건"만 보고 채점해 순위 정보가 통째로 사라진다.

## 1.3 `_run_name()` — 실행 이름에 설정값을 담는다

```python
f"ragas5-rrf_k{RRF_K}"
f"-top{RETRIEVAL_TOP_K}-cand{RETRIEVAL_CANDIDATE_SIZE}"
f"-chars{DOC_CONTEXT_MAX_CHARS}-recency{RECENCY_BOOST_MAX:g}"
f"-temp{LLM_TEMPERATURE:g}-judge_{JUDGE_MODEL}"
f"-{datetime.now():%m%d-%H%M}"
```

예: `ragas5-rrf_k60-top5-cand50-chars3000-recency0.04-temp0-judge_solar-judge-0916-2140`

접두사를 `qa-`에서 **`ragas5-`로 바꿨다.** 이전 run과 지표 구성이 달라 섞어서 비교하면 안 되는데,
Langfuse 목록에서는 이름만 보인다. 이름이 다르면 실수로 같은 축에 올려놓지 않는다.

나머지는 검색 결과나 점수를 바꾸는 설정값이다. 끝의 시각은 같은 설정을 여러 번 돌릴 때
이름이 겹치지 않게 하기 위한 것이다. `EVAL_RUN_NAME`을 주면 그 이름을 그대로 쓴다.

**`run_experiment(name=...)`만 주면 안 된다.** langfuse 4.x는 `run_name`을 생략하면
`name` 뒤에 ISO 타임스탬프를 붙여 실제 run 이름을 만든다. 그러면 지정한 이름과 Langfuse UI에
보이는 이름이 달라진다. `name`과 `run_name`에 같은 값을 넘겨 고정한다.

## 1.4 Langfuse 연결 — `langfuse_client.py`

평가 스크립트 셋이 전부 이 모듈을 거쳐 클라이언트를 얻는다.

```python
from evaluation.langfuse_client import connect
client = connect()          # 환경변수 적용 + auth_check + 실패 시 SystemExit
```

**왜 모듈로 뺐나.** 연결 확인을 `run_qa`에 두면 문항만 올리는 `push_dataset_36`이
`run_qa`를 import하게 되고, 그러면 Elasticsearch 클라이언트와 LLM 모듈까지 딸려 온다.
문항을 업로드하는 데 검색 엔진이 필요할 이유가 없다. 이 모듈은 `app.config`와 `langfuse`
두 개만 본다. 키를 환경변수로 옮기는 여섯 줄도 세 스크립트에 복사돼 있던 것을 여기로 모았다.

### 확인 순서

`main()`은 **연결 → 데이터셋 → RAGAS** 순으로 확인한다. 순서가 곧 메시지의 정확도다.
연결이 안 된 채로 데이터셋을 읽으면 "데이터셋 없음"처럼 보이고, 실제 원인인 키·지역
문제를 엉뚱한 데서 찾게 된다.

| 단계 | 잡는 것 |
|---|---|
| `settings_problem()` | 키가 비었거나 `pk-lf-` / `sk-lf-` 접두사가 아닌 경우 (호출 전에 잡힌다) |
| `client.auth_check()` | 연결은 되는데 키가 거부되는 경우 — **대부분 지역(region) 불일치다** |
| `load_dataset()` | 데이터셋 미업로드 |
| `_preflight()` | RAGAS 미설치, 지표 5종 중 누락 |

### 지역이 제일 흔한 원인이다

Langfuse 키는 **프로젝트가 있는 지역에서만** 통한다. 지역이 틀리면 키가 맞아도 401이 나고,
증상은 그냥 "인증 실패"라 키를 의심하며 헤매게 된다. `config.py`의 기본값은 EU
(`https://cloud.langfuse.com`)인데 이 프로젝트의 캡처 경로는 **jp**다
(`docs/presentation/CAPTURE_GUIDE.md`). `.env`에 `LANGFUSE_HOST`를 안 적으면 조용히
EU로 붙는다. 그래서 실패 메시지에 지역 목록을 항상 함께 찍는다.

### `verify_langfuse.py` — 36문항을 태우기 전에

`run_qa`는 36문항 × 지표 5종이라 판정 모델 호출이 수백 건이다. 연결이나 키가 틀렸을 때
그걸로 알아내면 시간과 비용을 버린다. 같은 경로를 **최소 호출로** 밟는 스크립트를 따로 뒀다.

```
[1/6] Langfuse 설정       키 존재·형식
[2/6] Langfuse 인증       auth_check()
[3/6] Langfuse 쓰기       trace 1건을 실제로 남기고 UI 주소를 찍는다
[4/6] 데이터셋            업로드 여부와 문항 수
[5/6] LiteLLM 게이트웨이  판정 모델 1회 + 임베딩 1회
[6/6] RAGAS              지표 5종 생성 (채점은 하지 않는다)
```

3번이 따로 있는 이유는 **인증이 됐다고 쓰기가 되는 것은 아니기** 때문이다. Langfuse는
이벤트를 비동기로 보내므로 `flush()`까지 해야 실제로 서버에 닿았는지 알 수 있다.
같은 이유로 `push_dataset_36`도 업서트 전에 `connect()`를 부른다. 키가 틀려도
`create_dataset_item()`은 그 자리에서 실패하지 않아서, 36줄의 `upserted ...`가 다 찍히고
맨 끝 개수 대조에서야 어긋난다.

5번은 실제 API를 부른다(아주 작은 비용). 건너뛰려면 `VERIFY_SKIP_LLM=1`.

---

# 2. RAGAS 표준 지표 5종

| 이름 | 보는 것 | 컨텍스트 | 정답 라벨 | 임베딩 |
|---|---|---|---|---|
| `faithfulness` | 답변이 컨텍스트에 근거하는가 (환각) | 필요 | 불필요 | 불필요 |
| `answer_relevancy` | 답변이 질문에 대답하는가 (동문서답) | 불필요 | 불필요 | **필요** |
| `context_precision` | 쓸모 있는 문서가 상위에 왔는가 | 필요 | **필요** | 불필요 |
| `context_recall` | 정답 근거를 다 가져왔는가 | 필요 | **필요** | 불필요 |
| `answer_correctness` | 답변이 정답과 사실로 일치하는가 | 불필요 | **필요** | 선택 |

전부 `ragas.metrics.collections`의 클래스다. 값은 0~1.
채점기는 점수 대신 `None`을 돌려줄 수 있고, `None`이면 그 문항은 그 지표의 평균에서 빠진다.

## 2.1 다섯 개로 무엇이 갈라지나

지표 하나로는 원인을 못 가른다. 다섯 개를 같이 봐야 어디를 고칠지가 나온다.

| 증상 | 손댈 곳 |
|---|---|
| `context_recall` 낮음 | **검색.** 정답 근거가 애초에 컨텍스트에 없다 (가중치·후보 수·청킹) |
| recall 높고 `faithfulness` 낮음 | **프롬프트.** 근거를 줬는데 모델이 무시하고 지어낸다 |
| 둘 다 높고 `answer_relevancy` 낮음 | **프롬프트.** 컨텍스트에 충실하지만 질문에 대답하지 않는다 |
| 셋 다 높고 `answer_correctness` 낮음 | **모델.** 근거도 맞고 충실한데 사실을 틀린다 |
| `context_recall` 높고 `context_precision` 낮음 | **검색 순위.** 정답은 가져왔는데 쓸모없는 문서가 위에 있다 |

`context_precision` / `context_recall` 두 축이 **삭제된 `retrieval_hit` / `retrieval_mrr`가
하던 일을 대신하고, 더 본다.** hit/MRR은 "정답으로 라벨링한 문서 1개"가 상위 5개에
들었는지만 봤다. 함께 딸려온 나머지 4건이 전부 무관해도 만점이었고, 반대로 정답 문서를 못
찾았어도 그 내용이 다른 문서에 들어 있으면 0점이었다. RAGAS의 두 축은 문서 id가 아니라
**내용**을 본다.

## 2.2 생성 — `_ragas_metrics()`

```python
client = AsyncOpenAI(base_url=f"{LITELLM_BASE_URL}/v1", api_key="litellm-local")
judge = llm_factory(model=JUDGE_MODEL, provider="openai", client=client,
                    max_tokens=RAGAS_JUDGE_MAX_TOKENS)
embeddings = OpenAIEmbeddings(client=client, model=DEFAULT_EMBEDDING_MODEL)

for metric in (Faithfulness(llm=judge),
               AnswerRelevancy(llm=judge, embeddings=embeddings),
               ContextPrecision(llm=judge),
               ContextRecall(llm=judge),
               AnswerCorrectness(llm=judge, embeddings=embeddings)):
    _RAGAS[metric.name] = metric        # 이름도 RAGAS가 정한 것을 그대로
```

- **판정 LLM과 임베딩 모두 LiteLLM 게이트웨이를 통한다.** 게이트웨이를 거쳐야 실서비스와 같은
  재시도·폴백 정책(`num_retries: 3`)과 Langfuse 콜백이 그대로 걸린다. RAGAS가 OpenAI를 직접
  찌르게 두면 그 호출만 관측·재시도 밖으로 샌다.
- 임베딩은 색인에 쓴 것과 같은 모델(`embedding-openai` = `text-embedding-3-small`)이다.
  `answer_relevancy`와 `answer_correctness`가 쓴다.
- `ragas`는 langchain/langgraph/datasets를 통째로 끌고 오므로 **서빙 이미지에 넣지 않고**
  `requirements-eval.txt`로 분리했다. 컨테이너를 새로 만들면 사라지므로 평가 전에 매번 설치한다.
- **`ImportError`가 아니라 `Exception`을 잡는다.** ragas가 설치돼 있어도 의존성 버전 충돌로
  import가 깨지는 경우가 있다(`langchain-community` 0.4에서 제거된 모듈 참조).
  `ImportError`만 잡으면 그건 잡히지 않고 실행 전체가 죽는다.
- **미설치를 조용히 넘기지 않는다.** 이제 채점기가 전부 RAGAS라 없으면 기록할 점수가 하나도
  없다. `_preflight()`가 실행 **전에** 이 함수를 불러 상태를 확인하고, 5종이 다 만들어지지
  않으면 설치 명령을 안내하며 시작하지 않는다.

### `max_tokens`를 반드시 넘긴다

RAGAS `llm_factory`의 기본값은 **1024**다(`InstructorModelArgs`). `faithfulness`는 답변을
문장 단위로 쪼개 문장마다 판정 JSON을 만들기 때문에, 답변이 길면 출력이 1024를 넘겨 잘리고
`IncompleteOutputException`으로 죽는다. 그 문항은 점수 없이 빠져 36문항 평균이 아니게 된다.

실측(2026-09-14): 문장 40개(2,510자) 답변에서 1024는 실패, **4096은 성공.**
`RAGAS_JUDGE_MAX_TOKENS = 4096`을 상수로 두고 넘긴다.
이건 지표 계산이 아니라 판정 LLM의 호출 한도라서 우리가 정해도 되는 값이다.

## 2.3 넘기는 필드 — `_ascore()`

지표마다 `ascore()`의 인자 이름이 다르다. 순서가 아니라 **키워드로** 넘겨서 순서 실수를 막는다.

```python
faithfulness       ascore(user_input, response, retrieved_contexts)
answer_relevancy   ascore(user_input, response)
context_precision  ascore(user_input, reference, retrieved_contexts)
context_recall     ascore(user_input, retrieved_contexts, reference)
answer_correctness ascore(user_input, response, reference)
```

`context_precision`과 `context_recall`은 **받는 인자가 같은데 순서가 다르다.**
위치 인자로 넘기면 `reference`와 `retrieved_contexts`가 뒤바뀌어도 타입이 맞아 조용히 통과한다.

### 이벤트 루프 처리와 trace 전파

RAGAS 지표는 `async` API(`metric.ascore()`)만 제공하는데, Langfuse는 채점기를 이미 돌고 있는
이벤트 루프 안에서 호출한다. 그 안에서 `asyncio.run()`을 부르면 거부된다.

```python
try:
    asyncio.get_running_loop()
except RuntimeError:
    result = call()                      # 루프 없음 -> 그냥 실행
else:
    ctx = contextvars.copy_context()     # <- 이 줄이 핵심이다
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(ctx.run, call).result()
```

**`copy_context()` 없이 `pool.submit(call)`로 넘기면 채점이 trace에서 사라진다.**
Langfuse 4.x는 OpenTelemetry 위에 있고, "지금 열려 있는 span"은 contextvar에 들어 있다.
새 스레드는 그걸 물려받지 않으므로 채점 구간이 실험 item 아래가 아니라 **부모 없는 별도
trace로 떨어진다.** 점수는 정상 기록되므로 **UI를 열어 보기 전까지 알아채지 못한다.**

실측으로 확인한 차이 (stub Langfuse 서버 + 실제 langfuse 클라이언트):

| | 채점 중 `get_current_trace_id()` |
|---|---|
| 전파 없음 | `None` — "No active span in current context" |
| `copy_context()` 전파 | 실험 item의 trace id와 **동일** |

### 채점 구간도 span으로 남긴다

```python
span_cm = get_client().start_as_current_observation(
    name=f"eval.{name}", as_type="evaluator")
```

`as_type="evaluator"`를 준다. 기본값 `"span"`으로 두면 `rag.search` 같은 파이프라인 단계와
같은 모양이라 trace에서 구분되지 않는다.

그래서 실험 item 하나를 열면 이렇게 펼쳐진다.

```
experiment-item-run
├─ experiment-item-task
│  ├─ rag.embedding
│  ├─ rag.search
│  ├─ rag.context_build
│  └─ rag.generation
└─ experiment-item-evaluation
   ├─ eval.faithfulness
   ├─ eval.answer_relevancy
   ├─ eval.context_precision
   ├─ eval.context_recall
   └─ eval.answer_correctness
```

점수만 남기면 값은 보이지만 **그 값이 어떻게 나왔는지는 안 보인다.** 어느 지표가 몇 초를
썼는지, 어디서 실패했는지가 이 span들로 읽힌다.

메타데이터에는 길이와 건수만 남긴다(컨텍스트 문서 수, 글자 수). 입력 전문을 넣으면 trace가
본문으로 뒤덮이고, 같은 내용이 이미 `rag.context_build`에 있다.

### 판정 호출까지 남기려면 — `EVAL_TRACE_JUDGE`

위의 span은 "채점에 몇 초 걸렸나"까지다. **RAGAS가 판정 모델에 무엇을 보내고 무엇을
받았는지**는 안 보인다. `EVAL_TRACE_JUDGE=1`을 주면 `langfuse.openai`를 import해서 OpenAI
SDK를 전역 계측하고, 판정 호출 하나하나가 generation으로 붙는다(프롬프트·응답·토큰·비용).

```bash
docker exec -e EVAL_TRACE_JUDGE=1 rag-ai-server python -m evaluation.run_qa
```

**기본값은 꺼짐이다.** 이 계측은 `instructor`(RAGAS가 구조화 출력에 쓴다)가 같은 메서드를
패치하는 자리와 겹친다. 지표 5종 생성까지는 확인했지만 **실제 판정 호출로는 확인하지
못했다**(그러려면 살아 있는 LiteLLM과 Upstage 키가 필요하다). 채점이 깨지는 쪽이 trace가
덜 자세한 쪽보다 나쁘므로, 확인 전까지 기본으로 켜지 않는다.

끈 상태에서도 판정 호출은 LiteLLM의 `success_callback: ["langfuse"]`로 Langfuse에 남는다.
다만 실험 trace 아래가 아니라 별도 trace로 뜬다.

### 결측 입력

| 상황 | 건너뛰는 지표 | 사유 기록 |
|---|---|---|
| `retrieved_contexts`가 0건 | `faithfulness`, `context_precision`, `context_recall` | `검색 결과 0건이라 채점 불가` |
| `expected_output`이 빈 값 | `context_precision`, `context_recall`, `answer_correctness` | `expected_output(reference) 없음` |

조용히 건너뛰지 않는다. 사유별로 세서 끝에 찍는다(3.2절).
36문항 평가셋은 `ground_truth_snippet`이 전부 채워져 있어 두 번째 경우는 나오지 않는다.

---

# 3. 판정 모델과 실패 처리

## 3.1 판정 모델 선택

| 역할 | 모델 |
|---|---|
| 답변 생성 | `deepseek-chat` 또는 `gpt-4o` (`select_optimal_model`이 질문 길이로 라우팅) |
| **판정** | **`solar-judge`** (`settings.JUDGE_MODEL`) |

판정 모델은 답변 생성에 쓰이는 두 모델 어느 쪽도 아니다. 같은 모델이 답하고 채점하면
자기 답변을 후하게 볼 수 있다. `litellm/config.yaml`에서 `solar-judge`에만 **폴백을 두지
않는다.** 판정자가 조용히 `gpt-4o`로 넘어가면 생성 모델이 자기 답을 채점하게 되어 분리한
의미가 사라진다. 차라리 실패시킨다.

## 3.2 채점 누락 탐지 — `_print_warnings()`

```python
if scored is not None and expected is not None and scored != expected:
    _FAILURES[f"문항 수 불일치: {expected}건 중 {scored}건만 실행됨"] += 1

if not _FAILURES:
    print("\n채점 누락 없음 - 모든 문항이 정상 채점됐습니다.")
    return

total = sum(_FAILURES.values())
print(f"경고: 채점 실패/누락 {total}건. 해당 문항은 평균 계산에서 빠졌으므로")
print("      이 실행의 점수를 다른 실행과 비교하면 안 됩니다.")
for reason, count in _FAILURES.most_common():
    print(f"  - {reason}: {count}건")
```

**세 겹으로 막는다.**

| 방어 | 잡는 것 |
|---|---|
| `_FAILURES` 사유별 집계 | 코드가 아는 실패 (RAGAS 호출 예외, 결측 입력, rag_task 예외, RAGAS 미설치) |
| `_print_metric_table()` 지표별 대조 | 지표 하나만 조용히 덜 채점된 경우 |
| `scored` vs `expected` 개수 대조 | 예상하지 못한 경로로 빠진 문항 |

`_print_metric_table()`은 지표마다 **평균 / 채점 / 대상 / 누락**을 한 줄로 찍는다.
"대상"은 데이터셋에서 미리 센 값이다(`_expected_scorable_counts()`). 평균만 보면 그게
36문항 평균인지 30문항 평균인지 알 수 없기 때문에, 분모를 항상 같이 출력한다.

```
=== RAGAS 지표별 채점 결과 ===
지표                          평균      채점      대상      누락
faithfulness                 -        0      36      36
```

누락이 없을 때도 **"채점 누락 없음"을 명시적으로 출력한다.** 침묵을 정상으로 오해하지 않게
하기 위해서다.

### 이 장치가 만들어진 계기 (2026-09-02)

recency를 켜고 재측정하던 중 판정 모델 호출에서 429(요청 한도 초과)가 5건 발생했다.
재시도가 없어 해당 문항들이 점수 없이 빠졌고, **전체 문항이 아니라 5문항이 빠진 평균이
출력됐다.** 실행은 정상 종료되고 점수도 그럴듯해서 로그를 보지 않으면 알 수 없었다.
(당시는 45문항 평가셋을 쓰던 때다. 그때의 점수는 조건이 달라 비교할 수 없으므로 이 문서에
남기지 않는다. 남는 것은 **장치가 만들어진 경위**뿐이다.)

원인은 평가가 판정 모델을 짧은 시간에 몰아치기 때문이다. 재발 방지는 **게이트웨이 한 곳에서**
처리했다. 호출부마다 재시도를 넣지 않고 `litellm/config.yaml`의 `router_settings`에
`num_retries: 3`, `retry_after: 5`를 뒀다. 같은 이유로 `max_concurrency`를 4로 둔다.

**지표를 5종으로 바꾸면서 판정 호출량이 늘었다.** 이전에는 문항당 자체 판정 2회 + RAGAS 2종,
지금은 RAGAS 5종에 임베딩 호출까지 붙는다. 429가 다시 보이면 `EVAL_MAX_CONCURRENCY`를
낮춰서 돌린다.

## 3.3 실패 원인 진단 — `_print_diagnosis()`

`answer_correctness < 0.7`인 문항만 골라, 나머지 네 지표로 원인을 가른다.

```python
if recall is not None and recall < 0.5:
    verdict = "검색 실패 (정답 근거가 컨텍스트에 없음)"
elif faith is not None and faith < 0.6:
    verdict = "생성 실패 (근거는 있는데 LLM이 근거 없이 답함)"
elif relevancy is not None and relevancy < 0.6:
    verdict = "생성 실패 (충실하지만 질문에 대답하지 않음)"
else:
    verdict = "생성 실패 (근거도 맞고 충실한데 사실이 틀림)"
```

**이전 분기와 달라진 점.** 예전에는 첫 갈래가 `retrieval_hit == 0`, 즉 "라벨링한 문서를
top5에서 못 찾았는가"였다. 지금은 `context_recall`, 즉 "정답 내용이 컨텍스트에 실렸는가"다.
문서를 찾았더라도 **필요한 대목이 컨텍스트에 안 실렸으면** 검색 실패로 잡힌다.
2026-09-16의 첨부파일명 누락이 정확히 그 경우였고, 이전 분기는 그걸 "이해·추론 실패"로
잘못 분류했다.

여전히 **자동 분류를 그대로 믿으면 안 된다.** 임계값(0.5 / 0.6)은 이 저장소가 정한 것이고
RAGAS와 무관하다. 분기는 어느 문항을 먼저 열어볼지 고르는 용도이며, 판정은 실제 컨텍스트를
보고 한다.

---

# 4. 평가 데이터셋

## 4.1 `confluence-rag-qa-36-indexed` (36문항)

원본은 저장소 루트의 `confluence_retrieval_eval_36_indexed.json` **하나뿐이다.**
`dataset_items_36.py`가 이 파일을 읽어 키 이름만 Langfuse item 형태로 바꾸고,
`push_dataset_36.py`가 업로드한다. 파이썬 스냅샷으로 옮겨 적지 않는다. 두 벌이 되면
질문이나 정답 라벨이 한쪽에서만 바뀌어도 알아채지 못한다.

| 원본 필드 | Langfuse item | 쓰는 지표 |
|---|---|---|
| `question` | `input` | 전부 (`user_input`) |
| `ground_truth_snippet` | `expected_output` | `context_precision`, `context_recall`, `answer_correctness` (`reference`) |
| `page_id` | `metadata.expected_doc_ids` | **채점에 안 쓴다.** 실패 문항을 열어볼 때의 단서 |

`page_id`가 채점에서 빠진 것이 이번 교체의 눈에 띄는 변화다. 문서 id 비교로 점수를 내던
`retrieval_hit` / `retrieval_mrr`가 사라졌기 때문이다. 필드는 남겨 둔다. 검색이 무엇을
가져왔는지 대조할 때 여전히 쓴다.

item id는 `qa36-001` 형식으로 고정한다. 여러 번 업로드해도 아이템이 늘지 않고 덮어쓴다.

**구성** (선별 근거는 `confluence_retrieval_eval_36_indexed_notes.md`)

| 축 | 분포 |
|---|---|
| `category` | semantic 8 / keyword 7 / boundary 7 / conversational 6 / attachment 6 / table 2 |
| `answerability` | answerable 28 / not_found 3 / answerable_negative 3 / unsupported 2 |
| 정답 문서 | 18종 (전부 색인 확인됨) |

## 4.2 `expected_output`의 성질 — 인용할 때 주의

`ground_truth_snippet`은 36문항 전부 채워져 있어 reference가 필요한 세 지표 모두 36건
채점된다. 다만 **문항 유형에 따라 그 값이 재는 것이 다르다.**

| 유형 | n | `ground_truth_snippet`의 내용 | 재는 것 |
|---|--:|---|---|
| `answerable` | 28 | 문서 본문에서 뽑은 사실 | 사실이 맞는가 |
| `answerable_negative` | 3 | 금지·제한 사실 | 사실이 맞는가 |
| `not_found` | 3 | "…문서가 존재하지 않음" | 정직하게 거절했는가 |
| `unsupported` | 2 | "…연차 일수는 답변하지 않는다" | 답하지 말아야 할 때 참았는가 |

뒤의 두 유형(5문항)은 사실 서술이 아니라 **동작 서술**이다. 그래서 `answer_correctness`
전체 평균은 "사실 정확도"가 아니라 "사실 정확도와 응답 정책 준수를 섞은 값"이다.
발표에서 단일 수치로 인용하려면 이 점을 함께 말해야 한다.

**이 5문항은 `context_recall` / `context_precision`에도 그대로 들어간다.** reference가
"문서가 존재하지 않음"인데 컨텍스트에서 그 문장을 찾을 수는 없으므로 두 지표는 구조적으로
낮게 나온다. 축별로 나눠 보지 않으면 검색이 나쁜 것처럼 읽힌다. 재측정 때 분리해서 봐야 한다.

## 4.3 평가셋의 출처

**사람이 처음부터 손으로 쓴 평가셋이 아니다.** 원본 55문항 중 50개는 LLM 자동 생성물이고
`attachment` 5개만 색인에서 실제 문서·첨부명을 확인해 작성했다. 이후 실패 문항을 열어보며
라벨 오류를 수정한 기록이 있다. **"자동 생성 후 검증·수정"이 정확한 표현이며,
"수동 검증 평가셋"이라고 하면 과장이다.**

---

# 5. 측정값

## 5.1 현재 상태 — 없다

**RAGAS 5종으로 측정한 값이 아직 없다.** 지표를 교체한 시점(2026-09-16)에 이 저장소에는
실행 결과가 없고, 이 문서는 수치를 지어내지 않는다.

```bash
docker exec rag-ai-server pip install -r /app/requirements-eval.txt
docker exec rag-ai-server python -m evaluation.push_dataset_36
docker exec -e EVAL_RUN_NAME=ragas5-baseline rag-ai-server python -m evaluation.run_qa
```

이 실행이 끝나면 `_print_metric_table()`의 출력을 5.3절에 그대로 옮긴다.
**평균만 옮기지 않는다.** 채점/대상/누락 열을 같이 옮겨야 몇 건 평균인지가 남는다.

## 5.2 이전 수치를 왜 안 가져왔나

`CHANGELOG.md`와 `EVAL_RESULTS_20260916.md`에 남은 이전 run의 점수들
(`answer_correctness 0.839`, `ragas_faithfulness 0.868` 등)은 **여기로 옮기지 않는다.**

| 이전 지표 | 옮기면 안 되는 이유 |
|---|---|
| `answer_faithfulness`, `answer_correctness` | 0장. 정의 없는 프롬프트 출력이다. 대응하는 새 지표와 이름만 같고 계산이 전혀 다르다 |
| `ragas_context_precision` | reference를 안 보는 변형이었다. 새 `context_precision`과 분모가 다르다 |
| `ragas_faithfulness` | **계산은 같다.** 다만 점수 이름이 `faithfulness`로 바뀌었고, 같은 표에 놓으면 나머지 넷도 이어진 값처럼 읽힌다 |
| `retrieval_hit`, `retrieval_mrr` | 대응하는 새 지표가 없다 |
| `answer_relevancy`, `context_recall` | 이전에 존재하지 않았다 |

**`ragas_faithfulness`만 이어 볼 수 있다.** 나머지 넷은 새로 시작한다.
이전 값들은 삭제하지 않고 `CHANGELOG.md`에 이력으로 남겨 둔다. 지운 척하는 것보다
"이 이름의 점수는 이렇게 만들어졌었다"가 남는 편이 낫다.

## 5.3 실행 결과

*(비어 있음 — 5.1절의 명령을 돌린 뒤 채운다)*

| 지표 | 평균 | 채점 | 대상 | 누락 |
|---|--:|--:|--:|--:|
| `faithfulness` | | | 36 | |
| `answer_relevancy` | | | 36 | |
| `context_precision` | | | 36 | |
| `context_recall` | | | 36 | |
| `answer_correctness` | | | 36 | |

---

# 6. 한계와 남은 일

## 6.1 한계

- **아직 한 번도 측정하지 않았다.** 5장. 코드가 도는 것과 지표가 쓸 만한 것은 다른 문제다.
- **36문항 1회 측정은 노이즈를 못 가린다.** 이전 지표에서 두 run을 비교했을 때 ±0.10짜리
  문항 변동이 6건 생겼다. 판정 LLM을 쓰는 이상 새 지표에서도 같은 폭을 예상해야 하고,
  그보다 작은 차이는 개선으로 읽으면 안 된다.
- 축별 문항이 2~8건이라 **category별 절대값은 인용할 수 없다.** 방향만 본다.
- 정답 문서가 18종에 몰려 있다. 문서 단위 편향이 지표에 그대로 남는다.
- **`not_found` / `unsupported` 5문항이 컨텍스트 지표를 구조적으로 끌어내린다** (4.2절).
- 36문항이 전부 단일턴이라 **멀티턴 검색의 이득이 지표로 잡히지 않는다.**
  `run_qa`는 `search_hybrid`를 직접 호출해 `build_search_query`의 대화 이력 반영 경로를
  타지 않는다. 실서비스와 이 한 지점만 다르다.
- 제목 단어를 쓰지 않는 질문을 사람이 직접 쓴 대조군이 없다. 2026-09-16에 v4 paraphrase를
  삭제한 뒤로 "하이브리드가 제목 없는 질문에서 버틴다"는 주장의 근거가 없다. **말할 수 없다.**

## 6.2 남은 일

| 우선순위 | 할 일 | 근거 |
|---|---|---|
| 1 | **RAGAS 5종 기준선 측정** | 5.1절. 이게 없으면 아래 항목의 근거도 없다 |
| 1 | **검색 방식 5종 비교 측정** | 7.8절. "왜 RRF인가"의 근거가 아직 순위 지표뿐이다 |
| 2 | 축별(`answerability`) 분리 집계 | 4.2절. 5문항이 컨텍스트 지표를 끌어내린다 |
| 3 | `unsupported` 문항의 응답 정책 (#35) | 답하면 안 될 때 답한다. 첨부 본문 미색인을 컨텍스트에 적어도 안 바뀐다 |
| 4 | `RECENCY_BOOST_MAX` 유지 여부 결정 | 끄면 #33을 되찾지만 근거가 1문항뿐. 새 지표로 다시 재야 한다 |
| 5 | #11의 1차 후보 회수 개선 (후보 50 밖) | 결합 방식으로는 못 고친다 |
| 6 | #8의 대형 문서 청크 희석 (29청크) | 청킹 전략 과제 |
| 7 | 제목 단어 없는 질문을 사람이 작성 | 6.1절 마지막 항목 |


---

# 7. 검색 방식 비교 — 어느 검색이 더 나은 컨텍스트를 만드나

`ai-server/evaluation/compare_methods.py`

## 7.1 왜 따로 있나

1~5장은 **운영 설정 하나**를 재서 "최종 품질이 어떤가"에 답한다.
이 장은 **검색 방식 5종**을 재서 "왜 이 방식을 골랐나"에 답한다. 질문이 다르다.

| 방식 | |
|---|---|
| `bm25` | BM25 단독 (키워드) |
| `knn` | kNN 단독 (의미) |
| `minmax` | min-max 정규화 후 가중평균 4:6 (운영 이전 방식) |
| `rrf` | RRF (현재 결합 방식), 최신성 가산점 OFF |
| `rrf_recency` | RRF + 최신성 가산점 (운영 설정 그대로) |

## 7.2 왜 RAGAS 지표 중 이 둘만인가

`context_precision`과 `context_recall`만 쓴다. **둘 다 답변(`response`)을 받지 않는다.**
필요한 것은 질문·정답 라벨·검색된 컨텍스트뿐이라, **답변을 한 번도 생성하지 않고** 잰다.

이건 비용 절감이 아니라 **측정 설계**다. 생성 지표(`faithfulness`, `answer_relevancy`,
`answer_correctness`)를 같이 재면 생성기·프롬프트·판정 노이즈가 한 겹 더 끼어들어
"이게 검색 차이인가 생성 차이인가"가 흐려진다. 검색 변수만 분리하려는 것이 이 표의
목적이므로, 생성을 아예 경로에서 뺀다.

생성 지표 3종은 운영 설정 하나에 대해 `run_qa.py`가 잰다. **RAGAS 5종은 두 표에 나눠
전부 등장한다.** 빠지는 지표는 없다.

## 7.3 Hit@5로는 왜 부족한가

`Hit@5`는 정답 문서가 top5에 들었는지만 본다. **함께 딸려온 나머지 네 문서가 전부
무관해도 만점이다.** 그런데 컨텍스트에는 다섯이 다 들어가고, 답변은 그 다섯을 보고
만들어진다. `context_precision`이 그 사각지대를 메운다.

실제로 이 저장소에는 결합 방식을 바꿨을 때 **hit은 그대로인데 상위 5개 구성이 38건 중
33건에서 달라진** 기록이 있다(2026-09-01). 검색 지표 두 개로는 그 차이가 안 보였다.

## 7.4 공정성 — 무엇을 고정했나

| 고정한 것 | 어떻게 |
|---|---|
| 후보 스냅샷 | 질문당 BM25 50청크 + kNN 50청크를 **한 번만** 조회하고 캐시. 모든 방식이 그 위에서 재랭킹만 달리한다 |
| 컨텍스트 조립 | 재랭킹 이후(top_k 문서 선별 → 문서 전체 청크 재조립)는 `search_hybrid`와 **같은 함수** |
| 판정 | `run_qa`와 **같은 `_ascore`**. 판정 모델도 `max_tokens`도 동일 |
| 최신성 가산점 | 기본 비교는 전부 OFF. 운영 설정은 `rrf_recency` 한 행으로만 |
| 실행 | 한 번의 실행 안에서 전부. 판정 모델을 다른 날 부르면 방식 차이인지 판정자 차이인지 구분이 안 된다 |

검색을 **전부 끝낸 뒤에** 채점을 시작한다(2단계). 검색과 채점을 섞어 병렬로 돌리면
후보 캐시에 여러 스레드가 동시에 들어가, "모든 방식이 같은 스냅샷을 봤다"가 설계가
아니라 우연이 된다.

## 7.5 분모를 맞춘다

`Hit@5`/`MRR`은 정답 문서 id가 있어야 계산된다. `page_id`가 빈 `not_found` 문항은
구조적으로 0점이라 검색 지표에서 빠진다. 그런데 RAGAS 지표만 36문항으로 재면
**한 표 안에서 컬럼마다 분모가 달라진다.** 비교표에서 그건 읽는 사람을 속인다.

그래서 주 표는 **`page_id`가 있는 문항으로 통일**하고, 36문항 기준 RAGAS는 참고로
따로 찍는다.

## 7.6 평균만 쓰지 않는다 — 문항 단위 승/패

36문항에서 평균 차이 0.02~0.03은 **아무 말도 못 한다.** 판정 LLM은 실행마다 문항 단위로
±0.10씩 흔들리고 전체 평균으로는 ±0.03 수준이다(6.1절). 그 폭 안의 차이는 노이즈와
구분되지 않는다.

그래서 RRF를 기준으로 **문항 단위 승/패를 센다**(동점 판정 폭 0.05).
"평균이 0.02 높다"는 못 믿어도 **"36문항 중 16건에서 이겼다"는 셀 수 있는 사실**이다.
이 저장소가 검색 비교에서 이미 쓴 방식이다.

## 7.7 실행

```bash
# 먼저 3문항만 돌려 확인한다 (판정 호출이 확 줄어든다)
docker exec -e COMPARE_LIMIT=3 rag-ai-server python -m evaluation.compare_methods

# 전체
docker exec rag-ai-server python -m evaluation.compare_methods
```

산출물: `compare_methods_ragas.csv` / `.json` (문항×방식 단위 원자료 + 요약 + 승패).
콘솔에는 비교 표, 채점 건수, 승패, 실패 사유가 찍힌다.

**이 스크립트는 Langfuse Experiment가 아니다.** 부모 trace가 없으니 채점 span을 만들지
않는다(`disable_eval_spans()`). Langfuse가 설정 안 된 환경에서 호출마다 인증 에러가
찍혀 표를 밀어내는 것을 막기 위함이기도 하다.

## 7.8 결과

*(비어 있음 — 7.7의 명령을 돌린 뒤 채운다. 숫자를 지어내지 않는다.)*

| 방식 | Hit@5 | MRR(top5) | MRR(전체) | context_precision | context_recall |
|---|--:|--:|--:|--:|--:|
| BM25 단독 | | | | | |
| kNN 단독 | | | | | |
| min-max 4:6 | | | | | |
| RRF | | | | | |
| RRF + 최신성 (운영) | | | | | |
