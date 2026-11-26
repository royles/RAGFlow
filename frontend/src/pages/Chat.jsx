import { useEffect, useLayoutEffect, useRef, useState } from "react"
import { api } from "../api"
import { Icon } from "../components"
import { Feedback, Scores } from "../quality"
import { Answer, Highlight, queryTerms } from "../lib/text"

const TOP_K = 5
let nextTurn = 0

// The saved chat to reopen for a knowledge base. "new" means the user left on an empty chat.
const rememberKey = (id) => `ragflow:chat:${id}`

const fromServer = (turn) => ({ id: `saved-${turn.id}`, question: turn.question, status: "done", result: turn.result, interaction: turn.interaction })

function when(stamp) {
  return new Date(stamp).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
}

export default function Chat({ id }) {
  const [kb, setKb] = useState(null)
  const [chatId, setChatId] = useState(null)
  const [chats, setChats] = useState([])
  const [turns, setTurns] = useState([])
  const [selected, setSelected] = useState(null)
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [history, setHistory] = useState(false)
  const end = useRef(null)

  async function openChat(chat) {
    const saved = await api.getConversation(chat)
    setChatId(chat)
    setTurns(saved.turns.map(fromServer))
    setSelected(null)
    localStorage.setItem(rememberKey(id), chat)
  }

  const refreshChats = () =>
    api
      .listConversations(id)
      .then((result) => {
        setChats(result.conversations)
        return result.conversations
      })
      .catch(() => [])

  useEffect(() => {
    let active = true
    localStorage.setItem("ragflow:last-kb", id)
    async function load() {
      try {
        const [next, list] = await Promise.all([api.getKnowledgeBase(id), api.listConversations(id)])
        if (!active) return
        setKb(next)
        setChats(list.conversations)
        const remembered = localStorage.getItem(rememberKey(id))
        const target = remembered === "new" ? null : list.conversations.find((chat) => chat.id === remembered) || list.conversations[0]
        if (target) await openChat(target.id)
      } catch (err) {
        if (active) setError(err.message)
      } finally {
        if (active) setLoading(false)
      }
    }
    load()
    return () => {
      active = false
    }
  }, [id])

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
      const result = await api.ask(id, { question, top_k: TOP_K, conversation_id: chatId || undefined })
      update(turn.id, { status: "done", result })
      if (result.conversation_id) {
        setChatId(result.conversation_id)
        localStorage.setItem(rememberKey(id), result.conversation_id)
        refreshChats()
      }
    } catch (err) {
      update(turn.id, { status: "error", error: err.message })
    } finally {
      setBusy(false)
    }
  }

  function update(turnId, changes) {
    setTurns((current) => current.map((turn) => (turn.id === turnId ? { ...turn, ...changes } : turn)))
  }

  function newChat() {
    setChatId(null)
    setTurns([])
    setSelected(null)
    setHistory(false)
    localStorage.setItem(rememberKey(id), "new")
  }

  async function removeChat(chat) {
    if (!window.confirm(`Delete this chat? ${chat.title}`)) return
    try {
      await api.deleteConversation(chat.id)
      if (chat.id === chatId) newChat()
      refreshChats()
    } catch (err) {
      setError(err.message)
    }
  }

  async function switchChat(chat) {
    setHistory(false)
    try {
      await openChat(chat.id)
    } catch (err) {
      setError(err.message)
    }
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
  if (!kb || loading) return <main className="chat" />

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
            {chats.length ? (
              <div className="history-wrap">
                <button type="button" className="ghost" onClick={() => setHistory(!history)} aria-expanded={history}>
                  <Icon name="history" /> History
                </button>
                {history ? (
                  <>
                    <div className="history-backdrop" onClick={() => setHistory(false)} />
                    <ul className="history" aria-label="Saved chats">
                      {chats.map((chat) => (
                        <li key={chat.id} className={chat.id === chatId ? "current" : ""}>
                          <button type="button" className="open" onClick={() => switchChat(chat)}>
                            <span className="title">{chat.title}</span>
                            <small>
                              {when(chat.updated_at)} · {chat.turns} {chat.turns === 1 ? "question" : "questions"}
                            </small>
                          </button>
                          <button type="button" className="ghost icon" aria-label={`Delete chat: ${chat.title}`} onClick={() => removeChat(chat)}>
                            <Icon name="trash" size={14} />
                          </button>
                        </li>
                      ))}
                    </ul>
                  </>
                ) : null}
              </div>
            ) : null}
            {turns.length ? (
              <button type="button" className="ghost" onClick={newChat}>
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
          {result.interaction_id ? (
            <div className="review">
              <Feedback interactionId={result.interaction_id} initial={turn.interaction} />
              {result.mode === "llm" ? (
                <Scores interactionId={result.interaction_id} auto={result.scoring} initial={turn.interaction} />
              ) : null}
            </div>
          ) : null}
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
