import { useEffect, useState } from "react"
import { api } from "../api"
import { Field } from "./Library"

export default function KnowledgeBase({ id }) {
  const [catalog, setCatalog] = useState(null)
  const [kb, setKb] = useState(null)
  const [documents, setDocuments] = useState([])
  const [form, setForm] = useState(null)
  const [question, setQuestion] = useState("What is the Dublin warehouse code?")
  const [topK, setTopK] = useState(5)
  const [thread, setThread] = useState([])
  const [error, setError] = useState("")
  const [busy, setBusy] = useState("")

  async function reload() {
    const [nextKb, nextDocs, nextCatalog] = await Promise.all([
      api.getKnowledgeBase(id),
      api.listDocuments(id),
      api.catalog(),
    ])
    setKb(nextKb)
    setDocuments(nextDocs.documents)
    setCatalog(nextCatalog)
    setForm({
      name: nextKb.name,
      description: nextKb.description,
      chunk_size: nextKb.chunk_size,
      chunk_overlap: nextKb.chunk_overlap,
      nn_algorithm: nextKb.nn_algorithm,
      chat_preset: nextKb.chat_preset,
      chat_model: nextKb.chat_model,
      bedrock_region: nextKb.bedrock_region,
    })
  }

  useEffect(() => {
    let active = true
    reload()
      .catch((err) => active && setError(err.message))
    return () => {
      active = false
    }
  }, [id])

  function update(event) {
    const { name, value } = event.target
    setForm((current) => ({ ...current, [name]: value }))
  }

  async function onSave(event) {
    event.preventDefault()
    setBusy("save")
    setError("")
    try {
      const saved = await api.updateKnowledgeBase(id, {
        ...form,
        chunk_size: Number(form.chunk_size),
        chunk_overlap: Number(form.chunk_overlap),
      })
      setKb(saved)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy("")
    }
  }

  async function onDeleteBase() {
    if (!window.confirm(`Delete ${kb.name}? Documents and vectors are removed.`)) return
    setBusy("delete-base")
    try {
      await api.deleteKnowledgeBase(id)
      window.location.hash = "#/"
    } catch (err) {
      setError(err.message)
      setBusy("")
    }
  }

  async function onUpload(event) {
    const file = event.target.files?.[0]
    event.target.value = ""
    if (!file) return
    setBusy("upload")
    setError("")
    try {
      await api.uploadDocument(id, file)
      const nextDocs = await api.listDocuments(id)
      setDocuments(nextDocs.documents)
      setKb(await api.getKnowledgeBase(id))
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy("")
    }
  }

  async function onSample() {
    setBusy("sample")
    setError("")
    try {
      await api.loadSample(id)
      setDocuments((await api.listDocuments(id)).documents)
      setKb(await api.getKnowledgeBase(id))
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy("")
    }
  }

  async function onDeleteDocument(documentId) {
    setBusy("delete-doc")
    setError("")
    try {
      await api.deleteDocument(id, documentId)
      setDocuments((await api.listDocuments(id)).documents)
      setKb(await api.getKnowledgeBase(id))
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy("")
    }
  }

  async function onAsk(event) {
    event.preventDefault()
    setBusy("ask")
    setError("")
    try {
      const result = await api.ask(id, { question, top_k: Number(topK) })
      setThread((current) => [...current, { question, ...result }])
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy("")
    }
  }

  if (!kb || !form || !catalog) {
    return <main className="layout single">{error ? <p className="banner">{error}</p> : <p>Loading…</p>}</main>
  }

  const chatUsesBedrock = form.chat_preset !== "none"
  const showRegion = chatUsesBedrock || kb.embed_provider === "bedrock"

  return (
    <main className="workspace">
      <div className="section-head">
        <p className="crumb">
          <a href="#/">Knowledge bases</a>
        </p>
        <h1>{kb.name}</h1>
        <p className="meta">
          {kb.distance} · {kb.nn_algorithm} · {kb.embed_model}
          {kb.chat_model ? ` · ${kb.chat_model}` : " · retrieval only"}
        </p>
      </div>
      {error ? <p className="banner">{error}</p> : null}
      <div className="columns">
        <section className="panel">
          <h2>Documents</h2>
          <div className="row">
            <label className="file">
              <input type="file" accept=".txt,.md,.markdown,.csv,.pdf,.docx" onChange={onUpload} />
              {busy === "upload" ? "Indexing…" : "Upload"}
            </label>
            <button type="button" onClick={onSample} disabled={busy === "sample"}>
              {busy === "sample" ? "Indexing…" : "Load sample"}
            </button>
          </div>
          <p className="hint">Text, Markdown, CSV, PDF, and DOCX. New files use the chunk size saved below.</p>
          {documents.length === 0 ? (
            <p className="empty">Nothing uploaded yet.</p>
          ) : (
            <ul className="docs">
              {documents.map((doc) => (
                <li key={doc.id}>
                  <div>
                    <strong>{doc.filename}</strong>
                    <small>{doc.chunk_count} chunks</small>
                  </div>
                  <button type="button" onClick={() => onDeleteDocument(doc.id)} disabled={busy === "delete-doc"}>
                    Remove
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
        <section className="panel chat">
          <h2>Ask</h2>
          <form onSubmit={onAsk}>
            <Field label="Question">
              <textarea value={question} onChange={(event) => setQuestion(event.target.value)} rows={3} required />
            </Field>
            <Field label="Passages" hint="How many chunks to retrieve.">
              <input type="number" min={1} max={20} value={topK} onChange={(event) => setTopK(event.target.value)} />
            </Field>
            <button type="submit" disabled={busy === "ask"}>
              {busy === "ask" ? "Searching…" : "Ask"}
            </button>
          </form>
          <div className="thread">
            {thread.map((turn, index) => (
              <article key={index}>
                <h3>{turn.question}</h3>
                <p className="mode">{turn.mode === "bedrock" ? "Answered with Amazon Bedrock" : "Grounded in retrieved passages"}</p>
                <p className="answer">{turn.answer}</p>
                {turn.citations?.length ? (
                  <p className="hint">
                    {turn.citations
                      .map((item) => `${item.filename} · chunk ${item.chunk_index} · score ${item.score}`)
                      .join("  ")}
                  </p>
                ) : null}
                {turn.mode === "bedrock" && turn.citations?.length ? (
                  <ol className="citations">
                    {turn.citations.map((item, citationIndex) => (
                      <li key={citationIndex}>
                        <strong>
                          {item.filename} · chunk {item.chunk_index} · {item.score}
                        </strong>
                        <span>{item.text}</span>
                      </li>
                    ))}
                  </ol>
                ) : null}
              </article>
            ))}
          </div>
        </section>
      </div>
      <form className="panel settings" onSubmit={onSave}>
        <h2>Settings</h2>
        <p className="hint">Chunk size and overlap apply to the next upload. Distance and the embedding model stay as they were when the base was created.</p>
        <div className="split">
          <Field label="Name">
            <input name="name" value={form.name} onChange={update} required maxLength={80} />
          </Field>
          <Field label="Nearest neighbour" hint="Used on the next question. Exact scan applies when this base uses a Qdrant server.">
            <select name="nn_algorithm" value={form.nn_algorithm} onChange={update}>
              {catalog.nn_algorithms.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <Field label="Description">
          <textarea name="description" value={form.description} onChange={update} rows={2} maxLength={500} />
        </Field>
        <div className="split">
          <Field label="Chunk size">
            <input name="chunk_size" type="number" min={100} max={8000} value={form.chunk_size} onChange={update} />
          </Field>
          <Field label="Overlap">
            <input name="chunk_overlap" type="number" min={0} value={form.chunk_overlap} onChange={update} />
          </Field>
        </div>
        <Field label="Chat model">
          <select name="chat_preset" value={form.chat_preset} onChange={update}>
            {catalog.chat_presets.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
              </option>
            ))}
          </select>
        </Field>
        {form.chat_preset === "custom" ? (
          <Field label="Bedrock chat model id">
            <input name="chat_model" value={form.chat_model} onChange={update} />
          </Field>
        ) : null}
        {showRegion ? (
          <Field label="AWS region">
            <input name="bedrock_region" value={form.bedrock_region} onChange={update} />
          </Field>
        ) : null}
        <p className="hint">
          Index: {kb.distance}, {kb.embed_dim} dimensions, {kb.embed_model}.
        </p>
        <div className="row">
          <button type="submit" disabled={busy === "save"}>
            {busy === "save" ? "Saving…" : "Save settings"}
          </button>
          <button type="button" className="danger" onClick={onDeleteBase} disabled={busy === "delete-base"}>
            Delete knowledge base
          </button>
        </div>
      </form>
    </main>
  )
}
