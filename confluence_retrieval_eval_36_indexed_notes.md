# 최종 평가셋(36문항) 선별 기록

`confluence_retrieval_eval_55_attachment_screened`에서 **정답 문서가 색인돼 있지 않은 19문항을 제외**하고 36문항으로 확정했다.
`id`는 1~36으로 새로 매기고, 원본 번호는 `source_id`에 남겼다.

## 제외 사유 — 정답 문서가 LLOYDK 스페이스 밖

색인 파이프라인은 `CONFLUENCE_SPACE_KEY=LLOYDK` 한 스페이스만 순회한다(`fetch_all_page_ids`).
아래 6개 문서는 Confluence에 존재하지만 다른 스페이스 소속이라 처음부터 색인 대상이 아니었고,
질문을 어떻게 바꿔도 검색될 수 없어 검색 평가에서 의미가 없다.

| page_id | 문서 | 실제 space | 제외한 원본 문항 |
|---|---|---|---|
| 372080645 | KDO-06. 설치 및 배포 가이드 | segM5ic0oN9L | 2, 19, 20, 21, 23 |
| 245170268 | AX Workspace - 09 1차 PoC 범위 변경 및 대상 재산정 | ILA | 29, 30, 32, 33, 35 |
| 246415367 | AX Workspace - 10 Platform-first AX Agent PoC 구현 현황 | ILA | 37, 38, 40, 41, 44 |
| 254541843 | UI/UX 결정사항 | aHBUQwIEKwUG | 7, 48 |
| 335380482 | DO-Parse 기능 리스트 | aHBUQwIEKwUG | 39 |
| 335380490 | DO-SPE 기능 리스트 | aHBUQwIEKwUG | 50 |

확인 방법: REST `content/{id}?expand=space`로 직접 조회했다.
`fetch_pages_by_ids`는 응답에 space 확장이 없으면 `settings.CONFLUENCE_SPACE_KEY`로 폴백하므로
(confluence_client.py:189) 이 6건도 LLOYDK로 보고한다 — 스페이스 판정에 쓰면 안 된다.

## 구성

- 36문항 / 고유 정답 문서 18개 (전부 색인 확인됨)
- category: keyword 7, semantic 8, conversational 6, boundary 7, attachment 6, table 2
- answerability: answerable 28, answerable_negative 3, not_found 3, unsupported 2
- retrieval_scoring: positive 33, negative 3

제외된 19문항의 category 분포는 table 7, semantic 7, conversational 3, boundary 1, keyword 1이었다.

## 원본 notes에서 이어지는 유의사항

- 51~54(현 32~35)는 첨부명 또는 페이지 색인 여부를 보는 문항이다.
- 54(현 35)는 retrieval은 정답 문서를 찾아야 하지만, 첨부 본문이 색인되지 않는 한 연차 일수를 답하면 안 된다.
- 55(현 36)는 attachment 축의 not_found 문항으로, retrieval 성능 통계에서는 제외한다.

## 측정 결과

검색 방식별 비교와 실패 문항 분석은 `docs/EVAL_RESULTS_20260916.md`에 있다.
수치를 여기 중복해서 적지 않는다.
