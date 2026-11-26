"""FastAPI application: JSON API, React UI, and the MCP endpoint."""

import csv
import io
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response

from backend import accesslog, audit, chunk_preview, judge, model_settings, model_test, service
from backend.catalog import public_catalog
from backend.errors import BadRequest, NotFound
from backend.mcp_server import mcp, transport_security
from backend.settings import ROOT, audit_retention_days

DIST = ROOT / "frontend" / "dist"

accesslog.install()

# The MCP route is registered on this app, not mounted under a sub-path.
# A Starlette Mount only matches /mcp/ (with a slash). MCP clients post to /mcp.
mcp_app = mcp.streamable_http_app(
    streamable_http_path="/mcp",
    json_response=True,
    stateless_http=True,
    transport_security=transport_security(),
    host="127.0.0.1",
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # A mounted Starlette app does not run its own lifespan. The session
    # manager has to be started here or every MCP request fails.
    _call_quietly(lambda: audit.purge(audit_retention_days()))
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="RAGFlow", lifespan=lifespan, redirect_slashes=False)
for route in mcp_app.routes:
    app.router.routes.insert(0, route)


def _call_quietly(func) -> None:
    try:
        func()
    except Exception:
        logging.getLogger("ragflow").exception("Startup task failed")


def _call(func):
    try:
        return func()
    except BadRequest as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except NotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "qdrant": "remote" if _remote_qdrant() else "embedded", "mcp_path": "/mcp"}


@app.get("/api/catalog")
def catalog() -> dict:
    return public_catalog()


@app.post("/api/chunk-preview/sample")
def chunk_sample(payload: dict) -> dict:
    return _call(lambda: chunk_preview.sample(bool(payload.get("use_llm"))))


@app.post("/api/chunk-preview")
def chunk_boundaries(payload: dict) -> dict:
    return _call(
        lambda: chunk_preview.preview(payload.get("text"), payload.get("chunk_size"), payload.get("chunk_overlap"))
    )


@app.get("/api/models")
def get_models() -> dict:
    return model_settings.public(model_settings.load())


@app.put("/api/models")
def put_models(payload: dict) -> dict:
    return _call(lambda: model_settings.save_update(payload))


@app.post("/api/models/test")
def test_model(payload: dict) -> dict:
    return _call(lambda: model_test.run(str(payload.get("role") or ""), payload.get("settings") or {}))


@app.get("/api/agent")
def agent() -> dict:
    return {
        "mcp_path": "/mcp",
        "transport": "streamable-http",
        "tools": [
            {
                "name": "list_knowledge_bases",
                "description": "List knowledge bases and their settings.",
            },
            {
                "name": "search_knowledge_base",
                "description": "Return the passages closest to a question.",
            },
            {
                "name": "ask_knowledge_base",
                "description": "Return a grounded answer and the passages it came from.",
            },
        ],
    }


@app.get("/api/knowledge-bases")
def list_knowledge_bases() -> dict:
    return {"knowledge_bases": service.list_knowledge_bases()}


@app.post("/api/knowledge-bases", status_code=201)
def create_knowledge_base(payload: dict) -> dict:
    return _call(lambda: service.create_knowledge_base(payload))


@app.get("/api/knowledge-bases/{kb_id}")
def get_knowledge_base(kb_id: str) -> dict:
    return _call(lambda: service.get_knowledge_base(kb_id))


@app.patch("/api/knowledge-bases/{kb_id}")
def update_knowledge_base(kb_id: str, payload: dict) -> dict:
    return _call(lambda: service.update_knowledge_base(kb_id, payload))


@app.delete("/api/knowledge-bases/{kb_id}", status_code=204)
def delete_knowledge_base(kb_id: str) -> None:
    _call(lambda: service.delete_knowledge_base(kb_id))


@app.get("/api/knowledge-bases/{kb_id}/documents")
def list_documents(kb_id: str) -> dict:
    return {"documents": _call(lambda: service.list_documents(kb_id))}


@app.post("/api/knowledge-bases/{kb_id}/documents", status_code=201)
async def upload_document(kb_id: str, file: UploadFile = File(...)) -> dict:
    data = await file.read()
    # Indexing embeds every chunk and can take a while. A worker thread keeps the
    # server responsive and lets several uploads index at the same time.
    return await run_in_threadpool(
        lambda: _call(lambda: service.add_document(kb_id, file.filename or "document.txt", data))
    )


@app.post("/api/knowledge-bases/{kb_id}/sample", status_code=201)
def load_sample(kb_id: str) -> dict:
    return _call(lambda: service.add_sample(kb_id))


@app.delete("/api/knowledge-bases/{kb_id}/documents/{document_id}", status_code=204)
def delete_document(kb_id: str, document_id: str) -> None:
    _call(lambda: service.delete_document(kb_id, document_id))


@app.post("/api/knowledge-bases/{kb_id}/search")
def search(kb_id: str, payload: dict) -> dict:
    return _call(lambda: service.search(kb_id, str(payload.get("question") or ""), payload.get("top_k", 5)))


@app.post("/api/knowledge-bases/{kb_id}/ask")
def ask(kb_id: str, payload: dict) -> dict:
    source = "ui" if payload.get("source") == "ui" else "api"
    return _call(lambda: service.ask(kb_id, str(payload.get("question") or ""), payload.get("top_k", 5), source))


@app.get("/api/interactions/{interaction_id}")
def interaction(interaction_id: str) -> dict:
    return _call(lambda: audit.get(interaction_id))


@app.post("/api/interactions/{interaction_id}/feedback")
def feedback(interaction_id: str, payload: dict) -> dict:
    return _call(
        lambda: audit.rate(
            interaction_id,
            str(payload.get("rating") or ""),
            str(payload.get("reason") or ""),
            str(payload.get("comment") or ""),
        )
    )


@app.post("/api/interactions/{interaction_id}/score")
def score(interaction_id: str) -> dict:
    return _call(lambda: judge.score_interaction(interaction_id))


@app.get("/api/audit")
def audit_list(kb_id: str = "", rating: str = "", scored: str = "", limit: int = 50, offset: int = 0) -> dict:
    return _call(lambda: audit.list_items(kb_id, rating, scored, limit, offset))


@app.get("/api/audit/export")
def audit_export(kb_id: str = "", format: str = "jsonl") -> Response:
    if format not in ("jsonl", "csv"):
        raise HTTPException(status_code=400, detail="format must be jsonl or csv.")
    items = list(audit.export(kb_id))
    if format == "jsonl":
        body = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in items)
        media = "application/x-ndjson"
    else:
        body, media = _audit_csv(items), "text/csv"
    return Response(body, media_type=media, headers={"Content-Disposition": f'attachment; filename="ragflow-audit.{format}"'})


_CSV_COLUMNS = (
    "id", "created_at", "source", "kb_id", "kb_name", "question", "answer", "mode", "rating", "rating_reason",
    "rating_comment", "faithfulness", "relevance", "relevance_reason", "judge_model", "judge_error", "llm_provider",
    "llm_model", "rerank_provider", "rerank_model", "embed_provider", "embed_model", "chunk_size", "chunk_overlap",
    "nn_algorithm", "top_k", "elapsed_ms",
)


def _audit_csv(items: list[dict]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(_CSV_COLUMNS)
    for item in items:
        # A leading = + - @ makes spreadsheets run the cell as a formula.
        writer.writerow(
            [f"'{value}" if isinstance(value, str) and value[:1] in ("=", "+", "-", "@") else value
             for value in (item.get(column) for column in _CSV_COLUMNS)]
        )
    return out.getvalue()


@app.get("/")
def index():
    page = DIST / "index.html"
    if page.is_file():
        return FileResponse(page)
    return {
        "message": "RAGFlow API is running. Build frontend/ to serve the UI.",
        "mcp_path": "/mcp",
    }


@app.get("/{asset_path:path}")
def frontend(asset_path: str):
    if asset_path.startswith("api") or asset_path == "mcp" or asset_path.startswith("mcp/"):
        raise HTTPException(status_code=404, detail="Not found.")
    candidate = _safe_file(asset_path)
    if candidate is not None:
        return FileResponse(candidate)
    if "." in Path(asset_path).name:
        raise HTTPException(status_code=404, detail="Not found.")
    page = DIST / "index.html"
    if page.is_file():
        return FileResponse(page)
    raise HTTPException(status_code=404, detail="Not found.")


def _safe_file(asset_path: str) -> Path | None:
    if not asset_path or ".." in asset_path.split("/"):
        return None
    candidate = (DIST / asset_path).resolve()
    if not str(candidate).startswith(str(DIST.resolve())):
        return None
    if candidate.is_file():
        return candidate
    return None


def _remote_qdrant() -> bool:
    return bool(os.environ.get("QDRANT_URL", "").strip())
