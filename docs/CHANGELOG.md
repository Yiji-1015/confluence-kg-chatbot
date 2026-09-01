# 변경 이력

설계 판단이 필요했던 변경만 근거와 함께 남긴다. 사소한 수정은 git log로 충분하다.

## 2026-09-01

### 세션 소유자 검증 추가

`GET /api/sessions/{id}/messages`와 `DELETE /api/sessions/{id}`에 소유자 확인이 없어,
sessionId만 알면 다른 사용자의 대화를 읽고 삭제할 수 있었다. `GET /api/sessions`도
`userId` 없이 호출하면 전체 사용자의 대화방 목록을 반환했다.

- `ChatService.requireOwnedSession()`을 조회·삭제·기존 세션 이어쓰기 3경로가 공유한다.
- 불일치 시 403이 아닌 404를 반환한다. 403은 "그 대화방은 존재한다"를 알려주기 때문이다.
- `userId`는 브라우저 localStorage의 익명 ID다. 인증이 아니므로 위조 가능하고,
  이 조치는 "URL만 알면 뚫리는" 수준을 막는 것까지다. 실사용자 인증은 별도 과제.

### 검색·생성 파라미터를 설정으로 분리

같은 값이 코드 여러 곳에 흩어져 있어 한쪽만 바꾸면 조용히 어긋났다.
실제로 `top_k`가 `chat.py`와 `run_qa.py`에 따로 박혀 있었고, 결합 가중치는
상수는 3:7인데 주석은 3:2로 적혀 있었다.

전부 `app/config.py`로 모으고 `.env`에서 재빌드 없이 바꿀 수 있게 했다.
재색인은 필요 없다. 색인 데이터는 그대로 두고 검색 단계만 달라지는 값들이다.

| 설정 | 이전 | 현재 | 근거 |
|---|---|---|---|
| `RETRIEVAL_TOP_K` | 3 | 5 | 문서 3편은 근거가 얇다는 판단 |
| `RETRIEVAL_CANDIDATE_SIZE` | `max(top_k*5, 20)` = 20 | 50 | `*5`가 이긴 적 없는 죽은 식이었다. 후보 풀이 좁으면 한쪽 리스트 밖 청크가 0점 처리돼 사실상 탈락한다 |
| `DOC_CONTEXT_MAX_CHARS` | 4000 | 3000 | 문서 수를 늘린 만큼 문서당 길이를 줄임 |
| `HYBRID_BM25_WEIGHT` : `HYBRID_KNN_WEIGHT` | 3 : 7 | 4 : 6 | 아래 참고 |
| `LLM_TEMPERATURE` | 미지정(=모델 기본 1.0) | 0 | 아래 참고 |

컨텍스트 총량은 3×4000=12,000자에서 5×3000=15,000자로 늘었다. 문서를 더 많이,
대신 얕게 보는 방향이다.

#### 결합 가중치 3:7 → 4:6

두 검색기가 각각 후보를 가져온 뒤 chunk_id로 합치고, 한쪽에만 있는 청크는
없는 쪽 점수를 0.0으로 받는다. 그래서 가중치는 "몇 개씩 뽑는가"가 아니라
"한쪽에만 걸린 청크가 얼마나 손해를 보는가"를 정한다.

| 상황 | 3:7 | 4:6 |
|---|---|---|
| BM25 1위 / kNN 후보 밖 | 0.30 | 0.40 |
| kNN 1위 / BM25 후보 밖 | 0.70 | 0.60 |
| 격차 | 2.33배 | 1.50배 |

벡터 우위는 유지하되, 서버명·프로젝트명·날짜처럼 토큰이 정확히 겹치는 질문에서
키워드 매칭이 완전히 깔리지 않도록 조정했다. 과거 5:5에서 3:7로 옮겨온 이력이
있으므로 5:5로 되돌리지 않고 중간 지점을 택했다.

측정으로 확정한 값이 아니다. `run_qa.py`로 3:7과 비교해 `retrieval_hit`을 확인할 것.

#### temperature 0 고정

미지정 시 OpenAI/DeepSeek 기본값은 0이 아니라 1.0이다. 같은 질문·같은 컨텍스트에도
답이 매번 달라져, 평가 점수 차이가 설정 차이인지 노이즈인지 구분할 수 없었다.
RAG는 컨텍스트 충실도가 목적이므로 0으로 고정한다. LLM-judge 호출도 같은 함수를
지나므로 함께 결정적이 된다.

### 평가 파이프라인을 실서비스 경로와 일치시킴

`run_qa.py`가 `chat.py`를 호출하지 않고 같은 파이프라인을 자기가 다시 구현하고 있었다.
이후 `chat.py`에만 변경이 쌓이면서 둘이 벌어졌다.

- `generate_answer()`에 model을 넘기지 않아 **라우팅이 무시되고 항상 `deepseek-chat`으로**
  실행됐다. 12자 미만·1000자 이상 질문은 실서비스에서 `gpt-4o`가 답하는데, 평가는
  그 문항까지 deepseek로 채점하고 있었다. 라우팅의 효과를 측정할 방법 자체가 없었다.
- 컨텍스트 블록에 `(경로: ...)`가 빠져 실서비스와 다른 프롬프트를 측정하고 있었다.

컨텍스트 조립을 `app/llm/prompts.py:build_context_text()` 한 곳으로 모아 양쪽이 같은
함수를 쓰게 했다. 같은 파일 하단에 셀프체크가 있다: `python -m app.llm.prompts`.

### Query Rewrite 스캐폴딩 제거

`rewritten_query = request.query.strip()`가 전부였고, 응답의 `rewrittenQuery`는
원문을 그대로 돌려주는 필드였다. Spring도 읽지 않았다. 구현 시점에 다시 넣기로 하고
Python 스키마와 Java DTO에서 함께 제거했다. 재작성 실험 코드는
`notebooks/02_rag_pipeline_debugger.ipynb`에 남아 있다.

멀티턴에서 "그거 언제였지?" 같은 질문이 그대로 검색어가 되는 문제는 남아 있다.
history는 답변 생성에만 전달되고 검색에는 반영되지 않는다.

### 죽은 코드 제거

- **Redis**: Python에서 한 번도 쓰지 않는데 `requirements.txt`, `config.REDIS_URL`,
  compose의 `depends_on`에 남아 있었다. Redis는 Spring 전용이다.
- **CORS 미들웨어**: ai-server는 Spring만 호출하는 내부 API다. 게다가
  `allow_origins=["*"]` + `allow_credentials=True`는 브라우저가 거부하는 조합이었다.
- **Neo4j 환경변수**: GraphRAG 제거(97bf19e) 때 `.env.example`에 남은 잔재.
- **`evaluation/generate_dataset.py`(v1), `regenerate_failed.py`**: `dataset_items.py`가
  전부 `qa-v2-*` 45문항이라 `regenerate_failed`의 `FAILED_IDS`는 전부 "없는 id"로
  스킵되는 상태였다. `generate_dataset_v2.py`를 정식 이름으로 바꿨다.
- **`findAllByOrderByUpdatedAtDesc()`**: 세션 목록이 `userId` 필수가 되며 호출부가 사라졌다.

### ES 왕복 N+1 제거

`search_hybrid`가 뽑힌 문서마다 `_fetch_full_doc_text`를 따로 호출해, top_k에 비례해
왕복이 늘었다(top_k=5 기준 7회: BM25 1 + kNN 1 + 문서 5).

`terms` 쿼리 하나로 합쳐 3회로 줄였다. `doc_id` -> `chunk_index` 순으로 정렬해서
가져온 뒤 파이썬에서 문서별로 나눈다. 분리 로직은 `_group_chunk_texts()`로 빼서
ES 없이 검증 가능하게 했다: `python -m app.retrieval.es_client`.

LLM 생성이 1~3초라 체감 지연은 아니었다. 실익은 동시 요청 시 스레드풀 압박 완화다.

### 후보 검색에서 벡터 필드 전송 제거

BM25/kNN 검색에 `_source` 필터가 없어 1536차원 `text_vector`까지 딸려왔다.
후보 100청크 기준 요청당 수 MB를 전송·파싱하고 재랭킹 후 그대로 버리는 구조였다.
`RETRIEVAL_CANDIDATE_SIZE`를 20에서 50으로 올리며 이 낭비도 2.5배가 됐다.

재랭킹에 실제로 쓰는 9개 필드만 받도록 바꿨다. 벡터가 프롬프트로 들어간 적은 없고
(컨텍스트 조립은 title/path/text만 사용), 네트워크·파싱·메모리만 낭비하고 있었다.

### 색인 실패 버그: pandas NaN이 ES 문서에 섞임

증분 색인 실행 중 청크 43개가 `document_parsing_exception`으로 실패하는 것을 발견했다.
원인은 `fetch_pages_with_category()`가 계층 레벨 컬럼을 만들 때 해당 레벨이 없으면
`None`을 넣은 것. pandas가 이를 `NaN`(float)으로 바꾸고, 그 값이 `category` 필드로
흘러가 JSON 표준에 없는 `NaN` 토큰으로 직렬화돼 Elasticsearch가 거부했다.

조상이 없는 최상위 문서 9건이 해당됐고, 그 문서들의 청크 43개가 검색에서 빠져 있었다.
`helpers.bulk(raise_on_error=False)`라 경고만 찍히고 지나가서 발견이 늦었다.

`None` 대신 빈 문자열을 넣도록 고쳤다. 수정 후 재색인해 43/43 성공, 총 2,977청크 563문서.

### 색인 로그에 본문 없는 문서 수 표시

증분 색인 대상 66건 중 43청크만 나오는 것을 추적하다, 본문이 없어 청크를 0개 만드는
문서가 57건임을 확인했다. Confluence DB 매크로만 있는 페이지들이다.

ES에 아무것도 안 남으니 매 실행마다 계속 대상으로 잡히지만, 임베딩 호출이 없어
비용은 들지 않는다. 별도 목록을 관리하는 대신 숫자만 로그에 드러냈다.
이 값이 갑자기 늘면 파서가 깨진 신호다.

### 평가 실행 이름에 설정값 포함

`run_experiment(name="confluence-rag-qa")`로 고정돼 있어 Langfuse 목록에서 어떤 설정의
실행인지 구분할 수 없었다. 실제로 과거 실행의 가중치를 코드 주석에 메모해두고 있었다.

`qa-bm25_4-knn_6-top5-cand50-chars3000-temp0-0901-1301` 형태로 바꿔, 목록만 봐도
조건이 읽히게 했다. 끝의 시각은 같은 설정을 반복 실행할 때 이름 충돌을 막는다.

### 설정 기본값에서 회사 고유값 제거

`CONFLUENCE_BASE_URL`, `CONFLUENCE_SPACE_KEY`의 기본값에 실제 회사 주소와 스페이스 키가
박혀 있었다. 저장소를 클론한 사람이 `.env` 없이 실행하면 남의 Confluence를 향하게 된다.
중립적인 값으로 바꿨고, 실제 값은 `.env`가 공급하므로 동작은 그대로다.

### 내부 예외 문자열 노출 차단

`/internal/chat`이 실패하면 `detail=f"...{str(e)}"`로 예외 원문을 그대로 응답에 실었다.
Elasticsearch URL이나 자격 힌트가 샐 수 있어 로그에만 남기고 응답은 고정 문구로 바꿨다.

## 남은 과제

- **min-max 정규화의 절벽**: 각 리스트의 꼴찌가 항상 정확히 0.0이라, 후보 풀 마지막
  순위와 풀 밖이 점수상 구분되지 않는다. 정석은 RRF이나 ES basic 라이선스가
  `retriever.rrf`를 지원하지 않는다. 필요해지면 순위 기반으로 직접 구현할 것.
- **Query Rewrite**: 멀티턴 검색이 깨지는 유일한 근본 원인.
- **파라미터 실측**: 위 표의 값들은 판단으로 정한 것이고 측정으로 확정하지 않았다.
