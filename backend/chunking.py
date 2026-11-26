"""Split document text into overlapping character windows."""


def normalize(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def chunk_spans(text: str, size: int, overlap: int) -> list[tuple[int, int]]:
    """Return (start, end) offsets of each window in normalize(text).

    Windows are `size` characters and step by size - overlap, so neighbours share
    `overlap` characters. Indexing and the chunk preview both use this.
    """
    cleaned = normalize(text)
    if overlap < 0 or overlap >= size:
        raise ValueError("chunk_overlap must be zero or more and smaller than chunk_size")
    spans: list[tuple[int, int]] = []
    start = 0
    while cleaned and start < len(cleaned):
        end = min(start + size, len(cleaned))
        spans.append((start, end))
        if end >= len(cleaned):
            break
        start = end - overlap
    return spans


def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    """Return the non-empty windows of `text` as stripped strings."""
    cleaned = normalize(text)
    pieces = (cleaned[start:end].strip() for start, end in chunk_spans(cleaned, size, overlap))
    return [piece for piece in pieces if piece]
