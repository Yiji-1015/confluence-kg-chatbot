# AGENT_RULES.md

> 프로젝트 공통 운영 규칙
> 대상: Codex, Claude Code 및 기타 AI 코딩 에이전트

## 목적
이 프로젝트는 포트폴리오 품질을 최우선으로 한다.
기능 구현뿐 아니라 설계 근거와 평가 결과를 남기는 것을 목표로 한다.

## 개발 원칙
- `docs/PLAN.md`를 기준으로 작업한다.
- 한 번에 하나의 Phase만 진행한다.
- 범위를 임의로 확장하지 않는다.
- 기능 추가보다 기존 구조의 일관성을 우선한다.
- 새로운 라이브러리는 명확한 이유가 있을 때만 추가한다.
- **직관적이고 설명 가능한 코드**: 꼭 필요한 경우가 아니면 클래스 대신 함수(`def`)를 기본으로 사용하여, 면접에서 100% 직접 설명할 수 있는 가독성 높은 코드를 유지한다.

## 역할
Spring Boot
- REST API
- Session 관리
- Python AI Server orchestration

Python FastAPI
- Retrieval
- Query Rewrite
- Embedding
- Knowledge Graph
- LLM Pipeline

## 도구 사용 규칙

### Caveman
- 항상 사용
- 간결한 출력 유지

### gstack
자동 호출하지 않는다.
- Phase 시작: plan-eng-review
- Phase 종료: review 또는 qa-only
- 버그 분석: investigate

### Superpowers
평소에는 사용하지 않는다.
- 난해한 버그: systematic-debugging
- 최종 완료 직전: verification-before-completion

## 구현 원칙
- 작은 단위로 커밋한다.
- 기존 구조를 최대한 재사용한다.
- 테스트 가능한 상태를 유지한다.

## 평가
가능하면 다음 비교를 수행한다.
- BM25
- Hybrid Retrieval
- Hybrid + Graph
- Query Rewrite 적용 여부

Langfuse Trace를 남길 수 있으면 남긴다.

## 금지 사항
- PLAN.md 범위를 임의로 확대하지 않는다.
- ChromaDB를 다시 추가하지 않는다.
- 서로 다른 Embedding Space를 하나의 Index에서 혼합하지 않는다.
- Frontend를 먼저 완성하려 하지 않는다.
- 이유 없이 새로운 기술을 추가하지 않는다.

## 우선순위
1. Retrieval Core
2. Spring ↔ FastAPI
3. LiteLLM
4. Redis
5. Knowledge Graph
6. Evaluation
7. Frontend
8. Packaging

## 완료 기준
- 빌드 성공
- 실행 성공
- 핵심 기능 확인
- README 업데이트
- 필요 시 Langfuse Trace 확인

기능 수보다 설계와 품질이 설명 가능한 포트폴리오를 만든다.
