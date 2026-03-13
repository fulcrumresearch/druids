const BASE = '/api'

async function request(method, path, body) {
  const opts = {
    method,
    credentials: 'same-origin',
    headers: {},
  }
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json'
    opts.body = JSON.stringify(body)
  }
  const res = await fetch(`${BASE}${path}`, opts)
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || `HTTP ${res.status}`)
  }
  return res.json()
}

export function get(path) {
  return request('GET', path)
}

export function post(path, body) {
  return request('POST', path, body)
}

export function del(path) {
  return request('DELETE', path)
}
