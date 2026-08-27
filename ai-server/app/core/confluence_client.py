import re
import httpx
import pandas as pd
from typing import List, Dict, Any, Optional
from app.config import settings


def extract_page_id(url_or_id: str) -> str:
    """
    Confluence 링크 URL 또는 pageId 문자열에서 숫자 ID만 추출하는 함수.
    
    지원 형식:
    - https://.../spaces/SPACE/pages/12345678/...
    - https://.../spaces/SPACE/database/12345678?param=...
    - https://.../spaces/SPACE/whiteboards/12345678
    - https://.../pages/viewpage.action?pageId=12345678
    - 순수 숫자 ID: '12345678'
    """
    if not url_or_id:
        return ""
    text = str(url_or_id).strip()
    
    # 1. 쿼리스트링 pageId=1234
    match = re.search(r'pageId=(\d+)', text)
    if match:
        return match.group(1)
        
    # 2. 경로 기반 (/pages/1234, /database/1234, /whiteboard/1234, /folder/1234 등)
    match = re.search(r'/(?:pages|database|whiteboards?|folder)/(\d+)', text)
    if match:
        return match.group(1)
        
    # 3. 경로 끝 또는 중간의 4자리 이상 숫자
    match = re.search(r'/(\d{4,})(?:[/?#]|$)', text)
    if match:
        return match.group(1)
        
    # 4. 텍스트 내 4자리 이상 독립된 숫자
    match = re.search(r'\b(\d{4,})\b', text)
    if match:
        return match.group(1)
        
    return text


def _get_auth_headers() -> tuple:
    """
    Confluence REST API 호출용 Basic Auth 인증 헤더 정보 반환 (email, token)
    """
    email = settings.CONFLUENCE_EMAIL or ""
    token = settings.CONFLUENCE_API_TOKEN or ""
    return (email, token)


def fetch_all_page_ids(space_key: Optional[str] = None) -> List[str]:
    """
    [삭제 동기화 추적용 함수]
    현재 Confluence에 존재하는 전체 문서의 ID 목록만 빠르게 수집합니다.
    본문을 끌어오지 않고 ID만 수집하므로 1초 이내로 매우 빠르게 동작합니다.

    Elasticsearch에 저장된 ID들과 비교(Diffing)하여 삭제된 문서를 감지하는 데 활용됩니다.

    [페이지네이션]
    한 번의 요청으로는 최대 500개까지만 응답에 담겨오므로, 응답의 _links.next를
    끝까지 따라가며 전체 목록을 수집합니다.
    """
    target_space = space_key or settings.CONFLUENCE_SPACE_KEY
    auth = _get_auth_headers()
    page_ids = []

    url = f"{settings.CONFLUENCE_BASE_URL}/rest/api/content"
    params = {
        "spaceKey": target_space,
        "type": "page",
        "limit": 500,
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            while url:
                response = client.get(url, params=params, auth=auth)
                response.raise_for_status()
                data = response.json()

                for item in data.get("results", []):
                    page_ids.append(str(item["id"]))

                # _links.next는 다음 페이지 요청에 필요한 파라미터를 이미 포함한 상대경로
                next_path = data.get("_links", {}).get("next")
                url = f"{settings.CONFLUENCE_BASE_URL}{next_path}" if next_path else None
                params = None  # next_path에 파라미터가 이미 들어있으므로 이후 요청에서는 생략

    except Exception as e:
        print(f"[Confluence Error] 전체 페이지 ID 목록 수집 중 오류 발생: {e}")

    return page_ids


def fetch_confluence_pages(
    space_key: Optional[str] = None,
    modified_since: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Confluence 문서 본문(HTML) 및 작성자/수정 시각 메타데이터를 수집하는 함수.
    
    [증분 수집 지원]
    - modified_since (ISO 8601 형식 시각) 파라미터가 전달되면,
      해당 시각 이후에 새로 수정되거나 생성된 문서만 골라서 증분 수집(Incremental Fetch)합니다.
    """
    target_space = space_key or settings.CONFLUENCE_SPACE_KEY
    auth = _get_auth_headers()
    pages = []

    url = f"{settings.CONFLUENCE_BASE_URL}/rest/api/content"
    params = {
        "spaceKey": target_space,
        "type": "page",
        "expand": "body.storage,version,history.lastUpdated,history.createdBy",
        "limit": 100
    }

    try:
        with httpx.Client(timeout=15.0) as client:
            while url:
                response = client.get(url, params=params, auth=auth)
                response.raise_for_status()
                data = response.json()

                for item in data.get("results", []):
                    last_updated = item.get("history", {}).get("lastUpdated", {}).get("when")

                    # 증분 수집 처리: modified_since 시각 이전의 구 문서면 건너뜀
                    if modified_since and last_updated and last_updated < modified_since:
                        continue

                    page_info = {
                        "id": str(item["id"]),
                        "title": item.get("title", ""),
                        "space_key": target_space,
                        "html_body": item.get("body", {}).get("storage", {}).get("value", ""),
                        "author": item.get("history", {}).get("createdBy", {}).get("displayName", "Unknown"),
                        "version": item.get("version", {}).get("number", 1),
                        "last_updated": last_updated,
                        "url": f"{settings.CONFLUENCE_BASE_URL}/spaces/{target_space}/pages/{item['id']}"
                    }
                    pages.append(page_info)

                # _links.next는 다음 페이지 요청에 필요한 파라미터를 이미 포함한 상대경로
                next_path = data.get("_links", {}).get("next")
                url = f"{settings.CONFLUENCE_BASE_URL}{next_path}" if next_path else None
                params = None  # next_path에 파라미터가 이미 들어있으므로 이후 요청에서는 생략

    except Exception as e:
        print(f"[Confluence Error] 문서 수집 중 오류 발생: {e}")

    return pages


def fetch_page_by_id(page_id_or_url: str) -> Optional[Dict[str, Any]]:
    """
    특정 페이지 ID 또는 Confluence URL 1건에 대한 상세 본문 및 메타데이터 단건 조회 함수.
    """
    page_id = extract_page_id(page_id_or_url)
    if not page_id:
        print(f"[Confluence Error] 유효한 page_id를 추출할 수 없습니다: {page_id_or_url}")
        return None

    url = f"{settings.CONFLUENCE_BASE_URL}/rest/api/content/{page_id}"
    params = {
        "expand": "body.storage,version,history.lastUpdated,history.createdBy"
    }

    auth = _get_auth_headers()

    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(url, params=params, auth=auth)
            response.raise_for_status()
            item = response.json()

            return {
                "id": str(item["id"]),
                "title": item.get("title", ""),
                "space_key": item.get("space", {}).get("key", settings.CONFLUENCE_SPACE_KEY),
                "html_body": item.get("body", {}).get("storage", {}).get("value", ""),
                "author": item.get("history", {}).get("createdBy", {}).get("displayName", "Unknown"),
                "version": item.get("version", {}).get("number", 1),
                "last_updated": item.get("history", {}).get("lastUpdated", {}).get("when"),
                "url": f"{settings.CONFLUENCE_BASE_URL}/pages/{page_id}"
            }

    except Exception as e:
        print(f"[Confluence Error] 단건 페이지 조회 실패 (ID: {page_id}): {e}")
        return None


def fetch_pages_by_ids(page_ids: List[str]) -> List[Dict[str, Any]]:
    """
    페이지 ID 목록에 대해 본문을 포함한 상세 정보를 조회하는 함수.
    카테고리 필터링 등으로 이미 대상 ID가 정해진 경우, 스페이스 전체를 다시 훑지 않고
    필요한 문서만 골라서 가져올 때 사용한다.
    """
    pages = []
    for page_id in page_ids:
        page = fetch_page_by_id(page_id)
        if page:
            pages.append(page)
    return pages


def fetch_pages_with_category(space_key: Optional[str] = None) -> pd.DataFrame:
    """
    스페이스의 모든 문서를 "카테고리 경로"와 함께 가져오는 함수. 본문(body)은 가져오지 않고
    제목/조상(ancestors) 정보만 가져오므로 가볍고 빠르다.

    Confluence는 페이지 조회 시 expand=ancestors 옵션으로 "루트부터 자기 자신 바로 위 부모까지"의
    조상 페이지 목록을 함께 내려준다. 이를 이용해 문서 하나당 API 호출 한 번으로
    "대분류 / 중분류 / 문서 제목" 같은 카테고리 경로(path)를 만들 수 있다.

    반환하는 DataFrame에는 다음 컬럼이 있다.
        - id, title, path (예: "솔루션/개발 / 백엔드 가이드")
        - level_0, level_1, ... : path를 계층별로 쪼갠 컬럼 (예: level_0="솔루션/개발")
    """
    target_space = space_key or settings.CONFLUENCE_SPACE_KEY
    auth = _get_auth_headers()
    page_infos = []

    url = f"{settings.CONFLUENCE_BASE_URL}/rest/api/content"
    params = {
        "spaceKey": target_space,
        "type": "page",
        "expand": "ancestors",
        "limit": 200,
    }

    try:
        with httpx.Client(timeout=15.0) as client:
            while url:
                response = client.get(url, params=params, auth=auth)
                response.raise_for_status()
                data = response.json()

                for item in data.get("results", []):
                    ancestors = item.get("ancestors", [])
                    # 조상 제목 리스트를 그대로 보존한다. 문자열로 합쳤다가 "/"로 다시 쪼개면
                    # "솔루션/개발"처럼 제목 자체에 "/"가 포함된 경우 계층이 잘못 나뉘므로
                    # path 표시용 문자열과 레벨 분리용 리스트를 반드시 따로 다룬다.
                    title_parts = [a["title"] for a in ancestors] + [item["title"]]
                    page_infos.append({
                        "id": str(item["id"]),
                        "title": item["title"],
                        "path": " / ".join(title_parts),
                        "_title_parts": title_parts,
                    })

                next_path = data.get("_links", {}).get("next")
                url = f"{settings.CONFLUENCE_BASE_URL}{next_path}" if next_path else None
                params = None

    except Exception as e:
        print(f"[Confluence Error] 카테고리 목록 수집 중 오류 발생: {e}")

    df = pd.DataFrame(page_infos)
    if df.empty:
        return df

    max_level = df["_title_parts"].apply(len).max()
    for i in range(max_level):
        df[f"level_{i}"] = df["_title_parts"].apply(lambda parts, i=i: parts[i] if len(parts) > i else None)

    df = df.drop(columns=["_title_parts"])
    return df


def filter_pages_by_category(df: pd.DataFrame, filters: Dict[str, str]) -> List[str]:
    """
    fetch_pages_with_category()가 반환한 DataFrame을 카테고리 레벨 기준으로 필터링해
    해당 카테고리에 속한 문서 ID 목록만 뽑아내는 함수.

    예: filter_pages_by_category(df, {"level_0": "솔루션/개발"})
    """
    if df.empty:
        return []

    filtered = df
    for level, value in filters.items():
        if level in df.columns:
            filtered = filtered[filtered[level] == value]

    return filtered["id"].astype(str).tolist()
