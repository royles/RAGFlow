"""OpenAI client API: chat and embeddings through the `openai` SDK.

Any server that speaks the OpenAI API works by setting a base URL: OpenAI,
Azure OpenAI's v1 endpoint, vLLM, Ollama, or Cloudera AI Inference.

Re-ranking has no OpenAI endpoint. It calls `{base_url}/rerank`, the shape
Cohere, Jina, and vLLM serve: {model, query, documents, top_n}.
"""

import httpx
from openai import OpenAI, OpenAIError

from backend.errors import BadRequest

DEFAULT_BASE_URL = "https://api.openai.com/v1"


def _client(creds: dict) -> OpenAI:
    return OpenAI(api_key=creds["api_key"], base_url=creds["base_url"], timeout=60, max_retries=4)


def embed_texts(creds: dict, model: str, texts: list[str], dim: int, purpose: str) -> list[list[float]]:
    client = _client(creds)
    # Only the text-embedding-3 models accept a dimensions argument.
    extra = {"dimensions": dim} if model.startswith("text-embedding-3") else {}
    vectors: list[list[float]] = []
    for start in range(0, len(texts), 64):
        batch = texts[start : start + 64]
        try:
            response = client.embeddings.create(
                model=model, input=batch, encoding_format="float", **extra
            )
        except OpenAIError as exc:
            raise BadRequest(f"The OpenAI endpoint rejected the embedding request: {exc}") from exc
        for item in sorted(response.data, key=lambda row: row.index):
            vector = [float(value) for value in item.embedding]
            if len(vector) != dim:
                raise BadRequest(f"{model} returned {len(vector)} dimensions, but {dim} are configured.")
            vectors.append(vector)
    return vectors


def chat(creds: dict, model: str, prompt: str) -> str:
    client = _client(creds)
    try:
        response = client.chat.completions.create(
            model=model, messages=[{"role": "user", "content": prompt}]
        )
    except OpenAIError as exc:
        raise BadRequest(f"The OpenAI endpoint rejected the chat request: {exc}") from exc
    text = (response.choices[0].message.content or "").strip() if response.choices else ""
    if not text:
        raise BadRequest("The OpenAI endpoint returned an empty answer.")
    return text


def rerank(creds: dict, model: str, query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
    base = (creds["base_url"] or DEFAULT_BASE_URL).rstrip("/")
    try:
        response = httpx.post(
            f"{base}/rerank",
            json={"model": model, "query": query, "documents": documents, "top_n": min(top_n, len(documents))},
            headers={"Authorization": f"Bearer {creds['api_key']}"},
            timeout=60,
        )
    except httpx.HTTPError as exc:
        raise BadRequest(f"Could not reach {base}/rerank: {exc}") from exc
    if response.status_code >= 400:
        raise BadRequest(f"{base}/rerank answered {response.status_code}: {response.text[:200]}")
    try:
        rows = response.json().get("results") or []
        ranked = [(int(row["index"]), float(row.get("relevance_score", row.get("score", 0)))) for row in rows]
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise BadRequest(f"{base}/rerank returned a response this app could not read.") from exc
    if not ranked:
        raise BadRequest(f"{base}/rerank returned no results.")
    return sorted(ranked, key=lambda pair: pair[1], reverse=True)
