<script setup>
import { ref, computed, onMounted } from "vue"
import { get } from "../api.js"

const usage = ref(null)
const loading = ref(true)
const error = ref(null)

onMounted(async () => {
  try {
    usage.value = await get("/admin/usage")
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
})

const totalTokens = computed(() => {
  if (!usage.value) return 0
  const t = usage.value.tokens
  return t.input + t.output + t.cache_read + t.cache_creation
})

function formatNumber(n) {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M"
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "K"
  return String(n)
}

function statusClass(status) {
  if (status === "running" || status === "starting") return "badge-active"
  if (status === "completed") return "badge-completed"
  if (status === "error" || status === "failed" || status === "stopped") return "badge-error"
  return ""
}

function timeAgo(dateStr) {
  if (!dateStr) return ""
  const d = new Date(dateStr)
  const now = new Date()
  const diff = Math.floor((now - d) / 1000)
  if (diff < 60) return "just now"
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

function prNumber(url) {
  if (!url) return null
  const m = url.match(/\/pull\/(\d+)/)
  return m ? m[1] : null
}
</script>

<template>
  <div>
    <div class="page-header">
      <h1>Usage</h1>
      <p>Platform-wide statistics</p>
    </div>

    <div v-if="loading" class="empty-state">
      <span class="spinner"></span>
    </div>

    <div v-else-if="error" class="empty-state text-red">
      {{ error }}
    </div>

    <template v-else-if="usage">
      <!-- Summary cards -->
      <div class="card-grid mb-3">
        <div class="card stat-card">
          <div class="stat-label">Users</div>
          <div class="stat-value">{{ usage.users.total }}</div>
          <div class="stat-detail">{{ usage.users.subscribed }} subscribed</div>
        </div>
        <div class="card stat-card">
          <div class="stat-label">Repos</div>
          <div class="stat-value">{{ usage.repos_configured }}</div>
          <div class="stat-detail">with snapshots</div>
        </div>
        <div class="card stat-card">
          <div class="stat-label">Reviews</div>
          <div class="stat-value">{{ usage.executions.total }}</div>
          <div class="stat-detail">{{ usage.executions.by_status.completed || 0 }} completed</div>
        </div>
        <div class="card stat-card">
          <div class="stat-label">Tokens</div>
          <div class="stat-value">{{ formatNumber(totalTokens) }}</div>
          <div class="stat-detail">{{ formatNumber(usage.tokens.input) }} in / {{ formatNumber(usage.tokens.output) }} out</div>
        </div>
      </div>

      <!-- Status breakdown -->
      <h2 class="mb-2">Execution status</h2>
      <div class="flex gap-2 mb-3" style="flex-wrap: wrap;">
        <span
          v-for="(count, status) in usage.executions.by_status"
          :key="status"
          class="badge"
          :class="statusClass(status)"
        >
          {{ status }}: {{ count }}
        </span>
      </div>

      <!-- Token breakdown -->
      <h2 class="mb-2">Token breakdown</h2>
      <div class="card mb-3" style="font-size: 0.82rem;">
        <div class="flex justify-between mb-1">
          <span class="text-secondary">Input tokens</span>
          <span class="text-bright">{{ formatNumber(usage.tokens.input) }}</span>
        </div>
        <div class="flex justify-between mb-1">
          <span class="text-secondary">Output tokens</span>
          <span class="text-bright">{{ formatNumber(usage.tokens.output) }}</span>
        </div>
        <div class="flex justify-between mb-1">
          <span class="text-secondary">Cache read tokens</span>
          <span class="text-bright">{{ formatNumber(usage.tokens.cache_read) }}</span>
        </div>
        <div class="flex justify-between">
          <span class="text-secondary">Cache creation tokens</span>
          <span class="text-bright">{{ formatNumber(usage.tokens.cache_creation) }}</span>
        </div>
      </div>

      <!-- Recent executions -->
      <h2 class="mb-2">Recent executions</h2>
      <div v-if="usage.recent_executions.length">
        <table class="data-table">
          <thead>
            <tr>
              <th>Repo</th>
              <th>User</th>
              <th>Status</th>
              <th>PR</th>
              <th>Tokens</th>
              <th>Started</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="ex in usage.recent_executions" :key="ex.slug">
              <td class="text-secondary" style="font-size: 0.72rem;">{{ ex.repo_full_name || "\u2014" }}</td>
              <td class="text-secondary" style="font-size: 0.72rem;">{{ ex.user_login || "\u2014" }}</td>
              <td><span class="badge" :class="statusClass(ex.status)">{{ ex.status }}</span></td>
              <td>
                <a v-if="ex.pr_url" :href="ex.pr_url" target="_blank" class="text-bright">
                  #{{ prNumber(ex.pr_url) }}
                </a>
                <span v-else class="text-dim">&mdash;</span>
              </td>
              <td class="text-dim" style="font-size: 0.72rem;">
                {{ formatNumber((ex.input_tokens || 0) + (ex.output_tokens || 0)) }}
              </td>
              <td class="text-dim">{{ timeAgo(ex.started_at) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-else class="empty-state">No executions yet.</div>
    </template>
  </div>
</template>
