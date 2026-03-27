import { clearUser } from './auth'

const BASE = '/api'

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {}
  const opts: RequestInit = {
    method,
    credentials: 'same-origin',
    headers,
  }
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    opts.body = JSON.stringify(body)
  }
  const res = await fetch(`${BASE}${path}`, opts)
  if (res.status === 401) {
    clearUser()
    window.location.href = '/login'
    throw new Error('Not authenticated')
  }
  if (!res.ok) {
    const text = await res.text()
    let message = text || `HTTP ${res.status}`
    try {
      const json = JSON.parse(text)
      if (json.detail) message = json.detail
    } catch {}
    throw new Error(message)
  }
  return res.json() as Promise<T>
}

export function get<T>(path: string): Promise<T> {
  return request<T>('GET', path)
}

export function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>('POST', path, body)
}

export function del<T>(path: string): Promise<T> {
  return request<T>('DELETE', path)
}
