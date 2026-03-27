<script setup lang="ts">
import { ref, computed, watch, nextTick } from 'vue'
import type { TraceEvent } from '../types'

// A merged message may have a mutable text field
interface MergedMessage {
  type: TraceEvent['type']
  text?: string
  agent?: string | null
  tool?: string
  params?: unknown
  result?: unknown
  exit_code?: number | null
  duration_secs?: number | null
  from?: string
  error?: string
  message?: string
  ts?: string
}

const props = withDefaults(defineProps<{
  agentName: string
  messages?: TraceEvent[]
  showHeader?: boolean
  formatToolNames?: boolean
  sending?: boolean
}>(), {
  showHeader: true,
  formatToolNames: true,
  sending: false,
})

const emit = defineEmits<{
  close: []
  send: [text: string]
}>()

const mergedMessages = computed(() => {
  const result: MergedMessage[] = []
  for (const msg of (props.messages || [])) {
    if (msg.type === 'response_chunk') {
      const prev = result.length ? result[result.length - 1] : null
      if (prev && prev.type === 'response_chunk') {
        prev.text = (prev.text || '') + (msg.text || '')
        continue
      }
      result.push({ ...msg })
    } else {
      result.push({ ...msg })
    }
  }
  return result
})

const inputText = ref('')

function handleSend() {
  const text = inputText.value.trim()
  if (!text || props.sending) return
  emit('send', text)
  inputText.value = ''
}

// Smart scroll: auto-scroll unless user has scrolled up
const scrollContainer = ref<HTMLElement | null>(null)
const userScrolledUp = ref(false)

function onScroll() {
  if (!scrollContainer.value) return
  const el = scrollContainer.value
  const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
  userScrolledUp.value = distanceFromBottom > 80
}

watch(
  () => mergedMessages.value.length,
  async () => {
    if (userScrolledUp.value) return
    await nextTick()
    if (scrollContainer.value) {
      scrollContainer.value.scrollTop = scrollContainer.value.scrollHeight
    }
  },
)

function parseFrom(msg: MergedMessage): { from: string; text: string } {
  if (msg.from) return { from: msg.from, text: msg.text || '' }
  const match = msg.text && msg.text.match(/^\[From: ([^\]]+)\]\s*/)
  if (match) return { from: match[1], text: (msg.text || '').slice(match[0].length) }
  return { from: 'you', text: msg.text || '' }
}

function formatTool(name: string): string {
  if (!name) return ''
  const short = name.includes('__') ? name.split('__').pop()! : name
  return short.replace(/^druids:/, '').toUpperCase()
}

function displayTool(name: string): string {
  return props.formatToolNames ? formatTool(name) : name
}

function truncate(val: unknown, max = 200): string {
  if (!val) return ''
  let text: string
  if (typeof val === 'object') {
    if (Array.isArray(val)) {
      text = val.map((v: unknown) => typeof v === 'string' ? v : (v as Record<string, unknown>).text || JSON.stringify(v)).join(' ')
    } else {
      text = JSON.stringify(val)
    }
  } else {
    text = String(val)
  }
  if (text.length <= max) return text
  return text.slice(0, max) + '...'
}
</script>

<template>
  <div class="chat-panel" :class="{ embedded: !showHeader }">
    <div v-if="showHeader" class="chat-header">
      <span class="chat-agent-name">{{ agentName }}</span>
      <button class="chat-close" @click="emit('close')">&times;</button>
    </div>
    <div ref="scrollContainer" class="chat-messages" @scroll="onScroll">
      <div
        v-for="(msg, i) in mergedMessages"
        :key="i"
        class="chat-msg"
        :class="msg.type"
      >
        <!-- connected / disconnected -->
        <template v-if="msg.type === 'connected' || msg.type === 'disconnected'">
          <div class="msg-status">{{ msg.type }}</div>
        </template>

        <!-- prompt (message from another agent or user) -->
        <template v-else-if="msg.type === 'prompt'">
          <div class="msg-from">{{ parseFrom(msg).from }}</div>
          <div class="msg-body">{{ parseFrom(msg).text }}</div>
        </template>

        <!-- tool_use -->
        <template v-else-if="msg.type === 'tool_use'">
          <div class="msg-tool-label">{{ displayTool(msg.tool || '') }}</div>
          <div v-if="msg.params" class="msg-tool-params">{{ truncate(msg.params) }}</div>
        </template>

        <!-- tool_result -->
        <template v-else-if="msg.type === 'tool_result'">
          <div class="msg-tool-label result">{{ displayTool(msg.tool || '') }} result</div>
          <div v-if="msg.result" class="msg-tool-result">{{ truncate(msg.result, 300) }}</div>
          <div v-if="msg.exit_code !== undefined && msg.exit_code !== null" class="msg-tool-meta">
            exit {{ msg.exit_code }}
            <template v-if="msg.duration_secs != null"> &middot; {{ Number(msg.duration_secs).toFixed(1) }}s</template>
          </div>
        </template>

        <!-- response_chunk (agent thinking/writing) -->
        <template v-else-if="msg.type === 'response_chunk'">
          <div class="msg-response">{{ msg.text }}</div>
        </template>

        <!-- error -->
        <template v-else-if="msg.type === 'error'">
          <div class="msg-error">{{ msg.text || msg.message }}</div>
        </template>
      </div>

      <div v-if="!mergedMessages.length" class="msg-empty">Waiting for activity...</div>
    </div>
    <div class="chat-input">
      <input
        v-model="inputText"
        type="text"
        :placeholder="`Message ${agentName}...`"
        :disabled="sending"
        @keydown.enter="handleSend"
      />
      <button class="chat-send" :disabled="sending" @click="handleSend">Send</button>
    </div>
  </div>
</template>

<style scoped>
.chat-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: #2c2722;
  color: #c4b9a8;
  overflow: hidden;
  border-radius: 7px;
}

.chat-panel.embedded {
  border-radius: 0;
}

.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.75rem 1rem;
  border-bottom: 1px dotted rgba(255, 255, 255, 0.1);
  flex-shrink: 0;
}

.chat-agent-name {
  font-size: 0.8rem;
  font-weight: 600;
  color: #e8dfd0;
  letter-spacing: 0.02em;
}

.chat-close {
  background: none;
  border: none;
  color: #8a7e6e;
  font-size: 1.2rem;
  cursor: pointer;
  padding: 0 0.25rem;
  line-height: 1;
}

.chat-close:hover {
  color: #e8dfd0;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 0.75rem 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

/* Message types */
.chat-msg {
  font-size: 0.78rem;
  line-height: 1.55;
  max-width: 95%;
  word-break: break-word;
}

/* Status: connected / disconnected */
.msg-status {
  font-size: 0.65rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: #6b6356;
  text-align: center;
  padding: 0.25rem 0;
}

.chat-msg.connected .msg-status {
  color: #2d7a3e;
}

.chat-msg.disconnected .msg-status {
  color: #a3403a;
}

/* Prompt: message from user or another agent */
.msg-from {
  font-size: 0.65rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: #8a7e6e;
  margin-bottom: 0.15rem;
}

.msg-body {
  color: #e0d8ca;
  padding: 0.5rem 0.75rem;
  background: rgba(200, 180, 150, 0.1);
  border-radius: 2px;
  align-self: flex-end;
  white-space: pre-wrap;
}

/* Tool use / result */
.msg-tool-label {
  font-size: 0.65rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: #8a7e6e;
}

.msg-tool-label.result {
  color: #6b8a5e;
}

.msg-tool-params,
.msg-tool-result {
  font-size: 0.7rem;
  font-family: var(--font-mono);
  color: #9a8f7f;
  white-space: pre-wrap;
  word-break: break-all;
  padding: 0.25rem 0.4rem;
  background: rgba(0, 0, 0, 0.15);
  border-radius: 2px;
  margin-top: 0.2rem;
  overflow: hidden;
  max-height: 4rem;
}

.msg-tool-result {
  color: #7a9a6a;
}

.msg-tool-meta {
  font-size: 0.65rem;
  color: #6a5f50;
  margin-top: 0.15rem;
}

/* Response chunk */
.msg-response {
  color: #d4c9b8;
  white-space: pre-wrap;
  padding: 0.5rem 0.75rem;
}

/* Error */
.msg-error {
  color: #c45a52;
  padding: 0.25rem 0.4rem;
  background: rgba(164, 64, 58, 0.1);
  border-radius: 2px;
  border-left: 2px solid #a3403a;
}

/* Empty state */
.msg-empty {
  color: #6a5f50;
  font-size: 0.75rem;
  text-align: center;
  padding: 2rem 0;
}

/* Chat input */
.chat-input {
  display: flex;
  gap: 0.4rem;
  padding: 0.6rem 1rem;
  border-top: 1px dotted rgba(255, 255, 255, 0.1);
  flex-shrink: 0;
}

.chat-input input {
  flex: 1;
  background: rgba(0, 0, 0, 0.2);
  border: 1px dotted rgba(255, 255, 255, 0.1);
  border-radius: 2px;
  color: #d4c9b8;
  font-family: var(--font-mono);
  font-size: 0.75rem;
  padding: 0.4rem 0.6rem;
  outline: none;
}

.chat-input input::placeholder {
  color: #6b6356;
}

.chat-input input:focus {
  border-color: rgba(255, 255, 255, 0.2);
}

.chat-send {
  background: none;
  border: 1px dotted rgba(255, 255, 255, 0.1);
  border-radius: 2px;
  color: #8a7e6e;
  font-size: 0.75rem;
  cursor: pointer;
  padding: 0.3rem 0.6rem;
  line-height: 1;
}

.chat-send:hover {
  color: #e8dfd0;
  border-color: rgba(255, 255, 255, 0.2);
}

.chat-send:disabled {
  opacity: 0.4;
  cursor: default;
}
</style>
