"""Amazon Bedrock: embeddings, chat (Converse), and re-ranking.

Saved keys are used when present. Otherwise boto3 falls back to its normal
chain: environment variables, then the Cloudera AI runtime role. Blank key
variables are ignored so an empty prototype form does not hide the role.
"""

import json
import os
import threading

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from backend.errors import BadRequest

_PROFILE_PREFIXES = ("us", "eu", "apac", "jp", "au", "ca", "global", "us-gov")
_BLANK_KEYS = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN")


_clients: dict = {}
_clients_lock = threading.Lock()


def client(service: str, creds: dict):
    for name in _BLANK_KEYS:
        if os.environ.get(name, "").strip() == "":
            os.environ.pop(name, None)
    kwargs = {"region_name": creds["region"]}
    if creds.get("access_key_id"):
        kwargs["aws_access_key_id"] = creds["access_key_id"]
        kwargs["aws_secret_access_key"] = creds["secret_access_key"]
        kwargs["aws_session_token"] = creds.get("session_token")
    key = (service, *sorted((name, str(value)) for name, value in kwargs.items()))
    with _clients_lock:
        if key not in _clients:
            if len(_clients) >= 16:
                _clients.clear()
            # Clients are safe to share between threads; sessions are not, so each gets its own.
            config = Config(max_pool_connections=64, retries={"max_attempts": 8, "mode": "adaptive"})
            _clients[key] = boto3.session.Session().client(service, config=config, **kwargs)
        return _clients[key]


COHERE_BATCH = 96  # the most texts one Cohere Embed request accepts
SINGLE_BATCH = 8  # Titan takes one text per request, so small batches keep every thread busy


def batch_size(model: str) -> int:
    """How many chunks to hand to embed_texts at a time."""
    return COHERE_BATCH if "cohere.embed" in model else SINGLE_BATCH


def embedding_request(model: str, text: str | list[str], dim: int, purpose: str) -> dict:
    """Body for the Bedrock embedding models this app knows how to call.

    Cohere Embed takes a list of texts in one request; Titan takes a single text.
    """
    if "cohere.embed" in model:
        input_type = "search_query" if purpose == "query" else "search_document"
        return {
            "texts": text if isinstance(text, list) else [text],
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
    step = COHERE_BATCH if "cohere.embed" in model else 1
    for start in range(0, len(texts), step):
        group = texts[start : start + step]
        body = embedding_request(model, group if step > 1 else group[0], dim, purpose)
        try:
            response = runtime.invoke_model(modelId=model, body=json.dumps(body))
        except (BotoCoreError, ClientError) as exc:
            raise BadRequest(f"Amazon Bedrock rejected the embedding request: {exc}{_profile_hint(exc, model)}") from exc
        rows = _read_vectors(json.loads(response["body"].read()))
        if len(rows) != len(group):
            raise BadRequest(f"{model} returned {len(rows)} embeddings for {len(group)} texts.")
        for vector in rows:
            if len(vector) != dim:
                raise BadRequest(f"{model} returned {len(vector)} dimensions, but {dim} are configured.")
        vectors.extend(rows)
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


def _read_vectors(payload: dict) -> list[list[float]]:
    if isinstance(payload.get("embedding"), list):
        return [[float(value) for value in payload["embedding"]]]
    embeddings = payload.get("embeddings")
    if isinstance(embeddings, dict):
        embeddings = embeddings.get("float")
    if isinstance(embeddings, list) and embeddings and isinstance(embeddings[0], list):
        return [[float(value) for value in row] for row in embeddings]
    raise BadRequest("Amazon Bedrock returned an embedding response this app could not read.")


def _read_vector(payload: dict) -> list[float]:
    return _read_vectors(payload)[0]
