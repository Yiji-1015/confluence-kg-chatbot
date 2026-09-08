# 변경 이력

설계 판단이 필요했던 변경만 근거와 함께 남긴다. 사소한 수정은 git log로 충분하다.

## 2026-09-08

### 신원을 URL에서 헤더로 (`X-User-Id`)

모든 엔드포인트가 `userId`를 쿼리 파라미터로 받고 있었다. 두 가지가 걸렸다.

- 신원이 URL에 실려 액세스 로그·프록시 로그·브라우저 히스토리에 그대로 남는다.
  삭제 요청까지 `DELETE /api/sessions/{id}?userId=...` 형태였다.
- 컨트롤러와 서비스 시그니처마다 `userId`가 반복되는데, 정작 아무도 빈 값을 검증하지
  않았다. 빈 문자열이 와도 400이 아니라 빈 목록이 200으로 나갔다.

`@CurrentUser` 애너테이션과 `HandlerMethodArgumentResolver`로 `X-User-Id` 헤더에서
받는다. 헤더가 없거나 공백뿐이면 Spring 표준 `MissingRequestHeaderException`을 던진다.
이미 상속해 둔 `ResponseEntityExceptionHandler`가 400 ProblemDetail로 번역하므로
처리기를 새로 만들지 않았다. 검증 구멍도 이걸로 함께 닫힌다.

`ChatRequestDto`에서도 `userId`를 뺐다. 신원은 "무엇을 물었는가"와 다른 층위의 정보이고,
모든 엔드포인트가 공통으로 필요로 한다. 요청 본문마다 실어 나를 이유가 없다.

**API가 바뀐다.** 프론트는 `authHeaders()` 하나로 네 요청 모두에 헤더를 붙이도록 고쳤다.
다른 클라이언트는 없다.

### 대화방 목록 페이징, 대화 내역은 그대로 둔 이유

`getSessions`가 사용자의 대화방을 전부 반환했다. 쌓일수록 응답이 무한정 커지는데
사이드바는 최근 것만 보여준다. `Page<ChatSessionDto>`로 바꾸고
`@PageableDefault(size = 50, sort = "updatedAt", DESC)`를 걸었다.

프론트는 `page.content`를 읽고, `totalElements`가 더 크면 "최근 50개만 표시 (전체 N개)"를
함께 보여준다. **잘린 것을 숨기지 않는다.** 조용히 빠지면 사용자는 대화가 사라진 줄 안다.

`getMessages`는 일부러 페이징하지 않았다. 대화 내역 화면은 대화 전체를 그려야 하는데,
조용히 잘라내면 앞부분이 사라진 것처럼 보인다. 무한 쿼리보다 조용한 누락이 나쁘다.
제대로 하려면 "이전 대화 더 보기" 같은 화면 쪽 작업이 함께 필요하고, 그건 남겨둔다.

같이 고친 것: `loadRecentHistory`가 대화 전체를 읽어 메모리에서 마지막 10개만 잘라내고
있었다. 캐시가 만료될 때마다 세션 전체를 스캔하는 셈이었다. 필요한 만큼만 DB에서
가져온다. 정렬에 `createdAt`뿐 아니라 `id`를 함께 쓴다. `createdAt`은 애플리케이션이
`LocalDateTime.now()`로 넣기 때문에 같은 턴의 질문과 답변이 같은 값을 가질 수 있고,
그러면 순서가 실행할 때마다 달라져 대화가 뒤집혀 보인다.

### 스키마 소유권을 Hibernate에서 Flyway로

`ddl-auto: update`는 무엇이 언제 왜 바뀌었는지 기록을 남기지 않는다. 컬럼 삭제나
타입 변경은 조용히 건너뛰고, 되돌릴 방법도 없다. 운영에 나가기 전에 정리해야 할 항목이었다.

`spring-boot-starter-flyway` + `V1__init_chat_schema.sql`, `ddl-auto: validate`로 바꿨다.
validate는 엔티티와 실제 테이블이 어긋나면 기동 자체를 막는다.

기존 개발 DB에는 `ddl-auto: update`가 만들어 둔 테이블이 이미 있어서 그냥 켜면 멈춘다.

```
Found non-empty schema(s) "public" but no schema history table.
```

`baseline-on-migrate: true` + **`baseline-version: 0`** 으로 풀었다. 여기서 0이 핵심이다.
기본값 1이면 V1이 "이미 적용된 것"으로 간주돼 건너뛰고, V1이 함께 만드는 인덱스가
기존 DB에는 생기지 않는다. 0이면 V1이 실제로 실행되는데, 테이블은 `IF NOT EXISTS`라
그대로 두고 인덱스만 새로 생긴다. 새 DB에서는 스키마가 비어 있으므로 이 설정과 무관하게
V1부터 정상 실행된다.

없던 인덱스 두 개를 V1에 같이 넣었다.

- `(user_id, updated_at DESC)` — 사이드바의 페이지 조회
- `(session_id, created_at, message_id)` — 대화 조회와 최근 맥락 조회.
  FK 컬럼에 인덱스가 없으면 대화방 삭제 시 자식 테이블을 전부 훑는다.

### `save(새 세션)`이 내던 여분의 SELECT 제거

`ChatSession`은 `@Id`가 직접 할당된 String이고 `@Version`이 없다. 그래서 Spring Data의
`isNew()`가 `id != null`만 보고 "이미 존재하는 엔티티"라고 판단했고, `save()`가
`persist`가 아니라 `merge`로 갔다. merge는 존재 확인을 위해 INSERT 전에 SELECT를 한 번
더 낸다. 대화방을 만들 때마다 쓸모없는 조회가 하나씩 나가고 있었다.

`Persistable<String>`을 구현하고 `@Transient boolean isNew`를 `@PostPersist`/`@PostLoad`에서
내린다. 이제 `persist`로 가서 INSERT만 나간다.


### 전역 예외 처리기 도입 (에러 응답을 ProblemDetail로 통일)

지금까지 실패 응답의 형식이 세 갈래였다. `@Valid` 실패는 Boot 기본 에러 JSON,
`ResponseStatusException`은 또 다른 형태, AI 호출 실패는 아예 200 OK. 프론트가
읽을 수 있는 공통 계약이 없어서 `서버 오류 (400)`처럼 상태 코드만 보여주고 있었다.
그 결과 `ChatRequestDto`에 적어둔 "질문은 최대 1000자까지 입력 가능합니다."가
어디에도 도달하지 못했다.

`@RestControllerAdvice` + `ResponseEntityExceptionHandler` 상속으로 한 곳에 모으고,
응답 형식을 RFC 9457 `ProblemDetail`(`application/problem+json`)로 통일했다.

- 상속을 택한 이유: 본문 파싱 실패, 필수 파라미터 누락, 허용되지 않은 메서드 같은
  Spring MVC 표준 예외 처리가 이미 들어 있다. 직접 `@ExceptionHandler`를 나열하면
  같은 것을 다시 만들면서 빠뜨리게 된다. 실제로 오버라이드한 것은
  `handleMethodArgumentNotValid` 하나뿐이다.
- 검증 실패는 첫 번째 필드 메시지를 `detail`에, 필드별 전체를 `errors`에 담는다.
  `detail`은 사용자에게 그대로 보여줄 문장이고, `errors`는 폼 단위로 표시할 때 쓴다.
- 도메인 예외(`SessionNotFoundException`, `AiEngineException`)는 웹 타입을 참조하지
  않는다. 상태 코드로의 번역은 처리기 한 곳에서만 한다. 서비스가
  `ResponseStatusException`을 import하던 계층 역전(BACKEND_REVIEW 2번)을 풀기 위한
  자리를 먼저 만들어둔 것이다.
- `AiEngineException`은 502로 매핑하되 `ex.getMessage()`를 응답에 싣지 않는다.
  원인 예외 메시지에는 내부 AI 서버 URL과 예외 클래스명이 들어 있다. 원인은 로그로만.

프론트(`static/index.html`)도 실패 시 `problem.detail`을 읽도록 고쳤다. 서버만 고치면
메시지는 여전히 화면에 닿지 않는다.

### 서비스 계층에서 웹 예외 제거

`ChatService`가 `ResponseStatusException`을 던지고 있었다. 서비스가 HTTP 상태 코드를
알고 있다는 뜻이고, 계층이 역전된 상태였다. 같은 서비스를 배치나 다른 진입점에서 쓰면
그 자리에서 웹 예외가 튀어나온다.

`SessionNotFoundException`으로 바꿨다. 도메인 예외는 "이 대화방 없음"까지만 말하고,
404라는 판단은 `GlobalExceptionHandler`가 한다. 예외 클래스에 `@ResponseStatus`를 붙이는
방법도 있지만 쓰지 않았다. 그러면 상태 코드를 아는 주체가 도메인으로 다시 내려와,
같은 역전을 이름만 바꿔 유지하게 된다.

소유자 불일치를 403이 아닌 404로 답하는 판단은 그대로 유지했다(근거는 아래 2026-09-01).
바뀐 것은 그 판단이 놓인 위치뿐이다.

같이 고친 프론트 두 곳:

- `selectSession`이 `res.ok`를 확인하지 않고 바로 `messages.forEach`를 돌고 있었다.
  실패 본문은 배열이 아니라 ProblemDetail이라 "대화 불러오기 실패:
  messages.forEach is not a function"이라는 엉뚱한 문구가 뜬다.
- `deleteSession`은 응답을 확인조차 하지 않았다. 남의 대화방 삭제 시도가 404로 거절돼도
  사용자 화면에서는 성공한 것처럼 보였다. 소유자 검증을 넣어둔 의미가 없어지는 지점이다.
- `loadSessions`는 `if (!res.ok) return;`으로 실패를 조용히 삼켰다. 목록이 그냥 비어 보여서
  사용자는 대화가 사라진 줄 안다. 사이드바에 이유를 표시하도록 고쳤다.

이로써 프론트의 fetch 4곳이 모두 같은 계약(`problem.detail`)을 읽는다.

### AI 엔진 실패를 답변으로 위장하지 않는다

`AiEngineClient`가 `catch (Exception e)`로 모든 실패를 삼키고, 에러 문구를 `answer`에
담아 정상 응답처럼 반환하고 있었다. 200 OK로 나갔다. 문제가 세 갈래로 번졌다.

- 그 문구가 ASSISTANT 메시지로 PostgreSQL에 영구 저장되고 Redis 히스토리에도 들어갔다.
  다음 턴에 "AI 서버와 통신할 수 없습니다"가 LLM 컨텍스트로 전달된다.
  대화 이력이 오염되고, 그 오염은 대화가 이어지는 동안 계속 따라다닌다.
- `e.getMessage()`를 사용자 응답에 붙여 내부 AI 서버 URL과 예외 클래스명이 노출됐다.
- 상태 코드가 200이라 모니터링에서도 실패로 집계되지 않았다.

`createFallbackResponse`를 삭제하고 `AiEngineException`을 던진다. 처리기가 502로 매핑하고,
원인은 로그에만 남는다.

`catch (Exception)`을 `catch (RestClientException)`으로 좁혔다. RestClient가 내는 실패는
연결 거부·타임아웃·역직렬화 오류까지 모두 그 아래에 있다. 그 밖의 예외(요청 조립 중
NPE 등)는 AI 엔진 장애가 아니므로 502로 뭉뚱그리지 않고 500으로 내보낸다.

부수 효과: 이제 AI 호출이 실패하면 아무것도 저장되지 않는다. 실패한 턴이 흔적을 남기지
않는 쪽이 맞다고 봤다. 아래 트랜잭션 경계 변경에서도 이 성질은 유지된다
(AI 호출 전에는 DB에 쓰지 않고, 질문과 답변을 한 트랜잭션에 함께 저장한다).

기존 오염 데이터는 정리할 것이 없었다. `chatbot_db`의 `chat_messages`와 `chat_sessions`가
모두 0행이라 조회만 하고 DELETE는 실행하지 않았다.

### 트랜잭션 경계를 DB 작업 구간으로 좁힘

`processChat` 전체가 하나의 `@Transactional`이었고 그 안에서 AI 서버를 호출했다.
AI 호출은 최대 60초다. 그동안 DB 커넥션 하나가 아무 일도 하지 않으면서 묶여 있었다.
HikariCP 기본 풀은 10개, 커넥션 대기 타임아웃은 30초다. 동시 사용자 11명이면 11번째부터
30초를 기다리다 `SQLTransientConnectionException`을 받는다. 톰캣 스레드는 200개까지
요청을 받아들이므로, AI 서버가 느려지는 순간 그 여파가 커넥션 고갈로 번져 채팅과 무관한
대화방 목록 조회까지 같이 죽는다. 부하에서 가장 먼저 무너질 지점이었다.

`ChatPersistenceService`를 새로 만들어 DB 접근을 전부 옮겼다. `ChatService`에는
`@Transactional`이 하나도 없고 순서만 정한다.

별도 빈으로 나눈 것은 취향이 아니라 필요다. `@Transactional`은 프록시로 동작해서
같은 클래스 안에서 `this.method()`로 부르면 프록시를 지나지 않아 트랜잭션이 아예 걸리지
않는다(self-invocation). 어노테이션은 그대로인데 동작만 조용히 사라지므로 눈에 띄지 않는다.

순서는 이렇게 정했다.

```
processChat (트랜잭션 없음)
 ├─ canContinue(...)   @Transactional(readOnly)  ← 소유권 확인. 없으면 서버가 새 ID 발급
 ├─ Redis 히스토리 조회, 미스면 loadRecentHistory(...)  @Transactional(readOnly)
 ├─ aiEngineClient.requestChat(...)               ← 트랜잭션 밖
 └─ saveTurn(...)      @Transactional  ← 세션 + USER + ASSISTANT 한 번에
    그 다음 redisSessionService.saveTurn(...)     ← 트랜잭션 밖
```

두 가지를 지키느라 이 순서가 됐다.

- 소유권 확인이 Redis 히스토리 읽기보다 **먼저**여야 한다. 뒤집히면 남의 sessionId를
  넣어 그 사람의 대화 맥락을 LLM 컨텍스트로 받아볼 수 있다.
- USER 메시지 저장은 히스토리 조회보다 **나중**이어야 한다. 앞으로 당기면 방금 저장한
  질문이 자기 자신의 맥락으로 다시 들어간다.

DB를 먼저 쓰고 Redis를 나중에 쓴다. Redis는 JPA 트랜잭션에 참여하지 않으므로 순서가
반대면 DB가 롤백돼도 Redis에는 남아 다음 턴 컨텍스트가 오염된다. 대화방 삭제도 같은
이유로 DB 커밋 뒤에 Redis를 지운다.

대가로 `findById`가 두 번 나간다. 밀리초짜리 조회 두 번과 60초 커넥션 점유를 맞바꿨다.

`chat_history_cache` 지표의 의미가 달라졌다. miss가 이어쓰는 대화에서만 올라간다.
예전에는 새 대화방도 무조건 miss로 집계돼 비율이 부풀려져 있었다.

### Jackson 2 제거 (Boot 4의 기본은 Jackson 3)

`RedisConfig`가 Jackson 2 `ObjectMapper` 빈을 등록하고 `JavaTimeModule`을 붙이고 있었다.
그런데 Boot 4의 JSON 기본은 Jackson 3(`tools.jackson`)라 그 빈은 웹 계층에 닿지 못했다.
API 응답의 `LocalDateTime`이 제대로 나온 것은 Jackson 3가 java.time을 기본 내장하기
때문이지 이 설정 덕분이 아니었다. 즉 설정은 있는데 효과는 없고, 읽는 사람은 있다고 믿는
상태였다.

빈과 의존성 두 줄을 지우고 `ChatMapper`·`RedisSessionService`를 Jackson 3로 옮겼다.
Jackson 3는 `JsonMapper extends ObjectMapper`라 Boot가 자동 구성한 빈이 그대로 주입된다.
예외가 unchecked로 바뀌었으므로 `catch (Exception)`을 `catch (JacksonException)`으로 좁혔다.

### RestClient 타임아웃을 Boot 관례 자리로 (그리고 그 과정에서 만난 함정)

`AiClientConfig`가 `SimpleClientHttpRequestFactory`를 직접 만들어 타임아웃을 넣고 있었다.
Boot 4에서 그 자리는 `spring.http.clients.*` 속성이고, 팩토리를 직접 지정하면 그 속성이
무시된다. 속성으로 옮기고 `AiClientConfig`는 `baseUrl`만 잡게 했다.
(`spring.http.client.*` 단수형은 Boot 4.0에서 이미 폐기됐다. 복수형이 정식이다.)

**그랬더니 AI 호출이 전부 422로 실패했다.** ai-server가 "본문 없음"을 받았다.

```
{"detail":[{"type":"missing","loc":["body"],"msg":"Field required","input":null}]}
```

`requestFactory()`를 떼는 것은 타임아웃 설정만 넘기는 게 아니라 **전송 구현 선택까지**
Boot에 넘기는 일이었다. Boot 4가 고른 것은 JDK `HttpClient`이고, 이건 HTTP/2가 기본이라
평문 연결에서 h2c 업그레이드를 시도한다(`Connection: Upgrade`, `HTTP2-Settings`).
ai-server의 uvicorn(h11)은 HTTP/1.1 전용이라 업그레이드를 거절하는데, 그 과정에서 본문이
사라진다. uvicorn 로그에는 `Unsupported upgrade request`가 남는다.

`spring.http.clients.imperative.factory: simple`로 HTTP/1.1 전송을 명시했다.
`AiEngineRequestTransportTest`가 나가는 요청에 `Upgrade`/`HTTP2-Settings` 헤더가 없는지
확인한다. 일부러 `jdk`로 되돌려 이 테스트가 실제로 실패하는 것까지 확인했다.

원래 코드가 팩토리를 박아둔 것은 관례 위반이 맞았지만, 그 부작용으로 전송이 HTTP/1.1에
고정돼 있어 이 문제가 드러나지 않고 있었다.

### DTO를 record로, 대화방 ID 발급권을 서버로

DTO 6종이 `@Getter/@Builder/@NoArgsConstructor/@AllArgsConstructor` 4종 세트를 달고
있었다. `@NoArgsConstructor` 때문에 불변도 아니었다. Java 21 `record`로 바꿨다.
생성 지점이 많은 것들은 Lombok `@Builder`를 남겨 호출부를 그대로 뒀다.
Jackson 3와 Hibernate Validator 모두 record를 그대로 처리한다.

`@CrossOrigin(origins = "*")`도 지웠다. 프론트가 같은 8080에서 서빙되는 동일 출처라
애초에 필요 없었고, 인증 쿠키를 붙이는 순간 `allowCredentials`와 충돌하는 설정이었다.

동작이 하나 바뀌었다. 존재하지 않는 `sessionId`를 보내면 예전에는 **클라이언트가 준 ID로**
대화방을 만들어줬다. 서버가 ID 발급을 통제하지 못하는 구조다. 이제는 그 ID를 쓰지 않고
서버가 새로 발급한다. 404로 거절하는 선택지도 있었지만, 그러면 localStorage에 오래된 ID가
남은 사용자가 대화를 시작하지 못한다. 응답의 `sessionId`를 프론트가 갱신하므로 끊김은 없다.

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

### 평가 지표 확장: MRR, RAGAS

기준선 실행 후 실패 문항을 열어보다, `retrieval_hit`만으로는 볼 수 없는 것이 많다는 걸 확인했다.

**MRR 추가.** `retrieval_hit`은 정답이 상위 top_k 안에 있기만 하면 1점이라 1등과 5등을
구분하지 못한다. 실제로 결합 방식 4가지를 비교했더니 hit은 전부 0.974로 같았지만
MRR은 0.890~0.934로 갈렸다. 기존 라벨(`expected_doc_ids`)을 그대로 쓰므로 추가 비용이 없다.

**RAGAS 도입 (`context_precision`, `faithfulness`).** hit도 MRR도 "정답으로 라벨링한 문서
1개"만 본다. 함께 검색된 나머지 문서들의 품질은 어떤 지표도 보지 않고 있었는데,
결합 방식을 바꾸면 상위 5개 구성이 38건 중 33건에서 달라진다. `context_precision`이
그 사각지대를 메운다 (정답 라벨 불필요, 순위 가중).

`ragas_faithfulness`는 직접 구현한 `answer_faithfulness`와 나란히 기록해 교차 검증한다.
자체 판정 기준이 표준 지표와 어긋나지 않는지 확인하기 위함이며, 기존 지표는 걷어내지 않았다
(실패 원인 진단 로직이 자체 지표를 사용한다).

판정 LLM은 양쪽 모두 LiteLLM 게이트웨이의 `JUDGE_MODEL`을 쓴다. 다른 모델로 채점하면
지표 차이인지 모델 차이인지 구분할 수 없다.

**의존성 분리.** ragas는 langchain / langgraph / datasets 등 50개+ 패키지를 끌고 오므로
`requirements-eval.txt`로 분리해 서빙 이미지를 그대로 유지했다. ragas 0.4.3이
`langchain-community`를 버전 제한 없이 선언하는데 0.4.x에서 제거된 모듈을 참조해
import가 깨지므로 `langchain-community<0.4` 고정이 필요하다.

### 검색 결합 방식 4종 실측 비교

남은 검색 실패 1건(`qa-v2-021`)의 원인을 토큰 단위까지 추적한 뒤, 개선안을 비교했다.

원인은 네 겹이었다.
1. Nori가 `20260303`을 단일 토큰으로 잘라 질문의 `2026/년/3/월/일`과 한 토큰도 겹치지 않음
2. 색인 시 날짜를 펼치면 매칭은 되지만 `년/월/일`은 모든 날짜 문서에 흔해 IDF가 낮음
3. 후보 풀 50칸을 한 문서의 청크들이 독점 (문서 단위 dedup은 이미 늦은 시점)
4. `multi_match`의 `best_fields`가 최고 필드 점수만 채택해 `title_search` 기여분을 버림

| 방식 | hit@5 | MRR |
|---|---|---|
| best_fields + min-max 4:6 (현재) | 0.974 | 0.908 |
| tie_breaker=0.3 + min-max | 0.974 | 0.908 |
| most_fields + min-max | 0.974 | 0.890 |

필드 결합 방식을 바꿔도 `hit@5`는 전혀 움직이지 않고 MRR만 소폭 갈렸다. 38문항에서
1~2건 차이라 표본이 작아 우열을 확정할 수 없다고 보고 현재 방식을 유지했다.

남은 실패 1건은 질문 자체가 문서를 특정하지 못한다("LLOYDK 팀의 3월 3일 주간미팅" —
LLOYDK는 팀명이 아닌 스페이스명이고 같은 날짜 회의가 5건). 이 한 문항에 맞춰 전역
스코어링을 바꾸는 것은 과적합이라고 판단했다.

### 최신 문서 가산점 (기본 비활성)

후보를 `updated_at` 기준 5분위로 묶어 최신 그룹부터 0.04/0.03/0.02/0.01/0을 더하는
`RECENCY_BOOST_MAX`를 구현했다. 측정 결과 `retrieval_hit`은 전혀 변하지 않았고,
상위 5개 구성은 40건 중 10건에서 바뀌었는데 **전부 5순위 한 칸만 교체**됐다.
관련도 상위는 흔들지 않고 동점 근처에서만 작동한다는 설계 의도대로다.
정답 문서를 밀어낸 사례는 0건.

`retrieval_hit`을 올리지는 않았지만 **해를 끼치지도 않는다**는 것이 확인돼 기본값 0.04로
활성화한다. 사내 문서에서 최신본을 우선하는 것은 그 자체로 타당한 기본 동작이고,
동점 구간에서만 작동하도록 크기를 제한했기 때문이다.

`updated_at`은 Confluence 최종 수정 시각이라 "문서 내용의 날짜"와 다르다. 오래된
회의록에 오타 하나만 고쳐도 최신이 되므로, 최신성 신호로서 노이즈가 있다는 점은
한계로 남는다. 특정 날짜를 지정한 질문에서 역효과가 날 수 있어 가산점을 크게 두지 않았다.

검색 결과를 바꾸는 설정이므로 Langfuse 실행 이름에도 포함시켰다.

### 기준선 측정 결과 (2026-09-01)

모든 조건을 정리한 뒤의 첫 정식 기준선. Langfuse 실행 이름에 설정이 전부 담긴다:
`qa-bm25_4-knn_6-top5-cand50-chars3000-temp0-judge_solar-judge-...`

| 지표 | 값 | 라벨 필요 |
|---|---|---|
| `retrieval_hit` | 0.974 | 정답 문서 |
| `retrieval_mrr` | 0.908 | 정답 문서 |
| `answer_faithfulness` (직접 구현) | 0.951 | 불필요 |
| `ragas_faithfulness` (표준) | **0.838** | 불필요 |
| `answer_correctness` | 0.970 | 기대 답변 |
| `ragas_context_precision` | 0.835 | 불필요 |

**교차 검증에서 드러난 것: 자체 faithfulness가 표준보다 0.113 후하게 채점한다.**
RAGAS는 답변을 문장 단위로 쪼개 각 문장이 컨텍스트에 근거하는지 따로 판정하는데,
자체 구현은 답변 전체를 한 번에 보기 때문에 일부 문장이 근거 없어도 전체적으로
"근거함"으로 판정될 여지가 있다. 자체 지표만 봤다면 실제보다 좋게 평가하고 있었다.

`ragas_context_precision` 0.835는 검색된 문서 중 약 16%가 답변에 쓸모없었다는 뜻이다.
`RETRIEVAL_TOP_K=5`가 다소 넉넉한지 판단할 근거가 되며, 3과 5를 비교해볼 만하다.

이전 실행(오전, 데이터셋 정정 전)의 0.923 / 0.947 / 0.953과는 조건이 달라 직접 비교할 수 없다.

이 값은 `recency=0` 기준이다. 이후 recency를 0.04로 켜면서 `context_precision`이
0.800으로 낮아졌다 (아래 비교 참고).

### 채점 누락이 조용히 평균을 왜곡하던 문제 (2026-09-02)

recency를 켜고 재측정했더니 판정 모델 호출에서 429가 5건 발생했다. 재시도가 없어
해당 문항들은 `correctness=None`으로 빠졌고, **45문항이 아니라 40문항 평균이 출력됐다.**
실행은 정상 종료되고 점수도 그럴듯해서 로그를 보지 않으면 알 수 없다.

원인은 평가가 판정 모델을 짧은 시간에 몰아치기 때문이다. 자체 판정기 2종에
RAGAS의 내부 병렬 호출이 겹치면서 분당 한도를 넘겼다.

두 가지를 고쳤다.

- **게이트웨이에서 재시도**: `litellm/config.yaml`의 `router_settings`에
  `num_retries: 3`, `retry_after: 5`. 429는 잠시 뒤 재시도하면 대개 통과하므로
  호출부마다 재시도를 넣지 않고 게이트웨이 한 곳에서 처리한다.
- **실패를 크게 알리기**: 채점 실패를 사유별로 집계해 실행 끝에 출력한다.
  누락이 있으면 "이 실행의 점수를 다른 실행과 비교하면 안 된다"고 명시한다.
  없으면 "채점 누락 없음"을 찍어, 침묵을 정상으로 오해하지 않게 한다.

이 문제로 recency 활성화 후의 첫 두 측정값은 누락 때문인지 recency 때문인지
구분할 수 없어 폐기했다. 세 번째 실행에서 429가 사라져 비교 가능한 값을 얻었다.

### recency 켜기 전/후 비교 (2026-09-02)

| 지표 | recency 0 | recency 0.04 | 차이 |
|---|---|---|---|
| `retrieval_hit` | 0.974 | 0.974 | — |
| `retrieval_mrr` | 0.908 | 0.908 | — |
| `answer_faithfulness` | 0.951 | 0.989 | +0.038 |
| `ragas_faithfulness` | 0.838 | 0.861 | +0.023 |
| `answer_correctness` | 0.970 | 0.966 | -0.004 |
| `ragas_context_precision` | 0.835 | **0.800** | **-0.035** |

검색 지표는 완전히 동일하다. 정답 문서를 찾는 능력과 순위를 recency가 전혀 건드리지
않았다는 뜻이고, 사전 측정에서 "바뀌는 것은 항상 5순위 한 칸"이라고 확인한 것과 일치한다.

일관된 신호는 `context_precision` -0.035 하나다. 그리고 이는 예상되는 방향이다.
recency는 관련성이 아니라 최신성으로 문서를 밀어 올리므로, 5순위에 들어온 최신 문서가
질문과 덜 관련될 수 있다. 지표가 그 대가를 정확히 잡아냈다.

faithfulness 상승은 해석하지 않는다. 45문항 1회 측정에서 이 정도 변동은 판정 모델의
흔들림과 구분되지 않는다.

**결론: 켠 상태를 유지하되 이득이 측정되지 않았음을 명시한다.** 유지하는 근거는 지표가
아니라 "사내 문서에서 최신본을 우선하는 것이 타당한 기본 동작"이라는 제품 판단이며,
그 대가로 검색 관련성을 0.035 지불하고 있다. 이 값이 커지면 재검토한다.

### 남은 채점 실패

`ragas_faithfulness`의 `IncompleteOutputException`이 실행마다 1건씩 나온다. 429가 아니라
판정 모델이 구조화 출력을 완성하지 못하고 잘리는 경우다. 특정 문항의 답변이 길어서로
추정되며, 1건이라 평균 영향은 작지만 원인은 확인되지 않았다.

## Elasticsearch 별칭 연결 (2026-09-02)

`docs/ELASTICSEARCH.md`는 read/write alias를 `confluence-current`로 적어두었으나
실제로는 별칭이 존재하지 않았고 코드가 실제 인덱스명을 직접 참조하고 있었다.
문서에 설계만 있고 구현이 없는 상태였다.

별칭을 만들고 코드가 별칭만 부르도록 바꿨다. 이제 임베딩 모델이나 차원이 바뀌어
전체 재색인이 필요할 때, 새 버전 인덱스를 채워둔 뒤 별칭만 옮기면 된다.
재색인하는 동안 검색이 멈추지 않고, 문제가 생기면 별칭을 되돌리는 것만으로 롤백된다.
별칭이 없으면 같은 작업에 `.env` 수정과 컨테이너 재생성이 필요하고 그 사이 검색이 끊긴다.

인덱스 생성 함수도 함께 고쳤다. 별칭 이름으로 인덱스를 만들어버리면 갈아끼울 이름표가
사라져 무중단 경로가 막히므로, 실제 인덱스를 만든 뒤 별칭을 붙인다.

일상적인 증분 색인(수정일 기준)은 원래도 동작했다. 이 변경이 여는 것은 전체 재구축 경로다.

## RETRIEVAL_TOP_K 3 vs 5 측정 (2026-09-02)

`context_precision` 0.800은 컨텍스트의 약 20%가 답변에 쓰이지 않았음을 뜻하므로,
문서 수를 줄이면 오를 것으로 보고 재봤다.

| 지표 | top5 | top3 | 차이 |
|---|---|---|---|
| `retrieval_hit` | 0.974 | 0.974 | — |
| `retrieval_mrr` | 0.908 | 0.908 | — |
| `ragas_context_precision` | 0.800 | 0.844 | +0.044 |
| `answer_correctness` | 0.966 | 0.971 | +0.005 |
| `answer_faithfulness` | 0.989 | 0.953 | -0.036 |
| `ragas_faithfulness` | 0.861 | 0.793 | -0.068 |

예상대로 관련성은 올랐으나 충실성이 더 크게 떨어졌다. 방향은 둘 다 설명이 된다.
문서를 줄이면 컨텍스트는 깨끗해지지만 답변을 뒷받침할 근거도 함께 줄어든다.
최종 품질(`correctness`)은 노이즈 범위 안에서 동일하다.

**결론: top5 유지.** 충실성 하락이 관련성 상승보다 크고, 사내 문서 챗봇에서는
환각을 막는 쪽이 우선이다. correctness가 같으므로 바꿀 이유가 없다.

### 덤으로 확인된 것

`hit`과 `MRR`이 완전히 동일하다. 정답 문서가 4~5위였던 경우가 한 번도 없다는 뜻이다.
4~5위는 순수한 배경 정보였고 그것이 충실성을 받치고 있었다.

이는 `top_k`가 이 시스템에서 recall 손잡이가 아님을 보여준다. 후보는 `CANDIDATE_SIZE`
50개씩 이미 확보되고 `top_k`는 재랭킹 후 컨텍스트 예산을 정하는 값이라, 늘려도
검색이 더 찾아내지 않는다. recall을 올리려면 `CANDIDATE_SIZE`를 건드려야 하는데
남은 미스 1건(`qa-v2-021`)이 질문 품질 문제라 올릴 여지가 없다.

## 멀티턴 검색에 대화 이력 반영 (2026-09-02)

사용자는 앞 대화를 이어 "그거 며칠 전까지 내야 해?"처럼 묻는데, 이 문장에는 주제어가
없어 검색이 엉뚱한 문서를 가져왔다. 대화 이력이 답변 생성에는 전달되지만 검색에는
반영되지 않아, 멀티턴에서 검색이 조용히 빗나가는 구조였다.

직전 사용자 발화를 검색어 앞에 붙여 주제어를 복원한다(`SEARCH_HISTORY_TURNS`, 기본 1).
답변 생성에는 원본 질문을 그대로 쓰고 검색어만 바꾼다.

| 검색어 | 1위 문서 |
|---|---|
| `그거 며칠 전까지 내야 해?` | 20260713_리더주간미팅 |
| `연차 신청 절차 알려줘 그거 며칠 전까지 내야 해?` | 회사 연차 규정이 궁금해요! |

LLM으로 질의를 재작성하는 방식이 더 정확하지만 호출이 한 번 늘고 지연도 늘어난다.
붙이기만 해도 BM25는 주제어 토큰을, 임베딩은 주제 벡터를 되찾으므로 먼저 이쪽을 썼다.

### 한계

- **주제 전환에 약하다.** 앞 대화와 무관한 새 질문에도 직전 발화가 붙어 노이즈가 된다.
  `turns=1`이라 영향이 제한적이고, 새 질문 자체의 토큰이 대부분을 차지해 상위 순위는
  대체로 유지된다. 문제가 관측되면 LLM 재작성으로 올린다.
- **평가셋으로 검증되지 않는다.** 45문항이 전부 단일턴이라 이력이 없고,
  `run_qa`는 `search_hybrid`를 직접 호출해 이 경로를 타지 않는다. 따라서 기존 지표에
  영향이 없고, 동시에 이 변경의 이득도 지표로 잡히지 않는다. 멀티턴 문항을 넣기 전까지
  위 표의 사례 비교가 유일한 근거다.

## 남은 과제

- **min-max 정규화의 절벽**: 각 리스트의 꼴찌가 항상 정확히 0.0이라, 후보 풀 마지막
  순위와 풀 밖이 점수상 구분되지 않는다. 결합은 `_normalize_scores`(chunk_id -> 0~1)
  하나에 격리돼 있어 다른 방식으로 교체하려면 이 함수만 바꾸면 된다.

  **주의 - 결합 방식을 바꾸면 `RECENCY_BOOST_MAX`를 반드시 다시 잡아야 한다.** 지금
  가산점 0.04는 결합 점수 범위가 0~10인 것을 전제로 한 0.4% 미세조정이다. 점수 범위가
  작은 방식으로 바꾸면 같은 값이 지배적 요소가 되어 사실상 최신순 정렬이 된다.
  검색 지표만 보면 드러나지 않을 수 있다. 최신 문서가 정답인 문항에서는 오히려 점수가
  오르기 때문이다. 전환한다면 recency를 끈 상태로 먼저 비교해야 한다.

- **Query Rewrite**: 멀티턴 검색이 깨지는 유일한 근본 원인.
- **파라미터 실측**: 위 표의 값들은 판단으로 정한 것이고 측정으로 확정하지 않았다.
