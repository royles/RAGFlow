import { useCallback, useEffect, useState } from "react"
import { api } from "./api"
import { Icon } from "./components"
import Agents from "./pages/Agents"
import Chat from "./pages/Chat"
import Manage from "./pages/Manage"
import Models from "./pages/Models"
import NewKnowledgeBase from "./pages/NewKnowledgeBase"

function readRoute() {
  const hash = window.location.hash.replace(/^#/, "") || "/"
  const manage = hash.match(/^\/kb\/([^/]+)\/manage$/)
  if (manage) return { name: "manage", id: decodeURIComponent(manage[1]) }
  const chat = hash.match(/^\/kb\/([^/]+)$/)
  if (chat) return { name: "chat", id: decodeURIComponent(chat[1]) }
  if (hash === "/new") return { name: "new" }
  if (hash === "/models") return { name: "models" }
  if (hash === "/agents") return { name: "agents" }
  return { name: "home" }
}

export default function App() {
  const [route, setRoute] = useState(readRoute)
  const [bases, setBases] = useState([])
  const [loaded, setLoaded] = useState(false)

  const refresh = useCallback(
    () =>
      api
        .listKnowledgeBases()
        .then((result) => setBases(result.knowledge_bases))
        .catch(() => {})
        .finally(() => setLoaded(true)),
    [],
  )

  useEffect(() => {
    refresh()
    const onChange = () => setRoute(readRoute())
    window.addEventListener("hashchange", onChange)
    return () => window.removeEventListener("hashchange", onChange)
  }, [refresh])

  useEffect(() => {
    if (route.name !== "home" || !loaded || bases.length === 0) return
    const last = localStorage.getItem("ragflow:last-kb")
    const target = bases.find((item) => item.id === last) || bases[0]
    window.location.replace(`#/kb/${target.id}`)
  }, [route, loaded, bases])

  const activeKb = route.name === "chat" || route.name === "manage" ? route.id : null

  return (
    <div className="app">
      <aside className="sidebar">
        <a className="brand" href="#/">
          <span className="logo" aria-hidden="true" />
          RAGFlow
        </a>
        <a className="button new" href="#/new">
          <Icon name="plus" /> New knowledge base
        </a>
        <p className="label">Knowledge bases</p>
        <nav className="kb-list" aria-label="Knowledge bases">
          {bases.map((item) => (
            <a key={item.id} href={`#/kb/${item.id}`} aria-current={item.id === activeKb ? "page" : undefined}>
              <Icon name="book" size={15} />
              <span>{item.name}</span>
              <small>{item.document_count}</small>
            </a>
          ))}
          {loaded && bases.length === 0 ? <p className="muted small-note">None yet</p> : null}
        </nav>
        <nav className="foot" aria-label="Settings">
          <a href="#/models" aria-current={route.name === "models" ? "page" : undefined}>
            <Icon name="cpu" size={15} />
            <span>Models</span>
          </a>
          <a href="#/agents" aria-current={route.name === "agents" ? "page" : undefined}>
            <Icon name="plug" size={15} />
            <span>Agents (MCP)</span>
          </a>
        </nav>
      </aside>
      <div className="stage">
        {route.name === "chat" ? <Chat key={route.id} id={route.id} /> : null}
        {route.name === "manage" ? (
          <div className="page">
            <Manage key={route.id} id={route.id} onChange={refresh} />
          </div>
        ) : null}
        {route.name === "new" ? (
          <div className="page">
            <NewKnowledgeBase onChange={refresh} />
          </div>
        ) : null}
        {route.name === "models" ? (
          <div className="page">
            <Models />
          </div>
        ) : null}
        {route.name === "agents" ? (
          <div className="page">
            <Agents />
          </div>
        ) : null}
        {route.name === "home" && loaded && bases.length === 0 ? (
          <div className="page">
            <NewKnowledgeBase welcome onChange={refresh} />
          </div>
        ) : null}
      </div>
    </div>
  )
}
