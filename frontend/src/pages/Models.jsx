import { useEffect, useState } from "react"
import { api } from "../api"
import { Field } from "./Library"

const ROLES = [
  {
    id: "llm",
    title: "LLM",
    blurb: "Writes the answer from the retrieved passages. With none, answers are the passages themselves.",
  },
  {
    id: "embedding",
    title: "Embedding model",
    blurb: "Turns text into vectors. A knowledge base keeps the model it was created with.",
  },
  {
    id: "rerank",
    title: "Re-rank model",
    blurb: "Reorders the retrieved passages by relevance before the answer is written.",
  },
]

const EMPTY_SECRETS = { access_key_id: "", secret_access_key: "", session_token: "", api_key: "" }

export default function Models() {
  const [catalog, setCatalog] = useState(null)
  const [saved, setSaved] = useState(null)
  const [draft, setDraft] = useState(null)
  const [secrets, setSecrets] = useState(EMPTY_SECRETS)
  const [clear, setClear] = useState([])
  const [results, setResults] = useState({})
  const [testing, setTesting] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [notice, setNotice] = useState("")

  function adopt(next) {
    setSaved(next)
    setDraft({
      region: next.bedrock.region,
      base_url: next.openai.base_url,
      llm: { ...next.llm },
      embedding: { ...next.embedding },
      rerank: { ...next.rerank },
    })
    setSecrets(EMPTY_SECRETS)
    setClear([])
  }

  useEffect(() => {
    let active = true
    Promise.all([api.catalog(), api.getModels()])
      .then(([nextCatalog, nextModels]) => {
        if (!active) return
        setCatalog(nextCatalog)
        adopt(nextModels)
      })
      .catch((err) => active && setError(err.message))
    return () => {
      active = false
    }
  }, [])

  function payload() {
    const bedrock = { region: draft.region }
    for (const key of ["access_key_id", "secret_access_key", "session_token"]) bedrock[key] = secrets[key]
    return {
      bedrock,
      openai: { base_url: draft.base_url, api_key: secrets.api_key },
      llm: draft.llm,
      embedding: draft.embedding,
      rerank: draft.rerank,
      clear,
    }
  }

  function setCredential(event) {
    const { name, value } = event.target
    setSecrets((current) => ({ ...current, [name]: value }))
  }

  function toggleClear(name) {
    setClear((current) => (current.includes(name) ? current.filter((item) => item !== name) : [...current, name]))
  }

  function setRole(role, changes) {
    setDraft((current) => ({ ...current, [role]: { ...current[role], ...changes } }))
    setResults((current) => ({ ...current, [role]: undefined }))
  }

  function changeProvider(role, provider) {
    const first = catalog.model_suggestions[role]?.[provider]?.[0]
    const changes = { provider, model: first ? first.model : "" }
    if (role === "embedding") changes.dimensions = provider === "local" ? 384 : first?.dimensions || 1024
    if (provider === "none" || provider === "local") changes.model = provider === "local" ? "local-hashing" : ""
    setRole(role, changes)
  }

  function changeModel(role, model) {
    const changes = { model }
    if (role === "embedding") {
      const match = catalog.model_suggestions.embedding[draft.embedding.provider]?.find((item) => item.model === model)
      if (match) changes.dimensions = match.dimensions
    }
    setRole(role, changes)
  }

  async function testRole(role) {
    setTesting(role)
    setError("")
    try {
      const result = await api.testModel(role, payload())
      setResults((current) => ({ ...current, [role]: result }))
    } catch (err) {
      setResults((current) => ({ ...current, [role]: { ok: false, detail: err.message } }))
    } finally {
      setTesting("")
    }
  }

  async function testAll() {
    for (const role of ROLES) await testRole(role.id)
  }

  async function onSave(event) {
    event.preventDefault()
    setBusy(true)
    setError("")
    setNotice("")
    try {
      adopt(await api.saveModels(payload()))
      setNotice("Saved. The LLM and re-rank model apply to the next question.")
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  if (!saved || !draft || !catalog) {
    return <main className="layout single">{error ? <p className="banner">{error}</p> : <p>Loading…</p>}</main>
  }

  const usesBedrock = ROLES.some((role) => draft[role.id].provider === "bedrock")
  const usesOpenai = ROLES.some((role) => draft[role.id].provider === "openai")

  return (
    <form className="workspace models" onSubmit={onSave}>
      <div className="section-head">
        <p className="crumb">
          <a href="#/">Knowledge bases</a>
        </p>
        <h1>Models</h1>
        <p>Credentials and the three models RAGFlow uses. Secrets are stored on the server and never shown again.</p>
      </div>
      {error ? <p className="banner">{error}</p> : null}
      {notice ? <p className="notice">{notice}</p> : null}

      <div className="columns even">
        <section className="panel">
          <h2>AWS Bedrock</h2>
          <Field
            label="Default region"
            hint={`Used by any Bedrock model below without its own region. Blank uses ${saved.bedrock.effective_region} from the environment or the default.`}
          >
            <input value={draft.region} onChange={(event) => setDraft({ ...draft, region: event.target.value })} placeholder={saved.bedrock.effective_region} />
          </Field>
          <Secret
            label="Access key id"
            name="access_key_id"
            isSet={saved.bedrock.access_key_id_set}
            value={secrets.access_key_id}
            onChange={setCredential}
            cleared={clear.includes("bedrock.access_key_id")}
            onClear={() => toggleClear("bedrock.access_key_id")}
          />
          <Secret
            label="Secret access key"
            name="secret_access_key"
            isSet={saved.bedrock.secret_access_key_set}
            value={secrets.secret_access_key}
            onChange={setCredential}
            cleared={clear.includes("bedrock.secret_access_key")}
            onClear={() => toggleClear("bedrock.secret_access_key")}
          />
          <Secret
            label="Session token"
            name="session_token"
            isSet={saved.bedrock.session_token_set}
            value={secrets.session_token}
            onChange={setCredential}
            cleared={clear.includes("bedrock.session_token")}
            onClear={() => toggleClear("bedrock.session_token")}
          />
          <p className="hint">
            {saved.bedrock.credential_source === "saved"
              ? "Using the saved keys."
              : "No keys saved: the Cloudera AI runtime role or AWS environment credentials are used."}
          </p>
        </section>

        <section className="panel">
          <h2>OpenAI client API</h2>
          <Field
            label="Default base URL"
            hint={
              saved.openai.effective_base_url && !draft.base_url
                ? `Blank uses ${saved.openai.effective_base_url} from the environment. Each model below can set its own.`
                : "Used by any OpenAI model below that has no base URL of its own. Blank means https://api.openai.com/v1."
            }
          >
            <input value={draft.base_url} onChange={(event) => setDraft({ ...draft, base_url: event.target.value })} placeholder="https://api.openai.com/v1" />
          </Field>
          <Secret
            label="API key"
            name="api_key"
            isSet={saved.openai.api_key_set}
            value={secrets.api_key}
            onChange={setCredential}
            cleared={clear.includes("openai.api_key")}
            onClear={() => toggleClear("openai.api_key")}
          />
          <p className="hint">
            {saved.openai.api_key_source === "saved" && "Using the saved key."}
            {saved.openai.api_key_source === "environment" && "No key saved: OPENAI_API_KEY from the environment is used."}
            {saved.openai.api_key_source === "none" && "No key yet. A server that needs none can be used with just a base URL."}
          </p>
        </section>
      </div>

      {ROLES.map((role) => (
        <RoleCard
          key={role.id}
          role={role}
          catalog={catalog}
          value={draft[role.id]}
          fallbackRegion={draft.region || saved.bedrock.effective_region}
          fallbackUrl={draft.base_url || saved.openai.effective_base_url || "https://api.openai.com/v1"}
          result={results[role.id]}
          testing={testing === role.id}
          onBaseUrl={(baseUrl) => setRole(role.id, { base_url: baseUrl })}
          onRegion={(region) => setRole(role.id, { region })}
          onProvider={(provider) => changeProvider(role.id, provider)}
          onModel={(model) => changeModel(role.id, model)}
          onDimensions={(dimensions) => setRole(role.id, { dimensions })}
          onTest={() => testRole(role.id)}
        />
      ))}

      <section className="panel">
        <div className="row">
          <button type="submit" disabled={busy}>
            {busy ? "Saving…" : "Save settings"}
          </button>
          <button type="button" className="secondary" onClick={testAll} disabled={Boolean(testing)}>
            {testing ? "Testing…" : "Test all"}
          </button>
        </div>
        <p className="hint">
          Tests send one small real request using what is on this page, saved or not. A key left blank keeps the saved
          one.
          {usesBedrock ? " Bedrock re-ranking is only offered in some regions, and each model must be enabled in your AWS account." : ""}
          {usesOpenai ? " OpenAI re-ranking calls POST {base URL}/rerank, the shape used by Cohere, Jina and vLLM." : ""}
        </p>
      </section>
    </form>
  )
}

function Secret({ label, name, isSet, value, onChange, cleared, onClear }) {
  return (
    <Field label={label}>
      <input
        type="password"
        name={name}
        value={value}
        onChange={onChange}
        autoComplete="off"
        placeholder={isSet ? "Saved. Type to replace." : "Not set"}
        disabled={cleared}
      />
      {isSet ? (
        <span className="inline">
          <input type="checkbox" checked={cleared} onChange={onClear} /> Remove the saved value on save
        </span>
      ) : null}
    </Field>
  )
}

function RoleCard({ role, catalog, value, fallbackRegion, fallbackUrl, result, testing, onProvider, onBaseUrl, onRegion, onModel, onDimensions, onTest }) {
  const providers = catalog.providers[role.id]
  const suggestions = catalog.model_suggestions[role.id]?.[value.provider] || []
  const fixed = value.provider === "none" || value.provider === "local"
  const listId = `models-${role.id}`
  return (
    <section className="panel role">
      <h2>{role.title}</h2>
      <p className="hint">{role.blurb}</p>
      <div className="split">
        <Field label="Provider">
          <select value={value.provider} onChange={(event) => onProvider(event.target.value)}>
            {providers.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Model id" hint={fixed ? "Nothing to set." : "Pick a suggestion or type any model id."}>
          <input
            value={value.model}
            onChange={(event) => onModel(event.target.value)}
            list={listId}
            disabled={fixed}
            placeholder={fixed ? "" : "Model id"}
          />
          <datalist id={listId}>
            {suggestions.map((item) => (
              <option key={item.model} value={item.model}>
                {item.label}
              </option>
            ))}
          </datalist>
        </Field>
      </div>
      {value.provider === "bedrock" ? (
        <Field
          label="AWS region"
          hint={
            value.region
              ? "This model is called in this region."
              : `Blank uses the Bedrock region above: ${fallbackRegion}.${
                  role.id === "rerank" ? " Re-rank models are only offered in some regions, such as us-west-2." : ""
                }`
          }
        >
          <input value={value.region || ""} onChange={(event) => onRegion(event.target.value)} placeholder={fallbackRegion} />
        </Field>
      ) : null}
      {value.provider === "openai" ? (
        <Field
          label="Base URL"
          hint={value.base_url ? "This model is called at this address." : `Blank uses the default base URL: ${fallbackUrl}`}
        >
          <input value={value.base_url || ""} onChange={(event) => onBaseUrl(event.target.value)} placeholder={fallbackUrl} />
        </Field>
      ) : null}
      {role.id === "embedding" && !fixed ? (
        <Field label="Dimensions" hint="Must match what the model returns. The test checks this.">
          <input type="number" min={8} max={4096} value={value.dimensions} onChange={(event) => onDimensions(event.target.value)} />
        </Field>
      ) : null}
      <div className="row">
        <button type="button" className="secondary" onClick={onTest} disabled={testing}>
          {testing ? "Testing…" : "Test"}
        </button>
        {result ? <Result result={result} /> : null}
      </div>
    </section>
  )
}

function Result({ result }) {
  const status = result.skipped ? "skip" : result.ok ? "ok" : "fail"
  const label = result.skipped ? "Nothing to test" : result.ok ? "Works" : "Failed"
  return (
    <p className={`result ${status}`} role="status">
      <strong>{label}</strong>
      {result.latency_ms ? ` · ${result.latency_ms} ms` : ""} · {result.detail}
    </p>
  )
}
