"""Qdrant index. One collection per knowledge base.

The default client stores the index in the project data directory, so a
Cloudera AI Application does not need a separate Qdrant service. Set
QDRANT_URL to use a shared server instead.
"""

import os
import threading

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchValue,
    PointStruct,
    SearchParams,
    VectorParams,
)

from backend import advanced
from backend.settings import data_dir

_DISTANCES = {
    "cosine": Distance.COSINE,
    "euclid": Distance.EUCLID,
    "dot": Distance.DOT,
}

_lock = threading.RLock()
_client: QdrantClient | None = None
_client_key: tuple[str, str] | None = None


def create_collection(name: str, dim: int, distance: str) -> None:
    with _lock:
        qdrant = _qdrant()
        existing = {item.name for item in qdrant.get_collections().collections}
        if name in existing:
            return
        qdrant.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=dim, distance=_DISTANCES[distance]),
        )


def drop_collection(name: str) -> None:
    with _lock:
        qdrant = _qdrant()
        existing = {item.name for item in qdrant.get_collections().collections}
        if name in existing:
            qdrant.delete_collection(name)


def upsert(collection: str, points: list[dict]) -> None:
    structs = [
        PointStruct(id=point["id"], vector=point["vector"], payload=point["payload"])
        for point in points
    ]
    with _lock:
        qdrant = _qdrant()
        for start in range(0, len(structs), 64):
            qdrant.upsert(collection_name=collection, points=structs[start : start + 64], wait=True)


def search(collection: str, vector: list[float], limit: int, nn_algorithm: str) -> list[dict]:
    # Embedded Qdrant always scans every vector, so the exact flag is only
    # sent to a remote server, where HNSW is the real index.
    params = SearchParams(exact=True) if nn_algorithm == "exact" and _remote() else None
    with _lock:
        response = _qdrant().query_points(
            collection_name=collection,
            query=vector,
            limit=limit,
            with_payload=True,
            search_params=params,
        )
    hits = []
    for point in response.points:
        payload = point.payload or {}
        hits.append(
            {
                "document_id": payload.get("document_id", ""),
                "filename": payload.get("filename", ""),
                "chunk_index": payload.get("chunk_index", 0),
                "text": payload.get("text", ""),
                "score": round(float(point.score), 4),
            }
        )
    return hits


def delete_document(collection: str, document_id: str) -> None:
    selector = FilterSelector(
        filter=Filter(
            must=[FieldCondition(key="document_id", match=MatchValue(value=document_id))]
        )
    )
    with _lock:
        _qdrant().delete(collection_name=collection, points_selector=selector, wait=True)


def close_client() -> None:
    """Release the embedded index so tests can remove the data directory."""
    global _client, _client_key
    with _lock:
        if _client is not None:
            try:
                _client.close()
            except Exception:
                pass
        _client = None
        _client_key = None


def _remote() -> bool:
    return bool(advanced.get("qdrant_url"))


def _qdrant() -> QdrantClient:
    global _client, _client_key
    url = advanced.get("qdrant_url")
    api_key = advanced.get("qdrant_api_key") or None
    if url:
        key = ("url", url, api_key)
    else:
        path = data_dir() / "qdrant"
        path.mkdir(parents=True, exist_ok=True)
        key = ("path", str(path))
    if _client is not None and _client_key == key:
        return _client
    if _client is not None:
        try:
            _client.close()
        except Exception:
            pass
    if url:
        _client = QdrantClient(url=url, api_key=api_key, timeout=30)
    else:
        _client = QdrantClient(path=str(path), force_disable_check_same_thread=True)
    _client_key = key
    return _client
