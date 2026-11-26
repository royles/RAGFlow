# RAGFlow

Knowledge bases for Cloudera AI. Create a base, choose how documents are split, and search them with Qdrant. Ask from the React UI, the REST API, or MCP. The same retrieval function answers all three.

Models are optional. Without any, the app embeds text locally and returns the matching passages. The **Models** page connects AWS Bedrock or any OpenAI client API server and sets an LLM, an embedding model, and a re-rank model.

## What you can configure

Per knowledge base:

- Chunk size and overlap, in characters
- Nearest neighbour algorithm: HNSW or exact. On a Qdrant server, HNSW is the approximate index and exact scans every vector. The embedded index always scans every vector; the choice is still saved and used when `QDRANT_URL` points at a server.
- Distance: cosine, Euclidean, or dot product

Distance and the embedding model are fixed at creation because they define the Qdrant collection. The base keeps the embedding model that was configured when it was created. Chunking and the nearest-neighbour algorithm can be changed later. Chunking applies to the next upload.

Globally, on the Models page (`#/models`):

| Setting | Providers |
| --- | --- |
| Credentials | AWS Bedrock (region, access key, secret key, session token) and OpenAI client API (base URL, API key) |
| LLM | none, Bedrock (Converse), OpenAI chat completions |
| Embedding model | local hashing, Bedrock (Titan, Cohere), OpenAI embeddings |
| Re-rank model | none, Bedrock re-rank, OpenAI-style `POST {base URL}/rerank` (Cohere, Jina, vLLM) |

Each role has a **Test** button, and **Test all** runs the three. A test sends one small real request using what is on the page, saved or not, and reports success, latency, or the provider's error. The LLM and re-rank model apply to the next question. With a re-rank model, retrieval fetches a larger pool (at least 20 passages) and keeps the best `top_k`.

Set a base URL to use Azure OpenAI, vLLM, Ollama, or any other compatible server. A server that needs no key works with only a base URL.

### Credentials

Secrets are stored in `data/model_settings.json`, readable only by the app's user (mode 0600). The API never returns them: the page only shows whether one is saved. Leaving a secret field blank keeps the saved value, and "Remove the saved value" deletes it. Anything stored in the project filesystem can be read by people with access to the project, so prefer the runtime role for AWS and environment variables for keys when you can.

Without saved values, the app falls back to `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `AWS_REGION`, `OPENAI_API_KEY`, and `OPENAI_BASE_URL`, then to the Cloudera AI runtime role for AWS.

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

`start.py` reads the port from `CDSW_APP_PORT`, which Cloudera AI sets for applications. Locally, set it yourself:

```bash
CDSW_APP_PORT=8000 python3 start.py
```

Open `http://127.0.0.1:8000`, where 8000 is whatever you set. Load the sample handbook from a knowledge base and ask “What is the Dublin warehouse code?”

Vectors are stored in `data/` unless `QDRANT_URL` points at a Qdrant server.

## Bedrock notes

The region is the one where the model is enabled, and each model must be enabled for your AWS account. Chat uses the Bedrock Converse API, so Claude, Nova, and Llama model ids work. Embeddings support Titan Embeddings v1 and v2, and Cohere Embed. Re-ranking uses the Bedrock Agent Runtime Rerank API (Amazon Rerank, Cohere Rerank), which is offered in a limited set of regions. A model id is turned into its ARN for the configured region, or paste a full ARN.

## MCP

Agents call Streamable HTTP at `/mcp` on the same port as the UI.

| Tool | What it returns |
| --- | --- |
| `list_knowledge_bases` | Names, chunking, nearest-neighbour settings, and the embedding model |
| `search_knowledge_base` | Passages closest to the question |
| `ask_knowledge_base` | A grounded answer plus those passages |

`ask_knowledge_base` uses the LLM from the Models page when one is set, after re-ranking if a re-rank model is set. Otherwise it returns the passages. `knowledge_base` is an id, or a name when that name is unique.

On Cloudera AI the process binds to loopback and the platform proxies the public hostname, so `/mcp` accepts that hostname. Set `RAGFLOW_MCP_ALLOWED_HOSTS` (comma-separated) to restrict the Host header. Loopback and `CDSW_MASTER_IP` stay allowed.

```json
{
  "mcpServers": {
    "ragflow": {
      "url": "http://127.0.0.1:<CDSW_APP_PORT>/mcp"
    }
  }
}
```

Use the application URL instead of loopback for a client outside the runtime.

## Tests

```bash
python3 -m unittest discover -s tests -t . -v
```
