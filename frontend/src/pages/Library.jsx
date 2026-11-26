import { useEffect, useState } from "react"
import { api } from "../api"

const EMPTY = {
  name: "",
  description: "",
  chunk_size: 800,
  chunk_overlap: 120,
  distance: "cosine",
  nn_algorithm: "hnsw",
}

export default function Library() {
  const [catalog, setCatalog] = useState(null)
  const [bases, setBases] = useState([])
  const [agent, setAgent] = useState(null)
  const [models, setModels] = useState(null)
  const [form, setForm] = useState(EMPTY)
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let active = true
    Promise.all([api.catalog(), api.listKnowledgeBases(), api.agent(), api.getModels()])
      .then(([nextCatalog, nextBases, nextAgent, nextModels]) => {
        if (!active) return
        setCatalog(nextCatalog)
        setBases(nextBases.knowledge_bases)
        setAgent(nextAgent)
        setModels(nextModels)
        setForm((current) => ({ ...current, ...nextCatalog.defaults }))
      })
      .catch((err) => active && setError(err.message))
    return () => {
      active = false
    }
  }, [])

  function update(event) {
    const { name, value } = event.target
    setForm((current) => ({ ...current, [name]: value }))
  }

  async function onCreate(event) {
    event.preventDefault()
    setBusy(true)
    setError("")
    const payload = {
      ...form,
      chunk_size: Number(form.chunk_size),
      chunk_overlap: Number(form.chunk_overlap),
    }
    try {
      const created = await api.createKnowledgeBase(payload)
      window.location.hash = `#/kb/${created.id}`
    } catch (err) {
      setError(err.message)
      setBusy(false)
    }
  }

  return (
    <main className="layout">
      <section>
        <div className="section-head">
          <h1>Knowledge bases</h1>
          <p>Each base has its own chunking and Qdrant index. Models are set on the Models page.</p>
        </div>
        {error ? <p className="banner">{error}</p> : null}
        {bases.length === 0 ? (
          <p className="empty">No knowledge bases yet. Create one to upload documents and ask questions.</p>
        ) : (
          <ul className="cards">
            {bases.map((item) => (
              <li key={item.id}>
                <a href={`#/kb/${item.id}`}>
                  <strong>{item.name}</strong>
                  <span>{item.description || "No description"}</span>
                  <small>
                    {item.document_count} documents · {item.chunk_size}/{item.chunk_overlap} · {item.nn_algorithm} ·{" "}
                    {item.embed_model}
                  </small>
                </a>
              </li>
            ))}
          </ul>
        )}
        {agent ? <AgentPanel agent={agent} /> : null}
      </section>
      <form className="panel" onSubmit={onCreate}>
        <h2>New knowledge base</h2>
        <Field label="Name">
          <input name="name" value={form.name} onChange={update} required maxLength={80} />
        </Field>
        <Field label="Description">
          <textarea name="description" value={form.description} onChange={update} rows={2} maxLength={500} />
        </Field>
        <div className="split">
          <Field label="Chunk size" hint="Characters per passage.">
            <input name="chunk_size" type="number" min={100} max={8000} value={form.chunk_size} onChange={update} />
          </Field>
          <Field label="Overlap" hint="Characters shared with the next passage.">
            <input name="chunk_overlap" type="number" min={0} value={form.chunk_overlap} onChange={update} />
          </Field>
        </div>
        <div className="split">
          <Field
            label="Nearest neighbour"
            hint="On a Qdrant server, HNSW is approximate and exact scans every vector. The embedded index always scans every vector."
          >
            <select name="nn_algorithm" value={form.nn_algorithm} onChange={update}>
              {(catalog?.nn_algorithms || []).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Distance">
            <select name="distance" value={form.distance} onChange={update}>
              {(catalog?.distances || []).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </select>
          </Field>
        </div>
        {models ? <EmbeddingNote embedding={models.embedding} /> : null}
        <button type="submit" disabled={busy || !catalog}>
          {busy ? "Creating…" : "Create knowledge base"}
        </button>
      </form>
    </main>
  )
}

function EmbeddingNote({ embedding }) {
  const label = embedding.provider === "local" ? "the built-in local embedder" : `${embedding.model} (${embedding.provider})`
  return (
    <p className="note">
      This base will embed with {label}, {embedding.dimensions} dimensions. The choice is fixed once the base is
      created. Change it on the <a href="#/models">Models</a> page first.
    </p>
  )
}

function AgentPanel({ agent }) {
  return (
    <section className="agent">
      <h2>Agents</h2>
      <p>
        The same answers are available over MCP at <code>{agent.mcp_path}</code> ({agent.transport}). Point a model
        client at this application’s URL plus that path.
      </p>
      <ul>
        {agent.tools.map((tool) => (
          <li key={tool.name}>
            <code>{tool.name}</code>
            <span>{tool.description}</span>
          </li>
        ))}
      </ul>
    </section>
  )
}

export function Field({ label, hint, children }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint ? <small>{hint}</small> : null}
    </label>
  )
}
