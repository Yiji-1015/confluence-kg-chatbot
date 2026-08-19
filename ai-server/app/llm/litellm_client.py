import httpx
from typing import List, Optional
from app.config import settings


def embed_texts(texts: List[str], model: Optional[str] = None) -> List[List[float]]:
    """
    LiteLLM 게이트웨이(OpenAI 호환 /v1/embeddings)를 통해 텍스트 목록을 임베딩 벡터로 변환하는 함수.

    ELASTICSEARCH.md 기준: OpenAI text-embedding-3-small, 1536차원.
    입력 순서와 반환되는 벡터 리스트의 순서는 항상 동일하다.
    """
    if not texts:
        return []

    target_model = model or settings.DEFAULT_EMBEDDING_MODEL
    url = f"{settings.LITELLM_BASE_URL}/v1/embeddings"

    with httpx.Client(timeout=30.0) as client:
        response = client.post(url, json={"model": target_model, "input": texts})
        response.raise_for_status()
        data = response.json()

    return [item["embedding"] for item in data["data"]]


def generate_answer(query: str, context: str, model: Optional[str] = None) -> str:
    """
    LiteLLM 게이트웨이(OpenAI 호환 /v1/chat/completions)를 통해 검색된 context를 근거로 답변을 생성하는 함수.
    """
    target_model = model or settings.DEFAULT_LLM_MODEL
    url = f"{settings.LITELLM_BASE_URL}/v1/chat/completions"

    messages = [
        {
            "role": "system",
            "content": "당신은 사내 Confluence 문서를 기반으로 답변하는 어시스턴트입니다. "
                       "주어진 context에 근거해서만 답변하고, context에 없는 내용은 모른다고 답하세요.",
        },
        {
            "role": "user",
            "content": f"[Context]\n{context}\n\n[질문]\n{query}",
        },
    ]

    with httpx.Client(timeout=30.0) as client:
        response = client.post(url, json={"model": target_model, "messages": messages})
        response.raise_for_status()
        data = response.json()

    return data["choices"][0]["message"]["content"]
