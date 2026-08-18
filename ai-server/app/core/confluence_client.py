import httpx
from typing import List, Dict, Any, Optional
from app.config import settings


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
    """
    target_space = space_key or settings.CONFLUENCE_SPACE_KEY
    url = f"{settings.CONFLUENCE_BASE_URL}/rest/api/content"
    params = {
        "spaceKey": target_space,
        "type": "page",
        "limit": 500,
    }

    auth = _get_auth_headers()
    page_ids = []

    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(url, params=params, auth=auth)
            response.raise_for_status()
            data = response.json()

            for item in data.get("results", []):
                page_ids.append(str(item["id"]))

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
    url = f"{settings.CONFLUENCE_BASE_URL}/rest/api/content"
    params = {
        "spaceKey": target_space,
        "type": "page",
        "expand": "body.storage,version,history.lastUpdated,history.createdBy",
        "limit": 100
    }

    auth = _get_auth_headers()
    pages = []

    try:
        with httpx.Client(timeout=15.0) as client:
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

    except Exception as e:
        print(f"[Confluence Error] 문서 수집 중 오류 발생: {e}")

    return pages


def fetch_page_by_id(page_id: str) -> Optional[Dict[str, Any]]:
    """
    특정 페이지 ID 1건에 대한 상세 본문 및 메타데이터 단건 조회 함수.
    """
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
