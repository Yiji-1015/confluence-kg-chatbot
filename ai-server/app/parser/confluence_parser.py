import os
import re
from bs4 import BeautifulSoup, Tag
from typing import Dict, Any, List
from langchain_text_splitters import RecursiveCharacterTextSplitter


# Confluence storage HTML에만 존재하는 매크로/메타 태그. 본문 텍스트로 뽑히면 그대로
# 노이즈가 되므로 파싱 시작 단계에서 통째로 제거한다.
_NOISE_TAGS = ["ac:parameter", "ac:schema-version", "ac:macro-id", "ri:url", "script", "style"]


# 색인할 첨부파일 확장자. 사람이 이름을 지어 올리는 문서 형식만 받는다.
#
# 실측(2026-09-16, 670문서 / 첨부 참조 925건): 이미지가 483건(52.2%)으로 절반을 넘고
# 그 이름은 거의 전부 자동 생성이다 (image2022-7-18_14-9-6, 스크린샷 2025-10-31 오전 10.09.35,
# KakaoTalk_20231127_142544397). 이름을 색인해도 검색에 쓸모가 없을 뿐 아니라 해롭다.
# 날짜·id의 긴 숫자가 그대로 토큰이 되는데(image-20260914-001446 -> image / 20260914 / 001446)
# 코퍼스에 몇 건 없어 IDF가 극단적으로 높기 때문이다. 같은 함정이 이미 기록돼 있다:
# 날짜 토큰 20260303이 3,211청크 중 12건뿐이라 BM25 점수를 지배한 건(CHANGELOG 2026-09-13).
#
# 확장자로 거르면 pdf 200건 + excel 50건 = 251건(27.1%, 고유 234종)이 남고, 전부 사람이
# 지은 문서명이다 ("02. API 명세서", "K-water PoC WBS v4", "2022 취업규칙 신고서").
#
# pptx(63) / docx(44) / hwp(17+8)도 같은 성격이라 넓힐 후보다. 효과를 재고 늘린다.
_INDEXED_ATTACHMENT_EXTS = {".pdf", ".xlsx", ".xls", ".xlsm"}


def parse_confluence_html(html_content: str, metadata: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Confluence REST API에서 가져온 원본 storage HTML 본문을 파싱하는 함수.

    [핵심 처리 내용]
    1. 첨부파일(ri:attachment) 파일명 추출 — pdf/excel만 (_INDEXED_ATTACHMENT_EXTS 참고)
       노이즈 제거보다 먼저다. 매크로로 삽입된 첨부가 ac:parameter 안에 있기 때문이다.
    2. 매크로 노이즈 태그 제거 (ac:parameter, ac:schema-version 등)
    3. 내부 문서 링크(ac:link)를 "[본문](관련문서: 제목)" 형태로 보존 + 외부 링크(<a>) 보존
    4. 표(<table>)를 rowspan/colspan까지 반영해 마크다운 표로 변환
    5. 남은 Confluence 네임스페이스 태그(ac:*, ri:* 등)는 unwrap하여 본문에 이상한 태그가 남지 않게 정리
    """
    if not html_content:
        return {"cleaned_text": "", "metadata": metadata or {}, "attachments": []}

    soup = BeautifulSoup(html_content, "html.parser")

    # 1. 첨부파일 파일명 추출 (pdf/excel만, 확장자 제거한 이름만 보존)
    #
    # 노이즈 태그 제거보다 먼저 해야 한다. Confluence의 view-file/viewpdf 매크로는 첨부
    # 참조를 <ac:parameter ac:name="name"><ri:attachment .../></ac:parameter> 안에 넣는데,
    # ac:parameter는 _NOISE_TAGS라 decompose 대상이다. 순서가 뒤바뀌면 매크로로 삽입된
    # 첨부가 추출 전에 통째로 사라진다. pdf/excel은 대부분 이 매크로로 삽입되므로
    # 손실이 특히 컸다 (2026-09-16 확인: 고유 234종 중 73종만 남았다).
    # 같은 파일이 썸네일과 링크로 두 번 참조되는 경우가 흔해 중복을 제거한다. 중복이 남으면
    # 그 파일명의 term frequency만 부풀어 BM25 점수가 왜곡된다. 등장 순서는 유지한다.
    attachments: List[str] = []
    _seen_att = set()
    for att in soup.find_all("ri:attachment"):
        fname = att.get("ri:filename") or att.get("filename")
        if not fname:
            continue
        stem, ext = os.path.splitext(fname)
        # 확장자 판정이 먼저다. stem만 보면 형식을 알 수 없다.
        if ext.lower() not in _INDEXED_ATTACHMENT_EXTS:
            continue
        stem = stem.strip()
        if stem and stem not in _seen_att:
            _seen_att.add(stem)
            attachments.append(stem)

    # 2. 매크로 노이즈 태그 제거 (본문에 파라미터/스키마 값이 텍스트로 섞여 나오는 것 방지)
    for tag in soup.find_all(_NOISE_TAGS):
        tag.decompose()

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
    chunk_overlap: int = 150,
    attachments: List[str] = None
) -> List[Dict[str, Any]]:
    """
    파싱된 본문 텍스트를 RAG 검색에 적합한 작은 단위(Chunk)로 나누는 함수.

    [청킹 전략]
    - chunk_size=800: 한 청크당 약 800자 크기로 텍스트 분할
    - chunk_overlap=150: 문장 잘림으로 인한 문맥 단절을 방지하기 위해 앞뒤 청크가 150자씩 중복되도록 설정
    - chunk_id: '{doc_id}_chunk_{idx}' 형태로 고유 PK 생성 (Elasticsearch 덮어쓰기 및 Langfuse 추적에 활용)

    attachments는 문서 단위 정보라 그 문서의 모든 청크에 같은 값이 실린다. title과 같은
    취급이다. 어느 청크가 걸리든 첨부파일 이름으로 찾을 수 있어야 하기 때문이다.
    """
    attachments = attachments or []

    # 본문이 없고 첨부만 있는 문서. 사내 위키에는 이런 페이지가 흔하다 — 제목이 주제이고
    # 첨부 PDF가 내용 전부인 경우다 (취업규칙, 사업자등록증, 각종 인증서).
    # 실측(2026-09-16): pdf/excel 첨부 보유 102문서 중 29문서가 본문이 비어 있었다.
    #
    # 여기서 그냥 []를 돌려주면 그 문서는 색인에 아예 남지 않아 "취업규칙 어디 있어"에
    # 아무것도 걸리지 않는다. 첨부 이름을 본문 삼아 청크 하나를 만든다. 답변 모델이
    # 근거로 쓸 텍스트가 생기고, 임베딩도 제목과 파일명을 함께 태운다.
    if not text:
        if not attachments:
            return []
        text = "첨부파일: " + ", ".join(attachments)

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
            "attachments": attachments,          # 문서에 달린 첨부파일 이름 (확장자 제외)
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
