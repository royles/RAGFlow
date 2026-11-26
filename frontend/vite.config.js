import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"

export default defineConfig(({ command }) => {
  if (command === "build") {
    return { plugins: [react()] }
  }

  const port = Number(process.env.CDSW_APP_PORT)
  if (!port) {
    throw new Error("CDSW_APP_PORT must be set to run the Vite dev or preview server.")
  }
  const api = process.env.RAGFLOW_API_URL
  if (!api) {
    throw new Error("RAGFLOW_API_URL must point at the running RAGFlow API, for example http://127.0.0.1:<port>.")
  }
  const proxy = { "/api": api, "/mcp": api }
  const server = { host: "127.0.0.1", port, strictPort: true, proxy }

  return {
    plugins: [react()],
    server,
    preview: server,
  }
})
