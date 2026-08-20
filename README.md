# Confluence Knowledge Base Chatbot

사내 Confluence 문서를 검색하여 정확한 출처와 함께 답변을 안내하는 **지식 기반 사내 챗봇 프로젝트**입니다.

---

## 🛠️ 기술 스택 (Tech Stack)

| 구분 | 사용 기술 |
|---|---|
| **백엔드 (Backend)** | Java 21, Spring Boot, Spring Data JPA, RestClient |
| **AI 서버 (AI Engine)** | Python 3.13, FastAPI, BeautifulSoup4, Langfuse SDK |
| **검색 엔진 (Search Engine)** | Elasticsearch 8.15 (Nori 형태소 분석기 + 1536차원 Dense Vector) |
| **LLM 게이트웨이** | LiteLLM (`DeepSeek-Chat`, `GPT-4o`, `GPT-4o-mini`, `text-embedding-3-small`) |
| **데이터베이스 & 캐시** | PostgreSQL 16, Redis 7, Neo4j 5 |
| **모니터링 (Observability)** | Langfuse Cloud |
| **인프라 (Infrastructure)** | Docker, Docker Compose |

---

## 💡 주요 설계 및 구현 포인트

### 1. 세션 관리 및 영구 저장 분리 (Polyglot Persistence)
* **Redis**: 실시간 대화 흐름을 이어가기 위해 최근 5턴의 대화 내용을 캐싱하고 빠른 응답 속도를 유지합니다 (30분 만료 TTL).
* **PostgreSQL**: 대화방 목록과 주고받은 메시지 이력을 RDB에 영구 저장하여, 사용자가 언제든 이전 대화 내역을 다시 확인할 수 있도록 구성했습니다.

### 2. 질문 특성에 맞춘 동적 모델 라우팅 & Fallback (LiteLLM)
* **동적 모델 분기**: 질문이 짧아 의도 파악이 필요하거나 대화 맥락이 긴 경우 `GPT-4o`를 활용하고, 일반적인 질의에는 `DeepSeek-Chat`을 활용하여 안정성과 비용 효율을 함께 고려했습니다.
* **장애 대응 (Fallback)**: 주 모델 응답에 일시적 지연이나 오류가 발생할 경우 보조 모델(`GPT-4o-mini`)로 자동 우회하도록 설정했습니다.

### 3. 문서 구조를 고려한 하이브리드 검색
* Confluence 본문의 표(`<table>`)를 마크다운 표 구조로 보존하여 정보 왜곡을 줄였습니다.
* Nori 형태소 분석기를 적용한 **키워드 검색(BM25)과 의미 기반 벡터 검색(kNN)**을 결합하여 사내 용어 검색 정확도를 높였습니다.
* 문서의 계층 경로(Breadcrumb)와 원본 링크를 함께 제공하여 답변의 출처를 쉽게 확인할 수 있습니다.

### 4. 실시간 모니터링 및 트레이싱 (Langfuse)
* 질문 처리 소요 시간, 사용 토큰 수, 예상 비용, 검색된 문서 목록을 실시간으로 기록하여 파이프라인의 동작 상태를 추적할 수 있습니다.

---

## 🚀 로컬 실행 방법 (Quick Start)

### 1. 인프라 컨테이너 기동
```bash
docker compose up -d
```

### 2. Python AI 서버 실행
```bash
cd ai-server
uvicorn app.main:app --port 8000 --reload
```

### 3. Spring Boot 백엔드 실행
```bash
cd backend
./gradlew bootRun
```

* 브라우저에서 **`http://localhost:8080`** 접속
