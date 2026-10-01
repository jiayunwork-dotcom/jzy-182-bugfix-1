// Thin JSON API client. The browser never computes anything; every number
// on screen comes from one of these endpoints.

async function request(path, options = {}) {
  const resp = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  let body = null
  try {
    body = await resp.json()
  } catch {
    /* non-JSON error */
  }
  if (!resp.ok) {
    const errors = body?.errors || [{ field: '', message: `HTTP ${resp.status}` }]
    const err = new Error(errors.map((e) =>
      e.field ? `${e.field}: ${e.message}` : e.message).join('；'))
    err.errors = errors
    throw err
  }
  return body
}

export const api = {
  listProjects: () => request('/api/projects'),
  getProject: (id) => request(`/api/projects/${id}`),
  getPlan: (id) => request(`/api/projects/${id}/plan`),
  getState: (id, at) => request(
    `/api/projects/${id}/state${at ? `?at=${encodeURIComponent(at)}` : ''}`),
  getFever: (id) => request(`/api/projects/${id}/fever`),
  createProject: (payload) => request('/api/projects', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  postReport: (id, payload) => request(`/api/projects/${id}/reports`, {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
}
