<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useGraphLayout } from '../composables/useGraphLayout'
import { useSessionStream } from '../composables/useSessionStream'
import AgentNode from '../components/AgentNode.vue'
import ChatPanel from '../components/ChatPanel.vue'
import { get, post } from '../api'
import type { Edge, AgentStatus, AgentRecentMessage, TraceEvent, ExecutionDetail } from '../types'
import {
  agents as fakeAgents,
  edges as fakeEdges,
  agentStates as fakeAgentStates,
  recentMessages as fakeRecentMessages,
  chatMessages as fakeChatMessages,
} from '../fake-graph-data'

const route = useRoute()
const slug = computed(() => {
  const s = route.params.slug
  return (Array.isArray(s) ? s[0] : s) || ''
})
const isDemo = computed(() => slug.value === 'demo')

// --- Data sources ---
const agents = ref<string[]>([])
const edges = ref<Edge[]>([])

// Session stream (only used in live mode)
const stream = useSessionStream(slug, { raw: true })

// Unified accessors
const agentList = computed(() => {
  if (isDemo.value) {
    devExtra.value // subscribe to toggle changes
    return [...fakeAgents]
  }
  return agents.value.map(name => ({ name }))
})

const edgeList = computed(() => {
  if (isDemo.value) {
    devExtra.value // subscribe to toggle changes
    return [...fakeEdges]
  }
  return edges.value
})

function getStatus(name: string): AgentStatus {
  if (isDemo.value) return fakeAgentStates[name]?.status || 'idle'
  return stream.agentState.value[name]?.status || 'idle'
}

function getCaption(name: string): string {
  if (isDemo.value) return fakeAgentStates[name]?.caption || ''
  return stream.agentState.value[name]?.caption || ''
}

function getRecentMessages(name: string): AgentRecentMessage[] {
  if (isDemo.value) return fakeRecentMessages[name] || []
  return stream.agentState.value[name]?.recentMessages || []
}

function getChatMessages(name: string): TraceEvent[] {
  if (isDemo.value) return fakeChatMessages[name] || []
  return stream.allEvents.value[name] || []
}

// --- Layout ---
const hoveredAgent = ref<string | null>(null)
const selectedAgent = ref<string | null>(null)
const flashingEdge = ref(-1)
const graphArea = ref<HTMLElement | null>(null)

const { positions, containerWidth, containerHeight } = useGraphLayout(
  computed(() => agentList.value),
  computed(() => edgeList.value),
  {},
)

const nodeW = 280
const nodeH = 80

// Auto-scale graph to fit available space
const graphAreaSize = ref({ width: 0, height: 0 })
let resizeObserver: ResizeObserver | null = null

// Scale factor: how much to multiply layout positions to fill the viewport area
const graphScale = computed(() => {
  const areaW = graphAreaSize.value.width
  const areaH = graphAreaSize.value.height
  if (!areaW || !areaH || !containerWidth.value || !containerHeight.value) return 1
  const scaleX = areaW / containerWidth.value
  const scaleY = areaH / containerHeight.value
  return Math.min(scaleX, scaleY) * 0.92
})

// Offset to center the scaled layout within the area
const graphOffset = computed(() => {
  const areaW = graphAreaSize.value.width
  const areaH = graphAreaSize.value.height
  const s = graphScale.value
  const scaledW = containerWidth.value * s
  const scaledH = containerHeight.value * s
  return {
    x: (areaW - scaledW) / 2,
    y: (areaH - scaledH) / 2,
  }
})

// Map layout position to viewport position
function viewPos(name: string): { x: number; y: number } | null {
  const pos = positions.value.get(name)
  if (!pos) return null
  const s = graphScale.value
  const off = graphOffset.value
  return { x: pos.x * s + off.x, y: pos.y * s + off.y }
}

const edgeSet = computed(() => {
  const set = new Set<string>()
  for (const { from, to } of edgeList.value) {
    set.add(`${from}->${to}`)
  }
  return set
})

function edgePath(edge: Edge): string {
  const from = viewPos(edge.from)
  const to = viewPos(edge.to)
  if (!from || !to) return ''
  const x1 = from.x + nodeW / 2
  const y1 = from.y
  const x2 = to.x - nodeW / 2
  const y2 = to.y
  const dx = x2 - x1

  if (dx < 0) {
    const spread = 24
    const x1b = from.x - nodeW / 2
    const x2b = to.x + nodeW / 2
    const midX = (x1b + x2b) / 2
    const midY = (y1 + y2) / 2
    const gap = Math.abs(x1b - x2b)
    const arc = gap * 0.35
    return `M ${x1b} ${y1 + spread} Q ${midX} ${midY + spread + arc} ${x2b} ${y2 + spread}`
  }

  if (edgeSet.value.has(`${edge.to}->${edge.from}`)) {
    const spread = 24
    const midX = (x1 + x2) / 2
    const midY = (y1 + y2) / 2
    const arc = dx * 0.35
    return `M ${x1} ${y1 - spread} Q ${midX} ${midY - spread - arc} ${x2} ${y2 - spread}`
  }

  const cpOffset = Math.max(dx * 0.4, 40)
  return `M ${x1} ${y1} C ${x1 + cpOffset} ${y1}, ${x2 - cpOffset} ${y2}, ${x2} ${y2}`
}

function isEdgeRelevant(edge: Edge): boolean {
  if (!hoveredAgent.value) return true
  return edge.from === hoveredAgent.value || edge.to === hoveredAgent.value
}

function selectAgent(name: string) {
  selectedAgent.value = selectedAgent.value === name ? null : name
}

// --- Edge flash ---
let flashTimer: ReturnType<typeof setTimeout> | null = null
let flashClearTimer: ReturnType<typeof setTimeout> | null = null

function getActiveNames(): string[] {
  if (isDemo.value) {
    return Object.entries(fakeAgentStates)
      .filter(([, s]) => s.status === 'active')
      .map(([n]) => n)
  }
  return Object.entries(stream.agentState.value)
    .filter(([, s]) => s.status === 'active')
    .map(([n]) => n)
}

function scheduleFlash() {
  const delay = 2000 + Math.random() * 2000
  flashTimer = setTimeout(() => {
    const activeNames = getActiveNames()
    if (activeNames.length === 0) { scheduleFlash(); return }

    const currentEdges = edgeList.value
    const candidates = currentEdges
      .map((e, i) => ({ ...e, idx: i }))
      .filter(e => activeNames.includes(e.from) || activeNames.includes(e.to))
    if (candidates.length === 0) { scheduleFlash(); return }

    const pick = candidates[Math.floor(Math.random() * candidates.length)]
    flashingEdge.value = pick.idx
    flashClearTimer = setTimeout(() => { flashingEdge.value = -1 }, 400)

    scheduleFlash()
  }, delay)
}

// --- Dev toggle (demo mode only) ---
const devExtra = ref(false)
function toggleDevAgent() {
  if (!isDemo.value) return
  if (devExtra.value) {
    fakeAgents.splice(fakeAgents.findIndex(a => a.name === 'deploy-staging-exec'), 1)
    fakeEdges.splice(fakeEdges.findIndex(e => e.from === 'code-reviewer' && e.to === 'deploy-staging-exec'), 1)
  } else {
    fakeAgents.push({ name: 'deploy-staging-exec' })
    fakeEdges.push({ from: 'code-reviewer', to: 'deploy-staging-exec' })
    fakeAgentStates['deploy-staging-exec'] = { status: 'active', caption: 'deploying to staging' }
    fakeRecentMessages['deploy-staging-exec'] = [
      { type: 'tool_use', agent: 'deploy-staging-exec', tool: 'Terminal', params: 'git push origin main', ts: '2024-03-10T00:02:00Z' },
      { type: 'response_chunk', agent: 'deploy-staging-exec', text: 'Pushing to staging environment.', ts: '2024-03-10T00:02:01Z' },
    ]
  }
  devExtra.value = !devExtra.value
}

// --- Chat input (live mode only) ---
async function sendMessage(agentName: string, text: string) {
  if (isDemo.value || !text.trim()) return
  await post(`/executions/${slug.value}/agents/${agentName}/message`, { text: text.trim() })
}

// --- Lifecycle ---
async function initLive() {
  try {
    const data = await get<ExecutionDetail>(`/executions/${slug.value}`)
    agents.value = data.agents || []
    edges.value = data.edges || []
    stream.connect()
  } catch (e) {
    console.error('Failed to load execution:', e)
  }
}

watch(stream.agents, (streamAgents) => {
  for (const name of streamAgents) {
    if (!agents.value.includes(name)) {
      agents.value = [...agents.value, name]
    }
  }
})

watch(stream.topology, (topo) => {
  if (!topo) return
  edges.value = topo.edges
})

const autoSelectDemo = computed(() => {
  const agent = route.query.agent
  return Array.isArray(agent) ? agent[0] : agent
})

onMounted(() => {
  if (!isDemo.value) {
    initLive()
  }
  scheduleFlash()
  if (autoSelectDemo.value) {
    selectedAgent.value = autoSelectDemo.value
  }
  if (graphArea.value) {
    resizeObserver = new ResizeObserver(entries => {
      const { width, height } = entries[0].contentRect
      graphAreaSize.value = { width, height }
    })
    resizeObserver.observe(graphArea.value)
  }
})

onUnmounted(() => {
  if (flashTimer) clearTimeout(flashTimer)
  if (flashClearTimer) clearTimeout(flashClearTimer)
  resizeObserver?.disconnect()
  stream.disconnect()
})
</script>

<template>
  <div class="graph-page">
    <!-- Graph area (left) -->
    <div ref="graphArea" class="graph-area">
      <div class="graph-container">
        <!-- SVG edge layer -->
        <svg
          class="graph-edges"
          :viewBox="`0 0 ${graphAreaSize.width} ${graphAreaSize.height}`"
          :style="{ width: '100%', height: '100%' }"
        >
          <defs>
            <marker id="arrow" viewBox="0 0 10 8" refX="9" refY="4"
              markerWidth="8" markerHeight="6" orient="auto-start-reverse">
              <path d="M 1 1 L 8 4 L 1 7" fill="var(--text-secondary)" stroke="none" opacity="0.5" />
            </marker>
          </defs>
          <path
            v-for="(edge, i) in edgeList"
            :key="`${edge.from}-${edge.to}`"
            class="graph-edge"
            :class="{
              dimmed: !isEdgeRelevant(edge),
              flash: flashingEdge === i,
            }"
            :style="{ d: `path('${edgePath(edge)}')` }"
            marker-end="url(#arrow)"
          />
        </svg>

        <!-- HTML node layer -->
        <div class="graph-nodes">
          <div
            v-for="agent in agentList"
            :key="agent.name"
            class="node-wrapper"
            :class="{ 'node-wrapper-hovered': hoveredAgent === agent.name }"
            :style="{
              transform: viewPos(agent.name)
                ? `translate(${viewPos(agent.name)!.x - nodeW / 2}px, ${viewPos(agent.name)!.y - nodeH / 2}px)`
                : '',
            }"
          >
            <AgentNode
              :name="agent.name"
              :status="getStatus(agent.name)"
              :caption="getCaption(agent.name)"
              :recent-messages="getRecentMessages(agent.name)"
              :dimmed="hoveredAgent != null && hoveredAgent !== agent.name"
              :hovered="hoveredAgent === agent.name"
              :selected="selectedAgent === agent.name"
              @mouseenter="hoveredAgent = agent.name"
              @mouseleave="hoveredAgent = null"
              @click="selectAgent(agent.name)"
            />
          </div>
        </div>
      </div>

      <!-- Execution label -->
      <div class="exec-label">
        {{ isDemo ? 'demo' : slug }}
      </div>

      <!-- Dev toggle (demo mode only) -->
      <button v-if="isDemo" class="dev-toggle" @click="toggleDevAgent">
        {{ devExtra ? '- agent' : '+ agent' }}
      </button>

      <!-- Connection status (live mode) -->
      <div v-if="!isDemo" class="connection-status" :class="{ connected: stream.isConnected.value }">
        {{ stream.isDone.value ? 'done' : stream.isConnected.value ? 'live' : 'connecting...' }}
      </div>

      <!-- Program state panel -->
      <div v-if="!isDemo && Object.keys(stream.programState.value).length > 0" class="program-state-panel">
        <div class="program-state-header">Program State</div>
        <div class="program-state-content">
          <div v-for="(value, key) in stream.programState.value" :key="key" class="state-row">
            <span class="state-key">{{ key }}</span>
            <span class="state-value">{{ value }}</span>
          </div>
        </div>
      </div>
    </div>

    <div v-if="selectedAgent" class="chat-sidebar">
      <ChatPanel
        :agent-name="selectedAgent"
        :messages="getChatMessages(selectedAgent)"
        @close="selectedAgent = null"
        @send="(text: string) => sendMessage(selectedAgent!, text)"
      />
    </div>
  </div>
</template>

<!-- Override main-content constraints for full-bleed graph -->
<style>
.main-content:has(.graph-page) {
  max-width: none;
  padding: 0;
  overflow: hidden;
  flex: 1;
  min-height: 0;
}

.main-content:has(.graph-page) > * {
  max-width: none;
}

#app:has(.graph-page) {
  height: 100vh;
  overflow: hidden;
}
</style>

<style scoped>
.graph-page {
  width: 100%;
  height: 100%;
  display: flex;
  background: var(--bg);
}

.graph-area {
  flex: 1;
  overflow: hidden;
  position: relative;
}

.graph-container {
  position: absolute;
  inset: 0;
}

.graph-edges {
  position: absolute;
  top: 0;
  left: 0;
  pointer-events: none;
  overflow: visible;
}

.graph-edge {
  fill: none;
  stroke: var(--text-secondary);
  stroke-width: 1.2;
  stroke-dasharray: 4 3;
  opacity: 0.5;
  transition: d 0.4s ease, opacity 0.25s ease, stroke 0.15s ease;
}

.graph-edge.dimmed {
  opacity: 0.15;
}

.graph-edge.flash {
  stroke: var(--green);
  stroke-dasharray: none;
  opacity: 0.6;
  stroke-width: 1.5;
}

.graph-nodes {
  position: absolute;
  inset: 0;
}

.node-wrapper {
  position: absolute;
  transition: transform 0.4s ease;
  z-index: 1;
}

.node-wrapper-hovered {
  z-index: 10;
}

/* Execution label */
.exec-label {
  position: absolute;
  top: 1rem;
  left: 1rem;
  font-size: 0.65rem;
  font-family: var(--font-mono);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--text-dim);
}

/* Dev toggle */
.dev-toggle {
  position: absolute;
  bottom: 1rem;
  right: 1rem;
  font-size: 0.7rem;
  font-family: var(--font-mono);
  padding: 0.3rem 0.6rem;
  border: 1px dotted var(--border);
  border-radius: 2px;
  background: var(--bg);
  color: var(--text-dim);
  cursor: pointer;
}

.dev-toggle:hover {
  color: var(--text-bright);
  border-color: var(--text-secondary);
}

/* Connection status */
.connection-status {
  position: absolute;
  bottom: 1rem;
  right: 1rem;
  font-size: 0.65rem;
  font-family: var(--font-mono);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-dim);
}

.connection-status.connected {
  color: var(--green);
}

/* Program state panel */
.program-state-panel {
  position: absolute;
  top: 1rem;
  right: 1rem;
  max-width: 300px;
  background: rgba(44, 39, 34, 0.95);
  border: 1px dotted rgba(200, 180, 150, 0.25);
  border-radius: 4px;
  backdrop-filter: blur(4px);
}

.program-state-header {
  padding: 0.5rem 0.75rem;
  font-size: 0.65rem;
  font-family: var(--font-mono);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--text-secondary);
  border-bottom: 1px dotted rgba(200, 180, 150, 0.15);
}

.program-state-content {
  padding: 0.5rem;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  max-height: 300px;
  overflow-y: auto;
}

.state-row {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  padding: 0.4rem 0.5rem;
  background: rgba(200, 180, 150, 0.05);
  border-radius: 2px;
  font-size: 0.72rem;
}

.state-key {
  font-size: 0.65rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: rgba(154, 143, 127, 0.8);
  font-weight: 600;
}

.state-value {
  font-family: var(--font-mono);
  color: rgba(212, 202, 186, 0.9);
  word-break: break-all;
}

/* Chat sidebar */
.chat-sidebar {
  width: 35%;
  max-width: 480px;
  min-width: 300px;
  align-self: stretch;
  flex-shrink: 0;
  margin: 1.5rem 1.5rem 1.5rem 0;
  border: 1px dotted var(--border);
  border-radius: 8px;
  overflow: hidden;
}
</style>
