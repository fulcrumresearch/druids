<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import { get, post } from '../api'
import type { Dashboard, Devbox } from '../types'

const route = useRoute()
const routeRepo: string | null = route.params.owner
  ? `${route.params.owner as string}/${route.params.repo as string}`
  : null

// step: 0=loading, 1=enter repo, 2=provisioning, 3=wizard, 4=already configured
const step = ref(routeRepo ? 0 : 1)
const repoInput = ref(routeRepo || '')
const loading = ref(true)
const error = ref<string | null>(null)
const devbox = ref<Devbox | null>(null)

// Repo suggestions
const availableRepos = ref<string[]>([])
const filteredRepos = computed(() => {
  const q = repoInput.value.trim().toLowerCase()
  if (!q) return availableRepos.value
  return availableRepos.value.filter(r => r.toLowerCase().includes(q))
})

// Wizard types
interface ChatMessage {
  type: 'message'
  role: string
  text: string
  streaming?: boolean
}

interface ToolMessage {
  type: 'tool'
  id: string
  name: string
  kind: string
  input: string
  output: string
  status: string
  expanded: boolean
}

type WizardMessage = ChatMessage | ToolMessage

interface WizardStartResponse {
  slug: string
  status: 'started' | 'resumed'
  mode: 'setup' | 'modify'
}

// Wizard state
const activeRepo = ref<string | null>(null)
const slug = ref<string | null>(null)
const messages = ref<WizardMessage[]>([])
const messageText = ref('')
const sending = ref(false)
const saving = ref(false)
const saved = ref(false)
const chatEl = ref<HTMLElement | null>(null)
const userScrolledUp = ref(false)
const streamingMessage = ref<number | null>(null)
const textareaEl = ref<HTMLTextAreaElement | null>(null)
const interrupting = ref(false)
const resetting = ref(false)
const setupMode = ref<'setup' | 'modify'>('setup')

// SSE connection
let sseAbortController: AbortController | null = null
let lastEventId: string | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null


function parseSSEFrame(frame: string, onFrame: (id: string | null, event: string, data: string) => void) {
  if (!frame.trim()) return
  const lines = frame.split('\n')
  let id: string | null = null
  let event = 'message'
  let data = ''
  for (const line of lines) {
    if (line.startsWith('id:')) id = line.slice(3).trim()
    else if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) data = line.slice(5).trim()
  }
  if (data) onFrame(id, event, data)
}

function findToolById(msgs: WizardMessage[], id: string): ToolMessage | null {
  for (let i = msgs.length - 1; i >= 0; i--) {
    const msg = msgs[i]
    if (msg.type === 'tool' && msg.id === id) return msg
  }
  return null
}

function scrollToBottom() {
  if (userScrolledUp.value) return
  nextTick(() => {
    if (chatEl.value) chatEl.value.scrollTop = chatEl.value.scrollHeight
  })
}

function handleScroll() {
  if (!chatEl.value) return
  const el = chatEl.value
  const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40
  userScrolledUp.value = !atBottom
}

function handleEvent(id: string | null, event: string, rawData: string) {
  let data: Record<string, unknown>
  try { data = JSON.parse(rawData) } catch { return }

  if (id !== null) lastEventId = id

  if (event === 'message') {
    if (data.role === 'assistant' && streamingMessage.value !== null) {
      messages.value[streamingMessage.value] = {
        type: 'message', role: data.role as string, text: data.text as string,
      }
      streamingMessage.value = null
    } else {
      messages.value.push({
        type: 'message', role: data.role as string, text: data.text as string,
      })
    }
    scrollToBottom()
  }

  if (event === 'message_stream') {
    if (streamingMessage.value === null) {
      streamingMessage.value = messages.value.length
      messages.value.push({
        type: 'message', role: 'assistant', text: data.text as string, streaming: true,
      })
    } else {
      messages.value[streamingMessage.value] = {
        type: 'message', role: 'assistant', text: data.text as string, streaming: true,
      }
    }
    scrollToBottom()
  }

  if (event === 'tool') {
    const existing = findToolById(messages.value, data.id as string)
    if (existing) {
      existing.name = data.name as string
      existing.kind = data.kind as string
      existing.input = data.input as string
      existing.output = data.output as string
      existing.status = data.status as string
    } else {
      messages.value.push({
        type: 'tool',
        id: data.id as string,
        name: data.name as string,
        kind: data.kind as string,
        input: data.input as string,
        output: data.output as string,
        status: data.status as string,
        expanded: false,
      })
    }
    scrollToBottom()
  }

}

async function connectSSE() {
  if (sseAbortController) sseAbortController.abort()
  sseAbortController = new AbortController()

  const headers: Record<string, string> = {}
  if (lastEventId !== null) headers['Last-Event-ID'] = lastEventId

  try {
    const res = await fetch(`/api/setup/wizard/${slug.value}/chat`, {
      credentials: 'same-origin',
      headers,
      signal: sseAbortController.signal,
    })
    if (!res.ok || !res.body) {
      if (res.status === 404) {
        // Session is gone (server restart). Reset to step 1.
        slug.value = null
        step.value = 1
        return
      }
      return
    }

    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      const parts = buffer.split('\n\n')
      buffer = parts.pop()!
      for (const part of parts) {
        parseSSEFrame(part, handleEvent)
      }
    }
  } catch (e: unknown) {
    if (e instanceof Error && e.name === 'AbortError') return
  }

  reconnectTimer = setTimeout(connectSSE, 2000)
}

async function startWizard(repo: string, mode: 'setup' | 'modify' = 'setup') {
  error.value = null
  step.value = 2
  setupMode.value = mode

  try {
    const res = await post<WizardStartResponse>('/setup/wizard/start', { repo_full_name: repo, mode })
    slug.value = res.slug
    activeRepo.value = repo
    step.value = 3
    connectSSE()
  } catch (e: unknown) {
    error.value = (e as Error).message
    step.value = 1
  }
}

async function sendMessage() {
  if (!messageText.value.trim() || sending.value) return
  const text = messageText.value.trim()
  messageText.value = ''
  sending.value = true

  try {
    await post(`/setup/wizard/${slug.value}/message`, { text })
  } catch (e: unknown) {
    error.value = (e as Error).message
  } finally {
    sending.value = false
    nextTick(() => textareaEl.value?.focus())
  }
}

async function interrupt() {
  interrupting.value = true
  try {
    await post(`/setup/wizard/${slug.value}/interrupt`)
  } catch { /* best effort */ }
  interrupting.value = false
}

async function saveSnapshot() {
  saving.value = true
  try {
    await post('/setup/wizard/save', { repo_full_name: activeRepo.value })
    saved.value = true
  } catch (e: unknown) {
    error.value = (e as Error).message
  } finally {
    saving.value = false
  }
}

async function resetSetup() {
  if (!confirm('This will delete the current setup and start over. Continue?')) return
  resetting.value = true
  try {
    await post('/setup/wizard/reset', { repo_full_name: activeRepo.value || repoInput.value })
    slug.value = null
    activeRepo.value = null
    messages.value = []
    saved.value = false
    devbox.value = null
    step.value = 1
    if (sseAbortController) sseAbortController.abort()
  } catch (e: unknown) {
    error.value = (e as Error).message
  } finally {
    resetting.value = false
  }
}

function handleRepoSubmit() {
  const repo = repoInput.value.trim()
  if (!repo || !repo.includes('/')) return
  startWizard(repo)
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    sendMessage()
  }
}


function toggleToolOutput(msg: ToolMessage) {
  msg.expanded = !msg.expanded
}

function toolOutputLines(output: string): string[] {
  if (!output) return []
  return output.split('\n')
}

onMounted(async () => {
  // Fetch available repos in background (best-effort).
  get<{ repos: string[] }>('/github/repos')
    .then(r => { availableRepos.value = r.repos })
    .catch(() => {})

  if (!routeRepo) {
    loading.value = false
    return
  }

  try {
    const dash = await get<Dashboard>('/me/dashboard')
    const existing = dash.devboxes?.find(d => d.repo_full_name === routeRepo)

    if (existing) {
      devbox.value = existing

      if (existing.setup_slug) {
        slug.value = existing.setup_slug
        activeRepo.value = routeRepo
        step.value = 3
        connectSSE()
      } else if (existing.has_snapshot) {
        step.value = 4
      } else if (existing.instance_id) {
        step.value = 1
      } else {
        startWizard(routeRepo)
      }
    } else {
      startWizard(routeRepo)
    }
  } catch (e: unknown) {
    error.value = (e as Error).message
    step.value = 1
  } finally {
    loading.value = false
  }
})

onUnmounted(() => {
  if (sseAbortController) sseAbortController.abort()
  if (reconnectTimer) clearTimeout(reconnectTimer)
})
</script>

<template>
  <div>
    <div class="page-header">
      <h1>Setup</h1>
      <p>Configure a development environment for agents</p>
    </div>

    <!-- Step 0: Loading -->
    <div v-if="step === 0 || loading" class="empty-state">
      <span class="spinner"></span>
    </div>

    <!-- Step 1: Enter repo -->
    <div v-else-if="step === 1" style="max-width: 480px;">
      <div v-if="error" class="text-red mb-2" style="font-size: 0.8rem;">{{ error }}</div>

      <label>Repository</label>
      <div class="flex gap-1">
        <input
          v-model="repoInput"
          placeholder="owner/repo"
          @keydown.enter="handleRepoSubmit"
          style="flex: 1;"
        />
        <button class="btn btn-primary" @click="handleRepoSubmit" :disabled="!repoInput.includes('/')">
          Start
        </button>
      </div>
      <div v-if="filteredRepos.length" class="repo-suggestions">
        <button
          v-for="repo in filteredRepos"
          :key="repo"
          class="repo-suggestion"
          @click="repoInput = repo; handleRepoSubmit()"
        >
          {{ repo }}
        </button>
      </div>
      <p v-else class="text-dim mt-1" style="font-size: 0.72rem;">
        Enter the full repository name (e.g. acme/backend). The wizard will provision a VM,
        clone the repo, and walk through setup with you.
      </p>
    </div>

    <!-- Step 2: Provisioning -->
    <div v-else-if="step === 2" class="empty-state">
      <span class="spinner"></span>
      <p class="mt-2 text-secondary" style="font-size: 0.8rem;">
        Provisioning VM and cloning {{ repoInput }}...
      </p>
    </div>

    <!-- Step 3: Wizard -->
    <div v-else-if="step === 3" class="wizard-layout">
      <!-- Top bar -->
      <div class="wizard-topbar">
        <div class="flex gap-1" style="align-items: center;">
          <div v-if="saved" class="text-green" style="font-size: 0.72rem;">
            Snapshot saved.
          </div>
          <button
            v-if="!saved"
            class="btn btn-primary btn-sm"
            :disabled="saving"
            @click="saveSnapshot"
          >
            {{ saving ? 'Saving...' : 'Save snapshot' }}
          </button>
          <button
            class="btn btn-ghost"
            :disabled="resetting"
            @click="resetSetup"
            style="font-size: 0.68rem;"
          >
            {{ resetting ? 'Resetting...' : 'Redo setup' }}
          </button>
        </div>
      </div>

      <!-- Chat -->
      <div class="wizard-chat">
        <div class="wizard-chat-messages" ref="chatEl" @scroll="handleScroll">
          <div v-for="(msg, i) in messages" :key="i" class="chat-entry">
            <!-- Text message -->
            <template v-if="msg.type === 'message'">
              <div class="chat-message" :class="msg.role">
                <span class="chat-role">{{ msg.role === 'user' ? 'you' : 'agent' }}</span>
                <div class="chat-text">{{ msg.text }}</div>
              </div>
            </template>

            <!-- Tool call -->
            <template v-if="msg.type === 'tool'">
              <div class="chat-tool" :class="'tool-' + msg.status">
                <div class="tool-header" @click="toggleToolOutput(msg)">
                  <span class="tool-icon">{{ msg.status === 'active' ? '~' : msg.status === 'error' ? '!' : 'x' }}</span>
                  <span class="tool-name">{{ msg.name }}</span>
                  <span v-if="msg.input" class="tool-input">{{ msg.input }}</span>
                </div>
                <div v-if="msg.expanded && msg.output" class="tool-output">
                  <template v-if="toolOutputLines(msg.output).length > 20">
                    <pre>{{ toolOutputLines(msg.output).slice(0, 20).join('\n') }}</pre>
                    <span class="text-dim" style="font-size: 0.68rem;">
                      ... {{ toolOutputLines(msg.output).length - 20 }} more lines
                    </span>
                  </template>
                  <pre v-else>{{ msg.output }}</pre>
                </div>
              </div>
            </template>
          </div>
        </div>

        <!-- Input -->
        <div class="wizard-chat-input">
          <textarea
            ref="textareaEl"
            v-model="messageText"
            @keydown="handleKeydown"
            placeholder="Type a message..."
            rows="2"
          ></textarea>
          <div class="chat-input-buttons">
            <button class="btn btn-primary btn-sm" @click="sendMessage" :disabled="sending || !messageText.trim()">
              Send
            </button>
            <button class="btn btn-secondary btn-sm" @click="interrupt" :disabled="interrupting">
              {{ interrupting ? '...' : 'Interrupt' }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Step 4: Already configured -->
    <div v-else-if="step === 4" style="max-width: 480px;">
      <div class="card">
        <h3 style="margin-bottom: 0.5rem;">{{ repoInput }}</h3>
        <p class="text-secondary" style="font-size: 0.8rem; margin-bottom: 1rem;">
          This repository has a saved devbox snapshot. Agents will fork from this environment.
        </p>
        <div class="flex gap-1">
          <button class="btn btn-secondary btn-sm" @click="startWizard(repoInput, 'modify')">
            Modify setup
          </button>
          <button class="btn btn-ghost" @click="resetSetup" :disabled="resetting">
            {{ resetting ? 'Resetting...' : 'Redo setup' }}
          </button>
        </div>
      </div>
    </div>

    <!-- Error banner -->
    <div v-if="error && step === 3" class="text-red mt-2" style="font-size: 0.8rem;">
      {{ error }}
    </div>
  </div>
</template>

<style scoped>
.repo-suggestions {
  display: flex;
  flex-direction: column;
  gap: 1px;
  margin-top: 0.5rem;
}

.repo-suggestion {
  display: block;
  width: 100%;
  text-align: left;
  padding: 0.4rem 0.5rem;
  font-size: 0.78rem;
  color: var(--text-secondary);
  background: none;
  border: none;
  border-left: 2px solid transparent;
  cursor: pointer;
  font-family: var(--font-mono);
}

.repo-suggestion:hover {
  color: var(--text-bright);
  border-left-color: var(--text-secondary);
  background: var(--bg-hover);
}

.wizard-layout {
  display: flex;
  flex-direction: column;
  border: 1px dotted var(--border);
  border-radius: 2px;
  height: calc(100vh - 160px);
  min-height: 400px;
  overflow: hidden;
}

.wizard-topbar {
  padding: 0.5rem 1rem;
  border-bottom: 1px dotted var(--border);
  display: flex;
  justify-content: flex-end;
}

.wizard-chat {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.wizard-chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 1rem;
}

.chat-entry {
  margin-bottom: 0.75rem;
}

.chat-message {
  max-width: 100%;
}

.chat-role {
  font-size: 0.68rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--text-dim);
  display: block;
  margin-bottom: 0.15rem;
}

.chat-message.user .chat-role {
  color: var(--text-secondary);
}

.chat-text {
  font-size: 0.82rem;
  line-height: 1.6;
  white-space: pre-wrap;
  word-wrap: break-word;
}

.chat-message.user .chat-text {
  color: var(--text-bright);
}

.chat-tool {
  border-left: 2px dotted var(--border);
  padding: 0.25rem 0 0.25rem 0.75rem;
  font-size: 0.75rem;
}

.chat-tool.tool-active {
  border-left-color: var(--text-secondary);
}

.chat-tool.tool-done {
  border-left-color: var(--border);
}

.chat-tool.tool-error {
  border-left-color: var(--red);
}

.tool-header {
  display: flex;
  align-items: baseline;
  gap: 0.4rem;
  cursor: pointer;
  color: var(--text-secondary);
}

.tool-header:hover {
  color: var(--text);
}

.tool-icon {
  width: 0.8rem;
  text-align: center;
  flex-shrink: 0;
}

.tool-active .tool-icon {
  animation: spin 0.8s linear infinite;
  display: inline-block;
}

.tool-name {
  font-weight: normal;
}

.tool-input {
  color: var(--text-dim);
  font-size: 0.72rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 400px;
}

.tool-output {
  margin-top: 0.25rem;
  background: var(--bg-terminal);
  border-radius: 2px;
  padding: 0.5rem;
  overflow-x: auto;
}

.tool-output pre {
  margin: 0;
  font-size: 0.72rem;
  line-height: 1.4;
  white-space: pre-wrap;
  word-break: break-all;
}

.wizard-chat-input {
  border-top: 1px dotted var(--border);
  padding: 0.75rem 1rem;
  display: flex;
  gap: 0.5rem;
  align-items: flex-end;
}

.wizard-chat-input textarea {
  flex: 1;
  resize: none;
  font-size: 0.82rem;
  padding: 0.5rem 0.6rem;
  min-height: 2.5rem;
  max-height: 8rem;
}

.chat-input-buttons {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

@media (max-width: 720px) {
  .wizard-layout {
    height: auto;
    min-height: 80vh;
  }

  .wizard-chat-messages {
    min-height: 300px;
  }
}
</style>
