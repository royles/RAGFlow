import { useState } from "react"

// Add documents from web addresses, or from text pasted in. Both go through the same
// progress tracker as uploads, so this only collects the input.
export default function AddSource({ onClose, onUrls, onText }) {
  const [mode, setMode] = useState("url")
  const [urls, setUrls] = useState("")
  const [title, setTitle] = useState("")
  const [text, setText] = useState("")

  const addresses = urls
    .split(/\s+/)
    .map((item) => item.trim())
    .filter(Boolean)
  const ready = mode === "url" ? addresses.length > 0 : text.trim().length > 0

  function submit(event) {
    event.preventDefault()
    if (!ready) return
    if (mode === "url") onUrls([...new Set(addresses)])
    else onText({ title: title.trim(), text })
  }

  return (
    <form className="add-source" onSubmit={submit}>
      <div className="tabs" role="tablist">
        <button type="button" role="tab" aria-selected={mode === "url"} className={mode === "url" ? "on" : ""} onClick={() => setMode("url")}>
          Web address
        </button>
        <button type="button" role="tab" aria-selected={mode === "text"} className={mode === "text" ? "on" : ""} onClick={() => setMode("text")}>
          Paste text
        </button>
        <button type="button" className="ghost close" onClick={onClose}>
          Close
        </button>
      </div>
      {mode === "url" ? (
        <>
          <textarea
            rows={3}
            autoFocus
            value={urls}
            placeholder="https://example.com/handbook.pdf"
            aria-label="Web addresses, one per line"
            onChange={(event) => setUrls(event.target.value)}
          />
          <p className="hint">
            One address per line. Web pages, PDF, Word, CSV, Markdown, and text files. Google Docs links use your Google Drive connection.
          </p>
        </>
      ) : (
        <>
          <input value={title} maxLength={100} placeholder="Title (optional)" aria-label="Title" onChange={(event) => setTitle(event.target.value)} />
          <textarea rows={8} autoFocus value={text} placeholder="Paste or type the text to add" aria-label="Text" onChange={(event) => setText(event.target.value)} />
          <p className="hint">Saved as a text document, so it is chunked and cited like any other file.</p>
        </>
      )}
      <button type="submit" disabled={!ready}>
        {mode === "url" ? `Add ${addresses.length > 1 ? `${addresses.length} addresses` : "address"}` : "Add text"}
      </button>
    </form>
  )
}
