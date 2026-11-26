import { useEffect, useLayoutEffect, useRef, useState } from "react"
import { api } from "../api"
import { Icon } from "../components"
import { Answer, Highlight, queryTerms } from "../lib/text"

// Conversations survive switching between knowledge bases, not a page reload.
const threads = new Map()
const TOP_K = 5
let nextTurn = 0

export default function Chat({ id }) {
  const [kb, setKb] = useState(null)
  const [turns, setTurns] = useState(() => threads.get(id) || [])
  const [selected, setSelected] = useState(null)
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)
  const end = useRef(null)

  useEffect(() => {
    let active = true
    api
      .getKnowledgeBase(id)
      .then((next) => active && setKb(next))
      .catch((err) => active && setError(err.message))
    localStorage.setItem("ragflow:last-kb", id)
    return () => {
      active = false
    }
  }, [id])

  useEffect(() => {
    threads.set(id, turns)
  }, [id, turns])

  useLayoutEffect(() => {
    end.current?.scrollIntoView({ block: "end" })
  }, [turns])

  useEffect(() => {
    const onKey = (event) => event.key === "Escape" && setSelected(null)
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [])

  async function ask(question) {
    const turn = { id: ++nextTurn, question, status: "pending" }
    setTurns((current) => [...current, turn])
    setBusy(true)
    try {
      const result = await api.ask(id, { question, top_k: TOP_K })
      update(turn.id, { status: "done", result })
    } catch (err) {
      update(turn.id, { status: "error", error: err.message })
    } finally {
      setBusy(false)
    }
  }

  function update(turnId, changes) {
    setTurns((current) => current.map((turn) => (turn.id === turnId ? { ...turn, ...changes } : turn)))
  }

  function clear() {
    setTurns([])
    setSelected(null)
  }

  if (error && !kb) {
    return (
      <main className="chat">
        <div className="chat-main">
          <p className="banner">{error}</p>
        </div>
      </main>
    )
  }
  if (!kb) return <main className="chat" />

  const open = selected ? turns.find((turn) => turn.id === selected.turnId) : null

  return (
    <main className={`chat${open ? " with-panel" : ""}`}>
      <div className="chat-main">
        <header className="chat-head">
          <div>
            <h1>{kb.name}</h1>
            <p>
              {kb.document_count} {kb.document_count === 1 ? "document" : "documents"}
            </p>
          </div>
          <div className="row">
            {turns.length ? (
              <button type="button" className="ghost" onClick={clear}>
                New chat
              </button>
            ) : null}
            <a className="button ghost" href={`#/kb/${id}/manage`}>
              <Icon name="gear" /> Manage
            </a>
          </div>
        </header>

        <div className="thread">
          <div className="thread-inner">
            {turns.length === 0 ? <EmptyState kb={kb} /> : null}
            {turns.map((turn) => (
              <Turn
                key={turn.id}
                turn={turn}
                kbId={id}
                selected={selected?.turnId === turn.id ? selected.n : null}
                onSelect={(n) => setSelected({ turnId: turn.id, n })}
              />
            ))}
            <div ref={end} />
          </div>
        </div>

        <Composer onSend={ask} busy={busy} />
      </div>

      {open ? (
        <Passage
          turn={open}
          n={selected.n}
          onSelect={(n) => setSelected({ turnId: open.id, n })}
          onClose={() => setSelected(null)}
        />
      ) : null}
    </main>
  )
}

function EmptyState({ kb }) {
  return (
    <div className="empty-state">
      <h2>{kb.document_count ? `Ask ${kb.name}` : "Add documents to get started"}</h2>
      {kb.document_count ? (
        <p>Short questions work best. Answers cite the passages they came from.</p>
      ) : (
        <p>
          This knowledge base is empty. <a href={`#/kb/${kb.id}/manage`}>Upload documents</a> or load the sample.
        </p>
      )}
    </div>
  )
}

function Composer({ onSend, busy }) {
  const [value, setValue] = useState("")
  const box = useRef(null)

  useEffect(() => {
    box.current?.focus()
  }, [busy])

  function resize() {
    const element = box.current
    if (!element) return
    element.style.height = "auto"
    element.style.height = `${Math.min(element.scrollHeight, 168)}px`
  }

  function submit() {
    const question = value.trim()
    if (!question || busy) return
    onSend(question)
    setValue("")
    requestAnimationFrame(resize)
  }

  function onKeyDown(event) {
    // Enter sends. Shift+Enter makes a new line. Enter while composing (IME) is left alone.
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      submit()
    }
  }

  return (
    <div className="composer-wrap">
      <div className="composer">
        <textarea
          ref={box}
          rows={1}
          value={value}
          placeholder="Ask a question…"
          aria-label="Question"
          onChange={(event) => {
            setValue(event.target.value)
            resize()
          }}
          onKeyDown={onKeyDown}
          autoFocus
        />
        <button type="button" className="send" onClick={submit} disabled={!value.trim() || busy} aria-label="Send">
          <Icon name="send" />
        </button>
      </div>
      <p className="keys">
        <kbd>Enter</kbd> to send · <kbd>Shift</kbd> + <kbd>Enter</kbd> for a new line
      </p>
    </div>
  )
}

function Turn({ turn, kbId, selected, onSelect }) {
  const result = turn.result
  const citations = result?.citations || []
  const terms = queryTerms(turn.question)
  return (
    <article className="turn">
      <div className="question">{turn.question}</div>
      {turn.status === "pending" ? (
        <div className="thinking" aria-label="Searching">
          <span />
          <span />
          <span />
        </div>
      ) : null}
      {turn.status === "error" ? <p className="banner">{turn.error}</p> : null}
      {result ? (
        <div className="reply">
          {result.mode === "llm" ? (
            <div className="answer">
              <Answer text={result.answer} count={citations.length} onCite={onSelect} selected={selected} />
            </div>
          ) : null}
          {result.mode === "empty" ? (
            <p className="muted">
              {result.answer}{" "}
              {result.answer.includes("no documents") ? <a href={`#/kb/${kbId}/manage`}>Add documents</a> : null}
            </p>
          ) : null}
          {result.mode === "retrieval" ? <p className="lead">Closest passages</p> : null}
          {result.mode === "retrieval" ? (
            <div className="cards">
              {citations.map((item, index) => (
                <button
                  type="button"
                  key={index}
                  className={`passage-card${selected === index + 1 ? " active" : ""}`}
                  onClick={() => onSelect(index + 1)}
                >
                  <span className="source-label">
                    <b>{index + 1}</b> {item.filename} · chunk {item.chunk_index}
                  </span>
                  <span className="snippet">
                    <Highlight text={item.text} terms={terms} />
                  </span>
                </button>
              ))}
            </div>
          ) : null}
          {result.mode === "llm" && citations.length ? (
            <div className="sources">
              {citations.map((item, index) => (
                <button
                  type="button"
                  key={index}
                  className={`source${selected === index + 1 ? " active" : ""}`}
                  onClick={() => onSelect(index + 1)}
                  title={`${item.filename}, chunk ${item.chunk_index}`}
                >
                  <b>{index + 1}</b>
                  <span>{item.filename}</span>
                </button>
              ))}
            </div>
          ) : null}
          <Provenance result={result} />
        </div>
      ) : null}
    </article>
  )
}

function Provenance({ result }) {
  const parts = []
  if (result.mode === "llm") parts.push(result.llm.model)
  if (result.mode === "retrieval") parts.push("Retrieval only")
  if (result.rerank) parts.push(`re-ranked by ${result.rerank.model}`)
  if (result.citations.length) parts.push(`${result.citations.length} ${result.citations.length === 1 ? "passage" : "passages"}`)
  parts.push(`${(result.elapsed_ms / 1000).toFixed(1)}s`)
  return (
    <p className="prov">
      {parts.join(" · ")}
      {result.mode === "retrieval" ? (
        <>
          {" "}
          · <a href="#/models">Set an LLM</a> for written answers
        </>
      ) : null}
    </p>
  )
}

function Passage({ turn, n, onSelect, onClose }) {
  const citations = turn.result.citations
  const item = citations[n - 1]
  const terms = queryTerms(turn.question)
  const score = item.rerank_score !== undefined ? `re-rank ${item.rerank_score}` : `similarity ${item.score}`
  return (
    <aside className="passage" aria-label="Source passage">
      <header>
        <div>
          <p className="source-label">
            <b>{n}</b> of {citations.length}
          </p>
          <h2>{item.filename}</h2>
          <p className="muted">
            Chunk {item.chunk_index} · {score}
          </p>
        </div>
        <button type="button" className="ghost icon" onClick={onClose} aria-label="Close source">
          <Icon name="close" />
        </button>
      </header>
      <div className="passage-body">
        <Highlight text={item.text} terms={terms} />
      </div>
      <footer>
        <button type="button" className="ghost" onClick={() => onSelect(n - 1)} disabled={n <= 1}>
          <Icon name="chevronLeft" /> Previous
        </button>
        <button type="button" className="ghost" onClick={() => onSelect(n + 1)} disabled={n >= citations.length}>
          Next <Icon name="chevronRight" />
        </button>
      </footer>
    </aside>
  )
}
