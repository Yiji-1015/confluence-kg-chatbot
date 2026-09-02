# Elasticsearch 운영 기준

> 상태: 구현 완료 (2026-08-19, docker-compose + ai-server 기준 검증됨)
>
> 이 문서는 Elasticsearch 런타임과 인덱스 설정의 기준 문서다. `PLAN.md`의 기존 TEI/BGE-M3 관련 값보다 이 문서를 우선한다.

## 범위

현재 단계는 단일 VM의 Docker Compose 배포를 대상으로 한다. Kubernetes는 이후 ECK(Elastic Cloud on Kubernetes) 학습 단계에서 검토하며, 지금은 Kubernetes 매니페스트를 만들지 않는다.

## 버전

| 항목 | 값 | 이유 |
| --- | --- | --- |
| Elasticsearch | `8.15.0` | 현재 프로젝트 버전을 유지해 별도 업그레이드를 섞지 않는다. |
| Kibana | `8.15.0` | Elastic Stack 제품은 동일 버전을 사용한다. |
| 라이선스 | `basic` | 초기 기능에 trial 전용 기능이 필요 없다. |
| 한국어 분석 | `analysis-nori` | Elasticsearch 이미지와 동일 버전의 플러그인을 설치한다. |

버전 업그레이드는 snapshot 생성과 복구 검증 후 별도 작업으로 수행한다.

## 네트워크와 보안

| 설정 | 기준값 |
| --- | --- |
| `cluster.name` | `confluence-rag` |
| `node.name` | `rag-es-01` |
| `discovery.type` | `single-node` |
| `xpack.security.enabled` | `true` |
| HTTP TLS | 활성화 |
| Elasticsearch 호스트 포트 | `127.0.0.1:9200:9200` |
| Kibana 호스트 포트 | `127.0.0.1:5601:5601` |

- `9200`과 `5601`을 공인 인터페이스에 직접 공개하지 않는다.
- 애플리케이션 컨테이너는 Compose 내부 네트워크의 `https://elasticsearch:9200`을 사용한다.
- 외부 관리 접속은 SSH tunnel 또는 HTTPS reverse proxy를 사용한다.
- Elasticsearch CA와 인증서는 `elasticsearch-certutil`로 생성하고 읽기 전용으로 mount한다.
- 실제 비밀번호, 인증서 private key, API key는 Git에 저장하지 않는다. 로컬에서는 Git에서 제외된 `.env`, 외부 서버에서는 Docker secrets 또는 서버 secret manager를 사용한다.
- 애플리케이션은 `elastic` superuser가 아닌 전용 최소 권한 사용자를 사용한다.

Elastic 공식 문서는 self-managed 운영 환경에 인증, transport TLS, HTTP TLS 적용을 권장한다.

- [Self-managed security setup](https://www.elastic.co/docs/deploy-manage/security/self-setup)
- [Install Elasticsearch with Docker](https://www.elastic.co/docs/deploy-manage/deploy/self-managed/install-elasticsearch-with-docker)

## 리소스

| 설정 | 초기값 |
| --- | --- |
| 컨테이너 메모리 제한 | `${ES_MEMORY_LIMIT:-2g}` |
| JVM heap | Elasticsearch 자동 sizing |
| 데이터 볼륨 | `es_data:/usr/share/elasticsearch/data` |
| restart policy | `unless-stopped` |

- 현재 `ES_JAVA_OPTS=-Xms1g -Xmx1g` 고정값은 제거하고 자동 sizing을 사용한다.
- `2g`는 개발·초기 단일 서버 기본값이다. 실제 문서량과 heap pressure를 본 뒤 `ES_MEMORY_LIMIT`만 조정한다.
- 데이터와 snapshot repository는 같은 볼륨을 사용하지 않는다.

[Elastic JVM settings](https://www.elastic.co/docs/reference/elasticsearch/jvm-settings)

## Healthcheck

Docker healthcheck는 인증서와 인증 정보를 사용해 다음 API를 호출한다.

```text
GET /_cluster/health?wait_for_status=yellow&timeout=5s
```

| 항목 | 값 |
| --- | --- |
| interval | `10s` |
| timeout | `5s` |
| retries | `12` |
| start period | `60s` |

단일 노드는 replica가 할당되지 않아 `yellow`가 될 수 있으므로 readiness 기준은 `yellow` 이상으로 둔다. 서비스 healthcheck는 재시작과 의존 서비스 시작 순서에 사용하며, 장기 추세 모니터링을 대신하지 않는다.

[Cluster health API](https://www.elastic.co/docs/api/doc/elasticsearch/operation/operation-cluster-health)

## 클러스터, 노드, 인덱스, shard 관계

Elasticsearch에서는 노드가 먼저 클러스터를 구성하고, 인덱스가 primary shard로 나뉘어 노드에 배치된다.

```text
Elasticsearch cluster
├── node 1
│   └── primary shard 0
├── node 2
│   └── primary shard 1
└── node 3
    └── replica shard 0, 1
```

- Elasticsearch 프로세스 또는 컨테이너 인스턴스 하나가 Elasticsearch node 하나다.
- 동일한 `cluster.name`을 사용하는 node들이 Elasticsearch cluster를 구성한다.
- 인덱스 하나는 여러 node에 걸쳐 저장될 수 있고, node 하나는 여러 인덱스의 shard를 저장할 수 있다.
- vector는 일반 문서 필드와 함께 shard에 저장된다. kNN 검색은 관련 shard에서 실행되고 결과를 합친다.
- Kubernetes node와 Elasticsearch node는 다른 개념이다. Kubernetes에서는 보통 Elasticsearch Pod 하나가 Elasticsearch node 하나가 된다.

### Shard 생성과 확장

- `number_of_shards`는 인덱스를 생성할 때 정한다. 데이터 크기가 증가해도 primary shard가 자동으로 추가되지 않는다.
- Elasticsearch는 생성된 shard를 node에 자동 배치하지만, primary shard 수를 자동 변경하지 않는다.
- `number_of_replicas`는 실행 중 변경할 수 있다. Replica는 장애 대응과 읽기 처리량에 사용하며 primary와 같은 node에 배치될 수 없다.
- Primary shard 수를 늘려야 하면 더 많은 shard를 가진 새 버전 인덱스를 만들고 재색인한 뒤 alias를 전환한다. Split API나 rollover는 실제 필요가 확인될 때 검토한다.

현재 초기값은 단일 node에 `1 primary shard / 0 replicas`다. 데이터량, shard 크기, 색인 속도, 검색 latency를 측정한 뒤에만 shard 수를 늘린다.

[Clusters, nodes, and shards](https://www.elastic.co/docs/deploy-manage/distributed-architecture/clusters-nodes-shards)

## 인덱스 기준

OpenAI `text-embedding-3-small`의 기본 1536차원 벡터를 사용한다.

| 항목 | 값 |
| --- | --- |
| concrete index | `confluence-openai-v1` |
| read/write alias | `confluence-current` (2026-09-02 연결 완료) |
| primary shards | `1` |
| replicas | `0` |
| vector field | `embedding` |
| vector type | `dense_vector` |
| dimensions | `1536` |
| similarity | `cosine` |

권장 핵심 mapping:

```json
{
  "settings": {
    "number_of_shards": 1,
    "number_of_replicas": 0,
    "analysis": {
      "tokenizer": {
        "ko_nori_tokenizer": {
          "type": "nori_tokenizer",
          "decompound_mode": "mixed"
        }
      },
      "analyzer": {
        "ko_nori": {
          "type": "custom",
          "tokenizer": "ko_nori_tokenizer",
          "filter": ["lowercase"]
        }
      }
    }
  },
  "mappings": {
    "properties": {
      "chunk_id": { "type": "keyword" },
      "page_id": { "type": "keyword" },
      "space_key": { "type": "keyword" },
      "title": {
        "type": "text",
        "analyzer": "ko_nori",
        "fields": { "keyword": { "type": "keyword" } }
      },
      "content": { "type": "text", "analyzer": "ko_nori" },
      "chunk_index": { "type": "integer" },
      "ancestor_ids": { "type": "keyword" },
      "url": { "type": "keyword", "index": false },
      "updated_at": { "type": "date" },
      "metadata": { "type": "flattened" },
      "embedding": {
        "type": "dense_vector",
        "dims": 1536,
        "index": true,
        "similarity": "cosine"
      }
    }
  }
}
```

- 서로 다른 embedding model의 벡터를 같은 concrete index에 섞지 않는다.
- 모델이나 차원이 바뀌면 새 버전 index를 만들고 전체 재색인한 뒤 alias를 전환한다.
- hybrid score fusion과 reranking은 검색 구현 단계에서 결정하며 mapping에 미리 넣지 않는다.

[Dense vector field](https://www.elastic.co/docs/reference/elasticsearch/mapping-reference/dense-vector)

## 백업

- 실행 중인 데이터 볼륨 복사를 백업으로 취급하지 않는다.
- Elasticsearch snapshot repository를 사용한다.
- 외부 서버에서는 서버 장애와 분리된 S3 호환 object storage를 사용한다.
- 초기 SLM 기준은 하루 1회, `expire_after: 14d`, `min_count: 7`, `max_count: 30`이다.
- 최초 설정 후 snapshot 생성과 빈 테스트 cluster 복구를 한 번 검증한다.

[Snapshot and restore](https://www.elastic.co/docs/deploy-manage/tools/snapshot-and-restore)

## 모니터링 역할 분리

| 도구 | 담당 |
| --- | --- |
| Docker healthcheck | 프로세스와 cluster readiness |
| Elastic Stack Monitoring | node, JVM heap, CPU, disk, shard, indexing/search 상태 |
| Langfuse | retrieval, embedding, LLM trace와 latency, 비용, 품질 |

초기 Compose에는 healthcheck만 넣는다. 실제 외부 서버 운영 시 Elastic Agent와 Stack Monitoring을 추가한다. Langfuse에는 `retrieval` span을 기록하되 Elasticsearch 인프라 모니터링을 맡기지 않는다.

[Elastic Stack Monitoring](https://www.elastic.co/docs/deploy-manage/monitor/stack-monitoring)

## 구현 완료 조건

- Compose 설정 검증이 통과한다.
- TLS와 인증 없이는 Elasticsearch API에 접근할 수 없다.
- Nori plugin이 설치되고 한국어 analyze API 검증이 통과한다.
- healthcheck가 `healthy`가 된다.
- 1536차원 테스트 벡터를 색인하고 kNN 검색할 수 있다.
- Elasticsearch와 Kibana가 같은 버전을 사용한다.
- secret 또는 private key가 Git diff에 포함되지 않는다.
