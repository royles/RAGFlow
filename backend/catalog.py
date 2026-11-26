"""Model choices shown in the UI and accepted by the API.

Embedding and chat models are selected per knowledge base. The local embedder
needs no AWS account. Bedrock models use the runtime's AWS credential chain.
"""

EMBED_PRESETS = [
    {
        "id": "local",
        "label": "Local hashing embedder",
        "provider": "local",
        "model": "local-hashing",
        "dim": 384,
    },
    {
        "id": "titan-v2",
        "label": "Amazon Titan Embeddings v2",
        "provider": "bedrock",
        "model": "amazon.titan-embed-text-v2:0",
        "dim": 1024,
    },
    {
        "id": "titan-v1",
        "label": "Amazon Titan Embeddings v1",
        "provider": "bedrock",
        "model": "amazon.titan-embed-text-v1",
        "dim": 1536,
    },
    {
        "id": "cohere-en",
        "label": "Cohere Embed English v3",
        "provider": "bedrock",
        "model": "cohere.embed-english-v3",
        "dim": 1024,
    },
    {
        "id": "custom",
        "label": "Custom Bedrock embedding model",
        "provider": "bedrock",
        "model": "",
        "dim": None,
    },
]

CHAT_PRESETS = [
    {
        "id": "none",
        "label": "Retrieval only",
        "provider": "none",
        "model": "",
    },
    {
        "id": "haiku",
        "label": "Claude Haiku 4.5",
        "provider": "bedrock",
        "model": "anthropic.claude-haiku-4-5-20251001-v1:0",
    },
    {
        "id": "nova-lite",
        "label": "Amazon Nova Lite",
        "provider": "bedrock",
        "model": "amazon.nova-lite-v1:0",
    },
    {
        "id": "custom",
        "label": "Custom Bedrock chat model",
        "provider": "bedrock",
        "model": "",
    },
]

DISTANCES = [
    {"id": "cosine", "label": "Cosine"},
    {"id": "euclid", "label": "Euclidean"},
    {"id": "dot", "label": "Dot product"},
]

NN_ALGORITHMS = [
    {"id": "hnsw", "label": "HNSW"},
    {"id": "exact", "label": "Exact"},
]

LOCAL_DIM = 384


def public_catalog() -> dict:
    return {
        "embed_presets": EMBED_PRESETS,
        "chat_presets": CHAT_PRESETS,
        "distances": DISTANCES,
        "nn_algorithms": NN_ALGORITHMS,
        "defaults": {
            "chunk_size": 800,
            "chunk_overlap": 120,
            "distance": "cosine",
            "nn_algorithm": "hnsw",
            "embed_preset": "local",
            "chat_preset": "none",
            "bedrock_region": "us-east-1",
        },
    }
