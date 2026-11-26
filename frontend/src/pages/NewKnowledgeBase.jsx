import { useEffect, useState } from "react"
import { api } from "../api"
import { Field } from "../components"
import ChunkPreview from "./ChunkPreview"

const EMPTY = {
  name: "",
  description: "",
  chunk_size: 800,
  chunk_overlap: 120,
  distance: "cosine",
  nn_algorithm: "hnsw",
}

export default function NewKnowledgeBase({ welcome, onChange }) {
  const [catalog, setCatalog] = useState(null)
  const [models, setModels] = useState(null)
  const [form, setForm] = useState(EMPTY)
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let active = true
    Promise.all([api.catalog(), api.getModels()])
      .then(([nextCatalog, nextModels]) => {
        if (!active) return
        setCatalog(nextCatalog)
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
    try {
      const created = await api.createKnowledgeBase({
        ...form,
        chunk_size: Number(form.chunk_size),
        chunk_overlap: Number(form.chunk_overlap),
      })
      onChange()
      window.location.hash = `#/kb/${created.id}/manage`
    } catch (err) {
      setError(err.message)
      setBusy(false)
    }
  }

  const embedding = models?.embedding
  const embedLabel =
    embedding?.provider === "local" ? "the built-in local embedder" : `${embedding?.model} (${embedding?.provider})`

  return (
    <main className="workspace narrow">
      <div className="section-head">
        <h1>{welcome ? "Welcome to RAGFlow" : "New knowledge base"}</h1>
        <p>
          {welcome
            ? "Create a knowledge base, add documents, then ask questions and get answers grounded in them."
            : "Choose how documents are split. Models are set on the Models page."}
        </p>
      </div>
      {error ? <p className="banner">{error}</p> : null}
      <form className="panel" onSubmit={onCreate}>
        <Field label="Name">
          <input name="name" value={form.name} onChange={update} required maxLength={80} autoFocus />
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
        <ChunkPreview chunkSize={form.chunk_size} chunkOverlap={form.chunk_overlap} />
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
        {embedding ? (
          <p className="note">
            This base will embed with {embedLabel}, {embedding.dimensions} dimensions. The choice is fixed once the base
            is created. Change it on the <a href="#/models">Models</a> page first.
          </p>
        ) : null}
        <button type="submit" disabled={busy || !catalog}>
          {busy ? "Creating…" : "Create knowledge base"}
        </button>
      </form>
    </main>
  )
}
