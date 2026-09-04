# 명령어

## 전체 실행

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.search.yml \
  -f docker-compose.obs.yml \
  -f docker-compose.app.yml \
  -f docker-compose.monitoring.yml \
  up -d --build
```

## 계층별 실행

```bash
docker compose up -d
docker compose -f docker-compose.yml -f docker-compose.search.yml up -d
docker compose -f docker-compose.yml -f docker-compose.obs.yml up -d
docker compose -f docker-compose.yml -f docker-compose.app.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.monitoring.yml up -d
```

모니터링 계층은 단독으로 올려도 된다. Prometheus의 ai-server·backend 타겟만 DOWN이고
cAdvisor 지표와 Grafana 대시보드는 그대로 뜬다.

## 문서 색인

```bash
docker exec -it rag-ai-server python -m scripts.ingest --limit 10
docker exec -it rag-ai-server python -m scripts.ingest
docker exec -it rag-ai-server python -m scripts.ingest --force
docker exec -it rag-ai-server python -m scripts.ingest --category "솔루션/개발"
```

## 검색 품질 평가

RAGAS는 langchain/langgraph 등 50개+ 패키지를 끌고 와서 서빙 이미지에 넣지 않았다.
컨테이너를 재생성하면 사라지므로 평가 전에 설치한다.

```bash
docker exec rag-ai-server pip install -r /app/requirements-eval.txt
```

```bash
docker exec rag-ai-server python -m evaluation.run_qa
```

PowerShell에서는 `&&`를 쓸 수 없으므로 두 줄로 나눠 실행하거나 `;`로 잇는다.
데이터셋을 고쳤다면 실행 전에 Langfuse로 올려야 반영된다.

```bash
docker exec rag-ai-server python -m evaluation.push_dataset
```

## 상태 확인

```bash
docker compose ps
curl http://localhost:8000/internal/health
curl -s http://localhost:9090/api/v1/targets | grep -o '"health":"[a-z]*"'
curl -s http://localhost:3000/api/health
curl -k -u "elastic:$(grep -m1 '^ELASTIC_PASSWORD=' .env | cut -d= -f2-)" "https://localhost:9200/_cluster/health?pretty"
```

## Elasticsearch 비밀번호

`ELASTIC_PASSWORD`는 **클러스터 최초 부트스트랩 때만** 적용된다. 이미 만들어진 클러스터는
데이터 볼륨에 저장된 비밀번호를 계속 쓰므로, `.env`만 바꾸고 컨테이너를 재생성하면
ai-server가 인증에 실패한다(401).

이미 뜬 클러스터의 비밀번호를 바꾸려면 API로 직접 변경한다.

```bash
NEW_PW=$(grep -m1 '^ELASTICSEARCH_PASSWORD=' .env | cut -d= -f2-) && docker exec rag-elasticsearch curl -sk -u elastic:rag-password -X POST "https://localhost:9200/_security/user/elastic/_password" -H "Content-Type: application/json" -d "{\"password\":\"$NEW_PW\"}"
```

`.env`에 값이 없으면 compose 기본값 `rag-password`가 쓰인다. 공개 저장소에 노출된
값이므로 로컬 개발 외의 환경에서는 반드시 바꾼다.

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

호스트에서 직접 띄워도 Prometheus 설정은 건드리지 않아도 된다. 타겟이
`host.docker.internal`로 잡혀 있어 컨테이너 실행과 호스트 실행 양쪽에 그대로 닿는다.
근거는 `monitoring/prometheus/prometheus.yml` 상단 주석 참고.

컨테이너로 돌던 서비스를 호스트로 옮길 때는 포트가 겹치므로 해당 컨테이너만 내린다.

```bash
docker compose -f docker-compose.yml -f docker-compose.app.yml stop backend
```

Windows에서는 `./gradlew` 대신 `gradlew.bat`을 사용합니다.
