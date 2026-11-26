"""Turn text into vectors.

The local embedder is a hashed bag of words. It is deterministic and needs no
network, which makes the app usable before Bedrock is configured. It matches
shared words; it does not understand paraphrases. Bedrock embedding models do.
"""

import hashlib
import math
import re

from backend.bedrock import embed_texts as bedrock_embed
from backend.catalog import LOCAL_DIM


def embed(provider: str, model: str, dim: int, region: str, texts: list[str], purpose: str) -> list[list[float]]:
    if provider == "local":
        return [local_embed(text) for text in texts]
    return bedrock_embed(model, texts, dim, region, purpose)


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
