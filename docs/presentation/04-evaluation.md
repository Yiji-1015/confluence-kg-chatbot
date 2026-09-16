# 4. 평가와 분석 인사이트

[전체 목차](README.md) · 이전: [색인 관리](03-ingestion.md) · 다음: [AI 서비스화](05-ai-serving.md)

> **⚠️ 이 문서는 2026-09-16 이전 상태를 설명한다.** 그 뒤로 평가 계층이 두 번 바뀌었다.
> 평가셋이 45문항에서 36문항으로 줄면서 여기 링크된 스크립트 몇 개가 삭제됐고,
> 채점기 6종(`retrieval_hit`, `retrieval_mrr`, `answer_faithfulness`, `answer_correctness`,
> `ragas_faithfulness`, `ragas_context_precision`)이 **RAGAS 표준 5종으로 전부 교체됐다.**
> 아래의 지표 이름과 점수는 전부 옛 구성의 것이다.
> 현재 기준은 [EVALUATION.md](../EVALUATION.md)가 유일하다.

## 1. 코드와 근거

| 파일 | 역할 |
|---|---|
| [run_qa.py](../../ai-server/evaluation/run_qa.py) | RAG 실행, 6개 평가기, 누락 경고, 원인 분류 |
| [generate_dataset.py](../../ai-server/evaluation/generate_dataset.py) | 원본 평가셋 생성 로직 |
| [dataset_items.py](../../ai-server/evaluation/dataset_items.py) | 원본 문항 스냅샷 |
| [push_dataset.py](../../ai-server/evaluation/push_dataset.py) | 스냅샷을 Langfuse Dataset에 업서트 |
| [generate_paraphrase_dataset.py](../../ai-server/evaluation/generate_paraphrase_dataset.py) | 대조군 생성·제목 단어 겹침 계산 |
| [paraphrase_dataset_items.py](../../ai-server/evaluation/paraphrase_dataset_items.py) | 대조군 스냅샷 |
| [compare_fusion.py](../../ai-server/evaluation/compare_fusion.py) | 검색 방식 비교 |
| [EVALUATION.md](../EVALUATION.md), [CHANGELOG.md](../CHANGELOG.md) | 측정 조건·결과·원본 점수 대조 기록 |

아래 결과는 기록된 실험값이다. 이번 문서 작성에서 다시 측정한 값은 아니다.

## 2. 평가 대상과 평가기의 분리

```text
Langfuse Dataset의 문항
 → rag_task: 임베딩 → 검색 → 모델 선택 → Context → 답변
 → 결과 dict
 → 검색 평가 2개 + 자체 생성 평가 2개 + RAGAS 2개
 → Langfuse 기록 / 누락 경고 / 진단 출력
```

결과 dict는 answer, retrieved_doc_ids, context, retrieved_contexts를 담는다. context는 합친 문자열이고 retrieved_contexts는 문서별 본문 리스트다. RAGAS에 문서별 근거를 넘기기 위해 둘을 구분한다.

서비스와 `search_hybrid`, `build_context_text`, `select_optimal_model`을 공유하고, 생성 프롬프트도 공통이다. 하지만 평가가 Spring API 전체를 통과하지는 않는다. 평가 생성은 동기 경로이고 history가 없으며, 세션·Redis·HTTP 전송·멀티턴 검색 보정은 이 실험으로 검증되지 않는다.

## 3. 검색 지표: 정답 문서를 찾았는가

검색 지표는 LLM 판정 없이 expected_doc_ids와 검색 결과를 비교한다.

| 지표 | 문항별 계산 | 예시 |
|---|---|---|
| hit@5 | 정답 ID 하나라도 상위 5문서에 있으면 1, 없으면 0 | 1위와 5위 모두 1 |
| reciprocal rank | 첫 정답 순위의 역수, 없으면 0 | 1위=1, 2위=0.5, 5위=0.2 |
| MRR | 문항별 reciprocal rank 평균 | 같은 hit라도 순위 개선을 구분 |

known_gap이면 검색 채점에서 제외하고, 기대 문서 ID가 없으면 역시 제외한다. 코드의 판단은 유형 이름 자체보다 metadata의 `known_gap`과 `expected_doc_ids`를 따른다.

확정 평가 기록은 전체 45문항 중 검색 38문항이다. 나머지 7개는 본문 없는 문서 대응 2개와 문서에 없는 질문 5개다. 검색 38/38과 생성 45/45를 두고 “여섯 지표가 모두 45문항”이라고 설명하면 잘못이다.

## 4. 생성 지표: 찾은 근거로 제대로 답했는가

### 자체 correctness

질문, 기대 답변, 실제 답변을 판정 모델에 주고 핵심 정보의 일치를 0~1로 평가시킨다. 정규식으로 `SCORE:`와 `REASON:`을 읽고 점수는 0~1로 제한한다. 기대 답변이 없으면 채점하지 않는다.

### 자체 faithfulness

Context와 답변을 판정 모델에 주고 근거에 없는 내용이 있는지 평가한다. 문서를 못 찾았고 답변도 모른다고 하면 1점을 주도록 프롬프트에 명시돼 있다. 답변 전체를 한 번에 판정하는 방식이다.

### RAGAS faithfulness

`Faithfulness` 객체에 질문·답변·문서별 Context를 전달한다. 기록상 답변을 세부 주장으로 나누어 근거를 확인하는 방식과 자체 전체 답변 판정을 비교하려고 추가했다. 자체 점수와 값이 다르다고 어느 한쪽이 절대 정답인 것은 아니다.

### RAGAS context precision

`ContextPrecisionWithoutReference`를 사용한다. 기대 답변 라벨 없이 실제 응답에 대한 검색 근거의 유용성을 순위까지 고려해 평가하는 지표다. “문서 5개 중 인용한 문서 개수 / 5” 같은 단순 비율로 읽으면 안 된다.

네 생성 지표는 모두 판정 모델의 판단을 포함한다. correctness 0.958을 “실사용 정답률 95.8% 보장”으로 바꾸어 말하지 않는다.

## 5. 판정 모델과 실패·누락 처리

기본 judge 별칭은 `solar-judge`이며 [LiteLLM 설정](../../litellm/config.yaml)에서 `openai/solar-pro4`와 Upstage API로 연결한다. 생성 모델과 판정 모델을 분리해 자기 출력 선호를 줄이려는 설계다. 편향을 완전히 제거한다는 보장은 아니다.

자체 판정 호출 실패나 형식 파싱 실패는 None으로 반환하고 `_FAILURES`에 기록한다. None은 오답 0점과 다르다. 평균에서 빠지므로 실패가 많은 실행의 평균이 오히려 높아질 수 있다.

RAGAS는 별도 평가 의존성으로 지연 로딩한다. `max_tokens=4096`을 명시한 이유는 기록상 1024 출력 제한에서 긴 판정 JSON이 잘렸기 때문이다. 이미 실행 중인 이벤트 루프가 있으면 별도 스레드에서 `asyncio.run()`을 수행한다.

`_print_warnings()`는 실패 카운터와 전체 item 결과 개수를 확인한다. 그러나 RAGAS 미설치·빈 Context 때문에 조용히 None을 반환하는 모든 경로를 지표별 기대 개수와 대조하지는 않는다. 경고가 없다는 출력만으로 6개 지표 완전 채점을 단정하지 말고 실제 지표별 개수를 확인해야 한다.

## 6. 오류 원인 분류는 조사 출발점이다

`_print_diagnosis()`의 규칙:

| 조건 | 출력 |
|---|---|
| correctness ≥ 0.7 | 진단 목록에서 생략 |
| hit=0 | 검색 실패 |
| hit=1, 자체 faithfulness < 0.6 | 근거 없는 생성 |
| hit=1, 자체 faithfulness ≥ 0.6 | 이해·추론 실패 후보 |
| 점수 부족·채점 비대상 | 판단 보류 |

이 분류가 원인을 인과적으로 확정하지는 않는다. 정답 문서가 검색됐어도 재조립 길이 제한 때문에 정답 문장이 빠질 수 있고, 정답 라벨이 잘못됐을 수도 있다. 실제 답변·근거·라벨을 다시 열어볼 출발점으로 쓴다.

## 7. 평가셋 편향과 paraphrase 대조군

원본 생성 프롬프트는 문서를 특정할 단서를 질문에 넣도록 유도했다. 제목 단어가 많이 겹치면 BM25가 유리해질 수 있다. 그래서 같은 expected_doc_ids를 유지하면서 제목·경로 단어 대신 동의어·구어체를 쓰도록 GPT-4o에 요청했다.

제목 단어 겹침은 Nori가 아니라 정규식으로 계산한다.

```text
T = 제목에서 영문·숫자·한글 외 문자로 분리한 길이 2 이상 토큰 집합
Q = 질문에 같은 규칙 적용
title_leak = |T ∩ Q| / |T|
```

기록상 원본 평균 84.7%, 대조군 평균 6.4%다. 84.7%는 질문 중 편향된 질문의 비율이 아니라 **문항별 제목 토큰 겹침 비율의 평균**이다. “제목 단어를 전부 없앴다”도 정확하지 않다.

생성기는 질문 첫 줄을 취하고 source_id와 정답 문서 ID를 저장한다. 정답 보존을 프롬프트로 요구하지만 자동 의미 검증이나 사람의 최종 판정까지 강제하지 않는다. 일부 질문은 모호해질 수 있다. v4는 실제 사용자 표본도, 모든 편향이 제거된 데이터셋도 아니다.

## 8. 검색 비교 실험의 통제 범위

`compare_fusion.py`는 RRF, min-max 4:6, BM25, kNN, 슬롯 배분 3종을 비교한다. 질문 임베딩은 준비해 재사용하며 답변 생성·판정 LLM은 호출하지 않는다. 단, 임베딩 API는 사용한다.

RRF는 서비스 함수를 다시 호출하고 다른 방식은 별도 후보 조회 결과를 쓴다. 따라서 모든 방식이 같은 원시 후보 스냅샷을 한 번만 공유하는 실험은 아니다. 또한 현재 min-max 비교도 RRF 최대치 기준 최신성 helper를 호출한다. 과거 min-max 운영 설정을 완전히 재현한다고 보기 어렵다. 설정, 인덱스, 데이터셋 스냅샷을 함께 고정해 해석해야 한다.

## 9. 기록된 대표 결과

2026-09-14 / 583문서·3,211청크 / 검색 38문항:

| 방식 | 원본 MRR | paraphrase MRR |
|---|---:|---:|
| BM25 | 0.864 | 0.390 |
| kNN | 0.812 | 0.501 |
| RRF | 0.908 | 0.560 |

같은 대조군에서 RRF가 BM25보다 MRR 0.170 높았다. 이 결과는 “표현이 달라진 질문에서 의미 검색을 결합할 실익이 관찰됐다”로 설명할 수 있다. 모든 질의·회사 문서·사용자에게 보장된 개선은 아니다.

| 지표 | 확정 기록 | 채점 |
|---|---:|---:|
| hit@5 | 0.974 | 38/38 |
| MRR | 0.908 | 38/38 |
| correctness | 0.958 | 45/45 |
| 자체 faithfulness | 0.973 | 45/45 |
| RAGAS faithfulness | 0.856 | 45/45 |
| RAGAS context precision | 0.851 | 45/45 |

기록은 [CHANGELOG.md](../CHANGELOG.md)의 09-15 Langfuse 원본 대조 절을 참고한다. 실행 이름의 09-13 UTC는 09-14 KST와 같은 실행이다. 표본이 작고 반복 실험·신뢰구간이 없으므로 작은 차이를 통계적으로 유의하다고 말하지 않는다.

핵심 분석 경험은 지표를 만드는 것, 누락을 감지하는 것, 평가셋의 질문 조건을 바꾸어 대안을 비교하는 것이다.
