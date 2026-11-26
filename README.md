# RAGFlow

Knowledge bases for Cloudera AI. Create a base, choose how documents are split, and search them with Qdrant. Ask from the React UI, the REST API, or MCP. The same retrieval function answers all three.

Models are optional. Without any, the app embeds text locally and returns the matching passages. The **Models** page connects AWS Bedrock or any OpenAI client API server and sets an LLM, an embedding model, and a re-rank model.

## What you can configure

Per knowledge base:

- Chunk size and overlap, in characters
- Nearest neighbour algorithm: HNSW or exact. On a Qdrant server, HNSW is the approximate index and exact scans every vector. The embedded index always scans every vector; the choice is still saved and used when `QDRANT_URL` points at a server.
- Distance: cosine, Euclidean, or dot product

Below the chunk fields, a **chunk preview** shows about 500 words describing the RAG process, cut with the current size and overlap. Each chunk is numbered and tinted, and text shared by two neighbouring chunks is striped amber, so the effect of a setting is visible as you type. With an LLM configured, **Write with <model>** has it write a fresh sample. Otherwise, or if the call fails, built-in text is used. The preview uses the same splitting code as indexing.

Distance and the embedding model are fixed at creation because they define the Qdrant collection. The base keeps the embedding model that was configured when it was created. Chunking and the nearest-neighbour algorithm can be changed later. Chunking applies to the next upload.

Globally, on the Models page (`#/models`):

| Setting | Providers |
| --- | --- |
| Credentials | AWS Bedrock (region, access key, secret key, session token) and OpenAI client API (base URL, API key) |
| LLM | none, Bedrock (Converse), OpenAI chat completions |
| Embedding model | local hashing, Bedrock (Titan, Cohere), OpenAI embeddings |
| Re-rank model | none, Bedrock re-rank, OpenAI-style `POST {base URL}/rerank` (Cohere, Jina, vLLM) |
| Judge model (optional) | none, same as the LLM, Bedrock (Converse), OpenAI chat completions |

Each role has a **Test** button, and **Test all** runs every role. A test sends one small real request using what is on the page, saved or not, and reports success, latency, or the provider's error. The LLM and re-rank model apply to the next question. With a re-rank model, retrieval fetches a larger pool (at least 20 passages) and keeps the best `top_k`.

Set a base URL to use Azure OpenAI, vLLM, Ollama, or any other compatible server. The OpenAI credentials card holds a default base URL and the API key. Each of the LLM, embedding, re-rank, and judge cards can set its own base URL, so the models can live on different servers. A blank role URL uses the default, then `OPENAI_BASE_URL`, then `https://api.openai.com/v1`. The API key is shared by all of them. A server that needs no key works with only a base URL.

### Credentials

Secrets are stored in `data/model_settings.json`, readable only by the app's user (mode 0600). The API never returns them: the page only shows whether one is saved. Leaving a secret field blank keeps the saved value, and "Remove the saved value" deletes it. Anything stored in the project filesystem can be read by people with access to the project, so prefer the runtime role for AWS and environment variables for keys when you can.

Without saved values, the app falls back to `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `AWS_REGION`, `OPENAI_API_KEY`, and `OPENAI_BASE_URL`, then to the Cloudera AI runtime role for AWS.

## Using the app

The default view is a clean chat. Pick a knowledge base in the left panel and ask a short question: **Enter** sends, **Shift+Enter** adds a new line. Answers show numbered citations. Click one, or a source below the answer, to open the passage in the right-hand panel with the words from your question highlighted. Each answer notes the model, the re-rank model, the passage count, and the time, followed by thumbs up/down and, with a judge model, quality scores. Without an LLM it lists the closest passages instead.

Everything else is out of the way: **Manage** (per knowledge base) holds documents, chunking, and the chunk preview; **New knowledge base**, **Quality**, **Models**, and **Agents (MCP)** are in the left panel. The UI follows the system light or dark setting.

## Google Drive

Connect Google Drive once, then pick files from it in any knowledge base: **Manage**, **Google Drive**. The picker browses My Drive and Shared with me, searches by name, and imports several files at once through the same progress tracker as uploads. Text, Markdown, CSV, PDF and DOCX files are downloaded as they are. Google Docs and Slides are exported as text, and Sheets as CSV.

Access is **read-only**: the only scope requested is `https://www.googleapis.com/auth/drive.readonly`, so RAGFlow can read files but never change or delete them. Sign-in is the OAuth 2.0 authorization-code flow with PKCE and a single-use `state`. The refresh token is kept in `data/google_drive.json` (mode 0600) and is never sent to the browser. **Disconnect** on the Google Drive page revokes the grant.

Setup, on the Google Drive page (`#/google`):

1. In Google Cloud Console enable the **Google Drive API** and create an **OAuth client ID** of type **Web application**.
2. Add the redirect URI shown on the page as an **Authorized redirect URI**. It is the app's own address plus `/api/google/callback`, for example `https://ragflow.<your-domain>/api/google/callback` on Cloudera AI. Google requires HTTPS except for loopback addresses.
3. While the OAuth consent screen is in testing, add the accounts that will sign in as test users. `drive.readonly` is a restricted scope, so a public app needs Google's verification, whereas an Internal app in a Workspace organisation does not.
4. Paste the client ID and secret on the page, save, and press **Connect Google Drive**. `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` work too.

One Google account is connected for the whole app, so anyone who can open RAGFlow can import from it. Restrict who can open the application in Cloudera AI accordingly. The server needs outbound access to `accounts.google.com`, `oauth2.googleapis.com` and `www.googleapis.com`.

## Quality: feedback, audit, and scores

Under each answer, **thumbs up/down** record a rating. A thumbs-down offers an optional reason (incorrect, incomplete, wrong sources, unclear, other) and a comment. Votes never change retrieval by themselves. They label real answers for people to review.

Every answer is written to an **audit log** in `data/ragflow.db` with the question, the answer, the cited passages, the models and chunk settings used, the source (UI, MCP, or API), and the time. Feedback is append-only: changing or withdrawing a vote adds an event, so the history is kept and the latest event is the current rating. The log outlives the knowledge base it came from. Set `RAGFLOW_AUDIT_RETENTION_DAYS` to delete older answers at startup (default `0`, keep everything).

With a **judge model** set on the Models page, each LLM-written answer is scored in the background, so answers are never slowed:

- **Faithfulness (groundedness)**: the judge splits the answer into claims and checks each against the retrieved passages. The score is the share supported. Click the chip to see which claims are not. This is the main hallucination check.
- **Answer relevance**: a 1 to 5 rating of how directly the answer addresses the question, shown as a percentage.

Pick **Same as the LLM** for the simplest setup, or a different model for an independent check. Judge scores come from an LLM, so treat them as a screening signal and calibrate them against thumbs. They cost two extra model calls per answer.

The **Quality** page (`#/quality`) shows approval rate and average scores, filters by knowledge base, rating, or low faithfulness, expands each answer to show its sources and settings, and scores older answers on demand. **Export JSONL** (full records including cited passages and feedback events) or **CSV** to build test questions or tune chunking and re-ranking. The same data is at `GET /api/audit` and `GET /api/audit/export?format=jsonl|csv`.

## Run on Cloudera AI

Import the repository as a project (prototype tasks are in `.project-metadata.yaml`). The install job runs `scripts/install.py` in the project runtime. It does not create a virtualenv. The application script is `start.py`. It listens on `127.0.0.1:$CDSW_APP_PORT`.

Open the application from the project Applications tab. The React UI is the committed `frontend/dist`, so the runtime does not need Node.

To rebuild the UI on a machine that has Node:

```bash
cd frontend
npm install
npm run build
```

The access log shows the first `GET /` request only. Later repeats of it (health checks, reloads) are hidden.

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

An upload may produce at most `RAGFLOW_MAX_CHUNKS` chunks (default 5000; `0` removes the limit). Every chunk is embedded, and Bedrock embeddings are one request per chunk, so a very large file in one request can outlast the platform proxy timeout. Raise it if your embedding model is fast, or use a larger chunk size.

Select or drag several files onto a knowledge base's Documents panel to upload them together. They are indexed one at a time, and a file that fails is reported without stopping the rest.

There is no file size limit by default. Uploads are read into memory, so set `RAGFLOW_MAX_UPLOAD_MB` to cap them if the runtime has little RAM. The platform proxy may also have its own request size limit.

Vectors are stored in `data/` unless `QDRANT_URL` points at a Qdrant server.

## Bedrock notes

The region is the one where the model is enabled, and each model must be enabled for your AWS account. Chat uses the Bedrock Converse API, so Claude, Nova, and Llama model ids work. Newer models such as Claude Haiku 4.5 cannot be called by their bare id and need an inference profile id with a region prefix (`us.`, `eu.`, `global.`) or the profile ARN. The suggestions use these, and a Bedrock error asking for an inference profile is answered with the prefixed ids to try. Embeddings support Titan Embeddings v1 and v2, and Cohere Embed. Re-ranking uses the Bedrock Agent Runtime Rerank API (Amazon Rerank, Cohere Rerank), which is offered in a limited set of regions. A model id is turned into its ARN for the configured region, or paste a full ARN. Each of the LLM, embedding, and re-rank cards can set its own AWS region, which overrides the default region on the credentials card. This matters for re-ranking: if Bedrock answers “The provided model identifier is invalid”, the model is probably not offered in the region used (Amazon Rerank 1.0 and Cohere Rerank 3.5 are in us-west-2, for example), so set that region on the re-rank card only.

## MCP

Agents call Streamable HTTP at `/mcp` on the same port as the UI.

| Tool | What it returns |
| --- | --- |
| `list_knowledge_bases` | Names, chunking, nearest-neighbour settings, and the embedding model |
| `search_knowledge_base` | Passages closest to the question |
| `ask_knowledge_base` | A grounded answer plus those passages, and an `interaction_id` |
| `rate_answer` | Records `up`, `down`, or `none` (with an optional reason and comment) for an `interaction_id` |

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
