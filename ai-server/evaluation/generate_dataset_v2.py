"""
[QA Dataset v2 생성기]
Elasticsearch 인덱스(confluence-openai-v1)에서 카테고리별 대표 문서를 선별하고,
GPT-4o를 사용하여 신뢰도 높은 RAG 평가용 골든 데이터셋(v2)을 생성합니다.

질문 유형:
1. Factoid QA: 카테고리별 핵심 지식/규정/인프라/프로젝트 사실 질문 (35개)
2. Out-of-Domain QA: 사내 문서에 없는 질문으로 거절/환각 방지 능력 검증 (6개)
3. Known-Gap QA: Confluence DB 임베드 매크로 문서 안내 검증 (3개)
4. Multi-turn QA: 이전 대화 참조 멀티턴 질문 (4개)
"""
import json
import re
import sys
from typing import List, Dict, Any, Optional

# Windows 콘솔 출력 인코딩을 UTF-8로 설정
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from app.retrieval.es_client import get_es_client
from app.config import settings
from app.llm.litellm_client import generate_chat_completion

GEN_MODEL = "gpt-4o"
MAX_CONTEXT_CHARS = 2000

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

# 카테고리별 샘플링 목표 개수
CATEGORY_TARGETS = {
    "솔루션/개발": 6,
    "기획": 6,
    "Project": 5,
    "팀 회의록 (공개 운영 중)": 5,
    "LLOYDK에 오신 걸 환영합니다!": 5,
    "피앤씨": 3,
    "기술 스택": 2,
    "커뮤니케이션 가이드": 2,
    "피드백 세션": 2,
    "마케팅": 2,
    "리더십": 1,
    "세일즈": 1,
}

# 고정 Out-of-Domain 문항 (문서 부재 시 정직하게 거절하는지 환각 방지 검증)
OUT_OF_DOMAIN_ITEMS = [
    {
        "id": "qa-v2-ood-01",
        "input": "사내 피트니스 센터 이용 시간과 개인 락커 신청 방법 알려줘.",
        "expected_output": "사내 Confluence 문서에 피트니스 센터나 락커 신청에 관한 내용이 없어 확인할 수 없습니다.",
        "metadata": {"type": "out_of_domain", "expected_doc_ids": [], "category": "out-of-domain"}
    },
    {
        "id": "qa-v2-ood-02",
        "input": "구내식당 오늘 점심 메뉴와 식권 구매처가 어디야?",
        "expected_output": "사내 Confluence 문서에 구내식당 메뉴나 식권 관련 내용이 등록되어 있지 않습니다.",
        "metadata": {"type": "out_of_domain", "expected_doc_ids": [], "category": "out-of-domain"}
    },
    {
        "id": "qa-v2-ood-03",
        "input": "사내 동호회 신설 기준과 매월 지원금 신청 절차를 알려줘.",
        "expected_output": "사내 Confluence 문서에 동호회 지원금이나 신설 기준 관련 내용이 없습니다.",
        "metadata": {"type": "out_of_domain", "expected_doc_ids": [], "category": "out-of-domain"}
    },
    {
        "id": "qa-v2-ood-04",
        "input": "사옥 지하 주차장 1일 방문 차량 무료 주차권은 어디서 받아?",
        "expected_output": "사내 Confluence 문서에 주차권 발급 관련 안내가 등록되어 있지 않습니다.",
        "metadata": {"type": "out_of_domain", "expected_doc_ids": [], "category": "out-of-domain"}
    },
    {
        "id": "qa-v2-ood-05",
        "input": "직장 어린이집 입소 신청 기간과 제출 서류 안내해줘.",
        "expected_output": "사내 Confluence 문서에 직장 어린이집 관련 정보가 없습니다.",
        "metadata": {"type": "out_of_domain", "expected_doc_ids": [], "category": "out-of-domain"}
    },
    {
        "id": "qa-v2-ood-06",
        "input": "해외 출장 시 개인 항공사 마일리지 적립 및 사후 정산 규정이 어떻게 돼?",
        "expected_output": "사내 Confluence 문서에 해외 출장 마일리지 정산 관련 규정이 없습니다.",
        "metadata": {"type": "out_of_domain", "expected_doc_ids": [], "category": "out-of-domain"}
    },
]

# 고정 Known-Gap 문항 (DB 매크로 등으로 본문이 없고 링크만 있는 문서 대응 검증)
KNOWN_GAP_ITEMS = [
    {
        "id": "qa-v2-gap-01",
        "input": "03.설계_구성 아키텍처 문서에 적힌 상세 아키텍처 표 내용 전부 알려줘.",
        "expected_output": "해당 문서는 Confluence 데이터베이스 카드로 임베드되어 있어 본문 표 데이터를 직접 확인할 수 없으므로, 문서 내 링크를 직접 참고해 주세요.",
        "metadata": {"type": "known_gap", "expected_doc_ids": ["120979468"], "category": "Project", "known_gap": True}
    },
    {
        "id": "qa-v2-gap-02",
        "input": "솔루션 개발팀 주간 업무 리스트 데이터베이스 행 목록 다 보여줘.",
        "expected_output": "해당 내용은 Confluence 데이터베이스 임베드 링크로 구성되어 있어 상세 행 데이터는 확인할 수 없으니 링크를 확인해 주세요.",
        "metadata": {"type": "known_gap", "expected_doc_ids": ["120979468"], "category": "Project", "known_gap": True}
    }
]


def sample_unique_docs_by_category(es, index: str, category: str, n: int) -> List[Dict[str, Any]]:
    """특정 카테고리에서 서로 다른 doc_id를 가진 문서를 n개 샘플링하고 전체 텍스트 조합"""
    res = es.search(index=index, body={
        "size": 200,
        "_source": ["doc_id", "title", "text", "chunk_index", "path", "author"],
        "query": {"term": {"category": category}},
        "sort": [{"doc_id": "asc"}, {"chunk_index": "asc"}]
    })

    docs_map = {}
    for hit in res["hits"]["hits"]:
        s = hit["_source"]
        doc_id = s["doc_id"]
        if doc_id not in docs_map:
            docs_map[doc_id] = {
                "doc_id": doc_id,
                "title": s.get("title", ""),
                "category": category,
                "path": s.get("path", ""),
                "author": s.get("author", ""),
                "text": ""
            }
        if len(docs_map[doc_id]["text"]) < MAX_CONTEXT_CHARS:
            docs_map[doc_id]["text"] += ("\n\n" if docs_map[doc_id]["text"] else "") + s.get("text", "")

    # 텍스트가 너무 짧거나 빈 문서는 제외
    valid_docs = [d for d in docs_map.values() if len(d["text"].strip()) >= 50]
    return valid_docs[:n]


def generate_qa_from_doc(doc_info: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """문서 원문으로부터 GPT-4o를 이용해 고품질 질문과 정확한 기대 답변을 생성"""
    title = doc_info["title"]
    category = doc_info["category"]
    path = doc_info["path"]
    text = doc_info["text"][:MAX_CONTEXT_CHARS]

    prompt = f"""아래는 사내 Confluence 문서의 실제 원문입니다.
이 문서를 읽은 사내 임직원이 동료나 AI 챗봇에게 물어볼 법한 **자연스럽고 명확한 질문 하나와 그에 대한 정확한 정답**을 작성해 주세요.

[작성 규칙]
1. **질문의 명확성(단서 포함 필수)**:
   - "이 문서는", "이 프로젝트는", "이번 회의는" 처럼 모호한 대명사만 쓰지 마세요.
   - 문서 제목({title}), 상위 경로({path}), 팀명, 프로젝트명, 날짜 등 **이 문서를 고유하게 식별할 수 있는 구체적인 단서 키워드를 질문 안에 반드시 자연스럽게 포함**하세요.
   - 좋은 예: "삼성 SDS FabriX 파트너십 교육에서 강조된 주요 활용 방안이 뭐야?", "피앤씨팀 2025년 2월 온보딩 가이드의 수습 평가 기준 알려줘"
2. **답변의 정확성**:
   - 답변은 반드시 주어진 문서 내용(Context)에만 근거해야 하며, 1~2문장(핵심 팩트 위주)으로 명확하게 요약하세요.
3. **JSON 출력 포맷**:
   - 오직 아래 JSON 포맷으로만 출력하세요 (마크다운 백틱이나 추가 설명 금지):
   {{"question": "질문 내용", "answer": "기대 답변 내용"}}

[문서 정보]
- 카테고리: {category}
- 문서 제목: {title}
- 경로: {path}

[본문 내용]
{text}
"""
    try:
        raw_response = generate_chat_completion(
            messages=[{"role": "user", "content": prompt}],
            model=GEN_MODEL
        )
        match = _JSON_RE.search(raw_response)
        if not match:
            return None
        data = json.loads(match.group(0))
        q = data.get("question", "").strip()
        a = data.get("answer", "").strip()
        if q and a:
            return {"question": q, "answer": a}
    except Exception as e:
        print(f"  ❌ QA 생성 실패 ({title}): {e}")
    return None


def main():
    es = get_es_client()
    index = settings.ELASTICSEARCH_INDEX

    print("=" * 70)
    print(f"🚀 Confluence QA Dataset v2 생성 시작 (모델: {GEN_MODEL})")
    print("=" * 70)

    dataset_items = []
    item_index = 1

    # 1. 카테고리별 Factoid QA 생성
    for category, target_count in CATEGORY_TARGETS.items():
        sampled_docs = sample_unique_docs_by_category(es, index, category, target_count)
        print(f"\n📂 [{category}] 샘플링 문서 {len(sampled_docs)}건 (목표: {target_count}건)")

        for doc in sampled_docs:
            qa = generate_qa_from_doc(doc)
            if not qa:
                continue

            item_id = f"qa-v2-{item_index:03d}"
            item_index += 1

            dataset_items.append({
                "id": item_id,
                "input": qa["question"],
                "expected_output": qa["answer"],
                "metadata": {
                    "type": "factoid",
                    "expected_doc_ids": [doc["doc_id"]],
                    "category": category,
                    "doc_title": doc["title"]
                }
            })
            print(f"  ✅ [{item_id}] {doc['title']} ➔ Q: {qa['question']}")

    # 2. Known-Gap 문항 추가
    print(f"\n📂 [Known-Gap / DB 임베드] {len(KNOWN_GAP_ITEMS)}건 추가")
    dataset_items.extend(KNOWN_GAP_ITEMS)

    # 3. Out-of-Domain (사내 문서 부재/환각 방지) 문항 추가
    print(f"📂 [Out-of-Domain / 거부 검증] {len(OUT_OF_DOMAIN_ITEMS)}건 추가")
    dataset_items.extend(OUT_OF_DOMAIN_ITEMS)

    print("\n" + "=" * 70)
    print(f"🎉 총 {len(dataset_items)}개 문항 구성 완료 (Factoid: {item_index - 1}, Gap: {len(KNOWN_GAP_ITEMS)}, OOD: {len(OUT_OF_DOMAIN_ITEMS)})")
    print("=" * 70)

    # dataset_items.py 파일로 저장
    output_path = "evaluation/dataset_items.py"
    header = '''"""
Langfuse QA 데이터셋 v2 정의 (confluence-rag-qa-v2).

- Factoid QA: 실제 Confluence 문서 기반 팩트 검색 및 답변
- Out-of-Domain QA: 사내 문서에 없는 질문으로 환각/거절 능력 검증
- Known-gap QA: Confluence DB 매크로 등 본문 부재 문서 링크 안내 검증
"""

QA_DATASET_ITEMS = '''

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(header)
        f.write(json.dumps(dataset_items, ensure_ascii=False, indent=4))
        f.write("\n")

    print(f"💾 {output_path} 파일에 v2 데이터셋 저장 완료!")


if __name__ == "__main__":
    main()
