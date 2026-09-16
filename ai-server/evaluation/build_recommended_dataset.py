# -*- coding: utf-8 -*-
"""
[추천 데이터셋 생성 — attachment 축 10문항]

기존 5축(keyword/semantic/conversational/table/boundary) 평가셋에 더할 문항을 만든다.
모든 page_id / document_title / ground_truth_snippet은 2026-09-16 색인(confluence-openai-v3)에서
직접 확인한 실제 값이다. 손으로 고치지 말고 이 스크립트를 고친 뒤 다시 생성한다.

실행:
    python ai-server/evaluation/build_recommended_dataset.py

출력:
    ai-server/evaluation/recommended_dataset.json
    ai-server/evaluation/recommended_dataset.csv
"""
import csv, json, io, os

META = {
    "name": "추천 데이터셋",
    "description": "기존 5축(keyword/semantic/conversational/table/boundary) 평가셋에 더할 attachment 축 10문항",
    "created": "2026-09-16",
    "index_snapshot": {
        "concrete_index": "confluence-openai-v3",
        "documents": 643, "chunks": 3493,
        "attachment_documents": 126, "attachment_unique_names": 364,
        "attachment_only_documents": 34,
    },
    "why": (
        "기존 5축은 모두 '답이 본문에 있다'를 전제한다. 첨부파일 검색은 별도 BM25 필드"
        "(attachments^1.0)를 타고, 34개 문서는 첨부 수정 덕에 비로소 색인에 존재하므로"
        "현재 축으로는 이 경로가 전혀 측정되지 않는다."
    ),
    "field_notes": {
        "title_leak": "신규 권고 필드. 질문이 문서 제목/첨부명 단어를 그대로 포함하면 true. "
                      "v2가 84.7% 누출이라 검색이 실제보다 좋아 보였던 이력이 있다.",
        "retrieval_scoring": "검색 채점 대상 여부. not_found 문항만 negative.",
        "answerability": "unsupported = 검색은 정답 문서를 맞히지만 답변은 불가능한 경우. "
                         "첨부는 '파일 이름'만 색인하고 내용은 읽지 않기 때문이다.",
    },
    "caveats": [
        "축당 10문항이면 1문항이 0.10이다. 축별 수치는 진단용 방향 지표로만 쓰고 결론은 전체 수치로 낸다.",
        "unsupported 문항은 retrieval_scoring=positive다. 검색 성공과 답변 가능을 분리해 환각을 잡는 장치다.",
        "answerable_negative는 attachment 축에서 자연스럽게 나오지 않아 포함하지 않았다. 기존 boundary 축이 담당한다.",
        "실명·내부 IP가 담긴 문서(AWS 자격증 취득현황, 복합기 세팅 안내)는 의도적으로 제외했다.",
        "질문이 정답 문서를 하나로 특정하는지 반드시 확인한다. 초안의 '회사 근로 규정 문서'는 "
        "취업규칙과 안전보건 정책 양쪽에 해당해 폐기했다. qa-v2-021이 같은 이유로 남은 실패다.",
    ],
}

R = []
def add(i, q, pid, title, snip, ans, rs, leak, sub, note):
    R.append({"id": i, "category": "attachment", "question": q, "page_id": pid,
              "document_title": title, "ground_truth_snippet": snip,
              "answerability": ans, "retrieval_scoring": rs,
              "title_leak": leak, "subtype": sub, "note": note})

# ① 첨부 전용 문서 (본문 없음) — 첨부 수정 전에는 색인에 아예 없던 문서들
add("att-01", "롯데카드 프로젝트 일정표 파일 있어?", "98926876", "01_구축 일정 관리(WBS)",
    "첨부파일: [롯데카드] 생성형 AI 플랫폼 구축 WBS", "answerable", "positive", False,
    "attachment_only",
    "'롯데카드'가 문서 제목에는 없고 첨부명에만 있다. attachments 필드 없이는 찾을 수 없는 "
    "문항이면서 정답 문서가 하나로 특정된다.")
add("att-02", "사업자등록증 파일 있나요?", "20776268", "사업자등록증",
    "첨부파일: 사업자등록증_로이드케이_20251112, (주)로이드케이_정보통신공사업등록증",
    "answerable", "positive", True,
    "attachment_only", "제목 누출 있음. 첨부 2건이 달린 문서라 둘 다 제시하면 정답이다.")
add("att-03", "LG유플러스 쪽 운영자 매뉴얼 최신 버전 있어?", "99844245", "07_운영자 매뉴얼",
    "첨부파일: [LGU+] 운영자 매뉴얼-ver1.2(20250507)", "answerable", "positive", True,
    "attachment_only", "문서 제목(07_운영자 매뉴얼)만으로는 고객사를 알 수 없다. 첨부명에만 LGU+가 있다.")
add("att-04", "이노비즈 인증 받은 서류 좀 찾아줘", "25591826", "2025 이노비즈 확인서",
    "첨부파일: 2025 이노비즈확인서", "answerable", "positive", True,
    "attachment_only", "확인서류 계열 첨부 전용 문서가 코퍼스에 다수 있어 변별이 필요한 문항.")

# ② 본문+첨부 — 첨부명에만 겹치는 단어가 있는 경우
add("att-05", "ESG 경영 방침 문서 있어?", "20776301", "Mission / Vision / Core Value (MVC)",
    "ESG경영 방침_20240904", "answerable", "positive", False,
    "body_and_attachment",
    "본문과 제목에 'ESG'가 없다. BM25 단독으로는 attachments 필드를 통해 1위지만 "
    "하이브리드에서는 벡터 쪽 결과에 밀려 top5에 못 든다(2026-09-16 기준선). "
    "결합 방식이 첨부 신호를 죽이는지 보는 문항이다.")
add("att-06", "제휴병원 할인 혜택 안내문 파일로 받을 수 있어?", "37781780",
    "01. 25년 10월 제휴병원 혜택 안내", "25년 10월 기업공지 제휴병원 혜택 안내_세이프닥",
    "answerable", "positive", True,
    "body_and_attachment", "본문에도 혜택 내용이 있어 본문·첨부 양쪽이 맞는 정상 케이스.")

# ③ 첨부 '내용' 질문 — 검색은 성공, 답변은 불가능 (파일명만 색인하고 내용은 안 읽는다)
add("att-07", "취업규칙에서 연차는 며칠까지 쓸 수 있어?", "25329871", "취업규칙",
    "첨부파일: 20260325_취업규칙_V3", "unsupported", "positive", True,
    "attachment_content", "PDF 내용은 색인 대상이 아니다. 파일 위치는 안내하되 연차 일수를 지어내면 실패다.")
add("att-08", "운영자 매뉴얼에 나온 장애 대응 절차 알려줘", "99844245", "07_운영자 매뉴얼",
    "첨부파일: [LGU+] 운영자 매뉴얼-ver1.2(20250507)", "unsupported", "positive", True,
    "attachment_content", "매뉴얼 본문이 없으므로 절차를 서술하면 환각이다.")
add("att-09", "이노비즈 확인서 유효기간 언제까지야?", "25591826", "2025 이노비즈 확인서",
    "첨부파일: 2025 이노비즈확인서", "unsupported", "positive", True,
    "attachment_content", "확인서 안의 날짜는 읽을 수 없다. 연도(2025)를 유효기간으로 오인하는지도 함께 본다.")

# ④ 존재하지 않는 첨부 — 검색 채점 제외
add("att-10", "2027년 사업계획서 첨부파일 있어?", None, None, None,
    "not_found", "negative", False,
    "not_found", "코퍼스에 없는 연도의 문서. 비슷한 이름의 사업계획 문서를 끌어와 답하면 실패다.")

META["baseline_2026_09_16"] = {
    "note": "이 파일을 만든 시점의 운영 코드 기준. 목표치가 아니라 출발점 기록이다.",
    "retrieval_hit@5": "8/9 = 0.889 (not_found 1문항은 검색 채점 제외)",
    "failing": {"att-05": "BM25 단독으로는 1위이나 하이브리드 결합에서 top5 밖으로 밀림"},
    "config": "RRF K=60, top_k=5, BM25 fields = title^2.0 / attachments^1.0 / text",
}

out = {"metadata": META, "recommended_distribution": {
    "keyword": 10, "semantic": 10, "conversational": 10,
    "table": 10, "boundary": 10, "attachment": 10},
    "items": R}

base = os.path.dirname(os.path.abspath(__file__))
with io.open(f"{base}/recommended_dataset.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
    f.write("\n")

cols = ["id", "category", "question", "page_id", "document_title", "ground_truth_snippet",
        "answerability", "retrieval_scoring", "title_leak", "subtype", "note"]
with io.open(f"{base}/recommended_dataset.csv", "w", encoding="utf-8-sig", newline="") as f:
    w = csv.DictWriter(f, fieldnames=cols)
    w.writeheader()
    for r in R:
        w.writerow({k: ("" if r[k] is None else r[k]) for k in cols})

print("생성 완료")
for r in R:
    print(f"  {r['id']}  {r['subtype']:20} {r['answerability']:12} {r['retrieval_scoring']}")
