"""LLM-judged quality scores for answers.

Faithfulness: the judge splits the answer into factual claims and marks each as
supported or not by the retrieved passages. The score is the supported share,
so the unsupported claims can be read, not just counted.

Answer relevance: the judge rates how directly the answer addresses the
question, 1 to 5, stored as 0 to 1.
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from backend import audit, model_settings, models
from backend.errors import BadRequest

log = logging.getLogger("ragflow")

MAX_PASSAGE_CHARS = 12000
MAX_CLAIMS = 30
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="judge")

_FAITHFULNESS = """You are a strict fact checker for a retrieval-augmented assistant.

Passages (numbered):
{passages}

Answer to check:
{answer}

Split the answer into its individual factual claims. For each claim decide whether the passages above fully
support it. Use only the passages, never your own knowledge. If the answer only says the passages do not contain
the information, it has no claims.

Reply with JSON only, in this shape:
{{"claims": [{{"claim": "short claim text", "supported": true, "source": 1}}]}}
Use "source" for the passage number that supports the claim, or null when unsupported."""

_RELEVANCE = """You are grading an answer from a retrieval-augmented assistant.

Question:
{question}

Answer:
{answer}

Rate how directly the answer addresses the question, from 1 to 5:
1 = unrelated, 2 = mostly off-topic, 3 = partly addresses it, 4 = addresses it with minor gaps, 5 = fully and directly.
Judge relevance only, not whether the facts are correct. An answer that clearly says the information is not
available is relevant to a question the sources cannot answer.

Reply with JSON only, in this shape:
{{"score": 4, "reason": "one short sentence"}}"""


def enabled(cfg: dict) -> bool:
    return cfg["judge"]["provider"] != "none"


def judge_model(cfg: dict) -> str:
    return cfg[models.resolve(cfg, "judge")]["model"]


def score(cfg: dict, question: str, answer: str, passages: list[str]) -> dict:
    """Score one answer. Raises BadRequest when no judge is configured.

    A failure in one check does not discard the other: whatever worked is
    returned, and judge_error says what did not.
    """
    if not enabled(cfg):
        raise BadRequest("No judge model is configured. Set one on the Models page.")
    result: dict = {"judge_model": judge_model(cfg), "judge_error": ""}
    errors = []
    try:
        result.update(_faithfulness(cfg, answer, passages))
    except Exception as exc:  # providers raise BadRequest; SDKs can raise anything else
        errors.append(f"Faithfulness: {_message(exc, cfg)}")
    try:
        result.update(_relevance(cfg, question, answer))
    except Exception as exc:
        errors.append(f"Relevance: {_message(exc, cfg)}")
    result["judge_error"] = " ".join(errors)
    if len(errors) < 2:
        result["judged_at"] = _now()
    return result


def score_interaction(interaction_id: str) -> dict:
    """Score a stored answer from the audit log and save the result."""
    cfg = model_settings.load()
    item = audit.get(interaction_id)
    if item["mode"] != "llm":
        raise BadRequest("Only answers written by an LLM are scored.")
    result = score(cfg, item["question"], item["answer"], [c["text"] for c in item["citations"]])
    audit.save_scores(interaction_id, result)
    return audit.get(interaction_id)


def schedule(interaction_id: str) -> None:
    """Score in the background so the answer is never delayed by the judge."""
    _pool.submit(_run, interaction_id)


def check(cfg: dict) -> str:
    """A small known case for the Models page test: a supported answer should score fully."""
    result = score(
        cfg,
        "What colour is the sky?",
        "The sky is blue.",
        ["On a clear day the sky is blue because of the way air scatters sunlight."],
    )
    if result["judge_error"]:
        raise BadRequest(result["judge_error"])
    return (
        f"The judge returned valid scores (faithfulness {result['faithfulness']:.0%}, "
        f"relevance {result['relevance']:.0%})."
    )


def _run(interaction_id: str) -> None:
    try:
        score_interaction(interaction_id)
    except Exception:
        log.exception("Could not score answer %s", interaction_id)


def _faithfulness(cfg: dict, answer: str, passages: list[str]) -> dict:
    numbered, used = [], 0
    for index, text in enumerate(passages, start=1):
        text = text[: max(0, MAX_PASSAGE_CHARS - used)]
        used += len(text)
        numbered.append(f"[{index}] {text}")
    data = _json(models.generate(cfg, _FAITHFULNESS.format(passages="\n\n".join(numbered), answer=answer), "judge"))
    raw = data.get("claims")
    if not isinstance(raw, list):
        raise BadRequest("The judge did not return a claims list.")
    claims = []
    for item in raw[:MAX_CLAIMS]:
        if not isinstance(item, dict) or not str(item.get("claim") or "").strip():
            continue
        source = item.get("source")
        claims.append(
            {
                "claim": str(item["claim"]).strip()[:400],
                "supported": item.get("supported") is True,
                "source": source if isinstance(source, int) and 1 <= source <= len(passages) else None,
            }
        )
    supported = sum(1 for claim in claims if claim["supported"])
    return {
        "faithfulness": round(supported / len(claims), 3) if claims else 1.0,
        "faithfulness_claims": claims,
    }


def _relevance(cfg: dict, question: str, answer: str) -> dict:
    data = _json(models.generate(cfg, _RELEVANCE.format(question=question, answer=answer), "judge"))
    try:
        rating = float(data["score"])
    except (KeyError, TypeError, ValueError):
        raise BadRequest("The judge did not return a relevance score.") from None
    rating = min(5.0, max(1.0, rating))
    return {"relevance": round((rating - 1) / 4, 3), "relevance_reason": str(data.get("reason") or "").strip()[:400]}


def _json(text: str) -> dict:
    """The first JSON object in a model reply, which may wrap it in prose or a code fence."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise BadRequest("The judge did not reply with JSON.")
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        raise BadRequest("The judge's reply was not valid JSON.") from None
    if not isinstance(data, dict):
        raise BadRequest("The judge's reply was not a JSON object.")
    return data


def _message(exc: Exception, cfg: dict) -> str:
    text = str(exc) if isinstance(exc, BadRequest) else f"{type(exc).__name__}: {exc}"
    return model_settings.redact(text, cfg)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

