import { useCallback, useEffect, useRef, useState } from "react"
import { api } from "./api"
import { Icon } from "./components"

const ROOTS = [
  { id: "root", name: "My Drive" },
  { id: "shared", name: "Shared with me" },
]

function size(bytes) {
  if (bytes === null || bytes === undefined) return ""
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

// Browse Google Drive and tick the files to load. Choices are kept while moving between folders.
export default function DrivePicker({ onClose, onImport }) {
  const [status, setStatus] = useState(null)
  const [path, setPath] = useState([ROOTS[0]])
  const [query, setQuery] = useState("")
  const [search, setSearch] = useState("")
  const [files, setFiles] = useState([])
  const [next, setNext] = useState("")
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")
  const [chosen, setChosen] = useState(() => new Map())
  const request = useRef(0)

  useEffect(() => {
    api
      .google()
      .then(setStatus)
      .catch((err) => setError(err.message))
  }, [])

  useEffect(() => {
    const onKey = (event) => event.key === "Escape" && onClose()
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [onClose])

  const folder = path[path.length - 1]

  const load = useCallback(
    async (pageToken = "") => {
      const ticket = ++request.current
      setLoading(true)
      setError("")
      try {
        const page = await api.googleFiles({ parent: folder.id, q: search, page_token: pageToken })
        if (ticket !== request.current) return
        setFiles((current) => (pageToken ? [...current, ...page.files] : page.files))
        setNext(page.next_page_token)
      } catch (err) {
        if (ticket === request.current) setError(err.message)
      } finally {
        if (ticket === request.current) setLoading(false)
      }
    },
    [folder.id, search],
  )

  useEffect(() => {
    if (status?.connected) load()
  }, [status, load])

  function toggle(file) {
    setChosen((current) => {
      const copy = new Map(current)
      if (copy.has(file.id)) copy.delete(file.id)
      else copy.set(file.id, file)
      return copy
    })
  }

  const importable = files.filter((file) => file.importable)
  const allHere = importable.length > 0 && importable.every((file) => chosen.has(file.id))

  function toggleAll() {
    setChosen((current) => {
      const copy = new Map(current)
      for (const file of importable) {
        if (allHere) copy.delete(file.id)
        else copy.set(file.id, file)
      }
      return copy
    })
  }

  function openRoot(root) {
    setSearch("")
    setQuery("")
    setPath([root])
  }

  function openFolder(file) {
    setSearch("")
    setQuery("")
    setPath((current) => [...current, { id: file.id, name: file.name }])
  }

  function goTo(index) {
    setSearch("")
    setQuery("")
    setPath((current) => current.slice(0, index + 1))
  }

  const connected = status?.connected
  return (
    <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="modal drive" role="dialog" aria-modal="true" aria-label="Import from Google Drive">
        <header>
          <h2>
            <Icon name="drive" /> Import from Google Drive
          </h2>
          <button type="button" className="ghost icon" onClick={onClose} aria-label="Close">
            <Icon name="close" />
          </button>
        </header>

        {status && !connected ? (
          <div className="modal-body">
            <p>Google Drive is not connected yet.</p>
            <a className="button" href="#/google" onClick={onClose}>
              {status.configured ? "Connect Google Drive" : "Set up Google Drive"}
            </a>
          </div>
        ) : (
          <>
            <div className="drive-bar">
              <div className="tabs" role="tablist">
                {ROOTS.map((root) => (
                  <button
                    type="button"
                    role="tab"
                    key={root.id}
                    aria-selected={path[0].id === root.id}
                    className={path[0].id === root.id ? "on" : ""}
                    onClick={() => openRoot(root)}
                  >
                    {root.name}
                  </button>
                ))}
              </div>
              <form
                className="drive-search"
                onSubmit={(event) => {
                  event.preventDefault()
                  setSearch(query.trim())
                }}
              >
                <input
                  type="search"
                  value={query}
                  onChange={(event) => {
                    setQuery(event.target.value)
                    if (!event.target.value) setSearch("")
                  }}
                  placeholder="Search by name"
                  aria-label="Search Google Drive"
                />
              </form>
            </div>

            {search ? (
              <p className="crumbs">Results for “{search}” in all of Drive</p>
            ) : (
              <p className="crumbs">
                {path.map((step, index) => (
                  <span key={step.id}>
                    {index > 0 ? " / " : ""}
                    {index < path.length - 1 ? (
                      <button type="button" className="link" onClick={() => goTo(index)}>
                        {step.name}
                      </button>
                    ) : (
                      step.name
                    )}
                  </span>
                ))}
              </p>
            )}

            {error ? <p className="banner">{error}</p> : null}

            <div className="modal-body list">
              {importable.length ? (
                <label className="pick all">
                  <input type="checkbox" checked={allHere} onChange={toggleAll} />
                  <span>Select all {importable.length} importable files here</span>
                </label>
              ) : null}
              {files.map((file) =>
                file.is_folder ? (
                  <button type="button" key={file.id} className="pick folder" onClick={() => openFolder(file)}>
                    <Icon name="folder" size={16} />
                    <span className="name">{file.name}</span>
                  </button>
                ) : (
                  <label key={file.id} className={`pick${file.importable ? "" : " off"}`} title={file.importable ? "" : "This file type cannot be imported"}>
                    <input
                      type="checkbox"
                      disabled={!file.importable}
                      checked={chosen.has(file.id)}
                      onChange={() => toggle(file)}
                    />
                    <Icon name="file" size={16} />
                    <span className="name">{file.name}</span>
                    <span className="meta">{size(file.size)}</span>
                  </label>
                ),
              )}
              {!loading && !files.length && !error ? <p className="muted">Nothing here.</p> : null}
              {loading ? <p className="muted">Loading…</p> : null}
              {next && !loading ? (
                <button type="button" className="secondary" onClick={() => load(next)}>
                  Show more
                </button>
              ) : null}
            </div>
          </>
        )}

        <footer>
          <span className="muted">
            {chosen.size ? `${chosen.size} ${chosen.size === 1 ? "file" : "files"} selected` : "Text, Markdown, CSV, PDF, DOCX, Google Docs, Sheets, and Slides."}
          </span>
          <div className="row">
            <button type="button" className="ghost" onClick={onClose}>
              Cancel
            </button>
            <button type="button" disabled={!chosen.size} onClick={() => onImport([...chosen.values()])}>
              {chosen.size ? `Import ${chosen.size}` : "Import"}
            </button>
          </div>
        </footer>
      </div>
    </div>
  )
}
