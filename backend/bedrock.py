"""Amazon Bedrock embeddings and chat.

Credentials come from the normal AWS chain: an access key in the environment,
or the Cloudera AI runtime role. Blank key variables are ignored so an empty
prototype form does not override the role.
"""

import json
import os

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from backend.errors import BadRequest

_BLANK_KEYS = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN")


def embedding_request(model: str, text: str, dim: int, purpose: str) -> dict:
    """Body for the Bedrock embedding models this app knows how to call."""
    if "cohere.embed" in model:
        input_type = "search_query" if purpose == "query" else "search_document"
        return {
            "texts": [text],
            "input_type": input_type,
            "embedding_types": ["float"],
        }
    if "titan-embed-text-v2" in model:
        return {"inputText": text, "dimensions": dim, "normalize": True}
    if "titan-embed" in model:
        return {"inputText": text}
    raise BadRequest(
        "This embedding model is not supported. "
        "Use Titan Embeddings, Cohere Embed, or the local embedder."
    )


def embed_texts(model: str, texts: list[str], dim: int, region: str, purpose: str) -> list[list[float]]:
    client = _client(region)
    vectors: list[list[float]] = []
    for text in texts:
        body = embedding_request(model, text, dim, purpose)
        try:
            response = client.invoke_model(modelId=model, body=json.dumps(body))
        except (BotoCoreError, ClientError) as exc:
            raise BadRequest(f"Amazon Bedrock rejected the embedding request: {exc}") from exc
        payload = json.loads(response["body"].read())
        vector = _read_vector(payload)
        if len(vector) != dim:
            raise BadRequest(
                f"{model} returned {len(vector)} dimensions, but this knowledge base uses {dim}."
            )
        vectors.append(vector)
    return vectors


def complete(model: str, region: str, prompt: str) -> str:
    """Ask a Bedrock chat model to answer. Converse works across Claude, Nova, and Llama."""
    client = _client(region)
    try:
        response = client.converse(
            modelId=model,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 800, "temperature": 0.2},
        )
    except (BotoCoreError, ClientError) as exc:
        raise BadRequest(f"Amazon Bedrock rejected the chat request: {exc}") from exc
    parts = response.get("output", {}).get("message", {}).get("content", [])
    text = "".join(part.get("text", "") for part in parts).strip()
    if not text:
        raise BadRequest("Amazon Bedrock returned an empty answer.")
    return text


def _client(region: str):
    for name in _BLANK_KEYS:
        if os.environ.get(name, "").strip() == "":
            os.environ.pop(name, None)
    return boto3.client("bedrock-runtime", region_name=region)


def _read_vector(payload: dict) -> list[float]:
    if isinstance(payload.get("embedding"), list):
        return [float(value) for value in payload["embedding"]]
    embeddings = payload.get("embeddings")
    if isinstance(embeddings, dict):
        rows = embeddings.get("float") or []
        if rows:
            return [float(value) for value in rows[0]]
    if isinstance(embeddings, list) and embeddings and isinstance(embeddings[0], list):
        return [float(value) for value in embeddings[0]]
    raise BadRequest("Amazon Bedrock returned an embedding response this app could not read.")
