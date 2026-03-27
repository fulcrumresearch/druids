<script setup lang="ts">
import { toRef } from 'vue'
import { useTypewriter } from '../composables/useTypewriter'
import type { AgentStatus, AgentRecentMessage } from '../types'

const props = defineProps<{
  name: string
  status?: AgentStatus
  caption?: string
  recentMessages?: AgentRecentMessage[]
  dimmed?: boolean
  hovered?: boolean
  selected?: boolean
}>()

defineEmits<{
  mouseenter: []
  mouseleave: []
  click: []
}>()

const captionRef = toRef(props, 'caption', '')
const { displayText: typedCaption, isTyping } = useTypewriter(captionRef, { speed: 30 })

function formatTool(name: string): string {
  if (!name) return ''
  const short = name.includes('__') ? name.split('__').pop()! : name
  return short.replace(/^druids:/, '')
}

function formatParams(params: Record<string, unknown> | string): string {
  if (!params) return ''
  if (typeof params === 'string') {
    return params.length > 60 ? params.slice(0, 60) + '...' : params
  }
  const entries = Object.entries(params).filter(([, v]) => v !== undefined && v !== '')
  if (entries.length === 0) return ''
  const [, val] = entries[0]
  const str = typeof val === 'string' ? val : JSON.stringify(val)
  return str.length > 60 ? str.slice(0, 60) + '...' : str
}

function formatResult(result: unknown): string {
  if (typeof result === 'string') return result.length > 80 ? result.slice(0, 80) + '...' : result
  if (Array.isArray(result)) {
    const text = result.map((r: Record<string, unknown>) => (r.text as string) || '').join(' ')
    return text.length > 80 ? text.slice(0, 80) + '...' : text
  }
  return String(result)
}

function dotColor(status: string | undefined): string {
  if (status === 'active') return 'var(--green)'
  if (status === 'blocked') return 'var(--yellow)'
  if (status === 'disconnected') return 'var(--red)'
  return 'var(--text-dim)'
}
</script>

<template>
  <div
    class="agent-node"
    :class="{ dimmed, active: status === 'active', idle: status === 'idle', blocked: status === 'blocked', hovered, selected, disconnected: status === 'disconnected' }"
    @mouseenter="$emit('mouseenter')"
    @mouseleave="$emit('mouseleave')"
    @click="$emit('click')"
  >
    <div class="node-header">
      <span
        class="node-dot"
        :style="{ background: dotColor(status) }"
        :class="{ pulse: status === 'active' }"
      ></span>
      <span class="node-name">{{ name }}</span>
    </div>
    <div class="node-caption">{{ typedCaption }}<span v-if="isTyping" class="typing-cursor">|</span></div>
    <div v-if="hovered && recentMessages?.length" class="node-recent">
      <div
        v-for="(msg, i) in recentMessages"
        :key="i"
        class="recent-msg"
        :class="msg.type"
      >
        <template v-if="msg.type === 'tool_use'">
          <span class="msg-tool">{{ formatTool(msg.tool) }}</span>
          <span v-if="formatParams(msg.params)" class="msg-params">{{ formatParams(msg.params) }}</span>
        </template>
        <template v-else-if="msg.type === 'tool_result'">
          <span class="msg-tool">{{ formatTool(msg.tool) }} result</span>
          <span class="msg-params">{{ formatResult(msg.result) }}</span>
        </template>
        <template v-else-if="msg.type === 'response_chunk'">
          <span class="msg-text">{{ msg.text }}</span>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.agent-node {
  width: 280px;
  padding: 0.6rem 0.75rem;
  border: 1px dotted rgba(0, 0, 0, 0.25);
  border-radius: 2px;
  background: var(--bg);
  cursor: default;
  transition: opacity 0.25s ease, border-color 0.25s ease;
}

.agent-node.dimmed {
  opacity: 0.2;
}

.agent-node.selected {
  border-color: var(--text-secondary);
  border-style: solid;
  border-left: 3px solid var(--text);
  background: var(--bg-terminal);
}

.agent-node.hovered {
  z-index: 10;
  border-color: var(--text-secondary);
}

.node-header {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}

.node-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  flex-shrink: 0;
}

.node-dot.pulse {
  animation: dot-pulse 2.5s ease-in-out infinite;
  box-shadow: 0 0 4px 1px rgba(45, 122, 62, 0.3);
}

@keyframes dot-pulse {
  0%, 100% { opacity: 1; box-shadow: 0 0 4px 1px rgba(45, 122, 62, 0.3); }
  50% { opacity: 0.4; box-shadow: 0 0 2px 0px rgba(45, 122, 62, 0.1); }
}

.node-name {
  font-size: 0.78rem;
  font-weight: 600;
  color: var(--text-bright);
  word-break: break-word;
}

.agent-node.idle {
  opacity: 0.8;
}

.agent-node.idle .node-caption {
  color: var(--text-secondary);
}

.agent-node.blocked {
  border-color: rgba(138, 107, 26, 0.45);
}

.agent-node.disconnected {
  opacity: 0.55;
  border-style: dashed;
}

.agent-node.disconnected .node-name {
  text-decoration: line-through;
  text-decoration-color: var(--text-dim);
}

.node-caption {
  font-size: 0.7rem;
  color: var(--text);
  margin-top: 0.25rem;
  line-height: 1.4;
}

.typing-cursor {
  animation: blink 0.6s step-end infinite;
  color: var(--text-dim);
  font-weight: 300;
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}

.node-recent {
  margin-top: 0.3rem;
  border-top: 1px dotted var(--border-light);
  padding-top: 0.3rem;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  max-height: 120px;
  overflow-y: auto;
}

.recent-msg {
  font-size: 0.65rem;
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
}

.recent-msg .msg-tool {
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  color: var(--text-dim);
  font-size: 0.6rem;
}

.recent-msg .msg-text {
  color: var(--text-secondary);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.recent-msg .msg-params {
  color: var(--text-dim);
  font-family: var(--font-mono);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  display: block;
}
</style>
