# 6. 세션·백엔드·관측·배포

[전체 목차](README.md) · 이전: [AI 서비스화](05-ai-serving.md)

이 축은 범위가 넓어 세션, Spring 구조, 관측, Docker 순서로 나눴다. 코드 경로는 각각 해당 절에서 연결한다.

## 1. 질문이 들어와 저장되기까지

```text
Browser: query + sessionId + X-User-Id
 → Controller: 입력 검증
 → ChatService: 세션 소유자 확인 / 신규 ID 발급
 → Redis 최근 이력 조회, 비면 DB에서 복원
 → AiEngineClient: FastAPI 호출
 → ChatPersistenceService: 질문·답변·출처 저장 및 커밋
 → Redis에 최근 턴 저장
 → Browser: 답변·출처·sessionId 표시
```

코드: [ChatController.java](../../backend/src/main/java/com/yiji/Chatbot/controller/ChatController.java), [ChatService.java](../../backend/src/main/java/com/yiji/Chatbot/service/ChatService.java), [AiEngineClient.java](../../backend/src/main/java/com/yiji/Chatbot/service/AiEngineClient.java), [index.html](../../backend/src/main/resources/static/index.html).

## 2. Redis와 PostgreSQL의 차이

| 저장소 | 내용 | 이 프로젝트의 역할 |
|---|---|---|
| PostgreSQL | 세션, 전체 질문·답변, 출처 JSON, 시각 | 영속 기록·목록·과거 메시지 조회 |
| Redis | role/content 리스트 JSON | LLM에 전달할 최근 이력 캐시 |

[RedisSessionService.java](../../backend/src/main/java/com/yiji/Chatbot/service/RedisSessionService.java)는 `chat:session:{sessionId}`라는 문자열 키에 JSON 배열을 저장한다. Redis List 명령을 쓰는 구현은 아니다.

최근 5턴은 사용자 5개+assistant 5개, 최대 메시지 10개다. 저장할 때 예전 메시지를 앞에서 잘라낸다. TTL은 30분이며 saveTurn/saveHistory로 쓸 때 갱신한다. 단순 조회만으로 TTL을 연장하지 않는다. 10개라는 개수 제한은 토큰 수 제한과 다르다.

캐시가 비면 [ChatPersistenceService.java](../../backend/src/main/java/com/yiji/Chatbot/service/ChatPersistenceService.java)의 `loadRecentHistory()`가 createdAt 내림차순, id 내림차순으로 최근 10개를 조회한다. 뒤집어 오래된 순서로 LLM에 전달하고 role을 소문자로 변환한다. 같은 시각 메시지의 순서를 ID로 보완한다.

이를 Cache-Aside라고 부른다. 캐시에서 못 찾으면 원본 저장소를 조회해 채워 넣는 방식이다. Redis 만료와 PostgreSQL 대화 삭제는 다르다.

## 3. DB 저장 후 Redis 갱신은 무엇을 보호하는가

DB 트랜잭션은 질문과 답변을 함께 저장한다. 중간에 실패하면 둘을 함께 되돌리려는 경계다. Redis는 이 DB 트랜잭션에 참여하지 않는다.

Redis를 먼저 쓰고 DB 저장이 실패하면 Redis에만 존재하는 대화가 다음 LLM에 들어갈 수 있다. 그래서 DB 저장 메서드가 커밋된 뒤 Redis를 갱신한다. 삭제도 DB 삭제 후 Redis 삭제다.

다만 이것이 두 저장소의 원자적 동기화는 아니다.

- DB 커밋 뒤 Redis 연결 오류가 나면 DB에는 턴이 있는데 요청은 실패할 수 있다.
- Redis 읽기 연결 오류는 빈 캐시와 다르며 모두 DB 복원으로 처리하지 않는다.
- Redis 저장은 읽기→리스트 수정→쓰기이므로 같은 세션의 동시 요청에서 갱신 손실 가능성이 있다.

현재 잡는 예외는 주로 JSON 직렬화·역직렬화 오류다. “Redis가 죽어도 무조건 대화 가능”이라고 설명하면 안 된다.

## 4. Spring MVC 책임 분리

| 구성 | 책임 | 코드 |
|---|---|---|
| Controller | HTTP 경로·입력·응답 | [ChatController](../../backend/src/main/java/com/yiji/Chatbot/controller/ChatController.java) |
| Service | 채팅 순서 조율 | [ChatService](../../backend/src/main/java/com/yiji/Chatbot/service/ChatService.java) |
| PersistenceService | 짧은 DB 트랜잭션 | [ChatPersistenceService](../../backend/src/main/java/com/yiji/Chatbot/service/ChatPersistenceService.java) |
| Repository | 엔티티 조회·저장 | [ChatMessageRepository](../../backend/src/main/java/com/yiji/Chatbot/repository/ChatMessageRepository.java) |
| Mapper | 엔티티·DTO·출처 JSON 변환 | [ChatMapper](../../backend/src/main/java/com/yiji/Chatbot/mapper/ChatMapper.java) |
| AI client | FastAPI HTTP 요청 | [AiEngineClient](../../backend/src/main/java/com/yiji/Chatbot/service/AiEngineClient.java) |

외부 AI 응답을 기다리는 동안 DB 트랜잭션을 유지하면 연결을 오래 점유할 수 있다. `processChat()` 전체에는 트랜잭션을 붙이지 않고 DB 작업만 별도 서비스 메서드로 분리했다. 별도 빈을 거쳐 호출하는 구조는 Spring 트랜잭션 프록시가 적용되는 경계를 만든다.

API는 POST /api/chat, GET /api/sessions, GET /api/sessions/{id}/messages, DELETE /api/sessions/{id}다. 세션 목록은 기본 50개 페이지 단위, 메시지 목록은 현재 전체 반환이다.

## 5. 사용자 식별·오류·스키마

브라우저 localStorage의 ID를 `X-User-Id` 헤더로 전송한다. [CurrentUserArgumentResolver](../../backend/src/main/java/com/yiji/Chatbot/web/CurrentUserArgumentResolver.java)가 이를 Controller 인자로 주입한다. 소유자가 다른 세션은 404로 처리한다. 이는 로그인 인증이나 문서별 권한 연동이 아니라 클라이언트가 보내는 식별자 비교다.

[GlobalExceptionHandler](../../backend/src/main/java/com/yiji/Chatbot/exception/GlobalExceptionHandler.java)는 입력 오류 400, 세션 없음 404, AI 엔진 실패 502를 ProblemDetail로 반환한다. AI 호출 실패 문구를 성공 답변으로 DB에 저장하지 않는 것이 핵심이다.

[application.yaml](../../backend/src/main/resources/application.yaml)은 Flyway를 켜고 Hibernate는 validate로 둔다. [V1 SQL](../../backend/src/main/resources/db/migration/V1__init_chat_schema.sql)은 세션·메시지 테이블과 외래 키, 다음 조회 인덱스를 만든다.

- `(user_id, updated_at DESC)`: 사용자별 최근 대화방 목록.
- `(session_id, created_at, message_id)`: 대화 내역·최근 이력 순서 조회.

HTTP 설정은 connect=10초, read 기본 60초, imperative factory=simple이다. [AiClientConfig](../../backend/src/main/java/com/yiji/Chatbot/config/AiClientConfig.java)는 자동 구성된 RestClient.Builder를 받아 관측 설정을 함께 사용한다. 현재 설정 이유와 과거 HTTP/2 업그레이드 문제는 [BACKEND_REVIEW.md](../BACKEND_REVIEW.md)에 기록돼 있다.

## 6. Langfuse와 Prometheus의 관찰 단위

[metrics.py](../../ai-server/app/observability/metrics.py)의 `stage()`는 시작 시각을 기록하고, 끝날 때 elapsed를 Histogram에 넣는다. 예외면 단계 오류 카운터를 늘리고 다시 던지며, finally에서 시간은 항상 기록한다. 동시에 Langfuse observation에 단계 metadata를 추가한다.

| 단계 | 포함하는 실제 작업 |
|---|---|
| embedding | 검색용 질문 임베딩 HTTP 호출 |
| search | BM25, kNN, RRF, 최신성, 문서 선별, 본문 재조회 |
| context_build | 이미 얻은 본문을 제목·경로와 문자열로 조립 |
| generation | LiteLLM을 거친 전체 답변 생성 대기 |

특히 본문 재조회는 context_build가 아니라 search 시간에 포함된다.

| 지표 | 해석 |
|---|---|
| rag_stage_duration_seconds | 단계별 소요 시간 분포 |
| rag_requests_total | 성공·실패 요청 수 |
| rag_stage_errors_total | 단계에서 바깥으로 나온 예외 |
| rag_retrieved_documents | 최종 검색 문서 수 |
| rag_model_selected_total | 앱에서 최초 선택한 모델 분포 |
| chat_history_cache_total | 이어쓰는 대화의 캐시 hit/miss |

검색 내부에서 예외를 삼켜 빈 결과로 바꾸면 단계 오류 카운터가 놓칠 수 있다. cache miss 역시 TTL 외에 eviction·초기 상태·데이터 문제 등 원인이 가능하다.

Langfuse는 요청 하나를 따라가는 데 쓰고, Prometheus는 여러 요청의 시계열·분포를 모은다. LiteLLM의 success/failure callback은 모델 호출의 토큰·비용 추적용으로 설정돼 있다. FastAPI의 stage metadata 자체가 토큰·비용을 계산하는 것은 아니고, 두 경로의 trace가 완전히 하나로 이어지는지는 실제 기록 확인이 필요하다.

## 7. p95와 Grafana 대시보드

p95는 측정된 요청 시간의 95%가 그 값 이하였다는 의미다. 평균이 놓치는 느린 요청을 보려는 지표이며 단계별 p95를 더한다고 전체 p95가 되지는 않는다.

FastAPI 히스토그램은 0.05초부터 60초까지 버킷을 지정했다. Spring은 `percentiles-histogram` 설정을 켜 HTTP 서버·클라이언트의 bucket 시계열을 내보낸다.

[Prometheus 설정](../../monitoring/prometheus/prometheus.yml)은 기본 15초 간격으로 네 타겟을 수집한다. 앱은 host.docker.internal을 써 호스트 직접 실행과 컨테이너 포트 공개 환경을 함께 지원한다. cAdvisor는 컨테이너 이름으로 내부 통신한다.

| 대시보드 | 내용 |
|---|---|
| [01 Overview](../../monitoring/grafana/dashboards/01-overview.json) | 서비스 상태·지연·자원 전체 보기 |
| [02 Application](../../monitoring/grafana/dashboards/02-application.json) | HTTP·JVM |
| [03 Data & Middleware](../../monitoring/grafana/dashboards/03-data-middleware.json) | DB 연결 풀·캐시·검색 지연 |
| [04 AI / RAG](../../monitoring/grafana/dashboards/04-ai-rag.json) | RAG 단계별 지연·모델 선택 |
| [05 Infrastructure](../../monitoring/grafana/dashboards/05-infrastructure.json) | 컨테이너 CPU·메모리·네트워크 |

JSON과 provisioning을 저장소에 두어 대시보드 구성을 재현한다. DB·Redis·ES의 모든 내부 상태를 수집하는 전용 exporter는 없다. HikariCP 지표도 SQL 텍스트·실행계획을 대신하지 않는다.

## 8. Docker Compose 설정 지도

| 파일 | 역할 |
|---|---|
| [docker-compose.yml](../../docker-compose.yml) | PostgreSQL, Redis |
| [docker-compose.search.yml](../../docker-compose.search.yml) | ES, Kibana, 초기 인증서·계정 설정 |
| [docker-compose.obs.yml](../../docker-compose.obs.yml) | LiteLLM 게이트웨이 |
| [docker-compose.app.yml](../../docker-compose.app.yml) | FastAPI, Spring |
| [docker-compose.monitoring.yml](../../docker-compose.monitoring.yml) | Prometheus, Grafana, renderer, cAdvisor |

다섯 파일은 confluence-net을 공유한다. 모니터링 파일은 현재 renderer까지 네 서비스이며 과거 주석의 세 개와 다르다.

| 서비스 | 메모리 설정 | 추가 설정·의미 |
|---|---:|---|
| PostgreSQL | 1g | pg_data 볼륨으로 DB 유지 |
| Redis | 768m | 내부 maxmemory=512mb, allkeys-lru로 키 제거 가능 |
| ES | 2g | CPU 1.0, 단일 노드, es_data 볼륨 |
| Kibana | 1g | CPU 0.75 |
| FastAPI | 1g | 코드 디렉터리 read-only bind mount |
| Spring | 1g | JVM Xms256m / Xmx512m |
| LiteLLM | 1g | 설정 YAML·env 사용 |
| Prometheus | 512m | 시계열 7일 보관 |
| Grafana / renderer | 각각 512m | 패널 이미지 생성은 renderer 담당 |
| cAdvisor | 384m | v0.55.1, 일부 디스크 지표 비활성 |

컨테이너 메모리 제한과 JVM heap 또는 Redis 내부 한도는 같은 값이 아니다. 프로세스에는 heap 외 메모리도 필요하다. 이 값들을 부하 실험으로 최적화한 결과라고 단정하지 않는다.

ES는 인증·TLS가 설정되고 9200은 localhost로 바인딩된다. 다만 Python ES client는 `verify_certs=False`이고 기본 사용자는 elastic이다. 다른 주요 서비스 포트는 전체 인터페이스로 공개돼 있어 외부 배포 보안 완료 상태라고 설명할 수 없다.

코드 mount는 파일을 공유하는 것이며 uvicorn 자동 reload를 뜻하지는 않는다.

## 9. 이 축을 설명하는 관점

세션은 “대화의 영구 기록과 빠른 최근 맥락”을 나눈다. Spring은 “HTTP·흐름·DB 작업”을 나눈다. 관측은 “어디에서 시간이 걸리는지”를 기록한다. Docker는 “어떤 설정과 자원으로 함께 실행하는지”를 명시한다. 각 부분의 의도와 실제 보장 범위를 구분해서 설명하면 된다.
