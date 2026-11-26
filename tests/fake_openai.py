"""A tiny OpenAI-compatible server for tests and local UI checks.

Serves /v1/chat/completions, /v1/embeddings, and /v1/rerank. Embeddings reuse
the app's local hashing embedder (384 dimensions) so retrieval still works.
Re-ranking scores passages by words shared with the query.
"""

import re

from fastapi import FastAPI, Request

from backend.embeddings import local_embed

app = FastAPI()
calls: list[dict] = []


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    calls.append({"path": "chat", "auth": request.headers.get("authorization"), "body": body})
    prompt = body["messages"][0]["content"]
    text = "ready" if "single word" in prompt else "The fake model answers from passage [1]."
    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "created": 0,
        "model": body["model"],
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


@app.post("/v1/embeddings")
async def embeddings(request: Request):
    body = await request.json()
    calls.append({"path": "embeddings", "auth": request.headers.get("authorization"), "body": body})
    inputs = body["input"] if isinstance(body["input"], list) else [body["input"]]
    return {
        "object": "list",
        "model": body["model"],
        "data": [{"object": "embedding", "index": i, "embedding": local_embed(text)} for i, text in enumerate(inputs)],
        "usage": {"prompt_tokens": 1, "total_tokens": 1},
    }


@app.post("/v1/rerank")
async def rerank(request: Request):
    body = await request.json()
    calls.append({"path": "rerank", "auth": request.headers.get("authorization"), "body": body})
    query = _words(body["query"])
    scored = [
        {"index": index, "relevance_score": len(query & _words(document)) / (len(query) or 1)}
        for index, document in enumerate(body["documents"])
    ]
    scored.sort(key=lambda row: row["relevance_score"], reverse=True)
    return {"results": scored[: body.get("top_n", len(scored))]}
