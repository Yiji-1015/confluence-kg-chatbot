# 🛠️ 프로젝트 통합 명령어 가이드 (Cheat Sheet)

본 문서는 로컬 개발, 계층별 Docker 기동, 데이터 색인, QA 평가, 디버깅 등에 자주 사용되는 모든 명령어를 모아둔 치트시트(Cheat Sheet)입니다.

---

## 📑 목차
1. [계층별 Docker Compose 실행 및 종료](#1-계층별-docker-compose-실행-및-종료)
2. [로컬 애플리케이션 개발 실행](#2-로컬-애플리케이션-개발-실행)
3. [데이터 색인(Ingestion) 및 RAG 평가(Evaluation)](#3-데이터-색인ingestion-및-rag-평가evaluation)
4. [컨테이너 모니터링 및 상태 확인](#4-컨테이너-모니터링-및-상태-확인)
5. [데이터베이스 & 서비스 직접 CLI 접속](#5-데이터베이스--서비스-직접-cli-접속)
6. [컨테이너 초기화 및 리셋](#6-컨테이너-초기화-및-리셋)

---

## 1. 계층별 Docker Compose 실행 및 종료

### ① [Tier 1] Core 레이어 (PostgreSQL + Redis) — 기본 개발 필수
```bash
# 실행 (백그라운드)
docker compose up -d

# 실행 상태 확인
docker compose ps

# 종료 (컨테이너만 정지, 데이터 볼륨 유지)
docker compose down
```

### ② [Tier 2] Search 레이어 추가 (Elasticsearch 8.15 + Kibana)
```bash
# Core + Search 실행
docker compose -f docker-compose.yml -f docker-compose.search.yml up -d

# Search 레이어만 종료
docker compose -f docker-compose.search.yml down
```

### ③ [Tier 3] KG 레이어 추가 (Neo4j Graph DB)
```bash
# Core + KG 실행
docker compose -f docker-compose.yml -f docker-compose.kg.yml up -d

# KG 레이어만 종료
docker compose -f docker-compose.kg.yml down
```

### ④ [Tier 4] Gateway & Observability 레이어 추가 (LiteLLM)
```bash
# Core + Gateway 실행
docker compose -f docker-compose.yml -f docker-compose.obs.yml up -d
```

### ⑤ [Full Stack] 전체 모든 서비스 일괄 실행
```bash
# 전체 기동
docker compose \
  -f docker-compose.yml \
  -f docker-compose.app.yml \
  -f docker-compose.search.yml \
  -f docker-compose.kg.yml \
  -f docker-compose.obs.yml \
  up -d

# 전체 종료
docker compose \
  -f docker-compose.yml \
  -f docker-compose.app.yml \
  -f docker-compose.search.yml \
  -f docker-compose.kg.yml \
  -f docker-compose.obs.yml \
  down
```

---

## 2. 로컬 애플리케이션 개발 실행

Core 컨테이너(`docker compose up -d`)만 띄워둔 상태에서 로컬 호스트에서 직접 실행하는 방법입니다.

### ① Python FastAPI AI 엔진 (`ai-server`)
```bash
cd ai-server

# 가상환경 생성 (최초 1회)
python -m venv venv

# 가상환경 활성화
# [Windows PowerShell]
.\venv\Scripts\Activate.ps1
# [Mac/Linux]
source venv/bin/activate

# 의존성 설치
pip install -r requirements.txt

# 비동기 개발 서버 기동 (핫 리로드)
uvicorn app.main:app --port 8000 --reload
```
* 헬스체크 확인: `http://localhost:8000/internal/health`
* Swagger API 문서: `http://localhost:8000/docs`

### ② Spring Boot 백엔드 (`backend`)
```bash
cd backend

# [Windows]
.\gradlew.bat bootRun

# [Mac/Linux]
./gradlew bootRun
```
* 웹 챗봇 UI 접속: **`http://localhost:8080`**

---

## 3. 데이터 색인(Ingestion) 및 RAG 평가(Evaluation)

`ai-server` 디렉터리에서 실행합니다. (Search 컨테이너가 켜져 있어야 합니다.)

```bash
cd ai-server

# 1. Confluence 문서 크롤링 및 Elasticsearch 색인 (증분/전체)
python scripts/ingest.py

# 2. RAG QA 벤치마크 평가 스크립트 실행
python evaluation/run_qa.py

# 3. Langfuse 평가 데이터셋 생성/푸시
python evaluation/generate_dataset.py
python evaluation/push_dataset.py
```

---

## 4. 컨테이너 모니터링 및 상태 확인

### 실시간 메모리 및 CPU 사용량 모니터링
```bash
docker stats
```

### 전체 실행 중인 컨테이너 확인
```bash
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

### 특정 컨테이너 실시간 로그 확인
```bash
# PostgreSQL 로그
docker logs -f kg-postgres

# Redis 로그
docker logs -f kg-redis

# Elasticsearch 로그
docker logs -f kg-elasticsearch

# LiteLLM 게이트웨이 로그
docker logs -f kg-litellm

# Spring Boot 백엔드 로그
docker logs -f kg-backend
```

---

## 5. 데이터베이스 & 서비스 직접 CLI 접속

### PostgreSQL (`kg-postgres`)
```bash
# psql 쉘 접속
docker exec -it kg-postgres psql -U postgres -d chatbot_db

# (psql 내부 유용한 쿼리)
# \dt                 : 테이블 목록 조회
# SELECT * FROM chat_room;
# SELECT * FROM chat_message ORDER BY created_at DESC LIMIT 10;
# \q                  : 종료
```

### Redis (`kg-redis`)
```bash
# redis-cli 접속
docker exec -it kg-redis redis-cli

# (redis-cli 내부 유용한 명령어)
# keys *             : 캐싱된 세션 키 확인
# get "session:..."  : 대화 맥락 캐시 내용 확인
# ttl "session:..."  : 남은 유효 시간(초) 확인
# exit               : 종료
```

### Elasticsearch 클러스터 헬스체크 (TLS)
```bash
curl -k -u elastic:kg-password "https://localhost:9200/_cluster/health?pretty"
```

### Neo4j 지식 그래프 브라우저
* 브라우저에서 `http://localhost:7474` 접속
* ID: `neo4j`, PW: `kg-password` (또는 `.env`에 설정한 값)

---

## 6. 컨테이너 초기화 및 리셋

데이터 볼륨을 포함하여 완전 초기화하고 싶을 때 사용합니다.

```bash
# 전체 컨테이너 중지 및 볼륨/네트워크 완전 삭제 (주의: DB 데이터 삭제됨)
docker compose \
  -f docker-compose.yml \
  -f docker-compose.app.yml \
  -f docker-compose.search.yml \
  -f docker-compose.kg.yml \
  -f docker-compose.obs.yml \
  down -v

# 사용하지 않는 미사용 Docker 이미지 및 캐시 청소
docker system prune -f
```
