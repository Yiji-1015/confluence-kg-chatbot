import httpx
from typing import List, Optional
from app.config import settings
from app.llm.prompts import RAG_SYSTEM_PROMPT


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


def generate_chat_completion(messages: List[dict], model: Optional[str] = None) -> str:
    """
    임의의 메시지 리스트(System, User, Assistant)를 LiteLLM 게이트웨이(/v1/chat/completions)로 전달해 답변을 생성하는 범용 함수.
    (멀티턴 대화, 질문 재작성 등에 사용)
    """
    target_model = model or settings.DEFAULT_LLM_MODEL
    url = f"{settings.LITELLM_BASE_URL}/v1/chat/completions"

    with httpx.Client(timeout=30.0) as client:
        response = client.post(url, json={"model": target_model, "messages": messages})
        response.raise_for_status()
        data = response.json()

    return data["choices"][0]["message"]["content"]


def generate_answer(
    query: str,
    context: str,
    model: Optional[str] = None,
    history: Optional[List[dict]] = None,
) -> str:
    """
    LiteLLM 게이트웨이(OpenAI 호환 /v1/chat/completions)를 통해 검색된 context를 근거로 답변을 생성하는 RAG 전용 함수.

    history: [{"role": "user"|"assistant", "content": "..."}, ...] 형태의 이전 턴 목록.
    시스템 프롬프트 다음, 이번 턴의 [Context]+[질문] 메시지 앞에 그대로 끼워 넣어서
    "내 이름이 뭐라고 했지?" 같은 멀티턴 참조 질문에도 답할 수 있게 한다.
    """
    target_model = model or settings.DEFAULT_LLM_MODEL

    messages = [{"role": "system", "content": RAG_SYSTEM_PROMPT}]
    messages.extend(history or [])
    messages.append({
        "role": "user",
        "content": f"[Context]\n{context}\n\n[질문]\n{query}",
    })

    return generate_chat_completion(messages, model=target_model)
