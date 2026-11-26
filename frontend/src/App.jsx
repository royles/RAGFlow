import { useEffect, useState } from "react"
import Library from "./pages/Library"
import KnowledgeBase from "./pages/KnowledgeBase"
import Models from "./pages/Models"

function readRoute() {
  const hash = window.location.hash.replace(/^#/, "") || "/"
  const match = hash.match(/^\/kb\/([^/]+)$/)
  if (match) return { name: "kb", id: decodeURIComponent(match[1]) }
  if (hash === "/models") return { name: "models" }
  return { name: "library" }
}

export default function App() {
  const [route, setRoute] = useState(readRoute)

  useEffect(() => {
    const onChange = () => setRoute(readRoute())
    window.addEventListener("hashchange", onChange)
    return () => window.removeEventListener("hashchange", onChange)
  }, [])

  return (
    <div className="app">
      <header className="topbar">
        <a className="brand" href="#/">
          RAGFlow
        </a>
        <p>Knowledge bases for Cloudera AI</p>
        <nav>
          <a href="#/" aria-current={route.name !== "models" ? "page" : undefined}>
            Knowledge bases
          </a>
          <a href="#/models" aria-current={route.name === "models" ? "page" : undefined}>
            Models
          </a>
        </nav>
      </header>
      {route.name === "kb" ? <KnowledgeBase key={route.id} id={route.id} /> : null}
      {route.name === "models" ? <Models /> : null}
      {route.name === "library" ? <Library /> : null}
    </div>
  )
}
