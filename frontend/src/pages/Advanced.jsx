import { useEffect, useState } from "react"
import { api } from "../api"

const SOURCES = {
  saved: "Saved here",
  environment: "From the environment",
  default: "Default",
}

export default function Advanced() {
  const [data, setData] = useState(null)
  const [draft, setDraft] = useState({})
  const [clear, setClear] = useState([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [notice, setNotice] = useState("")

  function adopt(next) {
    setData(next)
    const values = {}
    for (const group of next.groups) for (const item of group.settings) values[item.key] = item.value
    setDraft(values)
    setClear([])
  }

  useEffect(() => {
    api
      .advanced()
      .then(adopt)
      .catch((err) => setError(err.message))
  }, [])

  const all = data ? data.groups.flatMap((group) => group.settings) : []

  function changed(item) {
    if (item.type === "secret") return Boolean(draft[item.key]) || clear.includes(item.key)
    return (draft[item.key] || "") !== item.value
  }

  async function onSave(event) {
    event.preventDefault()
    setBusy(true)
    setError("")
    setNotice("")
    const restart = all.filter((item) => item.restart && changed(item)).map((item) => item.label)
    try {
      adopt(await api.saveAdvanced({ values: draft, clear }))
      setNotice(
        restart.length
          ? `Saved. Restart the application for these to take effect: ${restart.join(", ")}.`
          : "Saved. The change applies now, and is re-applied whenever the app restarts.",
      )
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  if (!data) return <main className="workspace">{error ? <p className="banner">{error}</p> : <p className="muted">Loading…</p>}</main>

  const dirty = all.some(changed)

  return (
    <form className="workspace narrow" onSubmit={onSave}>
      <div className="section-head">
        <h1>Advanced</h1>
        <p>
          Overrides for the environment variables RAGFlow reads. A value saved here wins over the variable, and is kept on
          the server and put back into the environment each time the app starts. Clear a field to go back to the
          environment or the default.
        </p>
      </div>
      {error ? <p className="banner">{error}</p> : null}
      {notice ? <p className="notice">{notice}</p> : null}

      {data.groups.map((group) => (
        <section className="panel" key={group.name}>
          <h2>{group.name}</h2>
          {group.settings.map((item) => (
            <Setting
              key={item.key}
              item={item}
              value={draft[item.key] || ""}
              removing={clear.includes(item.key)}
              onChange={(value) => setDraft((current) => ({ ...current, [item.key]: value }))}
              onRemove={() => setClear((current) => (current.includes(item.key) ? current.filter((key) => key !== item.key) : [...current, item.key]))}
            />
          ))}
        </section>
      ))}

      <div className="row">
        <button type="submit" disabled={busy || !dirty}>
          {busy ? "Saving…" : "Save"}
        </button>
        {dirty ? (
          <button type="button" className="ghost" onClick={() => adopt(data)}>
            Discard changes
          </button>
        ) : null}
      </div>

      <section className="panel">
        <h2>Set by the environment</h2>
        <p className="hint">Fixed when the app starts, so they are shown here for reference only.</p>
        {data.environment.map((item) => (
          <div className="setting" key={item.env}>
            <div className="setting-head">
              <strong>{item.label}</strong>
              <code>{item.env}</code>
            </div>
            <p className="value-line">{item.value || <span className="muted">Not set</span>}</p>
            <p className="hint">{item.help}</p>
          </div>
        ))}
      </section>
    </form>
  )
}

function Setting({ item, value, removing, onChange, onRemove }) {
  const secret = item.type === "secret"
  const placeholder = secret
    ? item.is_set
      ? "Saved. Type to replace."
      : "Not set"
    : item.env_value || (item.type === "int" ? String(item.default) : "")
  const source = secret ? (item.source === "default" ? "Default" : SOURCES[item.source]) : SOURCES[item.source]
  return (
    <div className="setting">
      <div className="setting-head">
        <label htmlFor={`adv-${item.key}`}>{item.label}</label>
        <code>{item.env}</code>
        <span className={`badge ${item.source}`}>{source}</span>
        {item.restart ? <span className="badge restart">Applies after restart</span> : null}
      </div>
      <div className="setting-input">
        <input
          id={`adv-${item.key}`}
          type={secret ? "password" : "text"}
          inputMode={item.type === "int" ? "numeric" : undefined}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder={placeholder}
          autoComplete="off"
          disabled={removing}
        />
        {!secret && item.value && value === item.value ? (
          <button type="button" className="ghost" onClick={() => onChange("")}>
            Reset
          </button>
        ) : null}
      </div>
      {secret && item.source === "saved" ? (
        <span className="inline">
          <input type="checkbox" checked={removing} onChange={onRemove} /> Remove the saved value on save
        </span>
      ) : null}
      <p className="hint">
        {item.help}
        {!secret && item.source === "environment" && !item.value ? ` Currently ${item.env_value} from the environment.` : ""}
      </p>
    </div>
  )
}
