"""Split document text into overlapping character windows."""


def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    """Return non-empty windows of `size` characters, stepping by size - overlap."""
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not cleaned:
        return []
    if overlap < 0 or overlap >= size:
        raise ValueError("chunk_overlap must be zero or more and smaller than chunk_size")

    chunks: list[str] = []
    start = 0
    while start < len(cleaned):
        end = min(start + size, len(cleaned))
        piece = cleaned[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(cleaned):
            break
        start = end - overlap
    return chunks
