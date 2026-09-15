# 5. AI 서비스화: LiteLLM·라우팅·멀티턴

[전체 목차](README.md) · 이전: [평가](04-evaluation.md) · 다음: [백엔드·운영](06-backend-operations.md)

## 1. 코드 지도

| 파일 | 역할 |
|---|---|
| [chat.py](../../ai-server/app/api/v1/chat.py) | `/internal/chat` 전체 요청 처리 |
| [schemas/chat.py](../../ai-server/app/schemas/chat.py) | 요청·응답 데이터 형식 |
| [model_router.py](../../ai-server/app/llm/model_router.py) | 최초 답변 모델 선택 |
| [query_builder.py](../../ai-server/app/retrieval/query_builder.py) | 검색용 질문 구성 |
| [litellm_client.py](../../ai-server/app/llm/litellm_client.py) | 임베딩·생성 HTTP 호출 |
| [prompts.py](../../ai-server/app/llm/prompts.py) | Context와 시스템 지시문 |
| [LiteLLM 설정](../../litellm/config.yaml) | 공급자 매핑·재시도·fallback·추적 callback |
| [main.py](../../ai-server/app/main.py) | 서버 시작 warmup·종료 flush |

## 2. 한 질문의 처리 순서

Spring이 sessionId, query, history를 보낸다. FastAPI는 다음 순서로 처리한다.

1. query 양끝 공백을 제거한다.
2. 질문·이력 길이와 명시적 model 값으로 생성 모델을 선택한다.
3. 검색용 질문에 직전 사용자 발화를 붙인다.
4. 검색용 질문을 임베딩하고 하이브리드 검색을 실행한다.
5. 검색 결과에서 출처 DTO와 본문 Context를 만든다.
6. 시스템 지시문, 이전 대화, 현재 Context와 원래 질문을 선택 모델에 보낸다.
7. sessionId, answer, sources를 반환한다.

모델 선택은 검색 전에 일어난다. 검색된 문서 본문 길이는 모델 라우팅의 1,000자 기준에 포함되지 않는다.

## 3. LiteLLM의 위치

앱은 공급자별 주소 대신 LiteLLM의 `/v1/embeddings`와 `/v1/chat/completions`를 부른다. 별칭을 실제 모델과 API key 설정에 연결하는 책임은 YAML에 있다.

| 앱에서 쓰는 이름 | 설정된 공급자 모델 | 용도 |
|---|---|---|
| deepseek-chat | deepseek/deepseek-chat | 기본 답변 |
| gpt-4o | openai/gpt-4o | 규칙에 따른 답변 |
| gpt-4o-mini | openai/gpt-4o-mini | fallback |
| solar-judge | openai/solar-pro4, Upstage API | 평가 판정 |
| embedding-openai | openai/text-embedding-3-small | 임베딩 |

이는 현재 저장소의 매핑이며 공급자의 최신 제품·가격 설명이 아니다. Python 코드가 모델을 선택하는 정책과 LiteLLM이 그 모델 호출을 중계하는 역할을 구분한다.

## 4. 길이 기반 모델 라우팅

`select_optimal_model()`은 위에서부터 처음 맞는 조건을 반환한다.

| 우선순위 | 조건 | 반환 |
|---|---|---|
| 1 | override_model이 있음 | 지정 모델 |
| 2 | trim한 질문 길이 < 12 | gpt-4o |
| 3 | 질문 길이 + 모든 history.content 길이 ≥ 1000 | gpt-4o |
| 4 | 나머지 | deepseek-chat |

12자·1,000자는 토큰 수가 아니라 문자 수다. 이력에는 사용자와 assistant 메시지가 모두 포함된다. 긴 답변이 누적되면 짧지 않은 단순 질문도 gpt-4o로 갈 수 있다.

함수는 선택 사유도 반환해 trace metadata에 넣는다. 이름에 optimal이 있어도 최적 모델을 학습하는 알고리즘이 아니라 수동 규칙이다. “90% 절감” 같은 코드 주석은 이 시스템에서 실측한 비용 절감 성과로 인용하지 않는다.

## 5. 선택 실패 시 fallback

YAML의 라우팅 설정은 다음과 같다.

```text
deepseek-chat 장애 → gpt-4o-mini
gpt-4o 장애        → deepseek-chat
solar-judge       → fallback 없음
embedding-openai  → fallback 없음
```

임베딩은 다른 모델로 조용히 대체하면 저장 벡터와 질문 벡터의 의미 공간이 어긋날 수 있다. 판정 모델은 바뀌면 평가 조건이 달라지므로 fallback을 두지 않았다.

`num_retries=3`, `retry_after=5`, `allowed_fails=10`, `cooldown_time=30`이 설정돼 있다. 실제 fallback 발생 여부와 최종 공급자 모델은 실행 로그로 확인해야 한다. 앱의 선택 모델 카운터는 **처음 선택한 모델**이므로 fallback 후 실제 모델 사용량과 다를 수 있다.

## 6. 멀티턴은 검색과 생성 두 경로에 들어간다

### 검색

`build_search_query()`는 history에서 role=user인 발화만 추려 마지막 `SEARCH_HISTORY_TURNS`개와 현재 질문을 공백으로 연결한다. 기본값은 1, 0이면 비활성이다.

```text
이전 사용자: 휴가 신청 절차 알려줘
현재 사용자: 그건 언제까지야?
검색용 질문: 휴가 신청 절차 알려줘 그건 언제까지야?
```

LLM 재작성 호출은 없다. 이전 assistant 답변에서 핵심어를 추출하지도 않는다. 최근 질문 자체가 “그건?”이면 더 이전 주제가 빠질 수 있고, 새로운 주제로 바뀌면 이전 키워드가 방해될 수 있다.

### 생성

```text
system: 업무 지식은 Context에 근거하라
user: 과거 질문
assistant: 과거 답변
...
user: [Context] 이번에 검색한 문서들 + [질문] 현재 원래 질문
```

생성에는 검색용으로 합친 질문 대신 현재 원래 질문을 사용한다. 이전 답변에 사용했던 문서 본문 전체를 별도 저장해 재전달하는 구조는 아니다. 최근 이력은 [Redis·DB 문서](06-backend-operations.md)에서 설명한다.

## 7. Context와 출처의 차이

`build_context_text()`는 각 문서의 제목, 선택적 경로, 본문을 붙이고 문서 사이를 구분한다. 결과가 없으면 관련 문서를 찾지 못했다는 문자열을 사용한다.

시스템 지시문은 업무 질문은 Context에 근거하고, 대화 기억·인사는 history를 사용하고, DB 임베드 링크만 있으면 상세 행을 모른다고 안내하도록 요구한다. 프롬프트 지시는 사실성을 보증하지 않으므로 평가가 필요하다.

응답 sources는 선택된 검색 문서의 ID·제목·URL·작성자·카테고리·점수다. LLM 답변의 문장마다 실제 인용 근거를 검증한 결과가 아니며, 반환된 5문서를 모두 답변에 사용했다는 의미도 아니다. 점수는 RRF와 최신성 가산 결과이지 신뢰도 확률이 아니다.

## 8. 비동기·타임아웃·출력

임베딩과 생성은 `httpx.AsyncClient`를 사용한다. 네트워크 응답을 기다리는 동안 이벤트 루프가 다른 요청을 처리할 수 있도록 하는 방식이다. 한 요청 안에서는 임베딩 → 검색 → 생성 의존 순서를 따른다.

임베딩 timeout은 30초, 비동기 생성은 60초, 동기 생성은 30초다. Spring의 읽기 timeout까지 있으므로 전체 요청이 각 단계 timeout의 합만큼 항상 기다려주는 것은 아니다.

현재 클라이언트는 매 호출마다 HTTP client를 만들고 닫는다. 생성 응답은 완성된 문자열이며 streaming 설정은 없다. Web UI의 타이핑 효과는 실제 토큰 스트리밍과 구분해야 한다.

## 9. 시작 시 warmup과 종료 처리

`lifespan()`은 시작 시 “warmup”을 임베딩하고 top_k=1로 검색한다. 초기 의존 호출을 미리 수행하려는 목적이며 실패해도 서버 시작은 계속한다. 클라이언트를 계속 재사용하지 않으므로 앱의 영구 연결 풀을 예열해 첫 지연을 완전히 제거했다고 단정할 수 없다.

종료 시 Langfuse flush로 버퍼에 남은 trace 전송을 시도한다. 이 기능은 모델 품질과는 별개인 실행 수명 관리다.
