"""Amazon Bedrock: embeddings, chat (Converse), and re-ranking.

Saved keys are used when present. Otherwise boto3 falls back to its normal
chain: environment variables, then the Cloudera AI runtime role. Blank key
variables are ignored so an empty prototype form does not hide the role.
"""

import json
import os

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from backend.errors import BadRequest

_PROFILE_PREFIXES = ("us", "eu", "apac", "jp", "au", "ca", "global", "us-gov")
_BLANK_KEYS = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN")


def client(service: str, creds: dict):
    for name in _BLANK_KEYS:
        if os.environ.get(name, "").strip() == "":
            os.environ.pop(name, None)
    kwargs = {"region_name": creds["region"]}
    if creds.get("access_key_id"):
        kwargs["aws_access_key_id"] = creds["access_key_id"]
        kwargs["aws_secret_access_key"] = creds["secret_access_key"]
        kwargs["aws_session_token"] = creds.get("session_token")
    return boto3.client(service, **kwargs)


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
        "This Bedrock embedding model is not supported. Use Titan Embeddings or Cohere Embed."
    )


def embed_texts(creds: dict, model: str, texts: list[str], dim: int, purpose: str) -> list[list[float]]:
    runtime = client("bedrock-runtime", creds)
    vectors: list[list[float]] = []
    for text in texts:
        body = embedding_request(model, text, dim, purpose)
        try:
            response = runtime.invoke_model(modelId=model, body=json.dumps(body))
        except (BotoCoreError, ClientError) as exc:
            raise BadRequest(f"Amazon Bedrock rejected the embedding request: {exc}{_profile_hint(exc, model)}") from exc
        vector = _read_vector(json.loads(response["body"].read()))
        if len(vector) != dim:
            raise BadRequest(f"{model} returned {len(vector)} dimensions, but {dim} are configured.")
        vectors.append(vector)
    return vectors


def converse(creds: dict, model: str, prompt: str) -> str:
    """Converse works across Claude, Nova, and Llama models."""
    runtime = client("bedrock-runtime", creds)
    try:
        response = runtime.converse(
            modelId=model,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 800, "temperature": 0.2},
        )
    except (BotoCoreError, ClientError) as exc:
        raise BadRequest(f"Amazon Bedrock rejected the chat request: {exc}{_profile_hint(exc, model)}") from exc
    parts = response.get("output", {}).get("message", {}).get("content", [])
    text = "".join(part.get("text", "") for part in parts).strip()
    if not text:
        raise BadRequest("Amazon Bedrock returned an empty answer.")
    return text


def rerank(creds: dict, model: str, query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
    """Return (document index, relevance score), best first."""
    agent = client("bedrock-agent-runtime", creds)
    arn = model if model.startswith("arn:") else f"arn:aws:bedrock:{creds['region']}::foundation-model/{model}"
    try:
        response = agent.rerank(
            queries=[{"type": "TEXT", "textQuery": {"text": query}}],
            sources=[
                {
                    "type": "INLINE",
                    "inlineDocumentSource": {"type": "TEXT", "textDocument": {"text": document}},
                }
                for document in documents
            ],
            rerankingConfiguration={
                "type": "BEDROCK_RERANKING_MODEL",
                "bedrockRerankingConfiguration": {
                    "modelConfiguration": {"modelArn": arn},
                    "numberOfResults": min(top_n, len(documents)),
                },
            },
        )
    except (BotoCoreError, ClientError) as exc:
        raise BadRequest(f"Amazon Bedrock rejected the re-rank request: {exc}{_rerank_hint(exc, creds['region'])}") from exc
    return [(int(row["index"]), float(row["relevanceScore"])) for row in response["results"]]


def _profile_hint(exc: Exception, model: str) -> str:
    """Newer models can only be called through an inference profile id, not the bare model id."""
    if "inference profile" not in str(exc) or model.startswith("arn:"):
        return ""
    bare = model.split(".", 1)[1] if model.split(".", 1)[0] in _PROFILE_PREFIXES else model
    return (
        f" This model needs an inference profile id. Add a region prefix, for example "
        f"us.{bare}, eu.{bare} or global.{bare}, or paste the inference profile ARN."
    )


def _rerank_hint(exc: Exception, region: str) -> str:
    """Re-rank models are offered in few regions, and a wrong region reports an invalid model id."""
    if "model identifier is invalid" not in str(exc):
        return ""
    return (
        f" The region used was {region}. Re-rank models are only offered in some regions "
        "(for example us-west-2, ca-central-1, eu-central-1 and ap-northeast-1), and the model must be "
        "enabled in your AWS account. Set a Region on the re-rank model card to use a different one."
    )


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
