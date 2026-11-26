import { useEffect, useState } from "react"
import { api } from "../api"
import { Field, Icon } from "../components"
import ChunkPreview from "./ChunkPreview"

export default function Manage({ id, onChange }) {
  const [catalog, setCatalog] = useState(null)
  const [kb, setKb] = useState(null)
  const [documents, setDocuments] = useState([])
  const [form, setForm] = useState(null)
  const [error, setError] = useState("")
  const [notice, setNotice] = useState("")
  const [busy, setBusy] = useState("")
  const [uploads, setUploads] = useState([])
  const [dragging, setDragging] = useState(false)

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
    })
  }

  useEffect(() => {
    let active = true
    reload().catch((err) => active && setError(err.message))
    return () => {
      active = false
    }
  }, [id])

  function update(event) {
    const { name, value } = event.target
    setForm((current) => ({ ...current, [name]: value }))
  }

  async function run(label, work) {
    setBusy(label)
    setError("")
    setNotice("")
    try {
      await work()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy("")
    }
  }

  async function refreshDocuments() {
    setDocuments((await api.listDocuments(id)).documents)
    setKb(await api.getKnowledgeBase(id))
    onChange()
  }

  const onSave = (event) => {
    event.preventDefault()
    run("save", async () => {
      const saved = await api.updateKnowledgeBase(id, {
        ...form,
        chunk_size: Number(form.chunk_size),
        chunk_overlap: Number(form.chunk_overlap),
      })
      setKb(saved)
      onChange()
      setNotice("Saved. Chunk settings apply to the next upload.")
    })
  }

  const onDeleteBase = () => {
    if (!window.confirm(`Delete ${kb.name}? Documents and vectors are removed.`)) return
    run("delete-base", async () => {
      await api.deleteKnowledgeBase(id)
      onChange()
      window.location.hash = "#/"
    })
  }

  function patchUpload(key, changes) {
    setUploads((current) => current.map((item) => (item.key === key ? { ...item, ...changes } : item)))
  }

  // A few files go up and index at once. A bad file fails on its own row.
  async function uploadFiles(files) {
    if (!files.length) return
    const batch = files.map((file) => ({ key: ++uploadKey, file, name: file.name, size: file.size, status: "queued", sent: 0 }))
    setUploads(batch)
    setBusy("upload")
    setError("")
    setNotice("")
    let next = 0
    async function worker() {
      while (next < batch.length) {
        const item = batch[next++]
        patchUpload(item.key, { status: "uploading" })
        try {
          const row = await api.uploadDocument(id, item.file, (sent) =>
            patchUpload(item.key, { sent, status: sent >= 1 ? "indexing" : "uploading" }),
          )
          patchUpload(item.key, { status: "done", sent: 1, chunks: row.chunk_count })
          await refreshDocuments()
        } catch (err) {
          patchUpload(item.key, { status: "failed", message: err.message })
        }
      }
    }
    await Promise.all(Array.from({ length: Math.min(UPLOAD_PARALLEL, batch.length) }, worker))
    setBusy("")
  }

  const onUpload = (event) => {
    const files = [...(event.target.files || [])]
    event.target.value = ""
    uploadFiles(files)
  }

  const onDrop = (event) => {
    event.preventDefault()
    setDragging(false)
    if (busy) return
    uploadFiles([...event.dataTransfer.files])
  }

  const onSample = () =>
    run("sample", async () => {
      await api.loadSample(id)
      await refreshDocuments()
    })

  const onDeleteDocument = (documentId) =>
    run("delete-doc", async () => {
      await api.deleteDocument(id, documentId)
      await refreshDocuments()
    })

  if (!kb || !form || !catalog) {
    return <main className="single">{error ? <p className="banner">{error}</p> : <p className="muted">Loading…</p>}</main>
  }

  return (
    <main className="workspace">
      <div className="section-head">
        <p className="crumb">
          <a href={`#/kb/${id}`}>
            <Icon name="chevronLeft" size={14} /> Back to chat
          </a>
        </p>
        <h1>{kb.name}</h1>
        <p className="meta">
          {kb.distance} · {kb.nn_algorithm} · {kb.embed_model} · {kb.embed_dim} dimensions
        </p>
      </div>
      {error ? <p className="banner">{error}</p> : null}
      {notice ? <p className="notice">{notice}</p> : null}

      <section
        className={`panel${dragging ? " dragging" : ""}`}
        onDragOver={(event) => {
          event.preventDefault()
          setDragging(true)
        }}
        onDragLeave={(event) => !event.currentTarget.contains(event.relatedTarget) && setDragging(false)}
        onDrop={onDrop}
      >
        <h2>Documents</h2>
        <div className="row">
          <label className="file button">
            <input type="file" multiple accept=".txt,.md,.markdown,.csv,.pdf,.docx" onChange={onUpload} disabled={busy === "upload"} />
            {busy === "upload" ? "Uploading…" : "Upload"}
          </label>
          <button type="button" className="secondary" onClick={onSample} disabled={busy === "sample"}>
            {busy === "sample" ? "Indexing…" : "Load sample"}
          </button>
        </div>
        <p className="hint">Select or drop several files at once. Text, Markdown, CSV, PDF, and DOCX. New files use the chunk size saved below.</p>
        {uploads.length ? <UploadTracker uploads={uploads} busy={busy === "upload"} onClear={() => setUploads([])} /> : null}
        {documents.length === 0 ? (
          <p className="empty">Nothing uploaded yet.</p>
        ) : (
          <ul className="docs">
            {documents.map((doc) => (
              <li key={doc.id}>
                <div>
                  <strong>{doc.filename}</strong>
                  <small>{doc.chunk_count} {doc.chunk_count === 1 ? "chunk" : "chunks"}</small>
                </div>
                <button type="button" className="danger" onClick={() => onDeleteDocument(doc.id)} disabled={busy === "delete-doc"}>
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <form className="panel settings" onSubmit={onSave}>
        <h2>Settings</h2>
        <p className="hint">
          Chunk size and overlap apply to the next upload. Distance and the embedding model stay as they were when the
          base was created.
        </p>
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
        <ChunkPreview chunkSize={form.chunk_size} chunkOverlap={form.chunk_overlap} />
        <div className="row">
          <button type="submit" disabled={busy === "save"}>
            {busy === "save" ? "Saving…" : "Save settings"}
          </button>
          <button type="button" className="danger" onClick={onDeleteBase} disabled={busy === "delete-base"}>
            <Icon name="trash" /> Delete knowledge base
          </button>
        </div>
      </form>
    </main>
  )
}

const UPLOAD_PARALLEL = 3
let uploadKey = 0

const STATUS = {
  queued: "Waiting",
  uploading: "Uploading",
  indexing: "Indexing",
  done: "Added",
  failed: "Failed",
}

function UploadTracker({ uploads, busy, onClear }) {
  const finished = uploads.filter((item) => item.status === "done" || item.status === "failed").length
  const failed = uploads.filter((item) => item.status === "failed").length
  return (
    <div className="tracker" aria-live="polite">
      <div className="tracker-head">
        <strong>
          {busy ? `${finished} of ${uploads.length} finished` : `${uploads.length - failed} added${failed ? `, ${failed} failed` : ""}`}
        </strong>
        {busy ? null : (
          <button type="button" className="ghost" onClick={onClear}>
            Clear
          </button>
        )}
      </div>
      <ul>
        {uploads.map((item) => (
          <li key={item.key} className={item.status}>
            <div className="line">
              <span className="name" title={item.name}>
                {item.name}
              </span>
              <span className="state">
                {STATUS[item.status]}
                {item.status === "uploading" ? ` ${Math.round(item.sent * 100)}%` : ""}
                {item.status === "done" ? ` · ${item.chunks} ${item.chunks === 1 ? "chunk" : "chunks"}` : ""}
              </span>
            </div>
            <div
              className={`meter${item.status === "indexing" ? " busy" : ""}`}
              role="progressbar"
              aria-label={item.name}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round((item.status === "done" || item.status === "failed" ? 1 : item.sent) * 100)}
            >
              <span style={{ width: `${(item.status === "queued" ? 0 : item.sent) * 100}%` }} />
            </div>
            {item.message ? <p className="why-failed">{item.message}</p> : null}
          </li>
        ))}
      </ul>
    </div>
  )
}
