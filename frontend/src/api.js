const API_BASE = '/api'

async function request(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, options)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res
}

export const api = {
  upload(files) {
    const form = new FormData()
    for (const f of files) form.append('files', f)
    return request('/upload', { method: 'POST', body: form })
  },
  listUpload() {
    return request('/upload').then((r) => r.json())
  },
  deleteUpload(name) {
    return request(`/upload/${encodeURIComponent(name)}`, { method: 'DELETE' })
  },
  listOutput() {
    return request('/output').then((r) => r.json())
  },
  deleteOutput(name) {
    return request(`/output/${encodeURIComponent(name)}`, { method: 'DELETE' })
  },
  batchDeleteOutput(files) {
    return request('/output/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ files }),
    })
  },
  batchDownloadOutput(files) {
    return request('/output/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ files }),
    }).then((r) => r.blob())
  },
  getSettings() {
    return request('/settings/llm').then((r) => r.json())
  },
  putSettings(body) {
    return request('/settings/llm', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then((r) => r.json())
  },
}

export { API_BASE }
