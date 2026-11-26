"""Choices shown in the UI and accepted by the API."""

from backend.model_settings import ROLE_PROVIDERS

DISTANCES = [
    {"id": "cosine", "label": "Cosine"},
    {"id": "euclid", "label": "Euclidean"},
    {"id": "dot", "label": "Dot product"},
]

NN_ALGORITHMS = [
    {"id": "hnsw", "label": "HNSW"},
    {"id": "exact", "label": "Exact"},
]

PROVIDER_LABELS = {
    "none": "None",
    "local": "Local hashing (no account)",
    "bedrock": "AWS Bedrock",
    "openai": "OpenAI client API",
}

# Model ids offered as suggestions. Any id the provider accepts can be typed.
MODEL_SUGGESTIONS = {
    "llm": {
        "bedrock": [
            {"model": "us.anthropic.claude-haiku-4-5-20251001-v1:0", "label": "Claude Haiku 4.5 (US inference profile)"},
            {"model": "eu.anthropic.claude-haiku-4-5-20251001-v1:0", "label": "Claude Haiku 4.5 (EU inference profile)"},
            {"model": "global.anthropic.claude-haiku-4-5-20251001-v1:0", "label": "Claude Haiku 4.5 (global inference profile)"},
            {"model": "amazon.nova-lite-v1:0", "label": "Amazon Nova Lite"},
            {"model": "amazon.nova-pro-v1:0", "label": "Amazon Nova Pro"},
        ],
        "openai": [
            {"model": "gpt-4o-mini", "label": "GPT-4o mini"},
            {"model": "gpt-4.1-mini", "label": "GPT-4.1 mini"},
        ],
    },
    "embedding": {
        "bedrock": [
            {"model": "amazon.titan-embed-text-v2:0", "label": "Titan Embeddings v2", "dimensions": 1024},
            {"model": "amazon.titan-embed-text-v1", "label": "Titan Embeddings v1", "dimensions": 1536},
            {"model": "cohere.embed-english-v3", "label": "Cohere Embed English v3", "dimensions": 1024},
            {"model": "cohere.embed-multilingual-v3", "label": "Cohere Embed Multilingual v3", "dimensions": 1024},
        ],
        "openai": [
            {"model": "text-embedding-3-small", "label": "text-embedding-3-small", "dimensions": 1536},
            {"model": "text-embedding-3-large", "label": "text-embedding-3-large", "dimensions": 3072},
            {"model": "text-embedding-ada-002", "label": "text-embedding-ada-002", "dimensions": 1536},
        ],
    },
    "rerank": {
        "bedrock": [
            {"model": "amazon.rerank-v1:0", "label": "Amazon Rerank 1.0"},
            {"model": "cohere.rerank-v3-5:0", "label": "Cohere Rerank 3.5"},
        ],
        "openai": [
            {"model": "rerank-english-v3.0", "label": "Cohere rerank-english-v3.0"},
            {"model": "jina-reranker-v2-base-multilingual", "label": "Jina reranker v2"},
            {"model": "BAAI/bge-reranker-v2-m3", "label": "BGE reranker v2 m3 (vLLM)"},
        ],
    },
}


def public_catalog() -> dict:
    return {
        "distances": DISTANCES,
        "nn_algorithms": NN_ALGORITHMS,
        "providers": {
            role: [{"id": name, "label": PROVIDER_LABELS[name]} for name in names]
            for role, names in ROLE_PROVIDERS.items()
        },
        "model_suggestions": MODEL_SUGGESTIONS,
        "defaults": {
            "chunk_size": 800,
            "chunk_overlap": 120,
            "distance": "cosine",
            "nn_algorithm": "hnsw",
        },
    }
