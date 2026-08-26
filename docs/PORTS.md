# 포트 정리

> 이 프로젝트에서 로컬에 뜨는 모든 서비스의 포트를 한눈에 보기 위한 참고 문서.
> 저장소 루트의 Docker Compose 파일들이 실제 기준이며, 이 문서는 이를 사람이 읽기 좋게 옮겨 적은 것이다.

## Docker Compose 서비스

| 서비스 | 컨테이너 포트 | 호스트 바인딩 | 접속 주소 | 용도 |
|---|---|---|---|---|
| Elasticsearch | `9200` | `127.0.0.1`만 | `https://localhost:9200` | BM25 + Vector kNN 하이브리드 검색 (TLS+인증 필수) |
| Kibana | `5601` | 전체 인터페이스 | `http://localhost:5601` | Elasticsearch 디버깅 UI |
| LiteLLM | `4000` | 전체 인터페이스 | `http://localhost:4000` | LLM/임베딩 게이트웨이 |
| Neo4j (Browser) | `7474` | 전체 인터페이스 | `http://localhost:7474` | Knowledge Graph 브라우저 UI |
| Neo4j (Bolt) | `7687` | 전체 인터페이스 | `bolt://localhost:7687` | Knowledge Graph 드라이버 접속 |
| PostgreSQL | `5432` | 전체 인터페이스 | `postgresql://localhost:5432` | 대화방·메시지 영구 저장 |
| Redis | `6379` | 전체 인터페이스 | `redis://localhost:6379` | 최근 대화 세션 캐시 |
| FastAPI AI Engine | `8000` | 전체 인터페이스 | `http://localhost:8000` | Hybrid RAG 파이프라인 |
| Spring Boot / Web UI | `8080` | 전체 인터페이스 | `http://localhost:8080` | 외부 API와 웹 UI |

**"호스트 바인딩" 컬럼 의미**: `127.0.0.1`만이면 이 컴퓨터 안에서만 접속 가능하다. "전체 인터페이스"면 방화벽 설정에 따라 외부에서도 접속할 수 있다.

> **주의**: 현재 Elasticsearch만 `127.0.0.1`로 제한된다. 나머지 서비스는 전체 인터페이스에 열려 있으므로 외부 서버 배포 전 바인딩과 방화벽을 제한해야 한다.

## 애플리케이션 상태

| 서비스 | 포트 | 상태 |
|---|---|---|
| Python FastAPI (`ai-server`) | `8000` | 구현 및 컨테이너 구성 완료 |
| Spring Boot (`backend`) | `8080` | 구현 및 컨테이너 구성 완료 |
| Web UI | `8080` | Spring Boot 정적 리소스로 제공 |

FastAPI와 Spring Boot는 Docker Compose 또는 호스트에서 실행할 수 있다.

## 더 이상 쓰지 않는 것

- **TEI(embedding serving, 원래 `:8081` 계획)** — 초기 BGE-M3 로컬 서빙안에서 사용했지만 OpenAI `text-embedding-3-small`로 전환하면서 제거됨.
