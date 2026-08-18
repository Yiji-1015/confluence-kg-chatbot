from bs4 import BeautifulSoup
from typing import Dict, Any, List
from langchain_text_splitters import RecursiveCharacterTextSplitter


def parse_confluence_html(html_content: str, metadata: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Confluence REST API에서 가져온 원본 HTML 본문을 파싱하는 함수.
    
    [핵심 처리 내용]
    1. 내부/외부 링크(<a> 태그) 추출 및 보존
    2. 표(<table> 태그) 구조가 파괴되지 않도록 마크다운 표(| 제목 | 내용 |)로 변환
    3. HTML 태그가 제거된 깔끔한 텍스트 및 메타데이터 반환
    """
    if not html_content:
        return {"cleaned_text": "", "metadata": metadata or {}, "links": []}

    soup = BeautifulSoup(html_content, "html.parser")

    # 1. 문서 내부의 모든 링크(URL 및 참고 문서) 추출
    links = []
    for a_tag in soup.find_all("a", href=True):
        links.append({
            "text": a_tag.get_text(strip=True),
            "url": a_tag["href"]
        })

    # 2. 표(<table>) 구조를 마크다운 형태(| 제목 | 내용 |)로 텍스트화하여 보존
    for table in soup.find_all("table"):
        table_md = _table_to_markdown(table)
        table.replace_with(f"\n\n{table_md}\n\n")

    # 3. HTML 태그를 떼어내고 개행 문자로 구분된 본문 텍스트 추출
    cleaned_text = soup.get_text(separator="\n", strip=True)

    return {
        "cleaned_text": cleaned_text,
        "metadata": metadata or {},
        "links": links
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


def _table_to_markdown(table_soup: BeautifulSoup) -> str:
    """
    BeautifulSoup의 <table> 태그 객체를 마크다운 표 문자열(| 제목 | 내용 |)로 변환하는 헬퍼 함수
    """
    rows = table_soup.find_all("tr")
    if not rows:
        return ""

    table_data = []
    for row in rows:
        # 각 셀(td, th)의 텍스트 추출 및 개행문자 정리
        cols = [col.get_text(strip=True).replace("\n", " ") for col in row.find_all(["td", "th"])]
        if cols:
            table_data.append(cols)

    if not table_data:
        return ""

    # 마크다운 표 헤더(첫 번째 행) 및 구분선(| --- | --- |) 작성
    header = table_data[0]
    md_lines = ["| " + " | ".join(header) + " |"]
    md_lines.append("| " + " | ".join(["---"] * len(header)) + " |")

    # 데이터 행 작성 (열 개수가 부족할 경우 빈칸 패딩)
    for row in table_data[1:]:
        padded_row = row + [""] * (len(header) - len(row))
        md_lines.append("| " + " | ".join(padded_row[:len(header)]) + " |")

    return "\n".join(md_lines)
