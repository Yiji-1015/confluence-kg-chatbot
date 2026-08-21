"""
Langfuse QA 데이터셋 아이템 정의.

각 아이템은 실제 confluence-openai-v1 인덱스에 들어있는 문서를 근거로 작성했다.
metadata.expected_doc_ids로 "검색이 정답 문서를 찾았는지"와
"답변 생성이 맞았는지"를 분리해서 채점할 수 있게 한다.

- expected_doc_ids가 채워진 항목: 특정 문서에서 답이 나와야 하는 일반 질문
- expected_doc_ids가 빈 리스트인 항목: 관련 문서가 없어야 하는 질문 (환각 방지 검증)
- metadata.known_gap=True: 파서가 Confluence 임베디드 database 매크로를 못 읽어와서
  본문이 링크만 남은 문서를 겨냥한 질문 (검색/생성과 무관한 "수집 단계" 갭 진단용)
"""

QA_DATASET_ITEMS = [
    {
        "id": "qa-001",
        "input": "회사가 2025년에 벤처기업인증을 재선정받은 게 몇 월이야?",
        "expected_output": "2025년 9월",
        "metadata": {"expected_doc_ids": ["20709946"], "category": "company-history"},
    },
    {
        "id": "qa-002",
        "input": "삼성 SDS FabriX Service 파트너 계약은 언제 체결했어?",
        "expected_output": "2025년 7월",
        "metadata": {"expected_doc_ids": ["20709946"], "category": "company-history"},
    },
    {
        "id": "qa-003",
        "input": "우리 회사 직책제에서는 사원/대리/과장 같은 별도 직급이 있어?",
        "expected_output": "별도 직급 없이 직책제로 운영된다. 조직장은 대표이사/본부장/팀장/CXO, 역할은 PM/PL/AM이며 그 외 인원은 매니저로 호칭이 통일된다.",
        "metadata": {"expected_doc_ids": ["20710092"], "category": "hr-policy"},
    },
    {
        "id": "qa-004",
        "input": "RTX 4090 사내 서버의 IP 주소가 뭐야?",
        "expected_output": "192.168.123.33",
        "metadata": {"expected_doc_ids": ["113213442"], "category": "infra"},
    },
    {
        "id": "qa-005",
        "input": "RTX 6000 사내 서버에 장착된 GPU 스펙이 뭐야?",
        "expected_output": "RTX PRO 6000 Blackwell (96GB) 1장",
        "metadata": {"expected_doc_ids": ["113213442"], "category": "infra"},
    },
    {
        "id": "qa-006",
        "input": "vLLM Inference 서버의 헬스체크 엔드포인트 경로가 뭐야?",
        "expected_output": "/health",
        "metadata": {"expected_doc_ids": ["119111726"], "category": "infra"},
    },
    {
        "id": "qa-007",
        "input": "text-embedding-inference 서버에서 모델 정보를 조회할 때는 어떤 엔드포인트를 써?",
        "expected_output": "/info",
        "metadata": {"expected_doc_ids": ["119111726"], "category": "infra"},
    },
    {
        "id": "qa-008",
        "input": "사내 MCP 서버 모음에서 confluence-mcp로 뭘 할 수 있어?",
        "expected_output": "Confluence 페이지 검색, 새 페이지 작성, 기존 페이지 수정, 댓글 작성 등 Confluence 문서를 Claude가 직접 찾아보고 다루는 작업을 대신할 수 있다.",
        "metadata": {"expected_doc_ids": ["319782940"], "category": "mcp"},
    },
    {
        "id": "qa-009",
        "input": "이노포스트의 대표는 누구야?",
        "expected_output": "정종민",
        "metadata": {"expected_doc_ids": ["346882050"], "category": "partner-eval"},
    },
    {
        "id": "qa-010",
        "input": "이노포스트의 2025년 매출은 얼마야?",
        "expected_output": "약 25.98억원",
        "metadata": {"expected_doc_ids": ["346882050"], "category": "partner-eval"},
    },
    {
        "id": "qa-011",
        "input": "이노포스트의 임직원 수는 몇 명이야?",
        "expected_output": "약 33명",
        "metadata": {"expected_doc_ids": ["346882050"], "category": "partner-eval"},
    },
    {
        "id": "qa-012",
        "input": "Help Desk 운영 방안에서, 답변 정확도를 높이기 위해 어떤 도구로 데이터 기반 분석을 한다고 했어?",
        "expected_output": "Metabase와 Grafana를 활용한 데이터 기반 분석",
        "metadata": {"expected_doc_ids": ["99778599"], "category": "project-docs"},
    },
    {
        "id": "qa-013-gap",
        "input": "요구사항 정의서에 정의된 기능 요구사항 항목들을 알려줘",
        "expected_output": "본문에 실제 요구사항 목록이 없고 Confluence 임베디드 데이터베이스로 가는 링크만 있어, 이 내용만으로는 답할 수 없다고 안내해야 한다.",
        "metadata": {
            "expected_doc_ids": ["98926740"],
            "category": "known-gap",
            "known_gap": True,
            "gap_type": "unparsed_embedded_database",
        },
    },
    {
        "id": "qa-014-gap",
        "input": "구축 일정 관리(WBS) 문서에서 프로젝트 착수일이 언제로 잡혀있어?",
        "expected_output": "본문에 실제 WBS 일정 데이터가 없고 구글 스프레드시트 링크만 있어, 이 내용만으로는 답할 수 없다고 안내해야 한다.",
        "metadata": {
            "expected_doc_ids": ["99778562"],
            "category": "known-gap",
            "known_gap": True,
            "gap_type": "unparsed_embedded_database",
        },
    },
    {
        "id": "qa-015-nodoc",
        "input": "우리 회사 점심 식대 지원 정책이 어떻게 돼?",
        "expected_output": "관련된 사내 문서를 찾을 수 없어 답할 수 없다고 안내해야 한다.",
        "metadata": {"expected_doc_ids": [], "category": "out-of-domain"},
    },
    {
        "id": "qa-016-nodoc",
        "input": "오늘 서울 날씨 어때?",
        "expected_output": "Confluence 문서 기반 챗봇이므로 답변할 수 없는 질문이라고 안내해야 한다.",
        "metadata": {"expected_doc_ids": [], "category": "out-of-domain"},
    },
]
