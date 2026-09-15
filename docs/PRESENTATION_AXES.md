# EY 데이터분석 인턴 면접 — 프로젝트 발표 축

> 10분 발표에서 기능을 나열하지 않고, 데이터·검색·평가·서비스화의 의사결정 축으로 묶기 위한 콘텐츠 구조.
>
> 본문은 `★`, 짧게 언급하거나 질문 대비용은 `△`로 표시한다.

알고리즘 원리·실제 코드 경로·설정값·한계는 [발표 축별 상세 문서 목차](presentation/README.md)에 정리했다. 아래는 내용 선정용 요약이며, 장표 구성은 아직 확정하지 않았다.

## 1. 데이터 이해·정제 ★

[상세 코드 해설: 데이터 이해·정제](presentation/01-data-preparation.md)

### 포함 기능

- Confluence 수집과 페이지네이션
- HTML 정제
- 표를 Markdown 형태의 텍스트로 변환
- 링크 텍스트 보존
- 카테고리·문서 계층 경로 추출
- 약 800자 청킹과 메타데이터 부여

### 발표 메시지

> RAG 품질은 LLM 호출 이전에, 비정형 문서를 얼마나 검색 가능한 데이터로 구조화했는지에서 결정됩니다.

### 보여줄 수 있는 코드·문서

- `ai-server/app/core/confluence_client.py`
- `ai-server/app/parser/confluence_parser.py`
- `docs/SLIDES_RETRIEVAL.md` — 임베딩 스키마·파싱 관련 내용

### 임베딩 스키마·한국어 분석기

| 필드 | 역할 |
|---|---|
| `title`, `text` | Nori BM25 키워드 검색 |
| `text_vector` | 1536차원 cosine kNN 의미 검색 |
| `chunk_id` | 청크의 고유 ID 및 Elasticsearch `_id` |
| `doc_id` | 같은 원본 문서 청크 묶기·삭제 동기화 |
| `updated_at` | 증분 색인·최신성 가산점 |
| `chunk_index`, `total_chunks` | 문서 본문 순서 복원 |

Nori 형태소 분석기에는 품사 필터를 넣어 조사·어미 같은 문법 기능어를 제거하고, 영문은 lowercase로 통일한다. 드문 어미가 높은 IDF를 받아 핵심어보다 검색 점수에 영향을 주는 BM25 왜곡을 막기 위한 판단이다.

관련 코드:

- `ai-server/app/retrieval/es_client.py` — `create_confluence_index()`
- `ai-server/app/config.py`

---

## 2. 검색 품질 설계 ★

[상세 코드 해설: 검색 품질 설계](presentation/02-retrieval.md)

### 포함 기능

- Nori BM25와 품사 필터
- Dense Vector kNN 의미 검색
- BM25 후보 청크 50개 + kNN 후보 청크 50개
- RRF(Reciprocal Rank Fusion) 순위 결합
- 후보 청크의 수정 시각에 따른 최신성 가산점
- 서로 다른 문서 5개 선택
- 선택 문서의 청크를 순서대로 이어붙여 문맥 재조립

### 발표 메시지

> 키워드 일치와 의미 유사성은 서로 다른 실패를 보완합니다. 두 점수를 억지로 합치지 않고, 순위를 기준으로 RRF 결합해 두 검색이 함께 지지하는 결과를 우선했습니다.

### 꼭 구분할 점

```text
후보 단계: BM25 청크 50 + kNN 청크 50
LLM 입력: 서로 다른 문서 최대 5개, 문서당 3,000자를 기준으로 청크 추가 중단
```

후보 청크 50개를 LLM이 전부 읽는 구조가 아니다. 청크 단위로 추가하므로 3,000자를 넘길 수 있으며, overlap 중복 제거나 원본 HTML 복원은 하지 않는다.

### 보여줄 수 있는 코드·문서

- `ai-server/app/retrieval/es_client.py`
- `ai-server/app/retrieval/query_builder.py`
- `docs/RETRIEVAL.md`
- `docs/SLIDES_RETRIEVAL.md`

---

## 3. 증분 색인·삭제·비용 관리 ★

[상세 코드 해설: 증분 색인·삭제·비용](presentation/03-ingestion.md)

### 포함 기능

- `updated_at` 기반 증분 색인
- 변경되지 않은 문서의 임베딩 호출 생략
- 문서가 짧아질 때 고아 청크 제거
- Confluence 원본에서 삭제된 문서의 ES 동기화 삭제
- 부분 수집 시 삭제 동기화를 막는 안전장치
- 인덱스 alias 사용과 수동 전환을 위한 구조

### 발표 메시지

> 변경되지 않은 문서는 재처리를 생략하고, 수정되거나 삭제된 문서는 검색 데이터에 반영했습니다.

### 고아 청크 예시

```text
수정 전: [chunk_0][chunk_1][chunk_2][chunk_3][chunk_4]
수정 후: [chunk_0][chunk_1][chunk_2]
```

새로 생성되지 않는 `chunk_3`, `chunk_4`가 남으면 삭제된 옛 내용이 검색될 수 있다. 재색인 전 문서의 기존 청크를 먼저 모두 지워 해결한다.

### 보여줄 수 있는 코드·문서

- `ai-server/scripts/ingest.py`
- `ai-server/app/retrieval/es_client.py`
- `docs/RETRIEVAL.md` — 증분·삭제 처리 절

### 증분 색인의 실제 순서

```text
Confluence 문서·last_updated 수집
  -> 전체 수집 조건을 충족하면 원본에서 삭제된 문서의 ES 청크 정리
  -> ES의 doc_id별 updated_at 집계
  -> 같은 문서는 임베딩·색인 생략
  -> 달라진 문서는 파싱 / 청킹
  -> 달라진 문서는 기존 청크 전체 삭제
  -> 임베딩 / bulk 색인
```

변경되지 않은 문서는 임베딩 API 호출을 생략해 비용을 줄인다. 실제 절감액은 별도 측정이 필요하다. 기존 청크 삭제와 새 색인은 원자적 작업이 아니므로 중간 실패 시 누락 위험이 있다. 인덱스 매핑처럼 소급 적용할 수 없는 변경에는 새 concrete index와 alias 전환을 활용할 수 있지만, 현재 수집 스크립트가 전체 마이그레이션을 자동 수행하지는 않는다.

---

## 4. 평가와 분석 인사이트 ★

[상세 코드 해설: 평가·분석 인사이트](presentation/04-evaluation.md)

### 포함 기능

- 검색: hit@5, MRR
- 생성: answer correctness, 자체 faithfulness
- 표준 평가: RAGAS faithfulness, context precision
- 오답 원인 분류
- 평가셋 편향 발견
- 제목 단어를 제거한 paraphrase 대조군 생성
- BM25, kNN, RRF 방식 비교

### 발표 메시지

> 좋은 점수를 보여주는 것보다, 그 점수가 어떤 질문 조건에서 유효한지 검증하는 일이 더 중요했습니다. 초기 평가셋이 키워드 검색에 유리하다는 편향을 발견해 같은 정답 문서를 겨냥하는 대조군으로 다시 측정했습니다.

### 대표 결과

| 방식 | 제목 단어를 줄인 paraphrase 질문의 MRR |
|---|---:|
| BM25 단독 | 0.390 |
| 하이브리드 RRF | 0.560 |

측정 조건: 583문서·3,211청크·검색 평가 38문항.

### 전체 품질 지표 (2026-09-14)

| 계층 | 지표 | 값 | 채점 문항 |
|---|---|---:|---:|
| 검색 | hit@5 | 0.974 | 38 / 38 |
| 검색 | MRR | 0.908 | 38 / 38 |
| 생성 | answer correctness | 0.958 | 45 / 45 |
| 생성 | 자체 faithfulness | 0.973 | 45 / 45 |
| 생성 | RAGAS faithfulness | 0.856 | 45 / 45 |
| 생성 | RAGAS context precision | 0.851 | 45 / 45 |

검색 실패·컨텍스트 미준수·이해/추론 실패의 후보 원인을 이 지표 조합으로 분류한다. 확정적인 원인 판정은 아니다. 실서비스와 평가가 검색·프롬프트·모델 선택 함수를 공유하지만, 세션·멀티턴까지 같은 경로로 평가하는 것은 아니다.

### 보여줄 수 있는 코드·문서

- `ai-server/evaluation/run_qa.py`
- `ai-server/evaluation/compare_fusion.py`
- `ai-server/evaluation/generate_paraphrase_dataset.py`
- `docs/EVALUATION.md`

---

## 5. AI 서비스화 △

[상세 코드 해설: AI 서비스화](presentation/05-ai-serving.md)

### 포함 기능

- LiteLLM을 통한 임베딩·LLM 호출 단일화
- 질문 길이와 대화 이력 길이에 따른 모델 라우팅
- 멀티턴 이력을 답변 생성에 전달
- 후속 질문의 검색어에 직전 사용자 발화를 붙이는 검색 보정
- 답변과 Confluence 원문 출처 링크 반환

### 모델 라우팅 규칙

| 조건 | 모델 | 의도 |
|---|---|---|
| 질문이 12자 미만 | `gpt-4o` | 단서가 적은 짧은 질문의 의도 파악 |
| 질문과 이력의 합이 1,000자 이상 | `gpt-4o` | 긴 맥락의 복잡도 대응 |
| 그 외 | `deepseek-chat` | 일반 질문의 속도·비용 효율 |

이 규칙은 최적 모델을 증명한 실험 결과라기보다, 질문 복잡도에 따라 비용과 응답 품질을 분리하려는 제품 정책으로 설명하는 편이 정확하다.

### 발표 메시지

> 검색 파이프라인을 실제 사용 흐름으로 확장하기 위해, 질문 복잡도에 따른 모델 선택과 멀티턴 후속 질문의 주제어 보정을 추가했습니다.

### 보여줄 수 있는 코드·문서

- `ai-server/app/llm/litellm_client.py`
- `ai-server/app/llm/model_router.py`
- `ai-server/app/retrieval/query_builder.py`
- `ai-server/app/api/v1/chat.py`

---

## 6. 세션·백엔드·운영 △

[상세 코드 해설: 세션·백엔드·운영](presentation/06-backend-operations.md)

### 포함 기능

- PostgreSQL: 전체 대화와 출처 영속화
- Redis: 최근 5턴, TTL 30분 캐시
- Redis 만료 시 DB에서 최근 이력 복원
- Spring MVC 기반 채팅·세션 조회·삭제 API
- Prometheus/Grafana: 전체 요청의 지연·오류·병목 분석
- Langfuse: 요청별 검색·모델·단계 trace
- Docker Compose 계층 분리와 자원 제한

### 세션 저장소의 역할

| 저장소 | 저장 내용 | 목적 |
|---|---|---|
| PostgreSQL | 대화방, 전체 메시지, 출처 | 영구 보관·과거 대화 조회 |
| Redis | 최근 5턴(최대 10개 메시지), TTL 30분 | 다음 LLM 요청에 빠르게 전달 |

Redis 이력이 만료되면 PostgreSQL에서 최근 메시지를 읽어 Redis를 다시 채운다(Cache-Aside). 멀티턴 기능은 실제 동작을 검증했지만, 현재 평가셋에는 멀티턴 문항이 없다.

### 관측 도구의 역할

| 도구 | 답하는 질문 |
|---|---|
| Langfuse | 이 요청 하나가 왜 느렸고, 어떤 모델·문서를 썼는가? |
| Prometheus / Grafana | 전체 요청 중 어느 단계가 느리고 오류율·p95가 어떤가? |

FastAPI는 `embedding`, `search`, `context_build`, `generation` 단계에서 Langfuse trace와 Prometheus metric을 함께 남긴다.

### 발표 메시지

> 검색 모델만 구현하는 데서 멈추지 않고, 대화 이력·출처·품질·지연을 실제 서비스 흐름에서 확인할 수 있게 구성했습니다.

### 보여줄 수 있는 코드·문서

- `backend/src/main/java/com/yiji/Chatbot/service/ChatService.java`
- `backend/src/main/java/com/yiji/Chatbot/service/RedisSessionService.java`
- `ai-server/app/observability/metrics.py`
- `monitoring/grafana/dashboards/`
- `docker-compose*.yml`

---

## 7. 발표에 넣을 것과 빼둘 것

### 본문에 넣을 것

1. 파싱·청킹
2. BM25 + kNN + RRF
3. 청크 검색 → 문서 최대 5개 선택 → 문맥 재조립
4. 증분 색인·고아 청크·삭제 동기화
5. 평가셋 편향 발견과 대조군 재측정

### 짧게 언급하거나 질문 대비로 둘 것

- LiteLLM 모델 라우팅
- 멀티턴 검색 보정
- Redis와 PostgreSQL 역할 분리
- Grafana·Langfuse
- Docker Compose 설정
- Spring MVC

### 발표에서 피할 표현

- "멀티턴을 정량 평가했다" — 멀티턴 기능은 검증됐지만 평가셋에는 문항이 없다.
- "후보 50개 문서를 LLM에 모두 넣는다" — 후보는 청크고, LLM에는 최대 5개 문서만 전달한다.
- "현재 결합은 정규화 가중합" — 현재 구현은 RRF(K=60)다.
- "Confluence 640건"을 시점 없이 단정 — 발표 수치는 583문서·3,211청크 기준으로 고정한다.
