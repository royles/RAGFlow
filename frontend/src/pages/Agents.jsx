import { useEffect, useState } from "react"
import { api } from "../api"

export default function Agents() {
  const [agent, setAgent] = useState(null)
  const [error, setError] = useState("")

  useEffect(() => {
    api
      .agent()
      .then(setAgent)
      .catch((err) => setError(err.message))
  }, [])

  return (
    <main className="workspace narrow">
      <div className="section-head">
        <h1>Agents</h1>
        <p>Model agents can call RAGFlow over MCP and get the same grounded answers as this app.</p>
      </div>
      {error ? <p className="banner">{error}</p> : null}
      {agent ? (
        <section className="panel">
          <p>
            Endpoint <code>{agent.mcp_path}</code> on this application’s URL ({agent.transport}).
          </p>
          <ul className="tools">
            {agent.tools.map((tool) => (
              <li key={tool.name}>
                <code>{tool.name}</code>
                <span>{tool.description}</span>
              </li>
            ))}
          </ul>
          <pre className="snippet-code">{`{
  "mcpServers": {
    "ragflow": { "url": "${window.location.origin}${agent.mcp_path}" }
  }
}`}</pre>
        </section>
      ) : null}
    </main>
  )
}
