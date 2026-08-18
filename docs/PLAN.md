# Confluence Knowledge Graph RAG 챗봇 — PLAN v2

> Elasticsearch 런타임, 보안, 인덱스, OpenAI embedding 설정은 [`ELASTICSEARCH.md`](ELASTICSEARCH.md)를 최신 기준으로 사용한다. 아래 TEI/BGE-M3 관련 내용은 이전 설계 기록이다.

> 원본: https://github.com/Yiji-1015/Confluence_Chatbot  
> 기존 사내 Confluence 온보딩 RAG 챗봇을 기반으로 한 리팩토링 프로젝트
>
> 목표: 구조 보존형 Confluence 검색을 기반으로 Elasticsearch Hybrid Retrieval + Knowledge Graph + LiteLLM + Spring Boot 기반의 AI Application Backend를 구현한다.

---

## 1. 프로젝트 목표

기존 Confluence RAG 챗봇을 단순 LLM/RAG 데모가 아닌 검색, 그래프, 세션, 모델 게이트웨이, 백엔드가 분리된 AI Application 형태로 리팩토링한다.

주요 목표는 다음과 같다.

- 사내 Confluence 문서 전체를 지식베이스로 색인
- 기존 표/링크/첨부파일 정보를 보존하는 파서 재사용 및 개선
- Elasticsearch 기반 BM25 + Vector Hybrid Retrieval
- 사람·조직·문서·프로젝트 관계를 Neo4j Knowledge Graph로 구성
- 관계형 질문에는 Vector Retrieval 결과에 Graph Context 추가
- 멀티턴 질문을 Standalone Query로 재작성한 뒤 검색
- LiteLLM Gateway를 통한 LLM 호출 통합 및 모델 전환
- Redis 기반 대화 세션 및 Embedding Cache
- Spring Boot와 Python AI Server의 역할 분리
- Langfuse 기반 tracing, experiment 및 RAG 성능 평가
- 로컬에서 전체 시스템을 재현할 수 있는 Docker Compose 환경 구성

통합포털 연동은 범위에서 제외하며 독립 실행 가능한 단일 애플리케이션으로 개발한다.

---

## 2. 전체 아키텍처

```text
[React Frontend / Lovable]
          │
        REST
          │
          ▼
[Spring Boot :8080]
 Application Backend
          │
          ├── Session / API
          ├── Document Metadata
          ├── Job Management
          │
          └──── HTTP ────► [Python FastAPI :8000]
                              AI Engine
                                │
             ┌──────────────────┼───────────────────┐
             │                  │                   │
         [Redis]          [Elasticsearch]        [Neo4j]
      Session/Cache       Hybrid Retrieval     Knowledge Graph
             │
             └──────────────┐
                            ▼
                     [LiteLLM :4000]
                            │
                ┌───────────┴───────────┐
                │                       │
        DeepSeek / OpenAI          [TEI :8081]
              (LLM)              BGE-M3 Embedding

                    [Langfuse]
              Trace / Eval / Experiment
```

### 역할 분리

### Spring Boot

애플리케이션 계층을 담당한다.

- 외부 REST API
- session lifecycle 관리
- 문서 metadata CRUD
- indexing/job 요청 및 상태 관리
- Python AI Server 호출 및 orchestration
- 오류 응답 및 API contract 관리

AI 검색 및 추론 로직은 직접 수행하지 않는다.

### Python FastAPI

AI Engine 역할을 담당한다.

- Confluence 문서 수집
- 문서 파싱
- 청킹
- 임베딩
- Elasticsearch 검색
- Query Rewrite
- Knowledge Graph 구축 및 조회
- Retrieval 결과 병합
- LLM Answer Generation
- Langfuse trace/evaluation 연동

### LiteLLM

LLM 호출을 단일 Gateway로 통합한다.

- 모델 전환
- LLM fallback
- API configuration 중앙화

### Elasticsearch

전체 문서 검색의 기본 Retrieval Engine.

### Neo4j

문서 및 조직 간 관계 검색을 위한 Knowledge Graph.

### Redis

- 대화 session/history
- embedding cache

### Langfuse

운영 의존성이 아닌 관측/평가 계층으로 사용한다.

- LLM/RAG/Graph pipeline trace 확인
- Session 및 generation 단위 observability
- 평가용 Dataset 관리
- Baseline/Proposed experiment 비교
- Code-based evaluator / LLM-as-a-Judge 실행
- latency/token/cost 관측
- 실패 케이스 수집 및 재평가

---

## 3. 데이터 파이프라인

```text
[Confluence API / Local Sample Data]
              │
              ▼
        [Document Parser]
              │
     ┌────────┼─────────┐
     │        │         │
   Table    Link    Attachment
 Preservation
              │
              ▼
           [Chunking]
              │
       ┌──────┴──────┐
       │             │
       ▼             ▼
 [Embedding]    [Entity / Relation]
       │             Extraction
       ▼             │
[Elasticsearch]      ▼
                   [Neo4j]
```

기존 프로젝트에서 구현한 다음 파싱 로직을 최대한 재사용한다.

- 표 구조 처리
- metadata 보존
- 내부 링크 보존
- 첨부파일명 보존
- 문서 단위 식별자 유지

---

## 4. Elasticsearch Retrieval

이번 버전에서는 Vector DB를 Elasticsearch로 통일한다.

ChromaDB는 기존 프로젝트 구현 이력으로만 남기고 v2에서는 사용하지 않는다.

### 검색 방식

- BM25
- dense_vector kNN
- Hybrid Retrieval
- metadata filtering
- Nori 한국어 analyzer
- incremental indexing

기본 검색 흐름:

```text
Query
  │
  ├── BM25
  │
  └── Vector kNN
          │
          ▼
     Result Merge
          │
          ▼
      Top-K Context
```

향후 필요 시 reranking을 추가한다.

---

## 5. Embedding 설계

Embedding 모델 간 벡터 공간이 서로 다르므로 서로 다른 모델을 동일한 Elasticsearch index에서 자동 fallback하지 않는다.

### 기본 구성

사내 임베딩 API에 의존하지 않고 로컬 BGE-M3 단일 모델을 사용한다.

```text
[TEI]
BGE-M3 (local)
     │
     │ OpenAI 호환 /v1/embeddings
     ▼
[LiteLLM :4000]
     │
     ▼
[Python AI Server]
```

### Serving

serving은 HuggingFace Text Embeddings Inference(TEI)를 사용한다.

선택 근거:

- 공개 저장소에서 Docker Compose만으로 전체 재현 가능 (§1 목표)
- OpenAI 호환 `/v1/embeddings`를 제공하므로 LiteLLM에 OpenAI embedding과 동일한 형태로 등록 가능
- CPU/GPU 이미지를 태그 교체로 전환 가능

사내 API를 사용하지 않으므로 embedding fallback chain은 두지 않는다.
단일 모델을 사용하여 embedding space를 유지한다.

dense vector 차원은 1024이며 Elasticsearch mapping의 `dims`와 일치해야 한다.

serving 방식(CPU/GPU, 양자화 여부)이 바뀌면 벡터가 달라지므로 전체 재색인이 필요하다.
따라서 Phase 6 평가 이전에 serving 구성을 확정한다.

Embedding cache key에는 반드시 모델 정보를 포함한다.

```text
emb:{embedding_model}:{sha256(text)}
```

### OpenAI Embedding

OpenAI embedding을 비교 실험할 경우 별도 index를 생성한다.

예:

```text
confluence_bge_m3_v1
confluence_openai_v1
```

서로 다른 embedding model의 vector를 동일 index에서 혼합하지 않는다.

---

## 6. LiteLLM 설계

`litellm/config.yaml`

```yaml
model_list:

  - model_name: deepseek-chat
    litellm_params:
      model: deepseek/deepseek-chat
      api_key: os.environ/DEEPSEEK_API_KEY

  - model_name: gpt-4o-mini
    litellm_params:
      model: openai/gpt-4o-mini
      api_key: os.environ/OPENAI_API_KEY

  - model_name: embedding-local
    litellm_params:
      model: openai/bge-m3
      api_base: os.environ/EMBEDDING_API_URL
      api_key: dummy
```

Python AI Server는 LiteLLM Gateway를 통해 LLM과 Embedding을 호출한다.

```text
base_url=http://litellm:4000
```

LLM 모델 변경은 application code가 아닌 LiteLLM configuration을 통해 수행한다.

LLM fallback과 Embedding fallback은 구분한다.

- LLM: 서로 다른 모델 간 fallback 가능
- Embedding: fallback을 사용하지 않는다 (로컬 BGE-M3 단일 모델, §5)

Embedding 모델을 교체할 경우 fallback이 아닌 별도 index 생성 + 재색인으로 처리한다.

---

## 6-1. Langfuse Observability / Evaluation

Langfuse는 LLM/RAG pipeline의 tracing과 평가를 담당한다.

### Integration 원칙

- LiteLLM을 통한 LLM 호출은 Langfuse trace와 연결한다.
- Python AI Server의 주요 단계도 별도 span으로 기록한다.
- Spring Boot는 사용자 요청의 correlation/session identifier를 전달한다.
- Langfuse 장애가 핵심 chat/retrieval 기능을 중단시키지 않도록 관측 계층으로 분리한다.

권장 trace 구조:

```text
chat_request
   │
   ├── query_rewrite
   │
   ├── hybrid_retrieval
   │      ├── bm25
   │      └── vector_knn
   │
   ├── graph_retrieval (optional)
   │
   ├── context_merge
   │
   └── llm_generation
```

주요 관측 항목:

```text
latency
token usage
LLM cost
retrieved document IDs
graph entity/relation IDs
evaluator scores
session_id
experiment name
```

평가용 dataset과 experiment는 Langfuse에서 관리하되,
Recall@K, Hit Rate@K, MRR 같은 deterministic retrieval metric은
Python evaluator code로 계산하여 experiment 결과와 함께 기록한다.

---

## 7. Knowledge Graph / Ontology

### Node

```text
Person
Team
Document
Concept
System
Project
```

### Relationship

```text
(Person)-[AUTHORED]->(Document)
(Person)-[TOP_CONTRIBUTOR]->(Document)
(Person)-[LAST_MODIFIED]->(Document)
(Person)-[WORKS_IN]->(Team)
(Document)-[BELONGS_TO]->(Team)
(Document)-[BELONGS_TO]->(Project)
(Document)-[LINKS_TO]->(Document)
(Document)-[RELATES_TO]->(Concept)
```

---

## 8. Graph 데이터 생성 원칙

Graph Edge는 출처에 따라 두 종류로 구분한다.

### Deterministic Edge

Confluence metadata에서 직접 생성할 수 있는 관계.

```text
AUTHORED
TOP_CONTRIBUTOR
LAST_MODIFIED
LINKS_TO
```

LLM 추론을 사용하지 않는다.

### LLM-derived Semantic Edge

문서 내용을 기반으로 의미적으로 추출하는 관계.

```text
RELATES_TO
BELONGS_TO
Concept
System
Project
```

LLM이 생성한 관계와 metadata 기반 관계를 구분하여 저장한다.

가능하면 edge에 source/provenance metadata를 저장한다.

---

## 9. Retrieval Routing

Vector RAG를 기본 Retrieval 방식으로 사용한다.

Graph 검색은 Vector Retrieval을 대체하지 않고 관계형 질문에서 추가 Context를 제공한다.

```text
Question
   │
   ▼
Standalone Query Rewrite
   │
   ▼
Vector / BM25 Hybrid Retrieval
   │
   ├─────────────┐
   │             │
   │       Relation Intent?
   │             │
   │            YES
   │             │
   │             ▼
   │       Neo4j 1~2 hop
   │             │
   └──────┬──────┘
          ▼
   Context Merge
          │
          ▼
     LLM Answer
```

예:

```text
"휴가 신청 절차가 뭐야?"
→ Elasticsearch Retrieval

"A 시스템 담당자는 누구고 관련 가이드는 뭐야?"
→ Elasticsearch Retrieval
+ Neo4j Person/Team/Document 관계
```

---

## 10. Multi-turn Conversation

후속 질문을 그대로 Retrieval Query로 사용하지 않는다.

예:

```text
User:
"A 프로젝트의 구축 비용이 얼마야?"

User:
"그건 누가 담당해?"
```

두 번째 질문 `"그건 누가 담당해?"` 자체로는 검색 의미가 부족하다.

따라서 최근 대화 history를 기반으로 Standalone Query를 생성한다.

```text
[History]
      +
[Current Query]
      │
      ▼
Query Rewriter
      │
      ▼
"A 프로젝트 구축 담당자는 누구인가?"
      │
      ▼
Retrieval
```

최종 답변 생성 시에는 원래의 conversation history와 retrieved context를 함께 사용한다.

---

## 11. Redis Session

### Conversation History

```text
chat:{session_id}
```

value:

```json
[
  {"role": "user", "content": "..."},
  {"role": "assistant", "content": "..."}
]
```

TTL:

```text
24h
```

최근 N턴만 Query Rewrite 및 Answer Generation에 사용한다.

기본값:

```text
N = 10
```

---

## 12. Redis Cache

v2 초기 구현에서는 cache 범위를 최소화한다.

### 1. Conversation History

```text
chat:{session_id}
TTL: 24h
```

### 2. Embedding Cache

```text
emb:{embedding_model}:{sha256(text)}
TTL: 30d
```

동일 문서 또는 동일 chunk의 반복 임베딩 비용을 줄인다.

### Optional

검색 결과 캐시는 성능 측정 이후 필요할 경우 추가한다.

```text
search:{index_version}:{sha256(query)}
```

검색 index version을 key에 포함하여 문서 재색인 시 stale cache 문제를 줄인다.

### 제외

초기 버전에서는 answer cache를 구현하지 않는다.

멀티턴 context, 문서 변경, LLM model 변경 등으로 인해 cache invalidation 복잡도가 높기 때문이다.

---

## 13. Spring Boot API

예상 API:

```text
POST /api/chat
POST /api/sessions
GET /api/sessions/{sessionId}
POST /api/documents/index
GET /api/jobs/{jobId}
GET /api/documents/{documentId}
```

Spring Boot는 Python AI Server와 HTTP로 통신한다.

```text
Spring
   │
   │ POST /internal/chat
   ▼
FastAPI
```

Spring이 Elasticsearch 또는 Neo4j에 직접 접근하는 구조는 초기 버전에서는 사용하지 않는다.

AI 관련 데이터 접근은 Python AI Server를 통해 수행한다.

---

## 14. 기술 스택

| 영역 | 기술 |
|---|---|
| Application Backend | Spring Boot 3.5.x |
| Language | Java |
| AI Engine | Python / FastAPI |
| LLM Gateway | LiteLLM |
| Search Engine | Elasticsearch |
| Korean Analyzer | Nori |
| Graph DB | Neo4j |
| Cache / Session | Redis |
| Embedding | BGE-M3 (local) |
| Embedding Serving | TEI (Text Embeddings Inference) |
| LLM | DeepSeek / OpenAI |
| Evaluation / Trace | Langfuse |
| Container | Docker Compose |
| Frontend | React / Lovable |
| Search Debugging | Kibana |

Kibana는 Elasticsearch 개발/디버깅 도구로 사용하며 프로젝트 핵심 기능으로 취급하지 않는다.

Langfuse는 tracing, 실험 추적 및 평가를 위한 개발/관측 도구이며 핵심 비즈니스 로직의 런타임 필수 의존성으로 두지 않는다.

---

## 15. Docker Compose

초기 infrastructure:

```text
Elasticsearch
Kibana
LiteLLM
TEI (Embedding)
Redis
Neo4j
```

Application:

```text
Spring Boot
FastAPI
```

초기 개발 단계에서는 Application을 IDE에서 실행하고 Infrastructure만 Docker Compose로 실행할 수 있다.

최종 단계에서 Spring/FastAPI까지 containerize한다.

---

## 16. 프로젝트 구조

```text
confluence-kg-chatbot/
├── docs/
│   └── PLAN.md
├── backend/
│   └── Spring Boot
├── ai-server/
│   └── FastAPI
├── evaluation/
│   ├── datasets/
│   ├── evaluators/
│   └── experiments/
├── litellm/
│   └── config.yaml
├── elasticsearch/
│   └── Dockerfile
├── frontend/
│   └── React / Lovable
├── docker-compose.yml
└── README.md
```

---

## 17. Milestone

### Phase 1 — Retrieval Core

- [ ] Repository 구조 정리
- [ ] Elasticsearch + Nori
- [ ] 기존 Confluence parser 이식
- [ ] 전체 문서 chunking
- [ ] BGE-M3 embedding
- [ ] BM25 검색
- [ ] Vector kNN 검색
- [ ] Hybrid Retrieval
- [ ] 기본 Retrieval 평가

### Phase 2 — Application Backend

- [ ] FastAPI Retrieval API
- [ ] Spring Boot 기본 프로젝트
- [ ] Spring ↔ FastAPI HTTP 연동
- [ ] Chat API 구현
- [ ] Job/API contract 정의

### Phase 3 — LLM Gateway / Observability

- [ ] LiteLLM 구성
- [ ] DeepSeek/OpenAI 모델 전환
- [ ] LLM fallback 검증
- [ ] Langfuse 연결
- [ ] LiteLLM generation trace 확인
- [ ] Python AI pipeline span 설계

### Phase 4 — Multi-turn / Redis

- [ ] Redis session
- [ ] conversation history
- [ ] Query Rewrite
- [ ] embedding cache
- [ ] TTL 정책 적용

### Phase 5 — Knowledge Graph

- [ ] Ontology schema 확정
- [ ] metadata 기반 deterministic edge 구축
- [ ] LLM entity/relation extraction
- [ ] Neo4j 적재
- [ ] relation intent 판별
- [ ] Graph + Vector Context Merge

### Phase 6 — Evaluation

- [ ] Langfuse evaluation dataset 생성
- [ ] BM25 baseline experiment
- [ ] Hybrid Retrieval experiment
- [ ] Vector + Graph experiment
- [ ] 관계형 질문 subset 평가
- [ ] deterministic retrieval metrics 측정
- [ ] LLM-as-a-Judge 평가
- [ ] latency / token / cost 측정
- [ ] retrieval failure 분석
- [ ] 실패 trace를 evaluation dataset에 재추가

### Phase 7 — Frontend / Demo

- [ ] Lovable frontend
- [ ] 출처 표시
- [ ] 담당자/조직 metadata 표시
- [ ] demo dataset
- [ ] 통합 테스트

### Phase 8 — Packaging

- [ ] Spring Boot Dockerfile
- [ ] FastAPI Dockerfile
- [ ] 전체 Docker Compose 통합
- [ ] README
- [ ] architecture diagram
- [ ] 실행 방법 정리
- [ ] 평가 결과 표/그래프 정리

---

## 18. 평가 계획

포트폴리오에서는 기능 구현 자체보다 각 설계 선택이 실제 성능 개선으로 이어졌는지 검증하는 것을 목표로 한다.

### 18-1. 실험 구성

#### Baseline A — BM25

```text
BM25
```

#### Baseline B — Hybrid Retrieval

```text
BM25 + Vector kNN
```

#### Proposed — Hybrid + Knowledge Graph

```text
BM25 + Vector kNN
+ Knowledge Graph Context
```

필요 시 Query Rewrite 적용 여부도 별도 ablation으로 비교한다.

```text
Hybrid
vs
Hybrid + Query Rewrite
```

### 18-2. Evaluation Dataset

평가 질문은 최소 다음 유형으로 구분한다.

```text
Fact
Procedure
Person / 담당자
Team / 조직
Document Relation
Project Relation
Multi-turn Follow-up
```

각 질문에는 가능한 범위에서 다음 reference를 구축한다.

```text
question
reference_answer
relevant_document_ids
relevant_entity_ids
question_type
```

실제 사내 데이터는 공개 저장소에 포함하지 않는다.

공개 포트폴리오에서는 별도 demo dataset 또는 비식별/합성 평가셋을 사용한다.

### 18-3. Deterministic Retrieval Metrics

retrieval 성능은 LLM Judge만 사용하지 않고 code 기반 지표를 함께 사용한다.

예:

```text
Recall@K
Hit Rate@K
MRR
```

reference document ID가 준비된 질문에 대해 측정한다.

관계형 질문에서는 별도로 다음을 측정할 수 있다.

```text
Entity Hit Rate
Correct Relation Hit
```

### 18-4. LLM Answer Evaluation

Langfuse의 evaluation/experiment 기능을 이용해 다음 항목을 평가한다.

#### Correctness
생성 답변과 reference answer의 의미적 일치 여부.

#### Relevance
답변이 사용자 질문에 직접적으로 대응하는지 평가.

#### Groundedness
답변이 retrieved context에 근거하고 있는지 평가.

#### Retrieval Relevance
검색된 문서가 질문과 실제로 관련 있는지 평가.

필요 시 LLM-as-a-Judge를 사용한다.

### 18-5. Langfuse Experiment

각 구조를 별도 Experiment로 실행한다.

```text
exp_bm25_v1
exp_hybrid_v1
exp_hybrid_graph_v1
exp_hybrid_graph_rewrite_v1
```

동일 evaluation dataset을 사용하여 실험 간 성능을 비교한다.

동일한 evaluation dataset을 기준으로 experiment별 evaluator score와 system metrics를 비교한다.

### 18-6. System Metrics

품질뿐 아니라 시스템 비용도 측정한다.

```text
Retrieval Latency
Graph Query Latency
End-to-End Latency
LLM Token Usage
LLM Cost
```

특히 Graph Context 추가 전후의 품질 개선과 latency 증가를 함께 비교한다.

최종 결론에서는 다음과 같은 trade-off를 설명할 수 있어야 한다.

```text
Graph를 모든 질문에 적용하는 것이 아니라
관계형 질문에 선택적으로 적용했을 때
품질 개선 대비 latency 비용이 가장 합리적이었다.
```

### 18-7. Failure Analysis

평가 점수만 제시하지 않고 실패 유형을 분석한다.

```text
Retrieval Miss
Wrong Entity
Wrong Relation
Insufficient Context
Query Rewrite Failure
Hallucination
Stale Metadata
```

Langfuse trace를 이용하여 Query Rewrite, retrieval, graph query, LLM generation 중 어느 단계에서 실패했는지 확인한다.

대표 실패 사례는 수정 후 regression test dataset에 추가한다.

```text
Failure
   ↓
Trace Analysis
   ↓
Dataset 추가
   ↓
Pipeline 수정
   ↓
Re-evaluation
```

---

## 19. 포트폴리오 성과 제시 방식

최종 README에서는 단순히 "GraphRAG를 구현했다"가 아니라 비교 결과를 중심으로 작성한다.

예:

```text
BM25 → Hybrid Retrieval 적용 후 Recall@5 개선

Hybrid → Hybrid + Graph 적용 후
관계형 질문의 Entity Hit Rate 개선

Query Rewrite 적용 후
멀티턴 Follow-up retrieval 성공률 개선
```

실제 수치는 실험 이후에만 기입한다.

성과가 없는 기능은 억지로 개선 효과가 있다고 주장하지 않고, 품질/latency trade-off 또는 실패 원인을 기록한다.

---

## 20. 프로젝트 범위에서 제외

초기 버전에서는 다음을 구현하지 않는다.

- 대규모 사용자 인증/인가
- SSO
- 통합포털 연동
- Kubernetes 배포
- 고가용성 Redis
- Elasticsearch Cluster 구성
- 분산 Transaction
- Answer Cache
- ChromaDB/Elasticsearch 이중 지원
- 여러 Embedding Space를 하나의 Index에서 혼합
- 복잡한 Ontology Reasoner
- 과도한 Frontend 기능

필요한 경우 후속 버전에서 확장한다.

---

## 21. Open Issues

- [ ] 실제 Confluence sample 문서를 기준으로 Ontology schema 검증
- [x] Local BGE-M3 serving 방식 결정 — TEI (§5)
- [ ] LiteLLM에서 TEI embedding endpoint 연동 방식 검증
- [ ] TEI 이미지 버전 태그 고정 및 배포 환경 CPU/GPU 결정
- [ ] Elasticsearch Hybrid Retrieval score fusion 방식 결정
- [ ] Graph relation intent 분류 방식 결정
- [ ] Knowledge Graph context와 Vector context 병합 방식 결정
- [ ] Retrieval evaluation dataset 구성
- [ ] Langfuse tracing 범위 및 evaluator 정의
