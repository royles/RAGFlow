"""Call the configured LLM and re-rank model, whichever provider hosts them."""

from backend import bedrock, model_settings, openai_compat
from backend.errors import BadRequest


def resolve(cfg: dict, role: str) -> str:
    """The judge can be set to reuse the LLM; every other role is itself."""
    return "llm" if role == "judge" and cfg["judge"]["provider"] == "same" else role


def generate(cfg: dict, prompt: str, role: str = "llm") -> str:
    """Run a prompt on the LLM, or on the judge model when role is "judge"."""
    role = resolve(cfg, role)
    section = cfg[role]
    if section["provider"] == "bedrock":
        return bedrock.converse(model_settings.bedrock_credentials(cfg, role), section["model"], prompt)
    if section["provider"] == "openai":
        return openai_compat.chat(model_settings.openai_credentials(cfg, role), section["model"], prompt)
    raise BadRequest("No LLM is configured." if role == "llm" else "No judge model is configured.")


def rerank_scores(cfg: dict, query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
    """(document index, score) pairs, best first, from the configured re-rank model."""
    section = cfg["rerank"]
    if section["provider"] == "bedrock":
        ranked = bedrock.rerank(model_settings.bedrock_credentials(cfg, "rerank"), section["model"], query, documents, top_n)
    elif section["provider"] == "openai":
        ranked = openai_compat.rerank(model_settings.openai_credentials(cfg, "rerank"), section["model"], query, documents, top_n)
    else:
        raise BadRequest("No re-rank model is configured.")
    valid = [(index, score) for index, score in ranked if 0 <= index < len(documents)]
    return sorted(valid, key=lambda pair: pair[1], reverse=True)


def rerank_hits(cfg: dict, query: str, hits: list[dict], top_k: int) -> list[dict]:
    """Reorder retrieved passages with the re-rank model, if one is set."""
    if cfg["rerank"]["provider"] == "none" or not hits:
        return hits[:top_k]
    ranked = rerank_scores(cfg, query, [hit["text"] for hit in hits], top_k)
    reordered = []
    for index, score in ranked[:top_k]:
        hit = dict(hits[index])
        hit["rerank_score"] = round(score, 4)
        reordered.append(hit)
    return reordered
