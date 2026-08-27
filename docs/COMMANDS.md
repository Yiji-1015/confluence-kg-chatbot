# 명령어

## 전체 실행

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.search.yml \
  -f docker-compose.obs.yml \
  -f docker-compose.app.yml \
  up -d --build
```

## 계층별 실행

```bash
docker compose up -d
docker compose -f docker-compose.yml -f docker-compose.search.yml up -d
docker compose -f docker-compose.yml -f docker-compose.obs.yml up -d
docker compose -f docker-compose.yml -f docker-compose.app.yml up -d --build
```

## 문서 색인

```bash
docker exec -it rag-ai-server python -m scripts.ingest --limit 10
docker exec -it rag-ai-server python -m scripts.ingest
docker exec -it rag-ai-server python -m scripts.ingest --force
docker exec -it rag-ai-server python -m scripts.ingest --category "솔루션/개발"
```

## 상태 확인

```bash
docker compose ps
curl http://localhost:8000/internal/health
curl -k -u elastic:rag-password "https://localhost:9200/_cluster/health?pretty"
```

## 로그

```bash
docker logs -f rag-postgres
docker logs -f rag-redis
docker logs -f rag-elasticsearch
docker logs -f rag-litellm
docker logs -f rag-ai-server
docker logs -f rag-backend
```

## 로컬 개발

```bash
cd ai-server
python -m uvicorn app.main:app --reload --port 8000
```

```bash
cd backend
./gradlew bootRun
```

Windows에서는 `./gradlew` 대신 `gradlew.bat`을 사용합니다.
