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
  uploadDocument: (id, file) => {
    const body = new FormData()
    body.append("file", file)
    return request(`/api/knowledge-bases/${id}/documents`, { method: "POST", body })
  },
  loadSample: (id) => request(`/api/knowledge-bases/${id}/sample`, { method: "POST" }),
  deleteDocument: (id, documentId) =>
    request(`/api/knowledge-bases/${id}/documents/${documentId}`, { method: "DELETE" }),
  ask: (id, payload) =>
    request(`/api/knowledge-bases/${id}/ask`, { method: "POST", body: JSON.stringify({ source: "ui", ...payload }) }),
  getInteraction: (id) => request(`/api/interactions/${id}`),
  rate: (id, payload) =>
    request(`/api/interactions/${id}/feedback`, { method: "POST", body: JSON.stringify(payload) }),
  scoreInteraction: (id) => request(`/api/interactions/${id}/score`, { method: "POST" }),
  audit: (params) => request(`/api/audit?${new URLSearchParams(params)}`),
}
