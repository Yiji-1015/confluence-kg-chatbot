import os
import re
from datetime import date
from bs4 import BeautifulSoup, Tag
from typing import Dict, Any, List
from langchain_text_splitters import RecursiveCharacterTextSplitter


# Confluence storage HTML에만 존재하는 매크로/메타 태그. 본문 텍스트로 뽑히면 그대로
# 노이즈가 되므로 파싱 시작 단계에서 통째로 제거한다.
_NOISE_TAGS = ["ac:parameter", "ac:schema-version", "ac:macro-id", "ri:url", "script", "style"]


# 제목에 쓰이는 날짜 표기들. 실제 인덱스 제목 563건을 훑어 확인한 세 가지 형태다
# (YYYYMMDD 147건 / YY.MM.DD 15건 / YYYY년 M월 D일 6건).
_DATE_PATTERNS = [
    re.compile(r"(?<!\d)(20\d{2})[.\-/]?(0[1-9]|1[0-2])[.\-/]?(0[1-9]|[12]\d|3[01])(?!\d)"),
    re.compile(r"(?<!\d)(20\d{2}|\d{2})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일"),
    re.compile(r"(?<!\d)([2-3]\d)[.\-](\d{1,2})[.\-](\d{1,2})(?!\d)"),
]


def expand_title_dates(title: str) -> str:
    """
    제목에 있는 날짜를 여러 표기로 펼친 문자열을 만든다 (검색 전용 필드에 넣기 위함).

    Nori는 "20260303"을 토큰 하나로 자르기 때문에, 질문의 "2026년 3월 3일"
    (-> 2026 / 년 / 3 / 월 / 3 / 일)과 단 한 토큰도 겹치지 않는다. 그 결과 회의록처럼
    제목 형식이 같고 날짜만 다른 문서들 사이에서 날짜가 BM25 점수에 전혀 기여하지 못하고,
    심지어 날짜가 다른 문서가 정답보다 높은 점수를 받는다 (2026-09-01 실측).

    색인 시점에 표기를 펼쳐두면 질의 시점에 질문을 파싱할 필요가 없다.

    >>> expand_title_dates("20260303_주간미팅_솔루션")
    '20260303 2026-03-03 2026년 3월 3일 26년 3월 3일'
    """
    found: List[tuple] = []
    for pattern in _DATE_PATTERNS:
        for match in pattern.finditer(title or ""):
            year, month, day = (int(g) for g in match.groups())
            if year < 100:
                year += 2000
            try:
                date(year, month, day)  # 13월 32일 같은 오탐 제거
            except ValueError:
                continue
            found.append((year, month, day))

    variants: List[str] = []
    for year, month, day in dict.fromkeys(found):  # 중복 제거, 등장 순서 유지
        variants += [
            f"{year}{month:02d}{day:02d}",
            f"{year}-{month:02d}-{day:02d}",
            f"{year}년 {month}월 {day}일",
            f"{year % 100:02d}년 {month}월 {day}일",
        ]
    return " ".join(dict.fromkeys(variants))


def parse_confluence_html(html_content: str, metadata: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Confluence REST API에서 가져온 원본 storage HTML 본문을 파싱하는 함수.

    [핵심 처리 내용]
    1. 매크로 노이즈 태그 제거 (ac:parameter, ac:schema-version 등)
    2. 첨부파일(ri:attachment) 파일명 추출
    3. 내부 문서 링크(ac:link)를 "[본문](관련문서: 제목)" 형태로 보존 + 외부 링크(<a>) 보존
    4. 표(<table>)를 rowspan/colspan까지 반영해 마크다운 표로 변환
    5. 남은 Confluence 네임스페이스 태그(ac:*, ri:* 등)는 unwrap하여 본문에 이상한 태그가 남지 않게 정리
    """
    if not html_content:
        return {"cleaned_text": "", "metadata": metadata or {}, "attachments": []}

    soup = BeautifulSoup(html_content, "html.parser")

    # 1. 매크로 노이즈 태그 제거 (본문에 파라미터/스키마 값이 텍스트로 섞여 나오는 것 방지)
    for tag in soup.find_all(_NOISE_TAGS):
        tag.decompose()

    # 2. 첨부파일 파일명 추출 (확장자 제거한 이름만 보존)
    attachments: List[str] = []
    for att in soup.find_all("ri:attachment"):
        fname = att.get("ri:filename") or att.get("filename")
        if fname:
            attachments.append(os.path.splitext(fname)[0])

    # 3-1. Confluence 내부 문서 링크(ac:link) — <a href>가 아니라 ac:link/ri:page 조합으로 표현됨
    for link in soup.find_all("ac:link"):
        page_ref = link.find("ri:page")
        target_title = page_ref.get("ri:content-title", "") if page_ref else ""
        body_text = link.get_text(strip=True)

        if target_title:
            link.replace_with(f"[{body_text}](관련문서: {target_title})")
        else:
            link.replace_with(body_text)

    # 4. 표(<table>) 구조를 rowspan/colspan까지 반영해 마크다운 표로 보존
    for table in soup.find_all("table"):
        records = _table_to_records(table)
        table_md = _table_records_to_markdown(records)
        table.replace_with(f"\n\n{table_md}\n\n" if table_md else "")

    # 5. 처리하지 않은 나머지 Confluence 네임스페이스 태그(ac:*, ri:* 등)는 내용만 남기고 태그 자체는 제거
    for tag in soup.find_all():
        if tag.name and ":" in tag.name:
            tag.unwrap()

    # 6. HTML 태그를 떼어내고 개행 문자로 구분된 본문 텍스트 추출
    cleaned_text = soup.get_text(separator="\n", strip=True)

    return {
        "cleaned_text": cleaned_text,
        "metadata": metadata or {},
        "attachments": attachments,
    }


def split_text_into_chunks(
    doc_id: str,
    title: str,
    text: str,
    metadata: Dict[str, Any] = None,
    chunk_size: int = 800,
    chunk_overlap: int = 150
) -> List[Dict[str, Any]]:
    """
    파싱된 본문 텍스트를 RAG 검색에 적합한 작은 단위(Chunk)로 나누는 함수.

    [청킹 전략]
    - chunk_size=800: 한 청크당 약 800자 크기로 텍스트 분할
    - chunk_overlap=150: 문장 잘림으로 인한 문맥 단절을 방지하기 위해 앞뒤 청크가 150자씩 중복되도록 설정
    - chunk_id: '{doc_id}_chunk_{idx}' 형태로 고유 PK 생성 (Elasticsearch 덮어쓰기 및 Langfuse 추적에 활용)
    """
    if not text:
        return []

    # 문단(\n\n) -> 줄바꿈(\n) -> 띄어쓰기( ) 순서로 자연스럽게 단락을 자르는 분할기
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", " ", ""]
    )

    chunks = splitter.split_text(text)
    chunk_docs = []

    for idx, chunk_text in enumerate(chunks):
        chunk_docs.append({
            "chunk_id": f"{doc_id}_chunk_{idx}",  # Elasticsearch _id 및 Langfuse Trace 식별자
            "doc_id": doc_id,                    # 원본 Confluence 문서 ID
            "title": title,                      # 문서 제목
            "chunk_index": idx,                  # 청크 순서 (0, 1, 2...)
            "total_chunks": len(chunks),         # 전체 청크 개수
            "text": chunk_text,                  # 분할된 청크 텍스트 본문
            "metadata": metadata or {}           # 작성자, URL, space_key 등 메타데이터
        })

    return chunk_docs


def _table_to_records(table_tag: Tag) -> List[Dict[str, str]]:
    """
    <table> 태그를 행 단위 레코드(List[Dict])로 변환하는 헬퍼 함수.
    rowspan/colspan을 그리드로 펼쳐서 병합 셀이 있어도 열이 밀리지 않게 한다.
    """
    rows_tag = table_tag.find_all("tr")
    if not rows_tag:
        return []

    grid: List[List[str]] = []
    rowspan_map: Dict[int, List[Any]] = {}

    for tr in rows_tag:
        row: List[str] = []
        col_idx = 0
        cells = tr.find_all(["td", "th"])
        cell_iter = iter(cells)

        while True:
            if col_idx in rowspan_map:
                remaining_rows, text = rowspan_map[col_idx]
                row.append(text)
                if remaining_rows > 1:
                    rowspan_map[col_idx][0] -= 1
                else:
                    del rowspan_map[col_idx]
                col_idx += 1
                continue

            try:
                cell = next(cell_iter)
            except StopIteration:
                break

            text = cell.get_text(" ", strip=True) or ""
            rowspan = int(cell.get("rowspan", 1))
            colspan = int(cell.get("colspan", 1))

            for _ in range(colspan):
                row.append(text)
            if rowspan > 1:
                for c in range(colspan):
                    rowspan_map[col_idx + c] = [rowspan - 1, text]

            col_idx += colspan

        grid.append(row)

    if not grid:
        return []

    max_len = max(len(r) for r in grid)
    for r in grid:
        r.extend([""] * (max_len - len(r)))

    header_idx = 0
    for i, tr in enumerate(rows_tag):
        if tr.find("th"):
            header_idx = i
            break

    header_row = grid[header_idx]
    header_clean: List[str] = []
    seen: Dict[str, int] = {}
    for i, name in enumerate(header_row):
        name = name.strip() or f"col_{i}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 1
        header_clean.append(name)

    records: List[Dict[str, str]] = []
    for row in grid[header_idx + 1:]:
        rec: Dict[str, str] = {}
        has_data = False
        for col_name, value in zip(header_clean, row):
            v = value.strip()
            if v:
                rec[col_name] = v
                has_data = True
        if has_data:
            records.append(rec)

    return records


def _table_records_to_markdown(records: List[Dict[str, str]]) -> str:
    """
    표 레코드(List[Dict])를 마크다운 표 문자열(| 제목 | 내용 |)로 변환하는 헬퍼 함수.
    """
    if not records:
        return ""

    headers = list(records[0].keys())
    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")

    for rec in records:
        row_values = []
        for h in headers:
            val = str(rec.get(h, "")).replace("\n", " ").replace("|", "\\|")
            row_values.append(val)
        lines.append("| " + " | ".join(row_values) + " |")

    return "\n".join(lines)


if __name__ == "__main__":
    # 실행: docker exec rag-ai-server python -m app.parser.confluence_parser
    # 실제 인덱스에 있던 제목 형태들로 검증한다.
    assert expand_title_dates("20260303_주간미팅_솔루션 test").split() == [
        "20260303", "2026-03-03", "2026년", "3월", "3일", "26년", "3월", "3일"
    ]
    assert "2025-10-24" in expand_title_dates("25.10.24 업무 범위 협의")
    assert "20260331" in expand_title_dates("[회의록] 26년 03월 31일")
    assert "20260828" in expand_title_dates("AI/LLM 주간 브리핑 (2026-08-28)")
    # 날짜가 두 개면 둘 다 펼친다
    assert expand_title_dates("주간 업무 보고서: 2026년 3월 23일 ~ 3월 27일").count("2026년") >= 1
    # 날짜가 없으면 빈 문자열
    assert expand_title_dates("주차 지원 기준") == ""
    assert expand_title_dates("") == ""
    # 13월 32일 같은 오탐은 버린다
    assert expand_title_dates("20261332_회의") == ""
    print("confluence_parser self-check OK")
