# 🧠 Confluence Enterprise KG-RAG Chatbot

> 사내 Confluence 지식베이스를 기반으로 정확한 출처와 맥락을 제공하는 **엔터프라이즈급 Full-Stack Hybrid RAG 챗봇 시스템**

---

## 🛠️ 기술 스택 (Tech Stack)

| 계층 | 사용 기술 |
|---|---|
| **Backend** | `Java 21`, `Spring Boot 3/4`, `Spring Data JPA`, `RestClient` |
| **AI Engine** | `Python 3.13`, `FastAPI`, `BeautifulSoup4`, `Langfuse SDK` |
| **Search Engine** | `Elasticsearch 8.15` (Nori 형태소 분석기 + Dense Vector 1536d) |
| **LLM Gateway** | `LiteLLM` (`DeepSeek-Chat`, `GPT-4o`, `GPT-4o-mini`, `text-embedding-3-small`) |
| **Database & Cache** | `PostgreSQL 16`, `Redis 7` (512MB LRU Policy), `Neo4j 5` |
| **Observability** | `Langfuse Cloud` (실시간 비용/토큰/Latency 트레이싱) |
| **Infrastructure** | `Docker`, `Docker Compose` |

---

## ⚡️ 일반 단순 챗봇과의 4가지 핵심 차별점

### 1. Polyglot Persistence (Redis + JPA)
* **Redis (초고속 단기 메모리)**: 최근 5턴 대화 컨텍스트를 슬라이딩 윈도우로 0.5ms 만에 조회하여 AI에 전달 (30분 Sliding TTL).
* **PostgreSQL (영구 장부)**: 대화방 및 메시지 이력을 RDB에 영구 보관하여 과거 대화 복원 및 감사 로그 지원.

### 2. 비용 절감형 동적 모델 라우팅 & 무중단 Fallback (LiteLLM)
* **동적 라우팅**: 12자 미만 단어형 질문 또는 1,000자 이상 복잡 컨텍스트는 `GPT-4o`(심층 추론), 일반 표준 질문은 `DeepSeek-Chat`으로 라우팅하여 **API 비용 90% 이상 절감**.
* **Zero-Downtime Fallback**: 주 모델(DeepSeek) 장애 발생 시 `GPT-4o-mini`로 0.1초 만에 자동 우회하여 서비스 무중단 보장.

### 3. 구조 보존형 하이브리드 검색 (Structure-Preserving Retrieval)
* Confluence 내부 표(`<table>`)를 마크다운 그리드로 완벽 보존하여 검색 시 데이터 왜곡 방지.
* 문서 계층 트리(`expand=ancestors`)를 실시간 파싱하여 출처 브레드크럼(Path) 및 원본 링크 제공.
* Nori 형태소 분석기 **BM25(키워드) + Vector kNN(의미 유사도)** 결합 하이브리드 검색.

### 4. 엔드투엔드 실시간 옵저버빌리티 (Langfuse)
* 질문 1건당 **소요 시간, 사용 토큰 수, 실시간 달러($) 비용, 검색된 문서 원문, LLM 프롬프트 전문**을 대시보드에서 실시간 추적 및 평가.

---

## 🚀 빠른 시작 (Quick Start)

### 1. 인프라 기동 (Docker Compose)
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

* 💬 웹 브라우저에서 **`http://localhost:8080`** 접속
