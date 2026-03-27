import { ref } from 'vue'
import type { Ref } from 'vue'
import type { User } from './types'

const user: Ref<User | null> = ref(null)

/**
 * Composable for accessing the current user.
 * In self-hosted mode, the server always returns a local user.
 */
export function useAuth(): { user: Ref<User | null> } {
  return { user }
}

export async function loadUser(): Promise<User> {
  if (user.value) return user.value
  const res = await fetch('/api/me', { credentials: 'same-origin' })
  if (!res.ok) {
    // In self-hosted mode, create a synthetic local user so the
    // dashboard works even if /api/me is not yet available.
    const localUser: User = { id: 'local', github_login: 'local', is_admin: true }
    user.value = localUser
    return localUser
  }
  const data: User = await res.json()
  user.value = data
  return data
}

/**
 * Clear the cached user. Called on 401 responses.
 */
export function clearUser(): void {
  user.value = null
}
