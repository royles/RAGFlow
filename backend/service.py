"""Knowledge base operations shared by the REST API and the MCP tools."""

import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from backend import audit, db, judge, model_settings, models, vectors
from backend.chunking import chunk_text
from backend.embeddings import embed
from backend.errors import BadRequest, NotFound
from backend.extract import extract_text
from backend.settings import max_chunks, sample_path

log = logging.getLogger("ragflow")

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_RERANK_POOL = 100
_DISTANCES = {"cosine", "euclid", "dot"}
_ALGORITHMS = {"hnsw", "exact"}


def list_knowledge_bases() -> list[dict]:
    return db.list_kbs()


def get_knowledge_base(kb_id: str) -> dict:
    return _require(kb_id)


def create_knowledge_base(payload: dict) -> dict:
    """Create a base. It keeps the embedding model configured right now,
    because the model sets the size of every vector in its Qdrant collection."""
    name = _name(payload.get("name", ""))
    description = str(payload.get("description") or "").strip()
    if len(description) > 500:
        raise BadRequest("Description must be 500 characters or fewer.")
    chunk_size, chunk_overlap = check_chunking(payload.get("chunk_size", 800), payload.get("chunk_overlap", 120))
    distance = str(payload.get("distance") or "cosine")
    algorithm = str(payload.get("nn_algorithm") or "hnsw")
    _check_index(distance, algorithm)
    embedding = model_settings.load()["embedding"]
    dim = int(embedding["dimensions"])
    kb_id = str(uuid.uuid4())
    collection = "kb" + kb_id.replace("-", "")
    vectors.create_collection(collection, dim, distance)
    try:
        db.insert_kb(
            {
                "id": kb_id,
                "name": name,
                "description": description,
                "chunk_size": chunk_size,
                "chunk_overlap": chunk_overlap,
                "nn_algorithm": algorithm,
                "distance": distance,
                "embed_provider": embedding["provider"],
                "embed_model": embedding["model"],
                "embed_dim": dim,
                "collection": collection,
                "created_at": _now(),
            }
        )
    except Exception:
        vectors.drop_collection(collection)
        raise
    return _require(kb_id)


def update_knowledge_base(kb_id: str, payload: dict) -> dict:
    current = _require(kb_id)
    fields: dict = {}
    if "name" in payload:
        fields["name"] = _name(payload.get("name", ""))
    if "description" in payload:
        description = str(payload.get("description") or "").strip()
        if len(description) > 500:
            raise BadRequest("Description must be 500 characters or fewer.")
        fields["description"] = description
    if "chunk_size" in payload or "chunk_overlap" in payload:
        size = payload.get("chunk_size", current["chunk_size"])
        overlap = payload.get("chunk_overlap", current["chunk_overlap"])
        fields["chunk_size"], fields["chunk_overlap"] = check_chunking(size, overlap)
    if "nn_algorithm" in payload:
        algorithm = str(payload.get("nn_algorithm") or "")
        _check_index(current["distance"], algorithm)
        fields["nn_algorithm"] = algorithm
    db.update_kb(kb_id, fields)
    return _require(kb_id)


def delete_knowledge_base(kb_id: str) -> None:
    current = _require(kb_id)
    db.delete_kb(kb_id)
    try:
        vectors.drop_collection(current["collection"])
    except Exception:
        log.exception("Could not drop Qdrant collection %s", current["collection"])


def list_documents(kb_id: str) -> list[dict]:
    _require(kb_id)
    return db.list_documents(kb_id)


def add_document(kb_id: str, filename: str, data: bytes) -> dict:
    current = _require(kb_id)
    safe_name = Path(filename or "document.txt").name
    if not safe_name or safe_name in {".", ".."}:
        raise BadRequest("The upload needs a file name.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise BadRequest("Files must be 15 MB or smaller.")
    text = extract_text(safe_name, data)
    if not text:
        raise BadRequest("No text could be read from that file.")
    pieces = chunk_text(text, current["chunk_size"], current["chunk_overlap"])
    limit = max_chunks()
    if limit and len(pieces) > limit:
        raise BadRequest(
            f"That file produced {len(pieces)} chunks. The limit is {limit}. "
            "Use a larger chunk size, or raise RAGFLOW_MAX_CHUNKS (0 removes the limit)."
        )
    vectors_batch = embed(
        current["embed_provider"], current["embed_model"], current["embed_dim"], pieces, "document"
    )
    document_id = str(uuid.uuid4())
    points = []
    for index, (piece, vector) in enumerate(zip(pieces, vectors_batch)):
        points.append(
            {
                "id": str(uuid.uuid4()),
                "vector": vector,
                "payload": {
                    "document_id": document_id,
                    "filename": safe_name,
                    "chunk_index": index,
                    "text": piece,
                },
            }
        )
    vectors.upsert(current["collection"], points)
    row = {
        "id": document_id,
        "kb_id": kb_id,
        "filename": safe_name,
        "byte_size": len(data),
        "chunk_count": len(pieces),
        "created_at": _now(),
    }
    try:
        db.insert_document(row)
    except Exception:
        vectors.delete_document(current["collection"], document_id)
        raise
    return row


def add_sample(kb_id: str) -> dict:
    path = sample_path()
    if not path.is_file():
        raise BadRequest("The sample handbook is not in this project.")
    return add_document(kb_id, path.name, path.read_bytes())


def delete_document(kb_id: str, document_id: str) -> None:
    current = _require(kb_id)
    vectors.delete_document(current["collection"], document_id)
    if not db.delete_document(kb_id, document_id):
        raise NotFound("Document not found.")


def search(name_or_id: str, question: str, top_k: int = 5) -> dict:
    """Return the passages closest to the question, with no generated answer."""
    current = resolve(name_or_id)
    hits = _retrieve(current, question, top_k, model_settings.load())
    return {"knowledge_base": _brief(current), "citations": hits}


def ask(name_or_id: str, question: str, top_k: int = 5, source: str = "api") -> dict:
    """Answer from retrieved passages.

    With an LLM configured, the model must use those passages. Without one, the
    answer is the passages themselves, so the result is still grounded.
    """
    started = time.monotonic()
    current = resolve(name_or_id)
    question = question.strip()
    if not question:
        raise BadRequest("Enter a question.")
    cfg = model_settings.load()
    brief = _brief(current)
    if current["document_count"] == 0:
        return {"knowledge_base": brief, "answer": "This knowledge base has no documents yet.", "mode": "empty", "citations": []}
    hits = _retrieve(current, question, top_k, cfg)
    if not hits:
        return {"knowledge_base": brief, "answer": "No matching passages were found.", "mode": "empty", "citations": []}
    llm, rerank = cfg["llm"], cfg["rerank"]
    if llm["provider"] != "none":
        answer = models.generate(cfg, _prompt(question, hits))
        mode = "llm"
    else:
        answer = _passages(hits)
        mode = "retrieval"
    result = {
        "knowledge_base": brief,
        "answer": answer,
        "mode": mode,
        "llm": {"provider": llm["provider"], "model": llm["model"]} if mode == "llm" else None,
        "rerank": {"provider": rerank["provider"], "model": rerank["model"]} if rerank["provider"] != "none" else None,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "citations": hits,
    }
    result["interaction_id"] = _audit(result, current, question, cfg, _top_k(top_k), source)
    return result


def _audit(result: dict, current: dict, question: str, cfg: dict, top_k: int, source: str) -> str | None:
    """Log the answer for review. A logging failure must never cost the user their answer."""
    try:
        interaction_id = audit.record(
            {
                "source": source if source in ("ui", "mcp", "api") else "api",
                "kb_id": current["id"],
                "kb_name": current["name"],
                "question": question,
                "answer": result["answer"],
                "mode": result["mode"],
                "top_k": top_k,
                "llm_provider": cfg["llm"]["provider"] if result["mode"] == "llm" else "",
                "llm_model": cfg["llm"]["model"] if result["mode"] == "llm" else "",
                "rerank_provider": cfg["rerank"]["provider"],
                "rerank_model": cfg["rerank"]["model"],
                "embed_provider": cfg["embedding"]["provider"],
                "embed_model": cfg["embedding"]["model"],
                "chunk_size": current["chunk_size"],
                "chunk_overlap": current["chunk_overlap"],
                "nn_algorithm": current["nn_algorithm"],
                "elapsed_ms": result["elapsed_ms"],
                "citations": result["citations"],
            }
        )
    except Exception:
        log.exception("Could not write the audit record")
        return None
    if result["mode"] == "llm" and judge.enabled(cfg):
        judge.schedule(interaction_id)
    return interaction_id


def resolve(name_or_id: str) -> dict:
    """Find a knowledge base by id, or by exact name when that name is unique."""
    row = db.get_kb(name_or_id)
    if row:
        return row
    matches = db.find_by_name(name_or_id)
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise NotFound(f"No knowledge base named {name_or_id!r}.")
    ids = ", ".join(item["id"] for item in matches)
    raise BadRequest(f"More than one knowledge base is named {name_or_id!r}. Use an id: {ids}")


def _retrieve(current: dict, question: str, top_k: int, cfg: dict) -> list[dict]:
    """Vector search, then re-rank when a re-rank model is configured.

    The re-ranker needs more candidates than the final count to be useful, so
    it reorders a larger pool and the best `top_k` are kept.
    """
    question = question.strip()
    if not question:
        raise BadRequest("Enter a question.")
    limit = _top_k(top_k)
    if current["document_count"] == 0:
        return []
    query = embed(
        current["embed_provider"], current["embed_model"], current["embed_dim"], [question], "query", cfg
    )[0]
    reranking = cfg["rerank"]["provider"] != "none"
    pool = min(max(limit * 4, 20), MAX_RERANK_POOL) if reranking else limit
    hits = vectors.search(current["collection"], query, pool, current["nn_algorithm"])
    return models.rerank_hits(cfg, question, hits, limit)


def _prompt(question: str, citations: list[dict]) -> str:
    lines = [
        "Answer the question using only the passages below.",
        "Cite a passage as [1] or [2] when you use it.",
        "If the passages do not contain the answer, say so.",
        "",
    ]
    for index, item in enumerate(citations, start=1):
        lines.append(f"[{index}] {item['filename']} (chunk {item['chunk_index']})")
        lines.append(item["text"])
        lines.append("")
    lines.append(f"Question: {question}")
    return "\n".join(lines)


def _passages(citations: list[dict]) -> str:
    lines = ["Retrieved passages:"]
    for index, item in enumerate(citations, start=1):
        lines.append(f"[{index}] {item['filename']}: {item['text']}")
    return "\n\n".join(lines)


def _require(kb_id: str) -> dict:
    row = db.get_kb(kb_id)
    if row is None:
        raise NotFound("Knowledge base not found.")
    return row


def _brief(row: dict) -> dict:
    return {"id": row["id"], "name": row["name"]}


def _name(value: str) -> str:
    name = str(value).strip()
    if not name:
        raise BadRequest("A knowledge base needs a name.")
    if len(name) > 80:
        raise BadRequest("Name must be 80 characters or fewer.")
    return name


def check_chunking(size: object, overlap: object) -> tuple[int, int]:
    size_n = _int(size, "chunk_size")
    overlap_n = _int(overlap, "chunk_overlap")
    if size_n < 100 or size_n > 8000:
        raise BadRequest("chunk_size must be between 100 and 8000 characters.")
    if overlap_n < 0 or overlap_n >= size_n:
        raise BadRequest("chunk_overlap must be zero or more and smaller than chunk_size.")
    return size_n, overlap_n


def _check_index(distance: str, algorithm: str) -> None:
    if distance not in _DISTANCES:
        raise BadRequest("distance must be cosine, euclid, or dot.")
    if algorithm not in _ALGORITHMS:
        raise BadRequest("nn_algorithm must be hnsw or exact.")


def _top_k(value: object) -> int:
    number = _int(value, "top_k")
    if number < 1 or number > 20:
        raise BadRequest("top_k must be between 1 and 20.")
    return number


def _int(value: object, label: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise BadRequest(f"{label} must be a whole number.") from exc


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
