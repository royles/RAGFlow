"""Knowledge base operations shared by the REST API and the MCP tools."""

import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from backend import db, vectors
from backend.bedrock import complete
from backend.catalog import CHAT_PRESETS, EMBED_PRESETS
from backend.chunking import chunk_text
from backend.embeddings import embed
from backend.errors import BadRequest, NotFound
from backend.extract import extract_text
from backend.settings import sample_path

log = logging.getLogger("ragflow")

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_CHUNKS = 400
_DISTANCES = {"cosine", "euclid", "dot"}
_ALGORITHMS = {"hnsw", "exact"}


def list_knowledge_bases() -> list[dict]:
    return [_public(row) for row in db.list_kbs()]


def get_knowledge_base(kb_id: str) -> dict:
    return _public(_require(kb_id))


def create_knowledge_base(payload: dict) -> dict:
    name = _name(payload.get("name", ""))
    description = str(payload.get("description") or "").strip()
    if len(description) > 500:
        raise BadRequest("Description must be 500 characters or fewer.")
    chunk_size, chunk_overlap = _chunking(payload.get("chunk_size", 800), payload.get("chunk_overlap", 120))
    distance = str(payload.get("distance") or "cosine")
    algorithm = str(payload.get("nn_algorithm") or "hnsw")
    _check_index(distance, algorithm)
    provider, model, dim = _embed_choice(
        str(payload.get("embed_preset") or "local"),
        str(payload.get("embed_model") or ""),
        payload.get("embed_dim"),
    )
    chat_provider, chat_model = _chat_choice(
        str(payload.get("chat_preset") or "none"),
        str(payload.get("chat_model") or ""),
    )
    region = _region(str(payload.get("bedrock_region") or ""))
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
                "embed_provider": provider,
                "embed_model": model,
                "embed_dim": dim,
                "chat_provider": chat_provider,
                "chat_model": chat_model,
                "bedrock_region": region,
                "collection": collection,
                "created_at": _now(),
            }
        )
    except Exception:
        vectors.drop_collection(collection)
        raise
    created = db.get_kb(kb_id)
    if created is None:
        raise NotFound("The knowledge base was not saved.")
    return _public(created)


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
        fields["chunk_size"], fields["chunk_overlap"] = _chunking(size, overlap)
    if "nn_algorithm" in payload:
        algorithm = str(payload.get("nn_algorithm") or "")
        _check_index(current["distance"], algorithm)
        fields["nn_algorithm"] = algorithm
    if "chat_preset" in payload or "chat_model" in payload:
        provider, model = _chat_choice(
            str(payload.get("chat_preset") or "custom"),
            str(payload.get("chat_model") or ""),
        )
        fields["chat_provider"] = provider
        fields["chat_model"] = model
    if "bedrock_region" in payload:
        fields["bedrock_region"] = _region(str(payload.get("bedrock_region") or ""))
    db.update_kb(kb_id, fields)
    updated = db.get_kb(kb_id)
    if updated is None:
        raise NotFound("Knowledge base not found.")
    return _public(updated)


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
    if len(pieces) > MAX_CHUNKS:
        raise BadRequest(f"That file produced {len(pieces)} chunks. The limit is {MAX_CHUNKS}.")
    vectors_batch = embed(
        current["embed_provider"],
        current["embed_model"],
        current["embed_dim"],
        current["bedrock_region"],
        pieces,
        "document",
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
    hits = _retrieve(current, question, top_k)
    return {"knowledge_base": _brief(current), "citations": hits}


def ask(name_or_id: str, question: str, top_k: int = 5) -> dict:
    """Answer from retrieved passages.

    With a Bedrock chat model, the model must use those passages. Without one,
    the answer is the passages themselves, so the result is still grounded.
    """
    current = resolve(name_or_id)
    question = question.strip()
    if not question:
        raise BadRequest("Enter a question.")
    limit = _top_k(top_k)
    if current["document_count"] == 0:
        return {
            "knowledge_base": _brief(current),
            "answer": "This knowledge base has no documents yet.",
            "mode": "empty",
            "citations": [],
        }
    hits = _retrieve(current, question, limit)
    if not hits:
        return {
            "knowledge_base": _brief(current),
            "answer": "No matching passages were found.",
            "mode": "empty",
            "citations": [],
        }
    if current["chat_provider"] == "bedrock" and current["chat_model"]:
        answer = complete(current["chat_model"], current["bedrock_region"], _prompt(question, hits))
        mode = "bedrock"
    else:
        answer = _passages(hits)
        mode = "retrieval"
    return {
        "knowledge_base": _brief(current),
        "answer": answer,
        "mode": mode,
        "citations": hits,
    }


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


def _retrieve(current: dict, question: str, top_k: int) -> list[dict]:
    question = question.strip()
    if not question:
        raise BadRequest("Enter a question.")
    limit = _top_k(top_k)
    if current["document_count"] == 0:
        return []
    query = embed(
        current["embed_provider"],
        current["embed_model"],
        current["embed_dim"],
        current["bedrock_region"],
        [question],
        "query",
    )[0]
    return vectors.search(current["collection"], query, limit, current["nn_algorithm"])


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


def _public(row: dict) -> dict:
    data = dict(row)
    data["embed_preset"] = _matching_preset(EMBED_PRESETS, row["embed_provider"], row["embed_model"])
    data["chat_preset"] = _matching_preset(CHAT_PRESETS, row["chat_provider"], row["chat_model"])
    return data


def _brief(row: dict) -> dict:
    return {"id": row["id"], "name": row["name"]}


def _matching_preset(presets: list[dict], provider: str, model: str) -> str:
    for item in presets:
        if item["id"] == "custom":
            continue
        if item["provider"] == provider and item["model"] == model:
            return item["id"]
    return "custom"


def _name(value: str) -> str:
    name = str(value).strip()
    if not name:
        raise BadRequest("A knowledge base needs a name.")
    if len(name) > 80:
        raise BadRequest("Name must be 80 characters or fewer.")
    return name


def _chunking(size: object, overlap: object) -> tuple[int, int]:
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


def _embed_choice(preset: str, model: str, dim: object) -> tuple[str, str, int]:
    found = next((item for item in EMBED_PRESETS if item["id"] == preset), None)
    if found is None:
        raise BadRequest("Unknown embedding model choice.")
    if found["id"] != "custom":
        return found["provider"], found["model"], int(found["dim"])
    model = model.strip()
    if not model:
        raise BadRequest("Enter a Bedrock embedding model id.")
    if dim is None or dim == "":
        raise BadRequest("Enter the embedding dimensions for a custom model.")
    dimensions = _int(dim, "embed_dim")
    if dimensions < 8 or dimensions > 4096:
        raise BadRequest("Embedding dimensions must be between 8 and 4096.")
    return "bedrock", model, dimensions


def _chat_choice(preset: str, model: str) -> tuple[str, str]:
    found = next((item for item in CHAT_PRESETS if item["id"] == preset), None)
    if found is None:
        raise BadRequest("Unknown chat model choice.")
    if found["id"] != "custom":
        return found["provider"], found["model"]
    model = model.strip()
    if not model:
        raise BadRequest("Enter a Bedrock chat model id.")
    return "bedrock", model


def _region(value: str) -> str:
    region = value.strip()
    if region:
        return region
    return os.environ.get("AWS_REGION", "").strip() or os.environ.get("AWS_DEFAULT_REGION", "").strip() or "us-east-1"


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
