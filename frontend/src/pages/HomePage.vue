<script setup>
import { ref, computed, onMounted } from 'vue'
import { get } from '../api.js'

const dashboard = ref(null)
const tasks = ref([])
const userInfo = ref(null)
const loading = ref(true)
const error = ref(null)

const isActive = computed(() => userInfo.value?.subscription_status === 'active')
const executionCount = computed(() => userInfo.value?.execution_count ?? 0)
const freeLimit = computed(() => userInfo.value?.free_tier_reviews ?? 15)
const freeRemaining = computed(() => Math.max(0, freeLimit.value - executionCount.value))
const isLimitReached = computed(() => !isActive.value && executionCount.value >= freeLimit.value)
const isNearLimit = computed(() => !isActive.value && !isLimitReached.value && freeRemaining.value <= 3)

onMounted(async () => {
  try {
    const [dash, taskList, me] = await Promise.all([
      get('/me/dashboard'),
      get('/tasks?active_only=false').catch(() => ({ tasks: [] })),
      get('/me'),
    ])
    dashboard.value = dash
    tasks.value = taskList.tasks || []
    userInfo.value = me
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
})

const reviews = computed(() => {
  const result = []
  for (const task of tasks.value) {
    for (const ex of (task.executions || [])) {
      result.push({
        slug: task.slug,
        spec: task.spec,
        repo: task.metadata?.repo_full_name || '',
        status: ex.status,
        pr_url: ex.pr_url,
        pr_number: ex.pr_url ? ex.pr_url.match(/\/pull\/(\d+)/)?.[1] : null,
        created_at: task.created_at,
      })
    }
  }
  return result.sort((a, b) => new Date(b.created_at) - new Date(a.created_at))
})

function statusClass(status) {
  if (status === 'running' || status === 'starting') return 'badge-active'
  if (status === 'completed') return 'badge-completed'
  if (status === 'error' || status === 'failed' || status === 'stopped') return 'badge-error'
  return ''
}

function timeAgo(dateStr) {
  if (!dateStr) return ''
  const d = new Date(dateStr)
  const now = new Date()
  const diff = Math.floor((now - d) / 1000)
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}
</script>

<template>
  <div>
    <div class="page-header">
      <h1>Dashboard</h1>
      <p>PR review overview</p>
    </div>

    <div v-if="loading" class="empty-state">
      <span class="spinner"></span>
    </div>

    <div v-else-if="error" class="empty-state text-red">
      {{ error }}
    </div>

    <template v-else>
      <!-- Billing banner -->
      <div v-if="isLimitReached" class="card mb-2" style="border-color: var(--red);">
        <span class="text-red">
          Free tier limit reached ({{ executionCount }}/{{ freeLimit }} reviews).
        </span>
        <router-link to="/billing" class="btn btn-sm btn-primary" style="margin-left: 0.75rem;">
          Subscribe
        </router-link>
      </div>
      <div v-else-if="isNearLimit" class="card mb-2" style="border-color: var(--yellow);">
        <span class="text-yellow">
          {{ freeRemaining }} free review{{ freeRemaining === 1 ? '' : 's' }} remaining.
        </span>
        <router-link to="/billing" style="margin-left: 0.75rem; font-size: 0.75rem;">
          View billing
        </router-link>
      </div>

      <!-- Configured repos -->
      <h2 class="mb-2">Repos</h2>
      <div v-if="dashboard?.devboxes?.length" class="card-grid mb-3">
        <router-link
          v-for="d in dashboard.devboxes"
          :key="d.repo_full_name"
          :to="`/setup/${d.repo_full_name}`"
          class="card"
          style="cursor: pointer; text-decoration: none; color: inherit;"
        >
          <h3>{{ d.repo_full_name }}</h3>
          <div class="mt-1 text-secondary" style="font-size: 0.75rem;">
            <span v-if="d.has_snapshot" class="text-green">Snapshot ready</span>
            <span v-else class="text-dim">No snapshot</span>
          </div>
        </router-link>
      </div>
      <div v-else class="card mb-3" style="display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 3rem 2rem; text-align: center;">
        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="var(--text-dim)" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" style="margin-bottom: 1rem; opacity: 0.6;">
          <path d="M3 7v10a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-6l-2-2H5a2 2 0 0 0-2 2z"/>
        </svg>
        <p style="font-size: 0.95rem; color: var(--text-secondary); margin: 0;">No repositories configured yet</p>
        <router-link to="/setup" class="btn btn-sm btn-primary" style="display: inline-flex; margin-top: 1.25rem;">
          Set up a repo
        </router-link>
      </div>

      <!-- Recent reviews -->
      <h2 class="mb-2">Recent reviews</h2>
      <div v-if="reviews.length">
        <table class="data-table">
          <thead>
            <tr>
              <th>Repo</th>
              <th>Status</th>
              <th>PR</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in reviews" :key="r.slug + r.status">
              <td class="text-secondary" style="font-size: 0.72rem;">{{ r.repo }}</td>
              <td><span class="badge" :class="statusClass(r.status)">{{ r.status }}</span></td>
              <td>
                <a v-if="r.pr_url" :href="r.pr_url" target="_blank" class="text-bright">
                  #{{ r.pr_number }}
                </a>
                <span v-else class="text-dim">&mdash;</span>
              </td>
              <td class="text-dim">{{ timeAgo(r.created_at) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-else class="empty-state">
        No reviews yet. Reviews will appear here when PRs are opened on your configured repos.
      </div>
    </template>
  </div>
</template>
