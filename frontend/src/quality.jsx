import { useEffect, useState } from "react"
import { api } from "./api"
import { Icon } from "./components"

export const REASONS = [
  ["incorrect", "Incorrect"],
  ["incomplete", "Incomplete"],
  ["wrong_sources", "Wrong sources"],
  ["unclear", "Unclear"],
  ["other", "Other"],
]

export function percent(value) {
  return value === null || value === undefined ? "–" : `${Math.round(value * 100)}%`
}

function tone(value) {
  if (value === null || value === undefined) return "none"
  return value >= 0.8 ? "good" : value >= 0.5 ? "fair" : "poor"
}

// Thumbs up/down for one answer. Every click is stored as a new event, so
// changing your mind is allowed and the history is kept.
export function Feedback({ interactionId, initial = null, onChange }) {
  const [rating, setRating] = useState(initial?.rating || null)
  const [reason, setReason] = useState(initial?.rating_reason || "")
  const [comment, setComment] = useState(initial?.rating_comment || "")
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState("")

  async function send(next, extra = {}) {
    setError("")
    try {
      const item = await api.rate(interactionId, { rating: next, ...extra })
      setRating(item.rating)
      setSaved(true)
      onChange?.(item)
    } catch (err) {
      setError(err.message)
    }
  }

  function vote(next) {
    setSaved(false)
    if (rating === next) {
      setReason("")
      setComment("")
      return send("none")
    }
    return send(next)
  }

  return (
    <div className="feedback">
      <div className="thumbs" role="group" aria-label="Rate this answer">
        <button
          type="button"
          className={`thumb${rating === "up" ? " on up" : ""}`}
          aria-pressed={rating === "up"}
          aria-label="Good answer"
          title="Good answer"
          onClick={() => vote("up")}
        >
          <Icon name="thumbUp" size={15} />
        </button>
        <button
          type="button"
          className={`thumb${rating === "down" ? " on down" : ""}`}
          aria-pressed={rating === "down"}
          aria-label="Bad answer"
          title="Bad answer"
          onClick={() => vote("down")}
        >
          <Icon name="thumbDown" size={15} />
        </button>
        {saved && rating ? <span className="saved">Thanks, saved</span> : null}
      </div>
      {rating === "down" ? (
        <div className="why">
          <div className="chips" role="group" aria-label="What was wrong">
            {REASONS.map(([id, label]) => (
              <button
                type="button"
                key={id}
                className={`chip${reason === id ? " on" : ""}`}
                aria-pressed={reason === id}
                onClick={() => {
                  const next = reason === id ? "" : id
                  setReason(next)
                  send("down", { reason: next, comment })
                }}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="comment">
            <input
              value={comment}
              maxLength={1000}
              placeholder="What should it have said? (optional)"
              aria-label="Comment"
              onChange={(event) => setComment(event.target.value)}
              onKeyDown={(event) => event.key === "Enter" && send("down", { reason, comment })}
            />
            <button type="button" className="secondary" onClick={() => send("down", { reason, comment })}>
              Save
            </button>
          </div>
        </div>
      ) : null}
      {error ? <p className="banner">{error}</p> : null}
    </div>
  )
}

export function ScoreChips({ item }) {
  const [open, setOpen] = useState(false)
  const claims = item.faithfulness_claims || []
  const unsupported = claims.filter((claim) => !claim.supported)
  return (
    <div className="scores">
      <div className="chips">
        <button
          type="button"
          className={`score ${tone(item.faithfulness)}`}
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          title="Share of the answer's claims that its sources support"
          disabled={item.faithfulness === null}
        >
          Grounded {percent(item.faithfulness)}
          {unsupported.length ? ` · ${unsupported.length} unsupported` : ""}
        </button>
        <span className={`score ${tone(item.relevance)}`} title={item.relevance_reason || "How directly it answers the question"}>
          Relevant {percent(item.relevance)}
        </span>
        {item.judge_model ? <span className="muted tiny">judged by {item.judge_model}</span> : null}
      </div>
      {open ? (
        <ul className="claims">
          {claims.length === 0 ? <li className="muted">The answer made no factual claims to check.</li> : null}
          {claims.map((claim, index) => (
            <li key={index} className={claim.supported ? "ok" : "bad"}>
              <b aria-label={claim.supported ? "Supported" : "Not supported"}>{claim.supported ? "✓" : "✗"}</b>
              <span>
                {claim.claim}
                {claim.source ? <small> [{claim.source}]</small> : null}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
      {item.judge_error ? <p className="muted tiny">{item.judge_error}</p> : null}
    </div>
  )
}

// Scores arrive after the answer, so this polls until the judge has finished.
export function Scores({ interactionId }) {
  const [item, setItem] = useState(null)
  const [gaveUp, setGaveUp] = useState(false)

  useEffect(() => {
    let active = true
    let tries = 0
    async function poll() {
      try {
        const next = await api.getInteraction(interactionId)
        if (!active) return
        if (next.judged_at || next.judge_error) return setItem(next)
      } catch {
        // keep trying
      }
      if (++tries >= 60) return active && setGaveUp(true)
      setTimeout(poll, 1500)
    }
    poll()
    return () => {
      active = false
    }
  }, [interactionId])

  if (item) return <ScoreChips item={item} />
  if (gaveUp) return <p className="muted tiny">Scoring is taking a while. See the Quality page.</p>
  return <p className="muted tiny scoring">Scoring…</p>
}
