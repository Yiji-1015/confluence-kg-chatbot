# Spring MVC 관례 점검

2026-09-04 backend 전체 점검 결과. 고칠 것과 그 근거를 남긴다.
작업은 위에서부터 순서대로 한다. 아래로 갈수록 독립적이라 순서를 바꿔도 된다.

2026-09-08 전 항목을 코드와 다시 대조했다. 사실관계가 틀린 항목은 없었고,
줄 번호 하나를 고치고 놓쳤던 항목(7번)과 누락 경로 몇 개를 채웠다.

## 유지할 것

이미 관례에 맞아서 건드리지 않는다. 고치는 김에 같이 무너뜨리지 않도록 적어둔다.

- 계층 분리(Controller → Service → Repository). 컨트롤러는 위임만 한다.
- 전 구간 생성자 주입(`@RequiredArgsConstructor`). 필드 `@Autowired` 없음.
- 엔티티를 API로 노출하지 않고 DTO 반환 + `ChatMapper` 분리.
- `@Transactional(readOnly = true)` 구분, `spring.jpa.open-in-view: false`.
- 엔티티에 setter 없이 `@Builder` + 의도가 드러나는 메서드(`updateTimestamp()`).
- `RestTemplate`이 아닌 `RestClient`, 그것도 자동 구성된 `RestClient.Builder`를 주입받아
  Micrometer 계측(`http_client_requests_seconds`)을 살렸다. (`AiClientConfig`)
- `build.gradle`의 `spring-boot-starter-*-test` 모듈들은 Boot 4에서 쪼개진 정식
  아티팩트가 맞다. Maven Central에 전부 존재한다. 문제 없음.

---

## 1. 전역 예외 처리기 부재

`@ControllerAdvice` / `@ExceptionHandler`가 프로젝트에 하나도 없다.

`@Valid` 실패가 Boot 기본 에러 JSON으로 나가고, 프론트(`static/index.html:280`)는
`response.ok`만 보고 "서버 오류 (400)"만 띄운다. `ChatRequestDto`에 써둔
"질문은 최대 1000자까지" 메시지가 사용자에게 도달하지 못한다.

- `exception` 패키지 신설.
- 도메인 예외(`SessionNotFoundException`, `AiEngineException`) — 웹 의존성 없이.
- `GlobalExceptionHandler`: `@RestControllerAdvice` + `ResponseEntityExceptionHandler` 상속.
  상속하면 Spring 표준 예외 처리가 이미 들어 있다. `handleMethodArgumentNotValid`만
  오버라이드해 `BindingResult.getFieldErrors()`를 `ProblemDetail`(RFC 9457)에 채운다.

2번과 3번에서 던진 예외를 받아줄 곳이므로 이것부터 한다.

프론트도 같이 고쳐야 이 항목이 끝난다. 서버가 `detail`을 채워 보내도
`response.ok`만 보고 있으면 그 문장은 화면에 닿지 않는다.

- [x] 적용 (2026-09-08)
  - `exception/{SessionNotFoundException, AiEngineException, GlobalExceptionHandler}` 신설.
  - `static/index.html` 채팅 실패 경로가 `problem.detail`을 읽도록 수정.
  - `ChatControllerErrorHandlingTest`(`@WebMvcTest`) 추가. 통과 확인.
  - `AiEngineException`은 3번을 하기 전까지 던지는 쪽이 없다. 의도한 순서다.

## 2. 서비스 계층이 웹 예외에 의존

`ChatService.java:15, :162, :169` — 서비스가 `org.springframework.web.server.ResponseStatusException`을
import한다. 계층이 역전됐다.

1번의 도메인 예외로 교체하고 `org.springframework.web` import를 없앤다.
소유자 불일치를 404로 응답하는 판단 자체는 유지한다(근거는 CHANGELOG 2026-09-01).

- [x] 적용 (2026-09-08)
  - `ChatService`에서 `org.springframework.web.server.ResponseStatusException`과
    `org.springframework.http.HttpStatus` import 제거. 서비스에 남은 스프링 의존은
    `@Service`, `@Transactional` 둘뿐이다.
  - `:161`, `:168`을 `SessionNotFoundException`으로 교체. 404로의 번역은 처리기가 한다.
  - 프론트도 같이 고쳤다. `selectSession`은 `res.ok`를 보지 않고 바로 `messages.forEach`를
    돌고 있었다. 404 본문은 배열이 아니라 ProblemDetail이라 "대화 불러오기 실패:
    messages.forEach is not a function"이 뜬다. `deleteSession`은 아예 응답을 확인하지
    않아 남의 대화방 삭제가 404로 거절돼도 성공한 것처럼 보였다.
  - `ChatControllerErrorHandlingTest`에 404 번역 검증 1건 추가. 총 3건 통과.

## 3. AI 호출 실패가 200 OK로 위장된다

`AiEngineClient.java:57-59` — `catch (Exception e)`로 전부 삼키고 에러 문구를
`answer`에 담아 정상 응답처럼 반환한다. 두 가지로 번진다.

- 그 문구가 `ChatService.java:105-111`에서 ASSISTANT 메시지로 DB에 영구 저장되고 Redis
  히스토리에도 들어간다. 다음 턴에 "AI 서버와 통신할 수 없습니다"가 LLM 컨텍스트로 간다.
- `e.getMessage()`를 사용자 응답에 그대로 붙여 내부 URL·예외 클래스명이 노출된다.

위장 경로는 `catch` 말고 하나 더 있다. `AiEngineClient.java:48-51` — 응답이 `null`일 때도
같은 fallback을 탄다. 아래처럼 메서드째 지우면 둘 다 닫히지만, `:57-59`만 보고 고치면
`null` 경로가 남는다.

`createFallbackResponse`는 통째로 삭제. 예외를 던지고 advice에서 502로 매핑한다.

같이 할 것: 이미 저장된 오염 데이터 정리.
`content LIKE '%AI 검색 엔진 서버와 일시적으로%'`로 조회 후 확인하고 삭제.

- [x] 적용 (2026-09-08)
  - `createFallbackResponse` 삭제. `catch (Exception)` → `catch (RestClientException)`으로
    좁히고 `AiEngineException`을 던진다. `null` 응답 경로도 같이 예외로 바꿨다.
  - `Exception`을 통째로 잡지 않는 이유: RestClient가 내는 실패는 전부
    `RestClientException` 아래다. 그 밖의 예외(요청 조립 중 NPE 등)는 AI 엔진 장애가
    아니므로 502가 아니라 500으로 나가는 게 맞다.
  - `ChatControllerErrorHandlingTest`에 502 검증 추가. 응답에 내부 주소(`ai-server`)와
    원인 문구(`Connection refused`)가 실리지 않는 것까지 확인한다. 총 4건 통과.
- [x] 기존 오염 메시지 정리 — **해당 없음**
  - `rag-postgres`의 `chatbot_db`를 조회했다. `chat_messages` 0행, `chat_sessions` 0행으로
    테이블이 비어 있다. 지울 것이 없어 DELETE는 실행하지 않았다.
  - 운영 DB에 데이터가 쌓인 뒤 이 코드를 배포한다면 그때 다시 조회해야 한다.

## 4. 60초짜리 외부 HTTP 호출이 트랜잭션 안에 있다

`ChatService.java:43`의 `@Transactional`이 `:88`의 AI 호출을 감싼다.
`ai-engine.timeout-seconds: 60` 동안 HikariCP 커넥션을 붙잡는다.
기본 풀 크기 10이면 동시 사용자 10명에서 포화된다. 부하 시 가장 먼저 터질 지점.

목표 구조:

```
processChat (트랜잭션 없음)
 ├─ prepareSession(...)      @Transactional  ← 세션 확보 + USER 메시지 저장
 ├─ aiEngineClient.requestChat(...)          ← 트랜잭션 밖
 └─ saveAnswer(...)          @Transactional  ← ASSISTANT 메시지 저장 + Redis
```

막히는 지점 두 개를 미리 적어둔다.

- `@Transactional`은 프록시 기반이라 같은 클래스 안에서 `this.prepareSession()`으로
  부르면 트랜잭션이 걸리지 않는다(self-invocation). 별도 빈으로 분리하거나
  `ChatService`를 오케스트레이션 담당과 영속화 담당으로 나눈다.
- 트랜잭션 밖으로 엔티티를 들고 나가면 준영속(detached) 상태가 된다.
  `ChatSession` 대신 `sessionId` 문자열만 넘긴다.

부수 효과로 트랜잭션 안에서 Redis를 쓰던 두 지점의 불일치(롤백해도 Redis에는 남는다)가
함께 풀린다. `redisSessionService.saveTurn`(`:95`)과, 캐시 미스 경로의
`recoverHistoryFromDb` → `saveHistory`(`:195`)다. 두 번째를 빠뜨리기 쉽다.

- [ ] 적용

## 5. Jackson 2와 3이 섞여 있다

확인한 사실: `spring-boot-starter-web:4.0.7` → `spring-boot-starter-jackson` →
`tools.jackson.core:jackson-databind:3.1.4`. Boot 4의 JSON 기본은 Jackson 3다.
(Boot 4는 Jackson 2도 `spring-boot-jackson2` 모듈로 함께 관리하지만 이 프로젝트는
그 모듈을 넣지 않았다.)

"관여하지 않는다"의 근거를 정확히 해둔다. Jackson 2 컨버터 클래스
(`MappingJackson2HttpMessageConverter`)는 spring-web 7.0.8에 아직 들어 있다.
클래스가 없어서 안 붙는 게 아니라, 붙는 조건이 따로 있다.
`spring-boot-http-converter:4.0.7`의
`Jackson2HttpMessageConvertersConfiguration`이 걸어둔 조건은 이렇다.

```
@ConditionalOnProperty(name = "spring.http.converters.preferred-json-mapper",
                       havingValue = "jackson2")
   또는  Jackson 3가 클래스패스에 없을 것
```

`application.yaml`에 `spring.http.*`가 없고 Jackson 3는 있으므로 컨버터가 등록되지 않는다.
바꿔 말해 **저 속성 한 줄이면 뒤집히는 조건부 사실**이다. 나중에 누가 호환성 때문에
`preferred-json-mapper: jackson2`를 넣으면 이 판단은 그날로 무효가 된다.

`RedisConfig.java:22`의 `@Bean ObjectMapper`는 `com.fasterxml.jackson`(Jackson 2)이라
`@RequestBody`/`@ResponseBody`와 `RestClient` 직렬화에 **전혀 관여하지 않는다**.
`JavaTimeModule` 등록과 `WRITE_DATES_AS_TIMESTAMPS` 비활성화는 Redis 히스토리와
`sourcesJson` 직렬화에만 적용된다. API 응답의 `LocalDateTime`이 제대로 나오는 건
Jackson 3가 java.time을 기본 내장하고 타임스탬프도 기본 비활성이기 때문이지,
이 설정 덕분이 아니다.

- `RedisConfig`의 `ObjectMapper` 빈 삭제.
- `build.gradle`에서 `com.fasterxml.jackson.core:jackson-databind`,
  `com.fasterxml.jackson.datatype:jackson-datatype-jsr310` 두 줄 삭제.
- `ChatMapper`, `RedisSessionService`의 import를 `tools.jackson.databind.ObjectMapper`로.
  Jackson 3는 `JsonMapper extends ObjectMapper`라 Boot가 자동 구성한 `JsonMapper` 빈이
  그대로 주입된다(확인함). 별도 `@Bean` 없이 타입만 바꾸면 된다.
  Jackson 3는 checked exception이 사라졌으므로 `try-catch (Exception)` 부분을 다시 본다.
- 웹 계층 설정이 필요해지면 `spring.jackson.*` 또는 `JsonMapperBuilderCustomizer`로.

- [ ] 적용

## 6. `@CrossOrigin(origins = "*")` 하드코딩

`ChatController.java:21`. 프론트는 `src/main/resources/static/index.html`로 같은 8080에서
서빙되므로 동일 출처다. CORS 자체가 불필요하니 지워도 아무 일도 일어나지 않는다.

나중에 프론트를 분리하면 `WebMvcConfigurer#addCorsMappings`에서 설정값으로 받는다.
컨트롤러에 와일드카드를 박아두면 인증 쿠키를 붙이는 순간 `allowCredentials`와 충돌한다.

- [ ] 적용

## 7. RestClient 타임아웃을 Boot 관례 밖에서 설정한다

`AiClientConfig.java:34-40` — `SimpleClientHttpRequestFactory`를 손으로 만들어
`requestFactory()`로 꽂는다. 폐기된 API는 아니라 동작은 한다. 다만 두 가지가 걸린다.

- Boot 4에는 `spring-boot-http-client`가 클래스패스에 있고, 타임아웃은
  `spring.http.client.connect-timeout` / `read-timeout` 속성이 정식 자리다.
  지금은 그 속성을 넣어도 이 팩토리가 덮어써서 먹지 않는다.
- `requestFactory()`를 직접 지정하면 Boot가 골라둔 클라이언트 구현(`ClientHttpRequestFactoryBuilder`)이
  통째로 대체되고 JDK `HttpURLConnection`에 고정된다. "유지할 것"에 적어둔
  `RestClient.Builder` 주입의 이점(관측·트레이스)은 남지만, 전송 계층 선택권은 잃는다.

`@Value` 필드 두 개도 `@ConfigurationProperties`가 제자리다. 값이 늘면 더 그렇다.

바꿀 방향: 타임아웃은 `spring.http.client.*`(또는 `RestClientCustomizer`)로 옮기고
`AiClientConfig`는 `baseUrl`만 잡는다.

- [ ] 적용

## 8. 잔손질

- `ChatController`의 `import ...web.bind.annotation.*` 와일드카드 정리.
- `ResponseEntity<List<...>>` → `List<...>` 직접 반환(동일한 200). `ResponseEntity`는
  상태·헤더를 바꿀 때만. `deleteSession`의 `noContent()`는 적절하므로 유지.
- DTO를 Java 21 `record`로. 지금은 `@NoArgsConstructor` 때문에 불변이 아니고
  `@Getter/@Builder/@NoArgsConstructor/@AllArgsConstructor` 4종 세트가 붙어 있다.
- `processChat`에서 존재하지 않는 `sessionId`가 오면 클라이언트가 준 ID로 새 세션을
  만든다(`orElseGet`). 서버가 ID 발급을 통제하지 못하는 구조.

- [ ] 적용

## 9. 나중에

- `userId`가 모든 엔드포인트에 쿼리 파라미터로 반복된다. `HandlerInterceptor` +
  `HandlerMethodArgumentResolver`로 뽑아내면 서비스 시그니처에서 사라진다.
  DELETE URL에 신원이 실려 액세스 로그에 남는 문제도 같이 해결된다.
  덧붙여 지금 `@RequestParam userId`에는 검증이 없다. 빈 문자열이 와도 400이 아니라
  빈 목록이 200으로 나간다(`ChatRequestDto`의 `@NotBlank`는 POST 본문에만 걸려 있다).
- `getSessions`, `getMessages`에 페이징 없음. Spring Data `Pageable`.
  같은 뿌리로 `ChatService.recoverHistoryFromDb`(`:178-185`)는 대화 이력을 통째로 읽어
  메모리에서 마지막 10개만 잘라낸다. 캐시가 만료될 때마다 세션 전체를 스캔한다.
- 테스트가 `contextLoads` 하나뿐. `spring-boot-starter-webmvc-test`가 이미 있으니
  `@WebMvcTest`로 컨트롤러 검증 추가.
- `ddl-auto: update` → Flyway. (MVC 관례와는 별개 사안)
