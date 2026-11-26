import { useCallback, useEffect, useState } from "react"
import { api } from "../api"
import { Feedback, REASONS, ScoreChips, percent } from "../quality"

const PAGE = 25

export default function Quality({ bases }) {
  const [filters, setFilters] = useState({ kb_id: "", rating: "", scored: "" })
  const [items, setItems] = useState([])
  const [total, setTotal] = useState(0)
  const [summary, setSummary] = useState(null)
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(true)

  const load = useCallback(
    async (offset = 0) => {
      setLoading(true)
      setError("")
      try {
        const page = await api.audit({ ...filters, limit: PAGE, offset })
        setItems((current) => (offset ? [...current, ...page.items] : page.items))
        setTotal(page.total)
        setSummary(page.summary)
      } catch (err) {
        setError(err.message)
      } finally {
        setLoading(false)
      }
    },
    [filters],
  )

  useEffect(() => {
    load()
  }, [load])

  function replace(item) {
    setItems((current) => current.map((row) => (row.id === item.id ? { ...row, ...item } : row)))
  }

  const exportUrl = (format) => `/api/audit/export?${new URLSearchParams({ format, kb_id: filters.kb_id })}`

  return (
    <div className="workspace quality">
      <div className="section-head">
        <h1>Quality</h1>
        <p>
          Every answer is kept with its sources, models and settings, plus the ratings and scores it received. Use it
          to find weak answers and to build test questions.
        </p>
      </div>
      {error ? <p className="banner">{error}</p> : null}

      {summary ? (
        <div className="stats">
          <Stat label="Answers" value={summary.total} />
          <Stat
            label="Approval"
            value={percent(summary.approval)}
            note={`${summary.up} up · ${summary.down} down`}
          />
          <Stat label="Faithfulness" value={percent(summary.faithfulness)} note={`${summary.scored} scored`} />
          <Stat label="Answer relevance" value={percent(summary.relevance)} note={`${summary.scored} scored`} />
        </div>
      ) : null}

      <div className="toolbar">
        <select
          aria-label="Knowledge base"
          value={filters.kb_id}
          onChange={(event) => setFilters({ ...filters, kb_id: event.target.value })}
        >
          <option value="">All knowledge bases</option>
          {bases.map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}
            </option>
          ))}
        </select>
        <select
          aria-label="Rating"
          value={filters.rating}
          onChange={(event) => setFilters({ ...filters, rating: event.target.value })}
        >
          <option value="">Any rating</option>
          <option value="down">Thumbs down</option>
          <option value="up">Thumbs up</option>
          <option value="unrated">Not rated</option>
        </select>
        <select
          aria-label="Scores"
          value={filters.scored}
          onChange={(event) => setFilters({ ...filters, scored: event.target.value })}
        >
          <option value="">Any score</option>
          <option value="low">Low faithfulness (under 70%)</option>
          <option value="yes">Scored</option>
          <option value="no">Not scored yet</option>
        </select>
        <span className="spacer" />
        <a className="button ghost" href={exportUrl("jsonl")} download>
          Export JSONL
        </a>
        <a className="button ghost" href={exportUrl("csv")} download>
          Export CSV
        </a>
      </div>

      {!loading && items.length === 0 ? (
        <p className="muted empty-note">No answers match. Ask a question in a knowledge base and it appears here.</p>
      ) : null}

      <div className="audit-list">
        {items.map((item) => (
          <Row key={item.id} item={item} onChange={replace} onRefresh={() => load()} />
        ))}
      </div>
      {items.length < total ? (
        <button type="button" className="secondary" onClick={() => load(items.length)} disabled={loading}>
          {loading ? "Loading…" : `Show more (${total - items.length} left)`}
        </button>
      ) : null}
    </div>
  )
}

function Stat({ label, value, note }) {
  return (
    <div className="stat">
      <span>{label}</span>
      <strong>{value}</strong>
      {note ? <small>{note}</small> : null}
    </div>
  )
}

function Row({ item, onChange, onRefresh }) {
  const [open, setOpen] = useState(false)
  const [detail, setDetail] = useState(null)
  const [scoring, setScoring] = useState(false)
  const [error, setError] = useState("")
  const shown = detail ? { ...item, ...detail } : item
  const reason = REASONS.find(([id]) => id === shown.rating_reason)?.[1]

  async function expand() {
    setOpen(!open)
    if (!open && !detail) {
      try {
        setDetail(await api.getInteraction(item.id))
      } catch (err) {
        setError(err.message)
      }
    }
  }

  async function score() {
    setScoring(true)
    setError("")
    try {
      const next = await api.scoreInteraction(item.id)
      setDetail(next)
      onChange(next)
    } catch (err) {
      setError(err.message)
    } finally {
      setScoring(false)
    }
  }

  function rated(next) {
    setDetail(next)
    onChange(next)
    onRefresh()
  }

  return (
    <article className={`audit-row${open ? " open" : ""}`}>
      <button type="button" className="audit-head" onClick={expand} aria-expanded={open}>
        <span className={`rating ${shown.rating || "none"}`} aria-label={shown.rating ? `Rated ${shown.rating}` : "Not rated"}>
          {shown.rating === "up" ? "▲" : shown.rating === "down" ? "▼" : "·"}
        </span>
        <span className="q">{item.question}</span>
        <span className="meta-line">
          {item.kb_name} · {new Date(item.created_at).toLocaleString()}
        </span>
        <span className="mini">
          {item.faithfulness !== null ? `G ${percent(item.faithfulness)}` : ""}
          {item.relevance !== null ? ` R ${percent(item.relevance)}` : ""}
        </span>
      </button>
      {open ? (
        <div className="audit-body">
          {error ? <p className="banner">{error}</p> : null}
          <p className="label">Answer</p>
          <p className="answer-text">{shown.answer}</p>
          {shown.rating === "down" && (reason || shown.rating_comment) ? (
            <p className="why-note">
              <b>{reason || "Thumbs down"}</b>
              {shown.rating_comment ? `: ${shown.rating_comment}` : ""}
            </p>
          ) : null}
          {detail?.citations?.length ? (
            <details>
              <summary>{detail.citations.length} cited passages</summary>
              <ol className="cited">
                {detail.citations.map((citation, index) => (
                  <li key={index}>
                    <small>
                      {citation.filename} · chunk {citation.chunk_index}
                    </small>
                    <p>{citation.text}</p>
                  </li>
                ))}
              </ol>
            </details>
          ) : null}
          {shown.judged_at || shown.judge_error ? <ScoreChips item={shown} /> : null}
          <p className="muted tiny">
            {[
              shown.source,
              shown.llm_model || "retrieval only",
              shown.rerank_model && `re-rank ${shown.rerank_model}`,
              `embed ${shown.embed_model}`,
              `chunks ${shown.chunk_size}/${shown.chunk_overlap}`,
              shown.nn_algorithm,
              `${(shown.elapsed_ms / 1000).toFixed(1)}s`,
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
          <div className="audit-actions">
            <Feedback key={shown.rating || "none"} interactionId={item.id} initial={shown} onChange={rated} />
            {shown.mode === "llm" ? (
              <button type="button" className="secondary" onClick={score} disabled={scoring}>
                {scoring ? "Scoring…" : shown.judged_at ? "Score again" : "Score"}
              </button>
            ) : null}
          </div>
        </div>
      ) : null}
    </article>
  )
}
