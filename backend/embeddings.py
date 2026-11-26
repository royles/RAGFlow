"""Turn text into vectors.

The local embedder is a hashed bag of words. It is deterministic and needs no
network, which makes the app usable before a provider is configured. It matches
shared words; it does not understand paraphrases. Bedrock and OpenAI embedding
models do.
"""

import hashlib
import math
import re

from backend import bedrock, model_settings, openai_compat
from backend.errors import BadRequest

LOCAL_DIM = model_settings.LOCAL_DIM


def embed(provider: str, model: str, dim: int, texts: list[str], purpose: str, cfg: dict | None = None) -> list[list[float]]:
    """Embed `texts`. `purpose` is "document" or "query"; some models treat them differently."""
    if provider == "local":
        return [local_embed(text) for text in texts]
    cfg = cfg or model_settings.load()
    if provider == "bedrock":
        return bedrock.embed_texts(model_settings.bedrock_credentials(cfg), model, texts, dim, purpose)
    if provider == "openai":
        return openai_compat.embed_texts(model_settings.openai_credentials(cfg, "embedding"), model, texts, dim, purpose)
    raise BadRequest(f"Unknown embedding provider {provider!r}.")


def local_embed(text: str) -> list[float]:
    vector = [0.0] * LOCAL_DIM
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    if not tokens:
        vector[0] = 1.0
        return vector
    for token in tokens:
        digest = hashlib.sha256(token.encode()).digest()
        index = int.from_bytes(digest[:4], "little") % LOCAL_DIM
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]
