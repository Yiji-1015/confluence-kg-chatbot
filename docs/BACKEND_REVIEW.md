# Spring MVC 관례 점검

2026-09-04 backend 전체 점검 결과. 고칠 것과 그 근거를 남긴다.
작업은 위에서부터 순서대로 한다. 아래로 갈수록 독립적이라 순서를 바꿔도 된다.

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

- [ ] 적용

## 2. 서비스 계층이 웹 예외에 의존

`ChatService.java:15, :162, :169` — 서비스가 `org.springframework.web.server.ResponseStatusException`을
import한다. 계층이 역전됐다.

1번의 도메인 예외로 교체하고 `org.springframework.web` import를 없앤다.
소유자 불일치를 404로 응답하는 판단 자체는 유지한다(근거는 CHANGELOG 2026-09-01).

- [ ] 적용

## 3. AI 호출 실패가 200 OK로 위장된다

`AiEngineClient.java:57-59` — `catch (Exception e)`로 전부 삼키고 에러 문구를
`answer`에 담아 정상 응답처럼 반환한다. 두 가지로 번진다.

- 그 문구가 `ChatService.java:104`에서 ASSISTANT 메시지로 DB에 영구 저장되고 Redis
  히스토리에도 들어간다. 다음 턴에 "AI 서버와 통신할 수 없습니다"가 LLM 컨텍스트로 간다.
- `e.getMessage()`를 사용자 응답에 그대로 붙여 내부 URL·예외 클래스명이 노출된다.

`createFallbackResponse`는 통째로 삭제. 예외를 던지고 advice에서 502로 매핑한다.

같이 할 것: 이미 저장된 오염 데이터 정리.
`content LIKE '%AI 검색 엔진 서버와 일시적으로%'`로 조회 후 확인하고 삭제.

- [ ] 적용
- [ ] 기존 오염 메시지 정리

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

부수 효과로 `redisSessionService.saveTurn`(`:95`)이 트랜잭션 안에 있어
롤백 시 Redis에만 데이터가 남던 불일치도 함께 풀린다.

- [ ] 적용

## 5. Jackson 2와 3이 섞여 있다

확인한 사실: `spring-boot-starter-web:4.0.7` → `spring-boot-starter-jackson` →
`tools.jackson.core:jackson-databind:3.1.4`. Boot 4의 JSON 기본은 Jackson 3다.
(Boot 4는 Jackson 2도 `spring-boot-jackson2` 모듈로 함께 관리하지만 이 프로젝트는
그 모듈을 넣지 않았다.)

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
  Jackson 3는 checked exception이 사라졌으므로 `try-catch (Exception)` 부분을 다시 본다.
- 웹 계층 설정이 필요해지면 `spring.jackson.*` 또는 `JsonMapperBuilderCustomizer`로.

- [ ] 적용

## 6. `@CrossOrigin(origins = "*")` 하드코딩

`ChatController.java:21`. 프론트는 `src/main/resources/static/index.html`로 같은 8080에서
서빙되므로 동일 출처다. CORS 자체가 불필요하니 지워도 아무 일도 일어나지 않는다.

나중에 프론트를 분리하면 `WebMvcConfigurer#addCorsMappings`에서 설정값으로 받는다.
컨트롤러에 와일드카드를 박아두면 인증 쿠키를 붙이는 순간 `allowCredentials`와 충돌한다.

- [ ] 적용

## 7. 잔손질

- `ChatController`의 `import ...web.bind.annotation.*` 와일드카드 정리.
- `ResponseEntity<List<...>>` → `List<...>` 직접 반환(동일한 200). `ResponseEntity`는
  상태·헤더를 바꿀 때만. `deleteSession`의 `noContent()`는 적절하므로 유지.
- DTO를 Java 21 `record`로. 지금은 `@NoArgsConstructor` 때문에 불변이 아니고
  `@Getter/@Builder/@NoArgsConstructor/@AllArgsConstructor` 4종 세트가 붙어 있다.
- `processChat`에서 존재하지 않는 `sessionId`가 오면 클라이언트가 준 ID로 새 세션을
  만든다(`orElseGet`). 서버가 ID 발급을 통제하지 못하는 구조.

- [ ] 적용

## 8. 나중에

- `userId`가 모든 엔드포인트에 쿼리 파라미터로 반복된다. `HandlerInterceptor` +
  `HandlerMethodArgumentResolver`로 뽑아내면 서비스 시그니처에서 사라진다.
  DELETE URL에 신원이 실려 액세스 로그에 남는 문제도 같이 해결된다.
- `getSessions`, `getMessages`에 페이징 없음. Spring Data `Pageable`.
- 테스트가 `contextLoads` 하나뿐. `spring-boot-starter-webmvc-test`가 이미 있으니
  `@WebMvcTest`로 컨트롤러 검증 추가.
- `ddl-auto: update` → Flyway. (MVC 관례와는 별개 사안)
