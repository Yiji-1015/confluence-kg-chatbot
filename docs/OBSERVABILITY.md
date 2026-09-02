# Observability (작업 중)

계층별 모니터링 체계. **2026-09-02 기준 계측과 수집 설정은 들어갔고, Grafana 대시보드
JSON과 검증이 남았다.** 이어서 할 일은 문서 맨 아래에 있다.

## 현재 시스템 구조 (실측)

컨테이너 9개. compose 파일 4개로 계층이 나뉘어 있다.

| 계층 | 컨테이너 | 비고 |
|---|---|---|
| Application | `rag-backend`(Spring Boot 4.0.7 / Java 21), `rag-ai-server`(FastAPI) | |
| Data | `rag-postgres`(16), `rag-redis`(7), `rag-elasticsearch`(8.15, Nori) | ES는 `mem_limit 2g`, `cpus 1.0` |
| 부속 | `rag-kibana`, `rag-es-setup`, `rag-kibana-setup` | 뒤 둘은 1회성 배치 |
| Gateway | `rag-litellm` | 모든 LLM/임베딩 호출의 단일 관문 |

요청 흐름:

```
사용자 → backend:8080 → ai-server:8000 ┬→ litellm:4000 → OpenAI / DeepSeek / Upstage
                │                      └→ elasticsearch:9200
                ├→ redis:6379   (대화 이력 캐시, 30분 TTL)
                └→ postgres:5432 (영구 대화 기록)
```

## 착수 전 상태에서 확인한 것

가정하지 않고 실제로 확인한 결과다.

- **Spring에 Actuator도 Micrometer도 없었다.** `build.gradle`에 web/jpa/redis/validation만 있었다.
- **ai-server에 `/metrics`가 없었다** (404). `/internal/health`는 있었으나 설정 문자열만
  돌려주어, Elasticsearch나 LiteLLM이 죽어도 `healthy`를 반환했다.
- **LiteLLM `/metrics`는 404다.** basic 구성에서는 Prometheus 지표를 내지 않는다.
- **서빙 경로의 Langfuse 추적이 죽어 있었다.** `chat.py`가 v2 API인
  `langfuse.decorators`를 import하는데 설치된 버전은 4.14.5로 그 모듈이 없다.
  `try/except ImportError`가 무해한 no-op 데코레이터로 대체해서, `@observe`도
  `langfuse_context`도 전혀 동작하지 않았다. 평가(`run_qa.py`)는 v4 API를 써서 정상이었다.
- **healthcheck는 postgres/redis/elasticsearch에만 있었다.** 앱 컨테이너에는 없었다.
- Elasticsearch는 `_cluster/health`, `_nodes/stats`를 내장으로 제공하고 Kibana도 이미 떠 있다.

## 도구 선택과 그 이유

**추가한 것: Prometheus + Grafana + cAdvisor (컨테이너 3개), Actuator + Micrometer(Spring),
prometheus-client(FastAPI).**

- **전용 exporter를 붙이지 않았다.** postgres/redis/elasticsearch exporter 3개를 추가하면
  컨테이너가 3개 늘지만, 운영 판단에 필요한 것은 "DB 서버 내부 수치"가 아니라 "우리 앱이
  DB를 쓸 때 겪는 지연과 커넥션 고갈"이다. 그것은 Actuator의 HikariCP 지표와 앱에서 잰
  단계별 지연으로 이미 나온다. ES 내부가 필요하면 이미 떠 있는 Kibana를 쓴다.
- **이미 있는 Elasticsearch + Kibana에 메트릭을 넣는 방안도 검토했으나 택하지 않았다.**
  ES는 `mem_limit 2g`로 RAG 검색을 담당하는 컴포넌트다. 거기에 메트릭 색인까지 얹으면
  감시 대상이 감시 비용을 부담하게 되어, 부하가 오를수록 관측이 함께 흔들린다.
  Grafana는 대시보드를 파일로 provisioning할 수 있어 코드 관리에도 유리하다.
- **Langfuse는 이미 의존성에 있어 새로 넣지 않았다.** 다만 죽어 있던 서빙 경로를 v4 API로
  되살렸다. Prometheus가 "전체 요청의 분포"를 답한다면 Langfuse는 "이 요청 하나가 왜
  느렸나"를 답한다. 둘은 대체재가 아니다.

## 추가한 계측

### ai-server (`app/observability/metrics.py`)

`stage()` 컨텍스트 매니저 하나가 Prometheus 히스토그램과 Langfuse span을 함께 남긴다.
따로 두면 한쪽만 갱신되어 서로 다른 이야기를 하게 된다.

| metric | 무엇을 탐지하나 |
|---|---|
| `rag_stage_duration_seconds{stage}` | **어느 단계가 병목인가.** embedding / search / context_build / generation |
| `rag_requests_total{status}` | 처리량과 에러율 |
| `rag_stage_errors_total{stage,exception}` | 어느 단계에서 깨졌는가 (전체 실패만 보면 알 수 없다) |
| `rag_retrieved_documents` | 검색 0건 = 답변은 반드시 실패. 지연이 아닌 "조용한 실패" |
| `rag_model_selected_total{model}` | 비싼 모델로 쏠리면 비용과 지연이 함께 오른다 |
| `process_*`, `python_gc_*` | prometheus_client가 기본 제공 |

히스토그램 버킷을 60초까지 늘렸다. 기본 버킷은 10초에서 끝나 LLM 지연을 전부 마지막
버킷에 몰아넣어 p95를 읽을 수 없다.

### backend (Actuator + Micrometer)

거의 전부 자동으로 나온다. 직접 계측하면 같은 것을 다시 만들게 된다.

| metric | 무엇을 탐지하나 |
|---|---|
| `http_server_requests_seconds` | API별 지연·에러율·처리량 |
| `http_client_requests_seconds` | **AI 엔진 호출 지연.** backend가 느린지 ai-server가 느린지 가른다 |
| `hikaricp_connections_pending` | 0보다 크면 커넥션이 모자라 요청이 줄 서는 중 |
| `hikaricp_connections_acquire_seconds` | 획득이 길면 풀 고갈, 점유가 길면 쿼리가 느림 |
| `jvm_memory_used_bytes`, `jvm_gc_pause_seconds` | 힙 부족 → GC 증가 → 지연 |
| `chat_history_cache_total{result}` | 캐시 미스율. Redis TTL(30분)이 대화 간격보다 짧은지 |

`http_client_requests_seconds`는 `AiClientConfig`가 `RestClient.builder()`를 직접
호출하고 있어 나오지 않았다. 자동 구성된 `RestClient.Builder`를 주입받도록 바꿔 얻었다.

`show-sql: true`도 껐다. 요청마다 전체 SQL을 로그로 찍어 지연과 로그량을 늘리는데,
같은 정보는 HikariCP 지표로 더 싸게 얻는다.

### 헬스체크

`/internal/health`가 Elasticsearch `ping()`과 LiteLLM `/v1/models`를 실제로 호출하고,
하나라도 끊기면 503을 돌려준다. ai-server compose에 healthcheck를 걸어 의존성이 끊기면
컨테이너가 `unhealthy`로 표시된다. backend 이미지에는 curl/wget이 없어 healthcheck 대신
Prometheus의 `up{job="backend"}`으로 상태를 본다.

## 이어서 할 일

**막힌 것: 디스크가 꽉 찼다(`ENOSPC`).** ai-server 이미지 빌드가 `EOF`로 실패하고
파일 쓰기도 실패한다. 아래 작업은 디스크 정리가 선행되어야 한다.

1. **디스크 정리** — `docker system df`로 확인 후 `docker image prune` 등
2. **Grafana 대시보드 JSON 5개 작성** → `monitoring/grafana/dashboards/`
   - `01-overview` 전체 상태·처리량·에러율·p50/p95/p99·계층별 지연 분해·컨테이너 자원
   - `02-application` API별 지연/요청/에러, RAG 단계별 실패, JVM 힙·GC·스레드
   - `03-data-middleware` HikariCP 풀·획득 시간, 캐시 적중률, ES 검색 지연, 검색 0건 비율
   - `04-ai-rag` 단계별 p95·소요 비중·평균, LLM 지연 분포, 모델 라우팅, 성공/실패
   - `05-infrastructure` 컨테이너 CPU·스로틀링·메모리·네트워크·디스크·재시작
3. **이미지 재빌드** — ai-server(prometheus-client), backend(actuator, micrometer)
4. **검증**
   - `curl localhost:8000/metrics` → `rag_*` 지표가 나오는지
   - `curl localhost:8080/actuator/prometheus` → `http_server_requests_*`가 나오는지
   - `localhost:9090/targets` → 3개 job이 전부 UP인지
   - Grafana(`localhost:3000`)에서 데이터소스 연결과 대시보드 등록 확인
   - 채팅 요청을 한 번 보내 Langfuse에 서빙 트레이스가 실제로 남는지 (죽어 있던 것 복구 확인)
5. **문서 마무리** — 실행 방법, 대시보드별 의미, 관측 못 하는 영역

## 아직 관측할 수 없는 영역

- **LiteLLM 내부** — `/metrics`가 없어 게이트웨이 자체의 지연·재시도·fallback 발생을
  직접 볼 수 없다. 앱에서 잰 `generation` 단계 지연에 합쳐져 보인다.
- **외부 LLM API** — OpenAI/DeepSeek/Upstage의 지연과 rate limit은 우리 쪽에서
  `generation` 지연과 실패로만 관측된다.
- **호스트 자원** — cAdvisor는 컨테이너 단위다. 호스트 전체 CPU/메모리/디스크는
  node_exporter가 필요하나 컨테이너가 하나 더 늘어 지금은 넣지 않았다.
- **Elasticsearch 내부** — 샤드·세그먼트·쿼리 캐시는 Kibana Stack Monitoring으로 본다.
