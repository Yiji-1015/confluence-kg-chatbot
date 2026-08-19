# Elasticsearch 데이터 백업 / 이전

> 이 문서는 `es_data` Docker Volume(Elasticsearch 인덱스 데이터)을 다른 컴퓨터로 옮기는 절차다.
> 목적은 재해복구(DR)가 아니라 **개발 환경 이전**(예: 이 컴퓨터 → 집 컴퓨터)이다.
>
> 운영 환경의 정식 백업(주기적 스냅샷, 보관 정책)은 `ELASTICSEARCH.md`의 "백업" 절을 따로 참고한다.
> 여기 방식(볼륨 통째로 tar 압축)은 그 문서에서 "정식 백업으로 취급하지 않는다"고 명시한 방법이며,
> 지금 단계에서는 환경 이전 목적으로만 사용한다.

## 전제 조건

- 옮길 컴퓨터에서도 저장소 폴더 이름을 **`confluence-kg-chatbot`으로 동일하게** 클론해야 한다.
  Docker Compose는 볼륨 이름을 `{폴더 이름}_es_data` 형태로 자동 생성하므로, 폴더 이름이 다르면 아래 명령의 볼륨 이름도 다시 맞춰야 한다.
- `.env`는 git에 포함되지 않으므로 별도로 옮겨야 한다 (USB, 비밀번호 관리자 등 안전한 수단 사용, 공개 채널로 전송 금지).
- `elasticsearch/certs/`는 옮길 필요 없다. 새 컴퓨터에서 `es-setup`이 자동으로 새로 생성한다 (인증서는 서버마다 새로 만드는 게 정상).

## 1. 백업 — 기존 컴퓨터에서

```bash
# 데이터 일관성을 위해 잠깐 멈춘다
docker compose stop elasticsearch

# 볼륨 내용을 tar.gz로 압축 (현재 폴더에 es_data_backup.tar.gz 생성)
docker run --rm \
  -v confluence-kg-chatbot_es_data:/data \
  -v "$(pwd)":/backup \
  alpine tar czf /backup/es_data_backup.tar.gz -C /data .

# 다시 켠다
docker compose start elasticsearch
```

생성된 `es_data_backup.tar.gz`와 `.env` 파일을 함께 옮긴다.

## 2. 복원 — 새 컴퓨터에서

```bash
# 1. 저장소 클론 (폴더 이름을 confluence-kg-chatbot으로 동일하게)
git clone <repo-url> confluence-kg-chatbot
cd confluence-kg-chatbot

# 2. 옮겨온 .env 파일을 저장소 루트에 넣는다

# 3. 볼륨을 먼저 만들기 위해 es-setup만 한 번 띄웠다 내린다 (볼륨만 생성됨)
docker compose up -d es-setup
docker compose down

# 4. 백업 파일 안의 내용을 볼륨에 풀어넣는다
docker run --rm \
  -v confluence-kg-chatbot_es_data:/data \
  -v "$(pwd)":/backup \
  alpine sh -c "cd /data && tar xzf /backup/es_data_backup.tar.gz"

# 5. 정상 기동
docker compose up -d
```

## 확인

```bash
ELASTIC_PW=$(grep '^ELASTIC_PASSWORD=' .env | cut -d= -f2)
curl -s --cacert elasticsearch/certs/ca/ca.crt -u "elastic:${ELASTIC_PW}" \
  "https://localhost:9200/_cat/indices?v"
```

옮기기 전 컴퓨터에 있던 인덱스(`confluence-openai-v1` 등)가 그대로 보이면 복원 성공.
