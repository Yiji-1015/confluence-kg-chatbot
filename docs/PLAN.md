# Confluence RAG Chatbot 계획

## 목표

Confluence 문서를 구조적으로 수집하고 Elasticsearch 하이브리드 검색과 LLM을 결합해, 출처가 있는 사내 문서 답변을 제공한다.

## 범위

- Confluence 전체·카테고리별 수집과 증분 색인
- HTML 정제, 표 보존, 문서 청킹
- Nori BM25와 Dense Vector kNN 결합 검색
- LiteLLM 기반 임베딩·답변 모델 라우팅
- Spring Boot 대화 API와 Web UI
- PostgreSQL 대화 영속화와 Redis 최근 대화 캐시
- Langfuse 추적과 검색 품질 평가

## 데이터 흐름

1. `scripts.ingest`가 Confluence 문서를 가져온다.
2. 파서가 본문을 정제하고 청크로 나눈다.
3. LiteLLM을 통해 임베딩을 생성한다.
4. 청크와 벡터를 Elasticsearch에 저장한다.
5. 질문마다 BM25와 kNN 검색 결과를 정규화해 결합한다.
6. 검색 문서와 대화 이력을 LLM에 전달한다.
7. Spring Boot가 답변, 출처, 세션을 사용자에게 반환한다.

## 구현 상태

- [x] Docker Compose 계층 분리
- [x] Confluence 수집·파싱·증분 색인
- [x] Elasticsearch 하이브리드 검색
- [x] LiteLLM 임베딩·답변 연동
- [x] Spring Boot 채팅 API와 Web UI
- [x] PostgreSQL·Redis 세션 처리
- [x] Langfuse 추적
- [ ] 검색 품질 데이터셋 확장
- [ ] 외부 배포 보안 강화
- [ ] 운영 백업·복구 자동화

## 완료 기준

- Compose 설정과 애플리케이션 빌드가 성공한다.
- 색인 후 질문에 답변과 Confluence 출처가 반환된다.
- 대화 이력이 세션 간 유지된다.
- 비밀값이 저장소에 포함되지 않는다.
- README와 운영 문서가 실제 실행 명령과 일치한다.
