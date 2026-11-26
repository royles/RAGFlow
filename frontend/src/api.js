async function request(path, options = {}) {
  const init = { ...options }
  if (!(options.body instanceof FormData)) {
    init.headers = { "Content-Type": "application/json", ...(options.headers || {}) }
  }
  const response = await fetch(path, init)
  if (response.status === 204) return null
  const text = await response.text()
  const body = text ? JSON.parse(text) : null
  if (!response.ok) {
    throw new Error(errorMessage(body, response.statusText))
  }
  return body
}

function errorMessage(body, fallback) {
  if (!body) return fallback
  if (typeof body.detail === "string") return body.detail
  if (Array.isArray(body.detail)) {
    return body.detail.map((item) => item.msg || String(item)).join(" ")
  }
  return fallback
}

export const api = {
  catalog: () => request("/api/catalog"),
  agent: () => request("/api/agent"),
  chunkSample: (useLlm) =>
    request("/api/chunk-preview/sample", { method: "POST", body: JSON.stringify({ use_llm: useLlm }) }),
  chunkPreview: (payload) => request("/api/chunk-preview", { method: "POST", body: JSON.stringify(payload) }),
  getModels: () => request("/api/models"),
  saveModels: (payload) => request("/api/models", { method: "PUT", body: JSON.stringify(payload) }),
  testModel: (role, settings) =>
    request("/api/models/test", { method: "POST", body: JSON.stringify({ role, settings }) }),
  listKnowledgeBases: () => request("/api/knowledge-bases"),
  createKnowledgeBase: (payload) =>
    request("/api/knowledge-bases", { method: "POST", body: JSON.stringify(payload) }),
  getKnowledgeBase: (id) => request(`/api/knowledge-bases/${id}`),
  updateKnowledgeBase: (id, payload) =>
    request(`/api/knowledge-bases/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  deleteKnowledgeBase: (id) => request(`/api/knowledge-bases/${id}`, { method: "DELETE" }),
  listDocuments: (id) => request(`/api/knowledge-bases/${id}/documents`),
  // XHR rather than fetch, because only XHR reports how many bytes have been sent.
  getUpload: (uploadId) => request(`/api/uploads/${uploadId}`),
  uploadDocument: (id, file, onProgress, uploadId) =>
    new Promise((resolve, reject) => {
      const body = new FormData()
      body.append("file", file)
      const xhr = new XMLHttpRequest()
      xhr.open("POST", `/api/knowledge-bases/${id}/documents`)
      if (uploadId) xhr.setRequestHeader("X-Upload-Id", uploadId)
      xhr.upload.onprogress = (event) => event.lengthComputable && onProgress?.(event.loaded / event.total)
      xhr.upload.onload = () => onProgress?.(1)
      xhr.onerror = () => reject(new Error("The upload could not reach the server."))
      xhr.onload = () => {
        let parsed = null
        try {
          parsed = xhr.responseText ? JSON.parse(xhr.responseText) : null
        } catch {
          // not JSON
        }
        if (xhr.status >= 200 && xhr.status < 300) resolve(parsed)
        else reject(new Error(errorMessage(parsed, xhr.statusText || `Upload failed (${xhr.status})`)))
      }
      xhr.send(body)
    }),
  advanced: () => request("/api/advanced"),
  saveAdvanced: (payload) => request("/api/advanced", { method: "PUT", body: JSON.stringify(payload) }),
  google: () => request("/api/google"),
  saveGoogle: (payload) => request("/api/google", { method: "PUT", body: JSON.stringify(payload) }),
  googleConnect: (redirectUri) =>
    request("/api/google/connect", { method: "POST", body: JSON.stringify({ redirect_uri: redirectUri }) }),
  googleDisconnect: () => request("/api/google/disconnect", { method: "POST" }),
  googleFiles: (params) => request(`/api/google/files?${new URLSearchParams(params)}`),
  importDriveFile: (id, fileId, uploadId) =>
    request(`/api/knowledge-bases/${id}/documents/google-drive`, {
      method: "POST",
      body: JSON.stringify({ file_id: fileId, upload_id: uploadId }),
    }),
  importUrl: (id, url, uploadId) =>
    request(`/api/knowledge-bases/${id}/documents/url`, { method: "POST", body: JSON.stringify({ url, upload_id: uploadId }) }),
  importText: (id, title, text, uploadId) =>
    request(`/api/knowledge-bases/${id}/documents/text`, { method: "POST", body: JSON.stringify({ title, text, upload_id: uploadId }) }),
  loadSample: (id) => request(`/api/knowledge-bases/${id}/sample`, { method: "POST" }),
  deleteDocument: (id, documentId) =>
    request(`/api/knowledge-bases/${id}/documents/${documentId}`, { method: "DELETE" }),
  ask: (id, payload) =>
    request(`/api/knowledge-bases/${id}/ask`, { method: "POST", body: JSON.stringify({ source: "ui", ...payload }) }),
  listConversations: (kbId) => request(`/api/knowledge-bases/${kbId}/conversations`),
  getConversation: (id) => request(`/api/conversations/${id}`),
  deleteConversation: (id) => request(`/api/conversations/${id}`, { method: "DELETE" }),
  getInteraction: (id) => request(`/api/interactions/${id}`),
  rate: (id, payload) =>
    request(`/api/interactions/${id}/feedback`, { method: "POST", body: JSON.stringify(payload) }),
  scoreInteraction: (id) => request(`/api/interactions/${id}/score`, { method: "POST" }),
  audit: (params) => request(`/api/audit?${new URLSearchParams(params)}`),
}
