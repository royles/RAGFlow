"""Read uploaded files into plain text, remembering where each part came from.

PDFs keep their page boundaries. Text, Markdown and CSV files keep their line
numbers (for a CSV, line 1 is the header row). Other files carry no position.
Offsets always refer to `Extracted.text`.
"""

import io
from dataclasses import dataclass, field
from pathlib import Path

from backend.chunking import normalize
from backend.errors import BadRequest

SUPPORTED = {".txt", ".md", ".markdown", ".csv", ".pdf", ".docx"}
LINE_BASED = {".txt", ".md", ".markdown", ".csv"}


@dataclass
class Extracted:
    text: str
    pages: list[tuple[int, int, int]] = field(default_factory=list)  # (start, end, page number)
    first_line: int | None = None  # line number of the first character of `text`

    def locate(self, start: int, end: int) -> dict:
        """Where the characters start..end sit: offsets, plus pages or lines when known."""
        where: dict = {"char_start": start, "char_end": end}
        covered = [page for first, last, page in self.pages if start < last and end > first]
        if covered:
            where["page_start"], where["page_end"] = min(covered), max(covered)
        if self.first_line is not None:
            last_char = max(start, end - 1)
            where["line_start"] = self.first_line + self.text.count("\n", 0, start)
            where["line_end"] = self.first_line + self.text.count("\n", 0, last_char)
        return where


def describe(where: dict) -> str:
    """A short label such as "p. 12", "pp. 12-13", "line 40" or "lines 40-52". Empty when unknown."""
    for first, last, one, many in (("page_start", "page_end", "p.", "pp."), ("line_start", "line_end", "line", "lines")):
        if where.get(first) is not None:
            low, high = where[first], where.get(last) or where[first]
            return f"{one} {low}" if low == high else f"{many} {low}\u2013{high}"
    return ""


def extract(filename: str, data: bytes) -> Extracted:
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED:
        names = ", ".join(sorted(SUPPORTED))
        raise BadRequest(f"Unsupported file type {suffix or '(none)'}. Use one of: {names}.")
    if suffix == ".pdf":
        return _pdf(data)
    if suffix == ".docx":
        return Extracted(normalize(_docx(data)))
    raw = data.decode("utf-8-sig", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    text = raw.strip()
    if suffix not in LINE_BASED:
        return Extracted(text)
    # Leading blank lines are trimmed from the text but still count as lines of the file.
    return Extracted(text, first_line=1 + raw[: len(raw) - len(raw.lstrip())].count("\n"))


def extract_text(filename: str, data: bytes) -> str:
    return extract(filename, data).text


def _pdf(data: bytes) -> Extracted:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    text, pages = "", []
    for number, page in enumerate(reader.pages, start=1):
        part = normalize(page.extract_text() or "")
        if not part:
            continue
        if text:
            text += "\n\n"
        pages.append((len(text), len(text) + len(part), number))
        text += part
    return Extracted(text, pages)


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
