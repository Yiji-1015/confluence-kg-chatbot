# 포트 정리

> 이 프로젝트에서 로컬에 뜨는 모든 서비스의 포트를 한눈에 보기 위한 참고 문서.
> `docker-compose.yml`이 실제 기준이며, 이 문서는 그걸 사람이 읽기 좋게 옮겨 적은 것이다.

## Docker Compose 서비스

| 서비스 | 컨테이너 포트 | 호스트 바인딩 | 접속 주소 | 용도 |
|---|---|---|---|---|
| Elasticsearch | `9200` | `127.0.0.1`만 | `https://localhost:9200` | BM25 + Vector kNN 하이브리드 검색 (TLS+인증 필수) |
| Kibana | `5601` | `127.0.0.1` + `192.168.123.42`(LAN) | `http://localhost:5601`, `http://192.168.123.42:5601` | Elasticsearch 디버깅 UI |
| LiteLLM | `4000` | 전체 인터페이스 | `http://localhost:4000` | LLM/임베딩 게이트웨이 |
| Neo4j (Browser) | `7474` | 전체 인터페이스 | `http://localhost:7474` | Knowledge Graph 브라우저 UI |
| Neo4j (Bolt) | `7687` | 전체 인터페이스 | `bolt://localhost:7687` | Knowledge Graph 드라이버 접속 |
| Redis | `6379` | 전체 인터페이스 | `redis://localhost:6379` | 대화 세션 / 임베딩 캐시 |

**"호스트 바인딩" 컬럼 의미**: `127.0.0.1`만이면 이 컴퓨터 안에서만 접속 가능(외부 네트워크에서 절대 접근 불가). "전체 인터페이스"면 이 컴퓨터의 모든 네트워크 인터페이스에 열려서, 방화벽 설정에 따라 외부에서도 접근 시도가 가능할 수 있음. `192.168.123.42`처럼 특정 사설 IP만 추가로 바인딩하면 같은 LAN 안에서만 열리고(인터넷 공인 IP 아님), Docker 브리지망(`172.17.x.x` 등) 같은 다른 인터페이스는 여전히 막혀있음.

> **주의**: 현재 Elasticsearch/Kibana만 `127.0.0.1`로 제한돼 있고(ELASTICSEARCH.md 기준), Neo4j/Redis/LiteLLM은 아직 전체 인터페이스에 열려 있다. 로컬 개발 단계라 당장은 문제없지만, 외부 노출된 서버에서 돌릴 때는 이것들도 같은 방식으로 잠가야 한다. (아직 처리 안 한 항목 — Phase 4/5에서 Redis/Neo4j를 실제로 쓰기 시작할 때 함께 검토)

## 애플리케이션 (Docker Compose 밖, PLAN.md 기준)

| 서비스 | 포트 | 상태 |
|---|---|---|
| Python FastAPI (`ai-server`) | `8000` | 구현 중 (IDE에서 직접 실행, `uvicorn app.main:app --port 8000`) |
| Spring Boot (`backend`) | `8080` | 아직 미착수 (Phase 2) |
| React Frontend | 미정 | 아직 미착수 (Phase 7) |

초기 개발 단계에서는 Infrastructure(위 Docker Compose 표)만 컨테이너로 띄우고, FastAPI/Spring Boot는 IDE에서 직접 실행한다 (`PLAN.md` §15).

## 더 이상 쓰지 않는 것

- **TEI(embedding serving, 원래 `:8081` 계획)** — 초기 설계(BGE-M3 로컬 서빙)에서 있었지만, OpenAI `text-embedding-3-small`로 전환하면서(`ELASTICSEARCH.md`) `docker-compose.yml`에서 제외됨. `PLAN.md`의 TEI 관련 서술은 과거 설계 기록으로만 남아있음.
