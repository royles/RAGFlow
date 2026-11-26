import { useEffect, useMemo, useState } from "react"
import { api } from "../api"

// Text is cut at every chunk start and end. Each piece belongs to one chunk, or to
// several where chunks overlap.
function segments(text, chunks) {
  const cuts = new Set([0, text.length])
  for (const chunk of chunks) {
    cuts.add(chunk.start)
    cuts.add(chunk.end)
  }
  const points = [...cuts].sort((a, b) => a - b)
  const pieces = []
  for (let i = 0; i < points.length - 1; i++) {
    const [start, end] = [points[i], points[i + 1]]
    const owners = chunks.filter((chunk) => chunk.start <= start && chunk.end >= end).map((chunk) => chunk.index)
    pieces.push({ start, end, owners, text: text.slice(start, end) })
  }
  return pieces
}

export default function ChunkPreview({ chunkSize, chunkOverlap }) {
  const [sample, setSample] = useState(null)
  const [layout, setLayout] = useState(null)
  const [error, setError] = useState("")
  const [loading, setLoading] = useState("")
  const [llm, setLlm] = useState(null)

  async function loadSample(useLlm) {
    setLoading(useLlm ? "llm" : "builtin")
    setError("")
    try {
      setSample(await api.chunkSample(useLlm))
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading("")
    }
  }

  useEffect(() => {
    loadSample(false)
    api.getModels().then((models) => setLlm(models.llm))
  }, [])

  useEffect(() => {
    if (!sample) return undefined
    const size = Number(chunkSize)
    const overlap = Number(chunkOverlap)
    if (!(size >= 100 && size <= 8000)) return showProblem("Chunk size must be between 100 and 8000 characters.")
    if (!(overlap >= 0 && overlap < size)) return showProblem("Overlap must be zero or more and smaller than the chunk size.")
    let active = true
    const timer = setTimeout(() => {
      api
        .chunkPreview({ text: sample.text, chunk_size: Number(chunkSize), chunk_overlap: Number(chunkOverlap) })
        .then((next) => {
          if (!active) return
          setLayout(next)
          setError("")
        })
        .catch((err) => active && setError(err.message))
    }, 250)
    return () => {
      active = false
      clearTimeout(timer)
    }
  }, [sample, chunkSize, chunkOverlap])

  function showProblem(message) {
    setError(message)
    return undefined
  }

  const hasLlm = llm && llm.provider !== "none"

  return (
    <section className="preview">
      <div className="preview-head">
        <h3>Chunk preview</h3>
        <div className="row">
          {hasLlm ? (
            <button type="button" className="secondary small" onClick={() => loadSample(true)} disabled={Boolean(loading)}>
              {loading === "llm" ? "Writing…" : `Write with ${llm.model}`}
            </button>
          ) : null}
          <button type="button" className="secondary small" onClick={() => loadSample(false)} disabled={Boolean(loading)}>
            Built-in text
          </button>
        </div>
      </div>
      <p className="hint">
        About 500 words describing the RAG process
        {sample?.source === "llm" ? `, written by ${sample.model}` : ""}. Change the chunk size or overlap to see
        where the cuts fall.
      </p>
      {sample?.note ? <p className="hint">{sample.note}</p> : null}
      {error ? <p className="banner">{error}</p> : null}
      {layout && !error ? <Boundaries layout={layout} /> : null}
    </section>
  )
}

function Boundaries({ layout }) {
  const pieces = useMemo(() => segments(layout.text, layout.chunks), [layout])
  const shared = layout.chunks.reduce((total, chunk, i) => {
    const previous = layout.chunks[i - 1]
    return previous ? total + Math.max(0, previous.end - chunk.start) : total
  }, 0)
  const starts = new Set(layout.chunks.map((chunk) => chunk.start))
  const first = new Map(layout.chunks.map((chunk) => [chunk.start, chunk.index]))

  return (
    <>
      <p className="legend">
        <span className="swatch c0" /> chunk <span className="swatch c1" /> next chunk <span className="swatch shared" />{" "}
        shared by two chunks
      </p>
      <p className="summary">
        {layout.chunks.length} {layout.chunks.length === 1 ? "chunk" : "chunks"} · {layout.text.length} characters ·{" "}
        {shared} repeated through overlap
      </p>
      <div className="sample" aria-label="Sample text with chunk boundaries">
        {pieces.map((piece) => {
          const shared = piece.owners.length > 1
          const className = shared ? "shared" : `c${piece.owners[0] % 2}`
          const title = shared
            ? `Shared by chunks ${piece.owners.map((n) => n + 1).join(" and ")}`
            : `Chunk ${piece.owners[0] + 1}`
          return (
            <span key={piece.start}>
              {starts.has(piece.start) ? <b className="mark">{first.get(piece.start) + 1}</b> : null}
              <span className={className} title={title}>
                {piece.text}
              </span>
            </span>
          )
        })}
      </div>
      <Strip layout={layout} />
    </>
  )
}

function Strip({ layout }) {
  const total = layout.text.length
  return (
    <div className="strip" aria-hidden="true">
      {layout.chunks.map((chunk, i) => {
        const previous = layout.chunks[i - 1]
        const next = layout.chunks[i + 1]
        const overlapChars = previous ? Math.max(0, previous.end - chunk.start) : 0
        const nextChars = next ? Math.max(0, chunk.end - next.start) : 0
        const length = chunk.end - chunk.start
        const width = (length / total) * 100
        return (
          <div className="lane" key={chunk.index}>
            <div
              className={`bar c${chunk.index % 2}`}
              style={{ left: `${(chunk.start / total) * 100}%`, width: `${width}%` }}
              title={`Chunk ${chunk.index + 1}: characters ${chunk.start}–${chunk.end}`}
            >
              {overlapChars ? <span className="bar-overlap start" style={{ width: `${(overlapChars / length) * 100}%` }} /> : null}
              {nextChars ? <span className="bar-overlap end" style={{ width: `${(nextChars / length) * 100}%` }} /> : null}
              <small>{chunk.index + 1}</small>
            </div>
          </div>
        )
      })}
    </div>
  )
}
