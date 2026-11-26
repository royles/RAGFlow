"""Read uploaded files into plain text."""

import io
from pathlib import Path

from backend.errors import BadRequest

SUPPORTED = {".txt", ".md", ".markdown", ".csv", ".pdf", ".docx"}


def extract_text(filename: str, data: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED:
        names = ", ".join(sorted(SUPPORTED))
        raise BadRequest(f"Unsupported file type {suffix or '(none)'}. Use one of: {names}.")
    if suffix == ".pdf":
        return _pdf(data)
    if suffix == ".docx":
        return _docx(data)
    return data.decode("utf-8-sig", errors="replace").strip()


def _pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    parts = [(page.extract_text() or "").strip() for page in reader.pages]
    return "\n\n".join(part for part in parts if part).strip()


def _docx(data: bytes) -> str:
    from docx import Document

    document = Document(io.BytesIO(data))
    lines = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                lines.append(" | ".join(cells))
    return "\n".join(lines).strip()
