<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick, watchEffect } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { get, post } from '../api.js'

const route = useRoute()
const router = useRouter()
const routeRepo = route.params.owner ? `${route.params.owner}/${route.params.repo}` : null
const appInstallUrl = router._user?.github_app_install_url || null

// step: 0=loading, 1=select repo, 2=provisioning, 3=wizard
const step = ref(routeRepo ? 0 : 1)
const repos = ref([])
const search = ref('')
const loading = ref(true)
const error = ref(null)
const selectedRepo = ref(null)
const devbox = ref(null)

// Wizard state
const slug = ref(null)
const messages = ref([])
const checklist = ref({
  secrets: 'pending',
  deps: 'pending',
  lint: 'pending',
  tests: 'pending',
  verify: 'pending',
  'setup-md': 'pending',
})
const messageText = ref('')
const sending = ref(false)
const saving = ref(false)
const saved = ref(false)
const chatEl = ref(null)
const userScrolledUp = ref(false)
const streamingMessage = ref(null)
const textareaEl = ref(null)
const interrupting = ref(false)
const resetting = ref(false)
const setupMode = ref('setup')

// SSE connection
let sseAbortController = null
let lastEventId = null
let reconnectTimer = null

const CHECKLIST_STEPS = [
  { key: 'secrets',   label: 'Configure secrets',   optional: true },
  { key: 'deps',      label: 'Install dependencies', optional: false },
  { key: 'lint',      label: 'Setup lint',           optional: true },
  { key: 'tests',     label: 'Setup tests',          optional: true },
  { key: 'verify',    label: 'Verify',               optional: false },
  { key: 'setup-md',  label: 'Write SETUP.md',       optional: false },
]

const filteredRepos = computed(() => {
  if (!search.value) return repos.value
  const q = search.value.toLowerCase()
  return repos.value.filter(r => r.full_name.toLowerCase().includes(q))
})

const TERMINAL_STATUSES = new Set(['done', 'skipped', 'error'])
const isSetupComplete = computed(() => {
  if (saved.value) return false
  return CHECKLIST_STEPS.every(s => TERMINAL_STATUSES.has(checklist.value[s.key]))
})

function parseSSEStream(text, onFrame) {
  // Split on double newlines to get frames
  const frames = text.split(/\n\n/)
  for (const frame of frames) {
    if (!frame.trim()) continue
    const lines = frame.split('\n')
    let id = null
    let event = 'message'
    let data = ''
    for (const line of lines) {
      if (line.startsWith('id:')) {
        id = line.slice(3).trim()
      } else if (line.startsWith('event:')) {
        event = line.slice(6).trim()
      } else if (line.startsWith('data:')) {
        data = line.slice(5).trim()
      }
    }
    if (data) {
      onFrame(id, event, data)
    }
  }
}

function findToolById(msgs, id) {
  if (!id) return null
  for (let i = msgs.length - 1; i >= 0; i--) {
    if (msgs[i].type === 'tool' && msgs[i].id === id) return msgs[i]
  }
  return null
}

function dispatchEvent(id, event, data) {
  if (id !== null) lastEventId = id
  let parsed
  try {
    parsed = JSON.parse(data)
  } catch {
    return
  }
  if (event === 'message_stream') {
    streamingMessage.value = { type: 'message', role: 'assistant', text: parsed.text }
    scrollToBottom()
    return
  }
  if (event === 'message') {
    streamingMessage.value = null
    messages.value.push({ type: 'message', role: parsed.role, text: parsed.text })
    scrollToBottom()
  } else if (event === 'tool') {
    streamingMessage.value = null
    const existing = findToolById(messages.value, parsed.id)
    if (existing) {
      // Update all fields (ACP updates title mid-execution, e.g. "Terminal" -> actual command)
      if (parsed.name) existing.name = parsed.name
      if (parsed.kind) existing.kind = parsed.kind
      if (parsed.input) existing.input = parsed.input
      existing.output = parsed.output
      existing.status = parsed.status
    } else if (parsed.status === 'active') {
      // No existing block -- tool just started, open a new one
      messages.value.push({ type: 'tool', id: parsed.id, name: parsed.name, kind: parsed.kind || 'other', input: parsed.input, output: parsed.output, status: 'active', expanded: false })
    } else {
      // No active block (replay path: only done events in history)
      messages.value.push({ type: 'tool', id: parsed.id, name: parsed.name, kind: parsed.kind || 'other', input: parsed.input, output: parsed.output, status: parsed.status, expanded: false })
    }
    scrollToBottom()
  } else if (event === 'checklist') {
    checklist.value[parsed.step] = parsed.status
  }
}

function onChatScroll() {
  if (!chatEl.value) return
  const el = chatEl.value
  const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
  userScrolledUp.value = distanceFromBottom > 80
}

async function scrollToBottom() {
  if (userScrolledUp.value) return
  await nextTick()
  if (chatEl.value) {
    chatEl.value.scrollTop = chatEl.value.scrollHeight
  }
}

async function connectChat(sessionSlug) {
  // Cancel any existing connection
  if (sseAbortController) {
    sseAbortController.abort()
    sseAbortController = null
  }
  if (reconnectTimer) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }

  const controller = new AbortController()
  sseAbortController = controller

  const headers = { 'Accept': 'text/event-stream' }
  if (lastEventId !== null) {
    headers['Last-Event-ID'] = String(lastEventId)
  }

  let res
  try {
    res = await fetch(`/api/setup/${sessionSlug}/chat`, {
      method: 'GET',
      credentials: 'same-origin',
      headers,
      signal: controller.signal,
    })
  } catch (e) {
    if (controller.signal.aborted) return
    // Network error -- reconnect after delay
    reconnectTimer = setTimeout(() => connectChat(sessionSlug), 2000)
    return
  }

  if (res.status === 404) {
    // Session gone (server restarted) -- start a new one
    try {
      const result = await post('/setup/start', { repo_full_name: selectedRepo.value.full_name, mode: setupMode.value })
      slug.value = result.slug
      lastEventId = null
      messages.value = []
      checklist.value = { secrets: 'pending', deps: 'pending', lint: 'pending', tests: 'pending', verify: 'pending', 'setup-md': 'pending' }
      reconnectTimer = setTimeout(() => connectChat(result.slug), 500)
    } catch (e) {
      error.value = 'Session lost. Please refresh and try again.'
    }
    return
  }

  if (!res.ok || !res.body) {
    reconnectTimer = setTimeout(() => connectChat(sessionSlug), 2000)
    return
  }

  // Read the stream
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    let chunk
    try {
      chunk = await reader.read()
    } catch {
      if (controller.signal.aborted) return
      break
    }
    if (chunk.done) break
    buffer += decoder.decode(chunk.value, { stream: true })

    // Process complete frames (separated by \n\n)
    const boundary = buffer.lastIndexOf('\n\n')
    if (boundary !== -1) {
      const complete = buffer.slice(0, boundary + 2)
      buffer = buffer.slice(boundary + 2)
      parseSSEStream(complete, dispatchEvent)
    }
  }

  // Process any remaining data left in the buffer when the stream closes.
  // The final SSE event may not end with \n\n before the server closes the connection.
  if (buffer.trim()) {
    parseSSEStream(buffer, dispatchEvent)
  }

  if (controller.signal.aborted) return
  // Stream closed -- reconnect
  reconnectTimer = setTimeout(() => connectChat(sessionSlug), 2000)
}

function stopChat() {
  if (sseAbortController) {
    sseAbortController.abort()
    sseAbortController = null
  }
  if (reconnectTimer) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
}

onMounted(async () => {
  if (routeRepo) {
    try {
      const dash = await get('/me/dashboard')
      const match = (dash.devboxes || []).find(d => d.repo_full_name === routeRepo)
      devbox.value = match || null
      selectedRepo.value = { full_name: routeRepo, name: routeRepo.split('/')[1] }

      if (match?.has_snapshot) {
        // Already configured -- go to step 3 with a done state
        step.value = 3
      } else if (match?.setup_slug) {
        // Session in progress -- skip provisioning, go straight to wizard
        slug.value = match.setup_slug
        step.value = 3
        connectChat(match.setup_slug)
      } else {
        // Not started -- go to provisioning
        await startSetup({ full_name: routeRepo, name: routeRepo.split('/')[1] })
      }
    } catch (e) {
      error.value = e.message
      step.value = 1
    } finally {
      loading.value = false
    }
  } else {
    try {
      const data = await get('/repos')
      repos.value = data.repos
    } catch (e) {
      error.value = e.message
    } finally {
      loading.value = false
    }
  }
})

// Widen the main-content container when the wizard layout is active
const wizardActive = computed(() => step.value === 3 && slug.value)
watchEffect(() => {
  const el = document.querySelector('.main-content')
  if (!el) return
  if (wizardActive.value) {
    el.classList.add('main-content--wide')
  } else {
    el.classList.remove('main-content--wide')
  }
})

onUnmounted(() => {
  stopChat()
  document.querySelector('.main-content')?.classList.remove('main-content--wide')
})

async function startSetup(repo, mode = 'setup') {
  selectedRepo.value = { full_name: repo.full_name, name: repo.name || repo.full_name.split('/')[1] }
  step.value = 2
  error.value = null
  setupMode.value = mode
  try {
    const result = await post('/setup/start', { repo_full_name: repo.full_name, mode })
    slug.value = result.slug
    step.value = 3
    connectChat(result.slug)
  } catch (e) {
    // Try to parse response for 409
    let msg = e.message
    try {
      const parsed = JSON.parse(msg)
      msg = parsed.detail || msg
    } catch {}
    if (msg.includes('409') || msg.toLowerCase().includes('already set up')) {
      error.value = 'Already set up.'
    } else {
      error.value = msg
    }
    step.value = 1
  }
}

function autoResizeTextarea(el) {
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 120) + 'px'
}

async function sendMessage() {
  const text = messageText.value.trim()
  if (!text || sending.value || !slug.value) return
  sending.value = true
  messageText.value = ''
  await nextTick()
  if (textareaEl.value) autoResizeTextarea(textareaEl.value)
  try {
    await post(`/setup/${slug.value}/message`, { text })
  } catch (e) {
    error.value = e.message
  } finally {
    sending.value = false
  }
}

function handleInputKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    sendMessage()
  }
}

async function interruptAgent() {
  if (interrupting.value || !slug.value) return
  interrupting.value = true
  try {
    await post(`/setup/${slug.value}/interrupt`, {})
  } catch (e) {
    error.value = e.message
  } finally {
    interrupting.value = false
  }
}

async function saveSnapshot() {
  saving.value = true
  error.value = null
  try {
    await post('/setup/save', { repo_full_name: selectedRepo.value.full_name })
    saved.value = true
    if (devbox.value) devbox.value.has_snapshot = true
    stopChat()
    router.push('/')
  } catch (e) {
    error.value = e.message
  } finally {
    saving.value = false
  }
}

async function resetSetup() {
  if (resetting.value || !selectedRepo.value) return
  resetting.value = true
  error.value = null
  try {
    stopChat()
    await post('/setup/reset', { repo_full_name: selectedRepo.value.full_name })

    // Clear local wizard state
    slug.value = null
    messages.value = []
    streamingMessage.value = null
    lastEventId = null
    checklist.value = {
      secrets: 'pending',
      deps: 'pending',
      lint: 'pending',
      tests: 'pending',
      verify: 'pending',
      'setup-md': 'pending',
    }

    // Restart setup
    await startSetup(selectedRepo.value)
  } catch (e) {
    error.value = e.message
    resetting.value = false
  } finally {
    resetting.value = false
  }
}

async function modifySetup() {
  if (resetting.value || !selectedRepo.value) return
  await startSetup(selectedRepo.value, 'modify')
}

function toggleToolExpanded(msg) {
  msg.expanded = !msg.expanded
}

function toolOutputLines(output) {
  if (!output) return []
  return output.split('\n')
}

function isLongOutput(output) {
  return toolOutputLines(output).length > 10
}
</script>

<template>
  <div>
    <div class="page-header">
      <h1>Setup</h1>
      <p v-if="selectedRepo">{{ selectedRepo.full_name }}</p>
      <p v-else>Configure a repository for use with Orpheus</p>
    </div>

    <div v-if="appInstallUrl" class="card mb-2" style="border-color: var(--border-light);">
      <span class="text-secondary" style="font-size: 0.82rem;">
        Don't see your repo? <a :href="appInstallUrl" target="_blank" rel="noopener">Install the GitHub App</a> to grant access.
      </span>
    </div>

    <div v-if="error" class="card mb-2" style="border-color: var(--red);">
      <span class="text-red">{{ error }}</span>
    </div>

    <!-- Step 0: Loading -->
    <div v-if="step === 0" class="empty-state">
      <span class="spinner"></span>
    </div>

    <!-- Step 1: Select repo -->
    <template v-if="step === 1">
      <div v-if="loading" class="empty-state">
        <span class="spinner"></span>
      </div>
      <template v-else>
        <div class="search-input mb-2">
          <span class="icon">/</span>
          <input v-model="search" placeholder="Search repositories..." />
        </div>
        <div class="card-grid">
          <div
            v-for="repo in filteredRepos"
            :key="repo.full_name"
            class="card repo-card"
            @click="startSetup(repo)"
          >
            <h3 class="repo-name">{{ repo.name }}</h3>
            <div class="repo-meta">{{ repo.full_name }}</div>
            <span v-if="repo.private" class="badge btn-sm">private</span>
          </div>
        </div>
        <div v-if="!filteredRepos.length" class="empty-state">
          No repositories found.
        </div>
      </template>
    </template>

    <!-- Step 2: Provisioning -->
    <template v-if="step === 2">
      <div class="card" style="text-align: center; padding: 3rem;">
        <span class="spinner" style="width: 24px; height: 24px;"></span>
        <div class="mt-2">
          {{ setupMode === 'modify' ? 'Launching modify wizard' : 'Launching setup wizard' }}
          for {{ selectedRepo?.full_name }}...
        </div>
        <div class="text-secondary mt-1" style="font-size: 0.75rem;">
          {{ setupMode === 'modify'
            ? 'Starting from saved snapshot. This may take a minute.'
            : 'Provisioning VM and starting agent. This may take a minute.' }}
        </div>
      </div>
    </template>

    <!-- Step 3: Wizard interface -->
    <template v-if="step === 3">
      <!-- Already configured state -->
      <div v-if="devbox?.has_snapshot && !slug" class="card">
        <div style="display: flex; align-items: baseline; gap: 0.75rem;">
          <h3>{{ selectedRepo?.full_name }}</h3>
          <span class="badge badge-active" style="color: var(--green); border-color: rgba(63, 185, 80, 0.3);">Configured</span>
        </div>
        <div class="text-secondary mt-1" style="font-size: 0.75rem;">
          This repository has a snapshot ready for code reviews.
        </div>
        <div class="mt-2" style="display: flex; gap: 0.5rem;">
          <button class="btn btn-secondary" :disabled="resetting" @click="modifySetup">
            Modify setup
          </button>
          <button class="btn btn-ghost" :disabled="resetting" @click="resetSetup">
            {{ resetting ? 'Resetting...' : 'Redo setup' }}
          </button>
        </div>
      </div>

      <!-- Saving state -->
      <div v-else-if="saving" class="card" style="text-align: center; padding: 3rem;">
        <span class="spinner" style="width: 24px; height: 24px;"></span>
        <div class="mt-2">Saving snapshot for {{ selectedRepo?.full_name }}...</div>
        <div class="text-secondary mt-1" style="font-size: 0.75rem;">
          Creating a snapshot of your environment. This may take a moment.
        </div>
      </div>

      <!-- Active wizard -->
      <div v-else class="wizard-layout">
        <!-- Left: checklist panel -->
        <div class="checklist-panel">
          <div class="checklist-steps">
            <div
              v-for="s in CHECKLIST_STEPS"
              :key="s.key"
              class="checklist-step"
              :class="checklist[s.key]"
            >
              <span class="step-icon">
                <span v-if="checklist[s.key] === 'active'" class="spinner" style="width: 12px; height: 12px;"></span>
                <span v-else-if="checklist[s.key] === 'done'" class="icon-done">x</span>
                <span v-else-if="checklist[s.key] === 'error'" class="icon-error">!</span>
                <span v-else-if="checklist[s.key] === 'skipped'" class="icon-skipped">-</span>
                <span v-else class="icon-pending"> </span>
              </span>
              <span class="step-label" :class="{ 'step-skipped-label': checklist[s.key] === 'skipped' }">
                {{ s.label }}
                <span v-if="s.optional" class="step-optional">(optional)</span>
              </span>
            </div>
          </div>

          <div class="checklist-footer">
            <div v-if="isSetupComplete && !saved" class="setup-complete-notice">
              Setup complete. Save a snapshot to use this environment for code reviews.
            </div>
            <button
              :class="['btn', isSetupComplete && !saved ? 'btn-primary' : 'btn-secondary']"
              style="width: 100%;"
              :disabled="saving"
              @click="saveSnapshot"
            >
              {{ saving ? 'Saving...' : saved ? 'Snapshot saved' : 'Save snapshot' }}
            </button>
            <div v-if="saved" class="text-green" style="font-size: 0.72rem; text-align: center;">
              Setup complete.
            </div>
            <button
              v-if="!saved"
              class="btn btn-ghost"
              style="width: 100%;"
              :disabled="resetting || saving"
              @click="resetSetup"
            >
              {{ resetting ? 'Resetting...' : 'Redo setup' }}
            </button>
          </div>
        </div>

        <!-- Right: chat panel -->
        <div class="chat-panel">
          <div class="chat-messages" ref="chatEl" @scroll="onChatScroll">
            <div v-if="!messages.length" class="empty-state" style="padding: 2rem 1rem;">
              <span class="spinner"></span>
              <div class="mt-1 text-secondary" style="font-size: 0.75rem;">Connecting to agent...</div>
            </div>

            <div
              v-for="(msg, i) in messages"
              :key="i"
              class="chat-entry"
              :class="msg.type === 'message' ? (msg.role === 'user' ? 'entry-user' : 'entry-agent') : 'entry-tool'"
            >
              <!-- Chat message -->
              <template v-if="msg.type === 'message'">
                <div class="message-role">{{ msg.role === 'user' ? 'you' : 'agent' }}</div>
                <div class="message-text">{{ msg.text }}</div>
              </template>

              <!-- Tool block -->
              <template v-else-if="msg.type === 'tool'">
                <div class="tool-header">
                  <span class="tool-kind">{{ msg.kind }}</span>
                  <span class="tool-name">{{ msg.name }}</span>
                  <span v-if="msg.status === 'active'" class="spinner" style="width: 10px; height: 10px; flex-shrink: 0;"></span>
                  <span v-else-if="msg.status === 'error'" class="tool-error-badge">error</span>
                  <button
                    v-else-if="isLongOutput(msg.output)"
                    class="tool-toggle"
                    @click="toggleToolExpanded(msg)"
                  >
                    {{ msg.expanded ? 'collapse' : 'expand' }}
                  </button>
                </div>
                <div v-if="msg.input" class="tool-input">{{ msg.input }}</div>
                <div
                  v-if="msg.output"
                  class="tool-output"
                  :class="{ 'tool-output-collapsed': isLongOutput(msg.output) && !msg.expanded }"
                >{{ msg.output }}</div>
              </template>
            </div>

            <!-- In-progress streaming message -->
            <div v-if="streamingMessage" class="chat-entry entry-agent">
              <div class="message-role">agent</div>
              <div class="message-text">{{ streamingMessage.text }}</div>
            </div>
          </div>

          <div class="chat-input-row">
            <textarea
              ref="textareaEl"
              v-model="messageText"
              class="chat-input"
              placeholder="Type a message..."
              rows="1"
              :disabled="sending || saved"
              @keydown="handleInputKeydown"
              @input="autoResizeTextarea($event.target)"
            />
            <button
              class="btn btn-secondary"
              style="flex-shrink: 0;"
              :disabled="interrupting || !slug || saved"
              @click="interruptAgent"
            >
              {{ interrupting ? '...' : 'Interrupt' }}
            </button>
            <button
              class="btn btn-primary"
              style="flex-shrink: 0;"
              :disabled="sending || !messageText.trim() || saved"
              @click="sendMessage"
            >
              {{ sending ? '...' : 'Send' }}
            </button>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.repo-card {
  cursor: pointer;
  overflow: hidden;
}

.repo-name {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.repo-meta {
  font-size: 0.72rem;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-bottom: 0.5rem;
}

.wizard-layout {
  display: flex;
  gap: 0;
  height: calc(100vh - 12rem);
  min-height: 400px;
  border: 1px solid var(--border-light);
  border-radius: 6px;
  overflow: hidden;
}

.checklist-panel {
  width: 260px;
  flex-shrink: 0;
  background: var(--bg-card);
  border-right: 1px solid var(--border-light);
  display: flex;
  flex-direction: column;
  padding: 1.25rem 1rem;
  gap: 0;
}

.checklist-steps {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.checklist-step {
  display: flex;
  align-items: flex-start;
  gap: 0.6rem;
  font-size: 0.78rem;
}

.step-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 1rem;
  height: 1.4em;
  flex-shrink: 0;
  font-family: var(--font-mono);
  font-size: 0.7rem;
}

.icon-done { color: var(--green); font-weight: bold; }
.icon-error { color: var(--red); font-weight: bold; }
.icon-skipped { color: var(--text-dim); }
.icon-pending { color: var(--text-dim); }

.checklist-step.done .step-label { color: var(--green); }
.checklist-step.error .step-label { color: var(--red); }
.checklist-step.active .step-label { color: var(--text-bright); }
.checklist-step.skipped .step-label { color: var(--text-dim); }
.checklist-step.pending .step-label { color: var(--text-secondary); }

.step-label {
  line-height: 1.4;
}

.step-skipped-label {
  text-decoration: line-through;
}

.step-optional {
  color: var(--text-dim);
  font-size: 0.68rem;
  margin-left: 0.25rem;
}

.checklist-footer {
  padding-top: 1rem;
  border-top: 1px solid var(--border-light);
  margin-top: 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.setup-complete-notice {
  font-size: 0.72rem;
  color: var(--green);
  line-height: 1.4;
}

/* Chat panel */
.chat-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  background: var(--bg);
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.chat-entry {
  max-width: 100%;
}

.entry-agent {
  align-self: flex-start;
  max-width: 85%;
}

.entry-user {
  align-self: flex-end;
  max-width: 85%;
}

.entry-tool {
  align-self: stretch;
}

.message-role {
  font-size: 0.65rem;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-dim);
  margin-bottom: 0.2rem;
}

.entry-user .message-role {
  text-align: right;
}

.message-text {
  font-size: 0.82rem;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.entry-agent .message-text {
  color: var(--text);
}

.entry-user .message-text {
  background: rgba(200, 180, 150, 0.08);
  border: 1px solid var(--border-light);
  border-radius: 6px;
  padding: 0.5rem 0.75rem;
  color: var(--text-bright);
}

/* Tool blocks */
.tool-header {
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
  background: var(--bg-terminal);
  border: 1px solid var(--border);
  border-bottom: none;
  border-radius: 6px 6px 0 0;
  padding: 0.4rem 0.75rem;
  font-size: 0.72rem;
}

.tool-kind {
  color: var(--text-dim);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  font-size: 0.65rem;
  flex-shrink: 0;
}

.tool-name {
  color: var(--text-bright);
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.tool-error-badge {
  color: var(--red);
  font-size: 0.65rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  flex-shrink: 0;
}

.tool-input {
  background: var(--bg-terminal);
  border-left: 1px solid var(--border);
  border-right: 1px solid var(--border);
  border-top: 1px solid var(--border-light);
  padding: 0.3rem 0.75rem;
  font-family: var(--font-mono);
  font-size: 0.72rem;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* When input is the last child (no output), close the border */
.tool-input:last-child {
  border-bottom: 1px solid var(--border);
  border-radius: 0 0 6px 6px;
}

.tool-toggle {
  background: none;
  border: none;
  cursor: pointer;
  color: var(--text-dim);
  font-family: var(--font-mono);
  font-size: 0.68rem;
  padding: 0;
  flex-shrink: 0;
}

.tool-toggle:hover {
  color: var(--text);
}

.tool-output {
  background: var(--bg-terminal);
  border: 1px solid var(--border);
  border-top: 1px solid var(--border-light);
  border-radius: 0 0 6px 6px;
  padding: 0.5rem 0.75rem;
  font-family: var(--font-mono);
  font-size: 0.75rem;
  color: var(--text);
  white-space: pre-wrap;
  word-break: break-all;
  line-height: 1.5;
  overflow: hidden;
}

.tool-output-collapsed {
  max-height: calc(1.5em * 4 + 1rem);
  -webkit-mask-image: linear-gradient(to bottom, black 60%, transparent 100%);
  mask-image: linear-gradient(to bottom, black 60%, transparent 100%);
}

/* Chat input */
.chat-input-row {
  display: flex;
  align-items: flex-end;
  gap: 0.5rem;
  padding: 0.75rem 1rem;
  border-top: 1px solid var(--border-light);
  background: var(--bg-card);
}

.chat-input {
  flex: 1;
  background: var(--bg);
  border: 1px solid var(--border);
  color: var(--text);
  font-family: var(--font-mono);
  font-size: 0.82rem;
  padding: 0.5rem 0.75rem;
  border-radius: 4px;
  resize: none;
  overflow-y: auto;
  min-height: 2rem;
}

.chat-input:focus {
  outline: none;
  border-color: var(--text);
}

.chat-input:disabled {
  opacity: 0.5;
}
</style>

<style>
/* Unscoped: widen parent container when wizard is active */
.main-content.main-content--wide {
  max-width: none;
}
</style>
