<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { get, post } from '../api'
import { timeAgo } from '../utils'
import type { Dashboard, ExecutionSummary, ApiKeyCreated } from '../types'

const dashboard = ref<Dashboard | null>(null)
const tasks = ref<ExecutionSummary[]>([])
const loading = ref(true)
const error = ref<string | null>(null)

const generatedKey = ref<string | null>(null)
const generatingKey = ref(false)

async function generateKey() {
  generatingKey.value = true
  try {
    const result = await post<ApiKeyCreated>('/keys', { name: 'cli' })
    generatedKey.value = result.key
  } catch (e) {
    generatedKey.value = null
  } finally {
    generatingKey.value = false
  }
}

onMounted(async () => {
  try {
    const [dash, taskList] = await Promise.all([
      get<Dashboard>('/me/dashboard'),
      get<{ executions: ExecutionSummary[] }>('/executions?active_only=false').catch(() => ({ executions: [] as ExecutionSummary[] })),
    ])
    dashboard.value = dash
    tasks.value = taskList.executions || []
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loading.value = false
  }
})

const showGuide = ref(false)

const isEmpty = computed(() => {
  return !dashboard.value?.devboxes?.length && !tasks.value.length
})

const reviews = computed(() => {
  return tasks.value
    .map((ex) => ({
      slug: ex.slug,
      spec: ex.spec,
      repo: ex.repo_full_name || '',
      status: ex.status,
      created_at: ex.started_at,
    }))
    .sort((a, b) => new Date(b.created_at || 0).getTime() - new Date(a.created_at || 0).getTime())
})

</script>

<template>
  <div>
    <div class="page-header">
      <h1>Dashboard</h1>
      <p>Agent executions</p>
    </div>

    <div v-if="loading" class="empty-state">
      <span class="spinner"></span>
    </div>

    <div v-else-if="error" class="empty-state text-red">
      {{ error }}
    </div>

    <template v-else>
      <!-- Getting started toggle -->
      <div v-if="!isEmpty && !showGuide" style="margin-bottom: 1.5rem;">
        <a href="#" @click.prevent="showGuide = true" class="text-dim" style="font-size: 0.8rem;">Show setup guide</a>
      </div>

      <!-- Getting started guide -->
      <div v-if="isEmpty || showGuide" class="card" style="padding: 2rem; margin-bottom: 1.5rem;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 1.5rem;">
          <h2 style="margin: 0;">Quickstart</h2>
          <a v-if="!isEmpty" href="#" @click.prevent="showGuide = false" class="text-dim" style="font-size: 0.8rem;">Hide</a>
        </div>

        <div style="margin-bottom: 1.5rem;">
          <h3 style="margin-bottom: 0.5rem;">1. Install the CLI and initialize your repo</h3>
          <pre style="background: var(--bg-terminal); padding: 0.75rem 1rem; border-radius: 2px; font-size: 0.8rem; overflow-x: auto; margin: 0;">uv tool install druids</pre>
          <div v-if="generatedKey" style="margin: 0.5rem 0;">
            <pre style="background: var(--bg-terminal); padding: 0.75rem 1rem; border-radius: 2px; font-size: 0.8rem; overflow-x: auto; margin: 0;">druids auth set-key {{ generatedKey }}</pre>
            <p class="text-secondary" style="font-size: 0.8rem; margin: 0.25rem 0 0 0;">Copy this now — the key won't be shown again.</p>
          </div>
          <div v-else style="margin: 0.5rem 0;">
            <button @click="generateKey" :disabled="generatingKey" class="btn btn-sm" style="margin-bottom: 0.5rem;">
              {{ generatingKey ? 'Generating...' : 'Generate API key' }}
            </button>
            <pre style="background: var(--bg-terminal); padding: 0.75rem 1rem; border-radius: 2px; font-size: 0.8rem; overflow-x: auto; margin: 0;">druids auth set-key &lt;your-api-key&gt;</pre>
          </div>
          <p class="text-secondary" style="font-size: 0.85rem; margin: 0.5rem 0 0.5rem 0;">
            From the root of your project, run <code>init</code> to add starter programs, the MCP server config, and Claude Code skills:
          </p>
          <pre style="background: var(--bg-terminal); padding: 0.75rem 1rem; border-radius: 2px; font-size: 0.8rem; overflow-x: auto; margin: 0;">druids init</pre>
          <p class="text-secondary" style="font-size: 0.85rem; margin: 0.5rem 0 0 0;">
            Restart Claude Code after running init so it picks up the MCP server.
          </p>
        </div>

        <div style="margin-bottom: 1.5rem;">
          <h3 style="margin-bottom: 0.5rem;">2. Set up a devbox</h3>
          <p class="text-secondary" style="font-size: 0.85rem; margin: 0 0 0.5rem 0;">
            A devbox is a snapshotted VM with your repo cloned and dependencies installed. Every execution forks from it. Tell Claude Code:
          </p>
          <pre style="background: var(--bg-terminal); padding: 0.75rem 1rem; border-radius: 2px; font-size: 0.8rem; overflow-x: auto; margin: 0;">Set up a devbox for this repo.</pre>
        </div>

        <div style="margin-bottom: 1.5rem;">
          <h3 style="margin-bottom: 0.5rem;">3. Run your first execution</h3>
          <p class="text-secondary" style="font-size: 0.85rem; margin: 0 0 0.5rem 0;">
            Tell Claude Code what you want built:
          </p>
          <pre style="background: var(--bg-terminal); padding: 0.75rem 1rem; border-radius: 2px; font-size: 0.8rem; overflow-x: auto; margin: 0;">Use druids to write a /health endpoint that returns 200 OK.</pre>
          <p class="text-secondary" style="font-size: 0.85rem; margin: 0.5rem 0 0 0;">
            Agents implement the feature on a branch and open a PR when done. You can monitor progress here on the dashboard, through the CLI, or via the MCP tools.
          </p>
        </div>

        <p class="text-secondary" style="font-size: 0.85rem; margin: 0;">
          See the <router-link to="/docs/get-started" class="text-bright">full getting started guide</router-link> for more details, including GitHub App setup and manual CLI workflows.
        </p>
      </div>
      <!-- Configured repos -->
      <h2 v-if="!isEmpty" class="mb-2">Repos</h2>
      <div v-if="dashboard?.devboxes?.length" class="card-grid mb-3">
        <div
          v-for="d in dashboard.devboxes"
          :key="d.repo_full_name"
          class="card"
        >
          <h3>{{ d.repo_full_name }}</h3>
        </div>
      </div>

      <!-- Recent executions -->
      <template v-if="!isEmpty">
      <h2 class="mb-2">Recent executions</h2>
      <div v-if="reviews.length">
        <div class="table-wrap">
          <table class="data-table">
            <thead>
              <tr>
                <th>Execution</th>
                <th>Status</th>
                <th>Repo</th>
                <th>Started</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in reviews" :key="r.slug">
                <td>
                  <router-link :to="`/executions/${r.slug}`" class="text-bright">{{ r.slug }}</router-link>
                </td>
                <td class="text-dim">{{ r.status }}</td>
                <td class="text-secondary" style="font-size: 0.72rem;">{{ r.repo }}</td>
                <td class="text-dim">{{ timeAgo(r.created_at) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="mobile-cards">
          <router-link
            v-for="r in reviews"
            :key="r.slug"
            :to="`/executions/${r.slug}`"
            class="mobile-card"
          >
            <div class="mobile-card-row">
              <span class="mobile-card-primary">{{ r.slug }}</span>
              <span class="mobile-card-dim">{{ r.status }}</span>
            </div>
            <div class="mobile-card-row">
              <span class="mobile-card-secondary">{{ r.repo }}</span>
              <span class="mobile-card-dim">{{ timeAgo(r.created_at) }}</span>
            </div>
          </router-link>
        </div>
      </div>
      <div v-else class="empty-state">
        No executions yet.
      </div>
      </template>
    </template>
  </div>
</template>

