# Confluence Knowledge Graph RAG 챗봇 — 기획 (v2)

> 원본: https://github.com/Yiji-1015/Confluence_Chatbot (사내 Confluence 온보딩 RAG 챗봇)
> 목표: RAG + GraphRAG(온톨로지) 하이브리드 + LiteLLM 게이트웨이 + Spring Boot

## 1. 목표

- 사내 Confluence 문서 **전체**를 지식베이스로 임베딩 (부분 임베딩 폐기)
- **RAG(벡터) + GraphRAG(온톨로지)** 하이브리드 — 둘 다 사용
- 사람·조직·문서 온톨로지 → "누가 담당/작성/수정?" 답변에 참고
- 사내 서버(임베딩 API, ES) 접속 불가 시 로컬로 자동 폴백
- **LiteLLM 게이트웨이**로 LLM/임베딩 통합 (모델 전환·폴백 config화)
- **멀티턴 대화** (session_id 기반, 히스토리 Redis 저장)
- **Redis 캐시** (대화 히스토리·임베딩·검색 결과)
- 통합포털 연동 없음 (단일 앱)
- 프론트: 러버블(Lovable) 신규

## 2. 아키텍처

```
[React 프론트 (러버블)] ←REST→ [Spring Boot :8080] ←HTTP→ [Python FastAPI :8000 (AI)]
                                   │   │                          │
                                   │   └──[Redis :6379]───┐      │
                                   │          (세션/캐시)      │
                                                    ┌────────┴────────┐
                                              [LiteLLM :4000 (LLM/임베딩 게이트웨이)]
                                                    │
                                        DeepSeek / GPT / 사내 BGE-M3 임베딩
                                                                 │
                                              [벡터DB: Chroma/ES] + [Neo4j 그래프]
```

- **Spring Boot** = 메인 서버 (문서 CRUD, 온톨로지 조회, 검색/챗 API)
- **Python FastAPI** = AI 전용 (수집, 청킹, 임베딩, 엔티티 추출, 그래프 구축, 검색)
- **LiteLLM** = LLM·임베딩 게이트웨이 (모델 전환/폴백을 config로)

## 3. LiteLLM 설계

`litellm/config.yaml`:
```yaml
model_list:
  - model_name: deepseek-chat
    litellm_params: { model: deepseek/deepseek-chat, api_key: os.environ/DEEPSEEK_API_KEY }
  - model_name: gpt-4o-mini
    litellm_params: { model: openai/gpt-4o-mini, api_key: os.environ/OPENAI_API_KEY }
  - model_name: bge-m3            # 사내 임베딩 (OpenAI 호환)
    litellm_params:
      model: openai/text-embedding-3-small   # OpenAI 호환 스키마 사용
      api_base: os.environ/EMBEDDING_API_URL
```

- Python AI 서버는 `base_url=http://localhost:4000` 으로 LLM·임베딩 호출
- Spring Boot도 필요 시 같은 게이트웨이 사용
- 사내 임베딩 API 죽으면 LiteLLM 폴백으로 OpenAI/로컬 전환

## 4. 데이터 파이프라인

```
[Confluence API / 로컬 폴더] ← 전체 수집
        ↓
[파싱: 표 평탄화, 내부 링크, 첨부명]   ← 원본 파서 재사용
        ↓
[청킹] → ① [전체 임베딩 → 벡터 인덱스]        (RAG용)
        → ② [LLM 엔티티·관계 추출 → Neo4j]   (GraphRAG용)
```

## 5. 온톨로지 스키마 v2 (사람·조직 포함)

```
엔티티: Person, Team, Document, Concept, System, Project
관계:
  (Person)-[AUTHORED]->(Document)          작성
  (Person)-[TOP_CONTRIBUTOR]->(Document)   최다 수정 (원본 코드 로직 승격)
  (Person)-[LAST_MODIFIED]->(Document)     마지막 수정
  (Person)-[WORKS_IN]->(Team)              소속
  (Document)-[BELONGS_TO]->(Team/Project)  문서 소속
  (Document)-[LINKS_TO]->(Document)        내부 링크
  (Document)-[RELATES_TO]->(Concept)       LLM 추출
```

## 6. 질문 라우팅

```
질문 → ① 관계형 판별("누가/담당/팀?") → Neo4j 2-hop 탐색
       ② 사실형 → 벡터 검색 (kNN)
       → 결과 병합 → LLM 답변 + 출처 + 담당자 메타
```

## 6-1. 멀티턴 대화 설계

```
[클라이언트] POST /api/chat {session_id, query}
     ↓
[Spring] session_id 없으면 생성 → Redis에 세션 기록
     ↓
[Python AI] Redis에서 대화 히스토리 조회 (최근 N턴, 기본 10턴)
     ↓
검색(현재 질문) → LLM 프롬프트 = [대화 히스토리] + [Context] + [질문]
     ↓
답변 저장 → Redis에 히스토리 append (TTL 24h)
```

- 후속 질문("그거 얼마야?", "더 자세히")은 히스토리 맥락으로 해석
- Redis 키 구조: `chat:{session_id}` → JSON 배열 [{role, content}]
- TTL 24h = 하루 뒤 세션 자연 만료 (Redis가 자동 삭제)
- Spring은 세션 ID만 관리, AI 판단 로직은 Python

## 6-2. Redis 캐시 설계

| 캐시 | 키 | TTL | 이유 |
|---|---|---|---|
| 대화 히스토리 | `chat:{session_id}` | 24h | 멀티턴 맥락 |
| 임베딩 | `emb:{sha256(text)}` | 30일 | 같은 텍스트 임베딩 재사용 (API 비용↓) |
| 검색 결과 | `search:{sha256(query)}` | 10분 | 동일 질문 반복 시 빠른 응답 |
| 답변 캐시 | `answer:{sha256(query)}` | 10분 | 대량 조회성 질문 (멀티턴은 제외) |

- 캐시 정책: 검색/답변은 10분 단기, 임베딩은 30일 장기 (비용 절감 핵심)
- 멀티턴 세션은 캐시 제외 (맥락이 다르므로)

## 7. 기술 스택

| 파트 | 스택 |
|---|---|
| 메인 서버 | Spring Boot 3.5.x (Java 25) |
| AI 서버 | Python FastAPI |
| LLM 게이트웨이 | LiteLLM (Docker) |
| 그래프DB | Neo4j (Docker) |
| 벡터DB | ChromaDB (로컬) / Elasticsearch + Kibana + nori (Docker, 무료 Basic) |
| 캐시/세션 | Redis (Docker) |
| 임베딩 | 사내 BGE-M3 → OpenAI → 로컬 BGE-M3 (폴백) |
| LLM | DeepSeek 기본 / GPT (LiteLLM으로 전환) |
| 프론트 | 러버블(Lovable) |

## 8. 디렉토리 구조

```
confluence-kg-chatbot/
├── docs/PLAN.md
├── backend/          # Spring Boot (메인 API)
├── ai-server/        # Python FastAPI (AI 전용)
├── litellm/          # LiteLLM config
├── elasticsearch/    # ES Dockerfile (nori 포함)
├── frontend/         # 러버블 (나중에)
└── docker-compose.yml  # Neo4j + LiteLLM + ES/Kibana + Redis
```

## 9. 단계 (Milestone)

1. [x] 기획 (본 문서)
2. [ ] GitHub repo + 프로젝트 구조
3. [ ] docker-compose (Neo4j + LiteLLM + ES/Kibana + Redis)
4. [ ] Python AI 서버: 수집→파싱→청킹→임베딩→그래프→검색
5. [ ] 멀티턴 + Redis 캐시 (ai-server)
6. [ ] Spring Boot: 문서/챗/온톨로지 API + AI 서버 연동
7. [ ] 러버블 프론트
8. [ ] 통합 테스트 + 데모 데이터

## 10. 오픈 이슈

- [ ] 온톨로지 스키마 확정 전 문서 샘플 확인
- [ ] 사내 임베딩 API 접속 여부 최종 확인
- [ ] LiteLLM 커스텀 임베딩(사내 API) 등록 방법 검증
