# 첨부파일 후보 선별 기록

원본 50개에 `ai-server/evaluation/recommended_dataset.json`의 attachment 후보 10개를 검토해 5개만 추가했다.

포함: att-04, att-05, att-06, att-07, att-10

제외:

- att-01: 고객사 프로젝트 일정표
- att-02: 법인 등록 서류
- att-03, att-08: 고객사 운영자 매뉴얼 및 장애 대응 절차
- att-09: att-04와 같은 문서의 반복 출처이며, unsupported 검증은 att-07로 유지

유의:

- 51~54는 첨부명 또는 페이지의 색인 여부를 보는 문항이다.
- 54는 retrieval은 정답 문서를 찾아야 하지만, 첨부 본문이 색인되지 않는 한 연차 일수를 답하면 안 된다.
- 55는 attachment 축의 not_found 문항으로, retrieval 성능 통계에서는 제외한다.
