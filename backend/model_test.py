"""Try a model setting with a small real request, before or after saving it."""

import time

from backend import embeddings, model_settings, models
from backend.errors import BadRequest

_QUERY = "What is the capital of France?"
_DOCUMENTS = ["Bananas are yellow and grow in bunches.", "Paris is the capital of France."]


def run(role: str, draft: dict) -> dict:
    """Test one role using the saved settings with `draft` applied on top.

    A failed provider call is a normal result (ok: false), not an HTTP error.
    Only settings that could never be valid raise BadRequest.
    """
    if role not in model_settings.ROLES:
        raise BadRequest(f"role must be one of: {', '.join(model_settings.ROLES)}.")
    cfg = model_settings.apply_update(model_settings.load(), draft or {})
    section = cfg[role]
    result = {"role": role, "provider": section["provider"], "model": section["model"]}

    if section["provider"] in ("none", "local"):
        result.update(ok=True, skipped=True, latency_ms=0, detail=_skipped(role, section["provider"]))
        return result

    started = time.monotonic()
    try:
        detail = _CHECKS[role](cfg)
        ok = True
    except Exception as exc:  # providers raise BadRequest; SDKs can raise anything else
        ok = False
        message = str(exc) if isinstance(exc, BadRequest) else f"{type(exc).__name__}: {exc}"
        detail = model_settings.redact(message, cfg)
    result.update(ok=ok, skipped=False, detail=detail, latency_ms=int((time.monotonic() - started) * 1000))
    return result


def _skipped(role: str, provider: str) -> str:
    if provider == "local":
        return "The local embedder runs inside this app and needs no credentials."
    return {"llm": "No LLM: answers are the retrieved passages.", "rerank": "No re-ranking."}[role]


def _check_llm(cfg: dict) -> str:
    reply = models.generate(cfg, "Reply with the single word: ready")
    return f"The model replied: {reply[:120]}"


def _check_embedding(cfg: dict) -> str:
    section = cfg["embedding"]
    vector = embeddings.embed(
        section["provider"], section["model"], int(section["dimensions"]), ["RAGFlow connection test"], "query", cfg
    )[0]
    return f"Returned a {len(vector)}-dimension vector."


def _check_rerank(cfg: dict) -> str:
    ranked = models.rerank_scores(cfg, _QUERY, _DOCUMENTS, len(_DOCUMENTS))
    best, score = ranked[0]
    verdict = "ranked the relevant passage first" if best == 1 else "ranked the irrelevant passage first, which looks wrong"
    return f"The model {verdict} (score {score:.3f})."


_CHECKS = {"llm": _check_llm, "embedding": _check_embedding, "rerank": _check_rerank}
