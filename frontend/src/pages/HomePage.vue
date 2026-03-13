<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { get } from '../api.js'
import { timeAgo } from "../utils.js"

const router = useRouter()


const dashboard = ref(null)
const tasks = ref([])
const loading = ref(true)
const error = ref(null)

onMounted(async () => {
  try {
    const [dash, taskList] = await Promise.all([
      get('/me/dashboard'),
      get('/executions?active_only=false').catch(() => ({ executions: [] })),
    ])
    dashboard.value = dash
    tasks.value = taskList.executions || []
  } catch (e) {
    error.value = e.message
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
      created_at: ex.started_at || ex.created_at,
    }))
    .sort((a, b) => new Date(b.created_at) - new Date(a.created_at))
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
          <h2 style="margin: 0;">Getting started</h2>
          <a v-if="!isEmpty" href="#" @click.prevent="showGuide = false" class="text-dim" style="font-size: 0.8rem;">Hide</a>
        </div>

        <div style="margin-bottom: 1.5rem;">
          <h3 style="margin-bottom: 0.5rem;">1. Install the CLI</h3>
          <pre style="background: var(--bg-terminal); padding: 0.75rem 1rem; border-radius: 2px; font-size: 0.8rem; overflow-x: auto; margin: 0;">uv pip install druids</pre>
        </div>

        <div style="margin-bottom: 1.5rem;">
          <h3 style="margin-bottom: 0.5rem;">2. Authenticate</h3>
          <p class="text-secondary" style="font-size: 0.85rem; margin: 0 0 0.5rem 0;">
            Go to <router-link to="/settings" class="text-bright">Settings</router-link> and generate an API key.
          </p>
          <pre style="background: var(--bg-terminal); padding: 0.75rem 1rem; border-radius: 2px; font-size: 0.8rem; overflow-x: auto; margin: 0;">druids auth set-key &lt;your-api-key&gt;</pre>
        </div>

        <div style="margin-bottom: 1.5rem;">
          <h3 style="margin-bottom: 0.5rem;">3. Create a devbox</h3>
          <p class="text-secondary" style="font-size: 0.85rem; margin: 0 0 0.5rem 0;">
            A devbox is a snapshotted sandbox with your repo cloned and dependencies installed. Executions start from this snapshot.
          </p>
          <pre style="background: var(--bg-terminal); padding: 0.75rem 1rem; border-radius: 2px; font-size: 0.8rem; overflow-x: auto; margin: 0;">druids setup start --repo owner/repo
# SSH in, install deps, then:
druids setup finish --name owner/repo</pre>
        </div>

        <div>
          <h3 style="margin-bottom: 0.5rem;">4. Run a program</h3>
          <pre style="background: var(--bg-terminal); padding: 0.75rem 1rem; border-radius: 2px; font-size: 0.8rem; overflow-x: auto; margin: 0;">druids exec .druids/basher.py --devbox owner/repo \
    task_name="test" task_spec="Hello world"</pre>
        </div>
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
                  <router-link :to="`/programs/${r.slug}`" class="text-bright">{{ r.slug }}</router-link>
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
            :to="`/programs/${r.slug}`"
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

