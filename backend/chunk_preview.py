"""Sample text and chunk boundaries, so chunk settings can be seen before use."""

from backend import model_settings, models
from backend.chunking import chunk_spans, normalize
from backend.errors import BadRequest
from backend.service import check_chunking

MAX_PREVIEW_CHARS = 8000

_PROMPT = (
    "Write about 500 words of plain prose describing the full retrieval-augmented generation (RAG) process "
    "for an AI assistant: ingesting and cleaning documents, chunking with overlap, embedding, indexing in a "
    "vector database, embedding the question, nearest-neighbour retrieval, re-ranking, building the prompt, "
    "generating a grounded answer with citations, and keeping the index fresh and evaluated. "
    "Use several paragraphs. No title, no markdown, no lists."
)

_BUILTIN = """Retrieval-augmented generation, usually called RAG, lets a language model answer questions from your own documents instead of relying only on what it learned during training. The process has an indexing stage that runs ahead of time and a query stage that runs for every question.

Indexing starts with ingestion. Files such as PDFs, web pages, spreadsheets and wiki exports are collected, and their text is extracted and cleaned. Headers, footers and navigation menus are removed so that only meaningful content remains. Useful metadata, such as the file name, author, date and access rights, is kept alongside the text because it helps with filtering and with citations later.

Next the text is split into chunks. A model cannot compare an entire book with a question, so each document is cut into passages of a few hundred words. Chunks that are too large blur several topics together, and chunks that are too small lose the context needed to make sense. To avoid cutting an idea in half at a boundary, neighbouring chunks usually overlap, repeating the last sentences of one chunk at the start of the next.

Each chunk is then passed through an embedding model, which turns text into a vector of numbers. Passages with similar meaning end up close together in this space, even when they share few words. The vectors, the original text and the metadata are written to a vector database, where an index such as HNSW makes nearest-neighbour search fast across millions of entries.

When a user asks a question, the query stage begins. The question is embedded with the same model that embedded the chunks, because vectors from different models cannot be compared. The database returns the nearest chunks by cosine or dot-product similarity, typically the top twenty or so. This first pass is fast but approximate, so it is tuned to find everything that might be relevant.

A re-ranking model often follows. It reads the question together with each candidate passage and scores how well the passage actually answers it. This is slower than vector search, so it only runs on the shortlist, and the best handful of passages are kept. Re-ranking noticeably improves precision, especially for questions phrased very differently from the source text.

The selected passages are then placed into a prompt along with the question and clear instructions: answer only from the passages, cite them by number, and say so when the answer is not present. The language model writes a response grounded in that material, and the application shows the citations so the reader can check each claim against the source.

Finally, the system has to be maintained. New and changed documents are re-indexed, deleted ones are removed, and the whole pipeline is evaluated with test questions that measure whether the right passages are retrieved and whether answers stay faithful to them. Chunk size, overlap, the embedding model and the number of retrieved passages are all settings worth tuning against those results."""


def sample(use_llm: bool) -> dict:
    """Return text to chunk. Uses the configured LLM when asked and available."""
    cfg = model_settings.load()
    llm = cfg["llm"]
    if use_llm and llm["provider"] != "none":
        try:
            text = normalize(models.generate(cfg, _PROMPT))[:MAX_PREVIEW_CHARS]
            return {"text": text, "source": "llm", "model": llm["model"], "note": ""}
        except Exception as exc:  # provider failures should not hide the preview
            message = model_settings.redact(str(exc), cfg)
            return {**_builtin(), "note": f"The LLM could not write a sample ({message}). Showing built-in text."}
    note = "" if llm["provider"] != "none" else "No LLM is configured, so this is built-in text."
    return {**_builtin(), "note": note}


def preview(text: str, size: object, overlap: object) -> dict:
    """Chunk boundaries as offsets into the returned (normalized) text."""
    size_n, overlap_n = check_chunking(size, overlap)
    cleaned = normalize(str(text or ""))
    if not cleaned:
        raise BadRequest("Add some text to preview.")
    if len(cleaned) > MAX_PREVIEW_CHARS:
        raise BadRequest(f"Previews are limited to {MAX_PREVIEW_CHARS} characters.")
    chunks = [{"index": i, "start": s, "end": e} for i, (s, e) in enumerate(chunk_spans(cleaned, size_n, overlap_n))]
    return {"text": cleaned, "chunk_size": size_n, "chunk_overlap": overlap_n, "chunks": chunks}


def _builtin() -> dict:
    return {"text": _BUILTIN, "source": "builtin", "model": ""}
