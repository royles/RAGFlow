# RAGFlow

Knowledge bases for Cloudera AI. Create a base, choose how documents are split, and search them with Qdrant. Ask from the React UI, the REST API, or MCP. The same retrieval function answers all three.

Amazon Bedrock is optional. Without it, the app embeds text locally and returns the matching passages. With it, a knowledge base can use a Bedrock embedding model and a Bedrock chat model.

## What you can configure

Per knowledge base:

- Chunk size and overlap, in characters
- Nearest neighbour algorithm: HNSW or exact. On a Qdrant server, HNSW is the approximate index and exact scans every vector. The embedded index always scans every vector; the choice is still saved and used when `QDRANT_URL` points at a server.
- Distance: cosine, Euclidean, or dot product
- Embedding model: local hashing, Titan Embeddings, Cohere Embed, or another Bedrock embedding model id
- Chat model: retrieval only, Claude Haiku 4.5, Nova Lite, or another Bedrock chat model id
- AWS region for that base

Distance and the embedding model are fixed at creation because they define the Qdrant collection. Chunking, the nearest-neighbour algorithm, and the chat model can be changed later. Chunking applies to the next upload.

## Run on Cloudera AI

Import the repository as a project (prototype tasks are in `.project-metadata.yaml`). The install job runs `scripts/install.py` in the project runtime. It does not create a virtualenv. The application script is `start.py`. It listens on `127.0.0.1:$CDSW_APP_PORT`.

Open the application from the project Applications tab. The React UI is the committed `frontend/dist`, so the runtime does not need Node.

To rebuild the UI on a machine that has Node:

```bash
cd frontend
npm install
npm run build
```

## Run locally

```bash
python3 -m pip install -r requirements.txt
python3 start.py
```

Open `http://127.0.0.1:8000`. Load the sample handbook from a knowledge base and ask “What is the Dublin warehouse code?”

Set `CDSW_APP_PORT` or `APP_PORT` to choose the port. Vectors are stored in `data/` unless `QDRANT_URL` points at a Qdrant server.

## Bedrock

Leave the AWS key variables empty when the runtime role can call Bedrock. The region is the one where the model is enabled. Chat uses the Bedrock Converse API, so Claude, Nova, and Llama model ids work. Embeddings support Titan Embeddings v1 and v2, and Cohere Embed.

## MCP

Agents call Streamable HTTP at `/mcp` on the same port as the UI.

| Tool | What it returns |
| --- | --- |
| `list_knowledge_bases` | Names, chunking, nearest-neighbour settings, and model ids |
| `search_knowledge_base` | Passages closest to the question |
| `ask_knowledge_base` | A grounded answer plus those passages |

`ask_knowledge_base` uses the knowledge base’s Bedrock chat model when one is set. Otherwise it returns the passages. `knowledge_base` is an id, or a name when that name is unique.

On Cloudera AI the process binds to loopback and the platform proxies the public hostname, so `/mcp` accepts that hostname. Set `RAGFLOW_MCP_ALLOWED_HOSTS` (comma-separated) to restrict the Host header. Loopback and `CDSW_MASTER_IP` stay allowed.

```json
{
  "mcpServers": {
    "ragflow": {
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

Use the application URL instead of loopback for a client outside the runtime.

## Tests

```bash
python3 -m unittest discover -s tests -v
```
