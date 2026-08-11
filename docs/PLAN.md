# Confluence Knowledge Graph RAG 챗봇 — 기획 (v2)

> 원본: https://github.com/Yiji-1015/Confluence_Chatbot (사내 Confluence 온보딩 RAG 챗봇)
> 목표: RAG + GraphRAG(온톨로지) 하이브리드 + LiteLLM 게이트웨이 + Spring Boot

## 1. 목표

- 사내 Confluence 문서 **전체**를 지식베이스로 임베딩 (부분 임베딩 폐기)
- **RAG(벡터) + GraphRAG(온톨로지)** 하이브리드 — 둘 다 사용
- 사람·조직·문서 온톨로지 → "누가 담당/작성/수정?" 답변에 참고
- 사내 서버(임베딩 API, ES) 접속 불가 시 로컬로 자동 폴백
- **LiteLLM 게이트웨이**로 LLM/임베딩 통합 (모델 전환·폴백 config화)
- 통합포털 연동 없음 (단일 앱)
- 프론트: 러버블(Lovable) 신규

## 2. 아키텍처

```
[React 프론트 (러버블)] ←REST→ [Spring Boot :8080] ←HTTP→ [Python FastAPI :8000 (AI)]
                                                                 │
                                                    ┌────────────┴────────────┐
                                              [LiteLLM :4000 (LLM/임베딩 게이트웨이)]
                                                    │
                                        DeepSeek / GPT / 사내 BGE-M3 임베딩
                                                                 │
                                              [벡터DB: Chroma] + [Neo4j 그래프]
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

## 7. 기술 스택

| 파트 | 스택 |
|---|---|
| 메인 서버 | Spring Boot 3.5.x (Java 25) |
| AI 서버 | Python FastAPI |
| LLM 게이트웨이 | LiteLLM (Docker) |
| 그래프DB | Neo4j (Docker) |
| 벡터DB | ChromaDB (로컬) / 사내 ES (옵션) |
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
├── frontend/         # 러버블 (나중에)
└── docker-compose.yml  # Neo4j + LiteLLM
```

## 9. 단계 (Milestone)

1. [x] 기획 (본 문서)
2. [ ] GitHub repo + 프로젝트 구조
3. [ ] docker-compose (Neo4j + LiteLLM)
4. [ ] Python AI 서버: 수집→파싱→청킹→임베딩→그래프→검색
5. [ ] Spring Boot: 문서/챗/온톨로지 API + AI 서버 연동
6. [ ] 러버블 프론트
7. [ ] 통합 테스트 + 데모 데이터

## 10. 오픈 이슈

- [ ] 온톨로지 스키마 확정 전 문서 샘플 확인
- [ ] 사내 임베딩 API 접속 여부 최종 확인
- [ ] LiteLLM 커스텀 임베딩(사내 API) 등록 방법 검증
