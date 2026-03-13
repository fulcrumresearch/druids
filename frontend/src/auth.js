import { ref } from 'vue'

const user = ref(null)

/**
 * Composable for accessing the current user.
 * In self-hosted mode, the server always returns a local user.
 */
export function useAuth() {
  return { user }
}

export async function loadUser() {
  if (user.value) return user.value
  const res = await fetch('/api/me', { credentials: 'same-origin' })
  if (!res.ok) {
    // In self-hosted mode, create a synthetic local user so the
    // dashboard works even if /api/me is not yet available.
    user.value = { id: 'local', github_login: 'local', is_admin: true }
    return user.value
  }
  const data = await res.json()
  user.value = data
  return data
}

/**
 * Clear the cached user. Called on 401 responses.
 */
export function clearUser() {
  user.value = null
}
