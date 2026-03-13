<script setup>
import { ref, computed, onMounted } from 'vue'
import { get, post, del } from '../api.js'

const keys = ref([])
const loading = ref(true)
const error = ref(null)

const newKeyName = ref('')
const creating = ref(false)
const createdKey = ref(null)
const copied = ref(false)

const copiedConfig = ref(false)

const cliExample = computed(() => {
  const token = createdKey.value ? createdKey.value.key : 'druid_...'
  return `{
  "base_url": "${window.location.origin}",
  "user_access_token": "${token}"
}`
})

function copyConfig() {
  navigator.clipboard.writeText(cliExample.value)
  copiedConfig.value = true
  setTimeout(() => { copiedConfig.value = false }, 2000)
}

async function loadKeys() {
  try {
    const data = await get('/keys')
    keys.value = data.keys
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function createKey() {
  if (!newKeyName.value.trim()) return
  creating.value = true
  try {
    const data = await post('/keys', { name: newKeyName.value.trim() })
    createdKey.value = data
    newKeyName.value = ''
    await loadKeys()
  } catch (e) {
    error.value = e.message
  } finally {
    creating.value = false
  }
}

async function revokeKey(id) {
  try {
    await del(`/keys/${id}`)
    keys.value = keys.value.filter(k => k.id !== id)
  } catch (e) {
    error.value = e.message
  }
}

function copyKey() {
  if (!createdKey.value) return
  navigator.clipboard.writeText(createdKey.value.key)
  copied.value = true
  setTimeout(() => { copied.value = false }, 2000)
}

function dismissCreated() {
  createdKey.value = null
  copied.value = false
}

function formatDate(iso) {
  if (!iso) return 'never'
  const d = new Date(iso)
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

onMounted(loadKeys)
</script>

<template>
  <div>
    <div class="page-header">
      <h1>Settings</h1>
      <p>API keys for programmatic access</p>
    </div>

    <!-- Newly created key banner -->
    <div v-if="createdKey" class="card mb-3" style="border-color: var(--green);">
      <div class="flex items-center justify-between mb-1">
        <span class="text-green" style="font-size: 0.78rem;">Key created. Copy it now -- it won't be shown again.</span>
        <button class="btn-ghost" @click="dismissCreated">dismiss</button>
      </div>
      <div class="flex items-center gap-1">
        <code style="flex: 1; font-size: 0.82rem; color: var(--text-bright); word-break: break-all;">{{ createdKey.key }}</code>
        <button class="btn btn-sm btn-secondary" @click="copyKey">
          {{ copied ? 'copied' : 'copy' }}
        </button>
      </div>
    </div>

    <!-- Create new key -->
    <h2 class="mb-2">Create key</h2>
    <form class="card mb-3 create-key-form" @submit.prevent="createKey">
      <div style="flex: 1;">
        <label>Name</label>
        <input
          v-model="newKeyName"
          placeholder="e.g. github-actions, local-dev"
          :disabled="creating"
        />
      </div>
      <button class="btn btn-primary" type="submit" :disabled="creating || !newKeyName.trim()">
        {{ creating ? 'Creating...' : 'Create' }}
      </button>
    </form>

    <!-- Key list -->
    <h2 class="mb-2">Your keys</h2>

    <div v-if="loading" class="empty-state">
      <span class="spinner"></span>
    </div>

    <div v-else-if="error" class="empty-state text-red">
      {{ error }}
    </div>

    <div v-else-if="!keys.length" class="empty-state">
      No API keys yet.
    </div>

    <template v-else>
      <div class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>Prefix</th>
              <th>Name</th>
              <th>Created</th>
              <th>Last used</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="k in keys" :key="k.id">
              <td><code>{{ k.prefix }}...</code></td>
              <td>{{ k.name }}</td>
              <td class="text-dim">{{ formatDate(k.created_at) }}</td>
              <td class="text-dim">{{ formatDate(k.last_used_at) }}</td>
              <td style="text-align: right;">
                <button class="btn-ghost" @click="revokeKey(k.id)">revoke</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="mobile-cards">
        <div v-for="k in keys" :key="k.id" class="mobile-card">
          <div class="mobile-card-row">
            <span class="mobile-card-primary">{{ k.name }}</span>
            <button class="btn-ghost" @click="revokeKey(k.id)">revoke</button>
          </div>
          <div>
            <code style="font-size: 0.75rem; color: var(--text-secondary);">{{ k.prefix }}...</code>
          </div>
          <div class="mobile-card-row">
            <span class="mobile-card-dim">Created {{ formatDate(k.created_at) }}</span>
            <span class="mobile-card-dim">Used {{ formatDate(k.last_used_at) }}</span>
          </div>
        </div>
      </div>
    </template>

    <!-- CLI usage hint -->
    <div class="card mt-3" style="font-size: 0.78rem; color: var(--text-secondary);">
      <div class="flex items-center justify-between mb-1">
        <p style="color: var(--text);">CLI usage</p>
        <button class="btn-ghost" @click="copyConfig">{{ copiedConfig ? 'copied' : 'copy' }}</button>
      </div>
      <p>Set your API key in <code>~/.druids/config.json</code>:</p>
      <pre style="margin-top: 0.5rem; color: var(--text-dim); white-space: pre-wrap;">{{ cliExample }}</pre>
    </div>

  </div>
</template>

<style scoped>
.create-key-form {
  display: flex;
  gap: 0.75rem;
  align-items: flex-end;
}

@media (max-width: 720px) {
  .create-key-form {
    flex-direction: column;
    align-items: stretch;
  }
}
</style>
