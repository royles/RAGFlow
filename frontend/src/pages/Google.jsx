import { useEffect, useState } from "react"
import { api } from "../api"
import { Field, Icon } from "../components"

export default function Google() {
  const [status, setStatus] = useState(null)
  const [clientId, setClientId] = useState("")
  const [secret, setSecret] = useState("")
  const [busy, setBusy] = useState("")
  const [error, setError] = useState("")
  const [notice, setNotice] = useState("")
  const redirectUri = `${window.location.origin}/api/google/callback`

  function adopt(next) {
    setStatus(next)
    setClientId(next.client_id)
    setSecret("")
  }

  useEffect(() => {
    // Google sends the browser back here with the outcome in the address.
    const query = new URLSearchParams(window.location.hash.split("?")[1] || "")
    if (query.get("connected")) setNotice("Google Drive is connected.")
    if (query.get("error")) setError(query.get("error"))
    if (query.size) history.replaceState(null, "", "#/google")
    api
      .google()
      .then(adopt)
      .catch((err) => setError(err.message))
  }, [])

  async function run(label, work) {
    setBusy(label)
    setError("")
    setNotice("")
    try {
      await work()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy("")
    }
  }

  const onSave = (event) => {
    event.preventDefault()
    run("save", async () => {
      adopt(await api.saveGoogle({ client_id: clientId, client_secret: secret }))
      setNotice("Saved.")
    })
  }

  const onConnect = () =>
    run("connect", async () => {
      const { url } = await api.googleConnect(redirectUri)
      window.location.assign(url)
    })

  const onDisconnect = () =>
    run("disconnect", async () => {
      adopt(await api.googleDisconnect())
      setNotice("Disconnected. RAGFlow no longer has access to your Drive.")
    })

  if (!status) {
    return <main className="workspace">{error ? <p className="banner">{error}</p> : <p className="muted">Loading…</p>}</main>
  }

  return (
    <div className="workspace narrow">
      <div className="section-head">
        <h1>Google Drive</h1>
        <p>Pick files from Drive and load them into a knowledge base. Access is read-only: RAGFlow can read files but never change or delete them.</p>
      </div>
      {error ? <p className="banner">{error}</p> : null}
      {notice ? <p className="notice">{notice}</p> : null}

      {status.connected ? (
        <section className="panel">
          <h2>
            <Icon name="drive" /> Connected
          </h2>
          <p>
            {status.account?.email ? (
              <>
                Signed in as <strong>{status.account.name || status.account.email}</strong>
                {status.account.name ? ` (${status.account.email})` : ""}.
              </>
            ) : (
              "Signed in."
            )}
          </p>
          <p className="hint">
            One Google account is connected for the whole app, so anyone who can open RAGFlow can import files from it. In a
            knowledge base, choose <b>Manage</b>, then <b>Google Drive</b>.
          </p>
          <div className="row">
            <button type="button" className="secondary" onClick={onDisconnect} disabled={Boolean(busy)}>
              {busy === "disconnect" ? "Disconnecting…" : "Disconnect"}
            </button>
          </div>
        </section>
      ) : (
        <section className="panel">
          <h2>Connect</h2>
          <p className="hint">
            {status.configured
              ? "Sign in with Google and allow read-only access to Drive."
              : "Save an OAuth client below first, then connect."}
          </p>
          <div className="row">
            <button type="button" onClick={onConnect} disabled={!status.configured || Boolean(busy)}>
              {busy === "connect" ? "Opening Google…" : "Connect Google Drive"}
            </button>
          </div>
        </section>
      )}

      <form className="panel" onSubmit={onSave}>
        <h2>OAuth client</h2>
        <ol className="steps">
          <li>
            In Google Cloud Console, enable the <b>Google Drive API</b> and create an <b>OAuth client ID</b> of type
            <b> Web application</b>.
          </li>
          <li>
            Add this as an <b>Authorized redirect URI</b>:
            <code className="copy">{redirectUri}</code>
          </li>
          <li>While the consent screen is in testing, add the Google accounts that will sign in as test users.</li>
          <li>Paste the client ID and secret here.</li>
        </ol>
        <Field label="Client ID" hint={status.client_source === "environment" ? "Using GOOGLE_CLIENT_ID from the environment." : ""}>
          <input value={clientId} onChange={(event) => setClientId(event.target.value)} autoComplete="off" placeholder="1234-abc.apps.googleusercontent.com" />
        </Field>
        <Field label="Client secret">
          <input
            type="password"
            value={secret}
            onChange={(event) => setSecret(event.target.value)}
            autoComplete="off"
            placeholder={status.client_secret_set ? "Saved. Type to replace." : "Not set"}
          />
        </Field>
        <div className="row">
          <button type="submit" disabled={Boolean(busy)}>
            {busy === "save" ? "Saving…" : "Save"}
          </button>
        </div>
        <p className="hint">
          Changing the client ID signs Drive out. The secret is stored on the server and never shown again. The app can
          also read GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET from the environment. The server needs to reach
          accounts.google.com, oauth2.googleapis.com and www.googleapis.com.
        </p>
      </form>
    </div>
  )
}
