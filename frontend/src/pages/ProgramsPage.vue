<script setup>
import { ref, computed, onMounted, onUnmounted, onActivated, onDeactivated, nextTick, watch } from "vue"
import { useRoute, useRouter } from "vue-router"
import { get, post } from "../api.js"
import { statusClass, timeAgo } from "../utils.js"
import { useSessionStream } from "../composables/useSessionStream.js"

const route = useRoute()
const router = useRouter()

// --- State ---
const selectedTask = ref(null)
const loading = ref(false)
const error = ref(null)
const chatInputs = ref({}) // agent_name -> input text
const sendingMessage = ref({}) // agent_name -> boolean
const scrollStates = ref({}) // agent_name -> { userScrolledUp }
const expandedAgent = ref(null) // which agent accordion is open

// --- Computed ---
const selectedSlug = computed(() => route.params.slug)

// SSE stream via shared composable
const stream = useSessionStream(selectedSlug)

const agents = computed(() => {
  if (!selectedTask.value) return []
  const agentNames = new Set()
  for (const name of (selectedTask.value.agents || [])) {
    agentNames.add(name)
  }
  for (const name of (selectedTask.value.connections || [])) {
    agentNames.add(name)
  }
  // Also include agents discovered by the stream
  for (const name of stream.agents.value) {
    agentNames.add(name)
  }
  return Array.from(agentNames).sort()
})

function goBack() {
  router.push("/")
}

async function loadTaskDetail(slug) {
  loading.value = true
  chatInputs.value = {}
  scrollStates.value = {}

  try {
    const data = await get(`/executions/${slug}`)
    selectedTask.value = data
    stream.connect()
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

// --- Chat ---
async function sendMessage(agentName) {
  const text = (chatInputs.value[agentName] || "").trim()
  if (!text) return
  if (!selectedTask.value) return

  const exec = selectedTask.value

  chatInputs.value[agentName] = ""
  sendingMessage.value[agentName] = true
  try {
    await post(`/executions/${exec.execution_slug}/agents/${agentName}/message`, { text })
  } catch (e) {
    // Show error inline
    const events = stream.allEvents.value[agentName] || []
    events.push({
      type: "error",
      agent: agentName,
      error: `Failed to send: ${e.message}`,
      ts: new Date().toISOString(),
    })
    stream.allEvents.value = { ...stream.allEvents.value }
  } finally {
    sendingMessage.value[agentName] = false
  }
}

function onChatKeydown(e, agentName) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault()
    sendMessage(agentName)
  }
}

// --- Scroll ---
function onAgentScroll(agentName, e) {
  const el = e.target
  const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
  if (!scrollStates.value[agentName]) {
    scrollStates.value[agentName] = { userScrolledUp: false }
  }
  scrollStates.value[agentName].userScrolledUp = distanceFromBottom > 80
}

function scrollAgentToBottom(agentName) {
  const el = document.querySelector(`[data-agent-chat="${agentName}"]`)
  if (el) {
    el.scrollTop = el.scrollHeight
  }
}

// Auto-scroll when new events arrive for the expanded agent
watch(
  () => {
    const name = expandedAgent.value
    if (!name) return 0
    return (stream.allEvents.value[name] || []).length
  },
  async () => {
    const name = expandedAgent.value
    if (!name) return
    const state = scrollStates.value[name]
    if (!state || !state.userScrolledUp) {
      await nextTick()
      scrollAgentToBottom(name)
    }
  },
)

// --- Formatting ---
function formatTime(ts) {
  if (!ts) return ""
  const d = new Date(ts)
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })
}

function truncateText(text, max) {
  if (!text || text.length <= max) return text
  return text.slice(0, max) + "..."
}

function toggleAgent(agentName) {
  expandedAgent.value = expandedAgent.value === agentName ? null : agentName
  if (expandedAgent.value === agentName) {
    nextTick(() => scrollAgentToBottom(agentName))
  }
}

function agentSummary(agentName) {
  const events = stream.allEvents.value[agentName] || []
  let toolAction = ""
  let lastWords = ""

  for (let i = events.length - 1; i >= 0; i--) {
    if (!toolAction && events[i].type === "tool_use") {
      const toolName = events[i].tool || "tool"
      // Look forward for a matching tool_result
      let hasResult = false
      let duration = null
      for (let j = i + 1; j < events.length; j++) {
        if (events[j].type === "tool_result" && events[j].tool === events[i].tool) {
          hasResult = true
          if (events[j].duration_secs != null) {
            duration = Number(events[j].duration_secs).toFixed(1)
          }
          break
        }
      }
      if (hasResult) {
        toolAction = duration ? `${toolName} (${duration}s)` : toolName
      } else {
        toolAction = `Running: ${toolName}...`
      }
    }
    if (!lastWords && events[i].type === "response_chunk" && events[i].text) {
      const text = events[i].text.trim()
      lastWords = text.length > 120 ? text.slice(0, 120) + "..." : text
    }
    if (toolAction && lastWords) break
  }

  return { toolAction, lastWords }
}

function isAgentConnected(agentName) {
  const events = stream.allEvents.value[agentName] || []
  for (let i = events.length - 1; i >= 0; i--) {
    if (events[i].type === "connected") return true
    if (events[i].type === "disconnected") return false
  }
  // Check execution data
  if (selectedTask.value) {
    if ((selectedTask.value.connections || []).includes(agentName)) return true
  }
  return false
}

// --- Lifecycle ---
watch(selectedSlug, (slug) => {
  stream.disconnect()
  selectedTask.value = null
  if (slug) {
    loadTaskDetail(slug)
  }
})

onMounted(() => {
  if (selectedSlug.value) {
    loadTaskDetail(selectedSlug.value)
  }
})

onDeactivated(() => {
  stream.disconnect()
})

onActivated(() => {
  if (selectedSlug.value) {
    loadTaskDetail(selectedSlug.value)
  }
})

onUnmounted(() => {
  stream.disconnect()
})
</script>

<template>
  <div class="programs-page">
    <template v-if="selectedSlug">
      <div class="detail-header">
        <button class="btn btn-ghost" @click="goBack">&larr; back</button>
        <h1 v-if="selectedTask">{{ selectedTask.execution_slug }}</h1>
        <div v-if="selectedTask" class="detail-status">
          <span
            class="badge"
            :class="statusClass(selectedTask.status)"
          >
            {{ selectedTask.status }}
          </span>
        </div>
      </div>

      <div v-if="loading" class="empty-state">
        <span class="spinner"></span>
      </div>

      <div v-else-if="error" class="empty-state text-red">
        {{ error }}
      </div>

      <div v-if="selectedTask && selectedTask.error" class="execution-error">
        {{ selectedTask.error }}
      </div>

      <template v-if="selectedTask">
        <!-- Agent accordion -->
        <div class="agent-accordion">
          <div
            v-for="agentName in agents"
            :key="agentName"
            class="agent-card"
            :class="{ expanded: expandedAgent === agentName }"
          >
            <!-- Header (always visible, clickable) -->
            <div class="agent-card-header" @click="toggleAgent(agentName)">
              <span class="agent-dot" :class="{ connected: isAgentConnected(agentName) }"></span>
              <span class="agent-card-name">{{ agentName }}</span>
              <span class="expand-indicator">{{ expandedAgent === agentName ? "\u25BE" : "\u25B8" }}</span>
            </div>

            <!-- Summary (visible when collapsed) -->
            <div v-if="expandedAgent !== agentName" class="agent-summary" @click="toggleAgent(agentName)">
              <div v-if="agentSummary(agentName).toolAction" class="summary-tool">{{ agentSummary(agentName).toolAction }}</div>
              <div v-else class="summary-idle">Waiting for activity...</div>
              <div v-if="agentSummary(agentName).lastWords" class="summary-words">{{ agentSummary(agentName).lastWords }}</div>
            </div>

            <!-- Full chat (visible when expanded) -->
            <template v-if="expandedAgent === agentName">
              <div
                class="agent-chat"
                :data-agent-chat="agentName"
                @scroll="onAgentScroll(agentName, $event)"
              >
                <template v-for="(event, idx) in (stream.allEvents.value[agentName] || [])" :key="idx">
                  <!-- Tool use -->
                  <div v-if="event.type === 'tool_use'" class="chat-msg tool-msg">
                    <div class="tool-label">{{ event.tool }}</div>
                    <div v-if="event.params" class="tool-params">{{ truncateText(typeof event.params === 'string' ? event.params : JSON.stringify(event.params), 200) }}</div>
                  </div>

                  <!-- Tool result -->
                  <div v-else-if="event.type === 'tool_result'" class="chat-msg tool-msg">
                    <div class="tool-label">{{ event.tool }} result</div>
                    <div v-if="event.result" class="tool-params">{{ truncateText(event.result, 300) }}</div>
                    <div v-if="event.exit_code !== undefined && event.exit_code !== null" class="tool-meta">
                      exit {{ event.exit_code }}
                      <template v-if="event.duration_secs != null"> &middot; {{ Number(event.duration_secs).toFixed(1) }}s</template>
                    </div>
                  </div>

                  <!-- Agent response -->
                  <div v-else-if="event.type === 'response_chunk'" class="chat-msg agent-msg">
                    {{ event.text }}
                  </div>

                  <!-- Prompt (user or system) -->
                  <div v-else-if="event.type === 'prompt'" class="chat-msg user-msg">
                    {{ truncateText(event.text, 500) }}
                  </div>

                  <!-- Connected / disconnected -->
                  <div v-else-if="event.type === 'connected'" class="chat-msg system-msg">
                    connected
                  </div>
                  <div v-else-if="event.type === 'disconnected'" class="chat-msg system-msg">
                    disconnected
                  </div>

                  <!-- Error -->
                  <div v-else-if="event.type === 'error'" class="chat-msg error-msg">
                    {{ event.error }}
                  </div>
                </template>

                <div v-if="!(stream.allEvents.value[agentName] || []).length" class="chat-empty">
                  Waiting for activity...
                </div>
              </div>

              <!-- Chat input -->
              <div class="agent-input">
                <input
                  v-model="chatInputs[agentName]"
                  type="text"
                  :placeholder="`Message ${agentName}...`"
                  :disabled="sendingMessage[agentName]"
                  @keydown="onChatKeydown($event, agentName)"
                />
                <button
                  class="btn btn-sm btn-primary"
                  :disabled="sendingMessage[agentName] || !(chatInputs[agentName] || '').trim()"
                  @click="sendMessage(agentName)"
                >
                  Send
                </button>
              </div>
            </template>
          </div>
        </div>

        <!-- Spec -->
        <div v-if="selectedTask.spec" class="spec-section">
          <h3>Spec</h3>
          <pre class="spec-content">{{ selectedTask.spec }}</pre>
        </div>
      </template>
    </template>
  </div>
</template>

<style scoped>
.programs-page {
  width: 100%;
}

.clickable-row {
  cursor: pointer;
}

.spec-preview {
  margin-left: 0.5rem;
  font-size: 0.75rem;
}

/* Detail header */
.detail-header {
  display: flex;
  align-items: center;
  gap: 1rem;
  margin-bottom: 1.5rem;
}

.detail-status {
  margin-left: auto;
}

/* Agent accordion */
.agent-accordion {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  margin-bottom: 2rem;
}

@media (max-width: 720px) {
  .detail-header {
    flex-wrap: wrap;
  }
  .detail-header h1 {
    order: 3;
    width: 100%;
    font-size: 1.25rem;
    margin-top: 0.25rem;
  }
  .detail-status {
    margin-left: auto;
    order: 2;
  }
  .agent-card.expanded {
    height: 60vh;
  }
}

/* Agent card */
.agent-card {
  background: #2c2722;
  border: 1px dotted rgba(200, 180, 150, 0.2);
  border-radius: 2px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  transition: border-color 0.15s;
}

.agent-card:not(.expanded):hover {
  border-color: rgba(200, 180, 150, 0.35);
}

.agent-card.expanded {
  height: 500px;
}

.agent-card-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.75rem 1rem;
  cursor: pointer;
}

.agent-card.expanded .agent-card-header {
  border-bottom: 1px dotted rgba(200, 180, 150, 0.15);
}

.expand-indicator {
  margin-left: auto;
  color: #7a6f60;
  font-size: 0.75rem;
  user-select: none;
  transition: color 0.15s;
}

.agent-card:hover .expand-indicator {
  color: #9a8f7f;
}

.agent-summary {
  padding: 0.4rem 1rem 0.75rem;
  cursor: pointer;
}

.summary-tool {
  font-size: 0.72rem;
  color: #9a8f7f;
  font-family: var(--font-mono);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.summary-idle {
  font-size: 0.72rem;
  color: #6a5f50;
}

.summary-words {
  font-size: 0.72rem;
  color: #6a5f50;
  font-style: italic;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  margin-top: 0.2rem;
}

.agent-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #5a5046;
  flex-shrink: 0;
}

.agent-dot.connected {
  background: var(--green);
  animation: pulse 2s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}

.agent-card-name {
  font-size: 0.85rem;
  font-weight: 600;
  color: #d4caba;
}

/* Chat area */
.agent-chat {
  flex: 1;
  overflow-y: auto;
  padding: 0.75rem 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.chat-msg {
  font-size: 0.78rem;
  line-height: 1.55;
  max-width: 95%;
  word-break: break-word;
}

.agent-msg {
  padding: 0.5rem 0.75rem;
  border-radius: 2px;
  align-self: flex-start;
  color: #c8bda8;
  white-space: pre-wrap;
}

.user-msg {
  background: rgba(200, 180, 150, 0.1);
  padding: 0.5rem 0.75rem;
  border-radius: 2px;
  align-self: flex-end;
  color: #e0d8ca;
  white-space: pre-wrap;
}

.system-msg {
  font-size: 0.7rem;
  color: #6a5f50;
  align-self: center;
  text-align: center;
  padding: 0.2rem 0;
}

.tool-msg {
  font-family: var(--font-mono);
  font-size: 0.72rem;
  border-left: 2px dotted rgba(200, 180, 150, 0.25);
  padding: 0.4rem 0.6rem;
  color: #8a7e6e;
  align-self: flex-start;
}

.tool-label {
  font-size: 0.65rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: #9a8f7f;
  margin-bottom: 0.1rem;
}

.tool-params {
  white-space: pre-wrap;
  overflow: hidden;
  max-height: 4rem;
  color: #7a6f60;
}

.tool-meta {
  font-size: 0.65rem;
  color: #6a5f50;
  margin-top: 0.15rem;
}

.error-msg {
  color: var(--red);
  font-size: 0.75rem;
  align-self: center;
  padding: 0.25rem 0.5rem;
}

.execution-error {
  color: var(--red);
  padding: 1rem;
  border: 1px dotted var(--red);
  border-radius: 2px;
  font-family: var(--font-mono);
  font-size: 0.85rem;
  white-space: pre-wrap;
  margin-bottom: 1rem;
}

.chat-empty {
  color: #6a5f50;
  font-size: 0.75rem;
  text-align: center;
  padding: 2rem 0;
}

/* Chat input */
.agent-input {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  border-top: 1px dotted rgba(200, 180, 150, 0.15);
}

.agent-input input {
  flex: 1;
  background: transparent;
  border: none;
  padding: 0.4rem 0;
  font-size: 0.78rem;
  color: #c8bda8;
}

.agent-input input:focus {
  border: none;
  outline: none;
}

/* Spec section */
.spec-section {
  background: var(--bg-card);
  border: 1px dotted var(--border);
  border-radius: 2px;
  padding: 1rem 1.25rem;
}

.spec-section h3 {
  margin-bottom: 0.5rem;
}

.spec-content {
  font-family: var(--font-mono);
  font-size: 0.78rem;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--text);
}
</style>
