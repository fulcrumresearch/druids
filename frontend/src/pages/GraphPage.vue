<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useGraphLayout } from '../composables/useGraphLayout.js'
import { useSessionStream } from '../composables/useSessionStream.js'
import AgentNode from '../components/AgentNode.vue'
import ChatPanel from '../components/ChatPanel.vue'
import { get, post } from '../api.js'
import {
  agents as fakeAgents,
  edges as fakeEdges,
  agentStates as fakeAgentStates,
  recentMessages as fakeRecentMessages,
  chatMessages as fakeChatMessages,
} from '../fake-graph-data.js'

const route = useRoute()
const slug = computed(() => route.params.slug)
const isDemo = computed(() => slug.value === 'demo')

// --- Data sources ---
// Demo mode: fake data. Live mode: SSE stream + API.

const agents = ref([])
const edges = ref([])
const liveAgentState = ref({})
const liveAllEvents = ref({})

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

function getStatus(name) {
  if (isDemo.value) return fakeAgentStates[name]?.status || 'idle'
  return stream.agentState.value[name]?.status || 'idle'
}

function getCaption(name) {
  if (isDemo.value) return fakeAgentStates[name]?.caption || ''
  return stream.agentState.value[name]?.caption || ''
}

function getRecentMessages(name) {
  if (isDemo.value) return fakeRecentMessages[name] || []
  return stream.agentState.value[name]?.recentMessages || []
}

function getChatMessages(name) {
  if (isDemo.value) return fakeChatMessages[name] || []
  return stream.allEvents.value[name] || []
}

// --- Layout ---
const hoveredAgent = ref(null)
const selectedAgent = ref(null)
const flashingEdge = ref(-1)
const graphArea = ref(null)

const { positions, containerWidth, containerHeight } = useGraphLayout(
  computed(() => agentList.value),
  computed(() => edgeList.value),
  {},
)

const nodeW = 210
const nodeH = 60

// Auto-scale graph to fit available space
const graphAreaSize = ref({ width: 0, height: 0 })
let resizeObserver = null

const graphScale = computed(() => {
  const areaW = graphAreaSize.value.width
  const areaH = graphAreaSize.value.height
  if (!areaW || !areaH || !containerWidth.value || !containerHeight.value) return 1
  const scaleX = areaW / containerWidth.value
  const scaleY = areaH / containerHeight.value
  const scale = Math.min(scaleX, scaleY)
  return Math.min(scale, 1) // never scale up, only down
})

// Compute left/top to center the scaled container in the graph area
const graphContainerStyle = computed(() => {
  const s = graphScale.value
  const cw = containerWidth.value
  const ch = containerHeight.value
  const aw = graphAreaSize.value.width
  const ah = graphAreaSize.value.height
  // Before ResizeObserver fires, fall back to CSS centering
  if (!aw || !ah) {
    return {
      width: cw + 'px',
      height: ch + 'px',
      left: '50%',
      top: '50%',
      transform: 'translate(-50%, -50%)',
    }
  }
  const left = (aw - cw * s) / 2
  const top = (ah - ch * s) / 2
  return {
    width: cw + 'px',
    height: ch + 'px',
    transform: `scale(${s})`,
    left: left + 'px',
    top: top + 'px',
  }
})

const edgeSet = computed(() => {
  const set = new Set()
  for (const { from, to } of edgeList.value) {
    set.add(`${from}->${to}`)
  }
  return set
})

function edgePath(edge) {
  const from = positions.value.get(edge.from)
  const to = positions.value.get(edge.to)
  if (!from || !to) return ''
  const x1 = from.x + nodeW / 2
  const y1 = from.y
  const x2 = to.x - nodeW / 2
  const y2 = to.y
  const dx = x2 - x1

  // Back-edge (right-to-left): quadratic bezier arc below
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

  // Paired forward edge: quadratic bezier arc above
  if (edgeSet.value.has(`${edge.to}->${edge.from}`)) {
    const spread = 24
    const midX = (x1 + x2) / 2
    const midY = (y1 + y2) / 2
    const arc = dx * 0.35
    return `M ${x1} ${y1 - spread} Q ${midX} ${midY - spread - arc} ${x2} ${y2 - spread}`
  }

  // Unpaired forward edge: straight
  const cpOffset = Math.max(dx * 0.4, 40)
  return `M ${x1} ${y1} C ${x1 + cpOffset} ${y1}, ${x2 - cpOffset} ${y2}, ${x2} ${y2}`
}

function isEdgeRelevant(edge) {
  if (!hoveredAgent.value) return true
  return edge.from === hoveredAgent.value || edge.to === hoveredAgent.value
}

function selectAgent(name) {
  selectedAgent.value = selectedAgent.value === name ? null : name
}

// --- Edge flash ---
let flashTimer = null
let flashClearTimer = null

function getActiveNames() {
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
  // Operates on the fake data module directly
  if (devExtra.value) {
    fakeAgents.splice(fakeAgents.findIndex(a => a.name === 'deploy-staging-exec'), 1)
    fakeEdges.splice(fakeEdges.findIndex(e => e.from === 'code-reviewer' && e.to === 'deploy-staging-exec'), 1)
  } else {
    fakeAgents.push({ name: 'deploy-staging-exec' })
    fakeEdges.push({ from: 'code-reviewer', to: 'deploy-staging-exec' })
    fakeAgentStates['deploy-staging-exec'] = { status: 'active', caption: 'deploying to staging' }
    fakeRecentMessages['deploy-staging-exec'] = [
      { type: 'tool_use', tool: 'Terminal', params: 'git push origin main' },
      { type: 'response_chunk', text: 'Pushing to staging environment.' },
    ]
  }
  devExtra.value = !devExtra.value
}

// --- Chat input (live mode only) ---
async function sendMessage(agentName, text) {
  if (isDemo.value || !text.trim()) return
  await post(`/executions/${slug.value}/agents/${agentName}/message`, { text: text.trim() })
}

// --- Lifecycle ---
async function initLive() {
  try {
    const data = await get(`/executions/${slug.value}`)
    agents.value = (data.agents || [])
    edges.value = data.edges || []
    stream.connect()
  } catch (e) {
    console.error('Failed to load execution:', e)
  }
}

// Watch for new agents from the stream
watch(stream.agents, (streamAgents) => {
  for (const name of streamAgents) {
    if (!agents.value.includes(name)) {
      agents.value = [...agents.value, name]
    }
  }
})

// Watch for topology updates (agents + edges) from the stream
watch(stream.topology, (topo) => {
  if (!topo) return
  edges.value = topo.edges
})

// Auto-select first agent in demo mode for testing
const autoSelectDemo = computed(() => route.query.agent)

onMounted(() => {
  if (!isDemo.value) {
    initLive()
  }
  scheduleFlash()
  // Allow ?agent=name query param to auto-select
  if (autoSelectDemo.value) {
    selectedAgent.value = autoSelectDemo.value
  }
  // Track graph area size for auto-scaling
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
      <div
        class="graph-container"
        :style="graphContainerStyle"
      >
        <!-- SVG edge layer -->
        <svg
          class="graph-edges"
          :viewBox="`0 0 ${containerWidth} ${containerHeight}`"
          :style="{ width: containerWidth + 'px', height: containerHeight + 'px' }"
        >
          <defs>
            <marker id="arrow" viewBox="0 0 10 8" refX="9" refY="4"
              markerWidth="10" markerHeight="8" orient="auto">
              <path d="M 1 1 L 8 4 L 1 7" fill="none" stroke="var(--text-secondary)" stroke-width="1" opacity="0.6" />
            </marker>
          </defs>
          <path
            v-for="(edge, i) in edgeList"
            :key="`${edge.from}-${edge.to}`"
            :d="edgePath(edge)"
            class="graph-edge"
            :class="{
              dimmed: !isEdgeRelevant(edge),
              flash: flashingEdge === i,
            }"
            marker-end="url(#arrow)"
          />
        </svg>

        <!-- HTML node layer -->
        <div
          class="graph-nodes"
          :style="{ width: containerWidth + 'px', height: containerHeight + 'px' }"
        >
          <div
            v-for="agent in agentList"
            :key="agent.name"
            class="node-wrapper"
            :class="{ 'node-wrapper-hovered': hoveredAgent === agent.name }"
            :style="{
              transform: positions.get(agent.name)
                ? `translate(${positions.get(agent.name).x - nodeW / 2}px, ${positions.get(agent.name).y - nodeH / 2}px)`
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
    </div>

    <div v-if="selectedAgent" class="chat-sidebar">
      <ChatPanel
        :agent-name="selectedAgent"
        :messages="getChatMessages(selectedAgent)"
        @close="selectedAgent = null"
        @send="(text) => sendMessage(selectedAgent, text)"
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
  transform-origin: 0 0;
  transition: transform 0.35s ease, left 0.35s ease, top 0.35s ease;
}

.graph-edges {
  position: absolute;
  top: 0;
  left: 0;
  pointer-events: none;
}

.graph-edge {
  fill: none;
  stroke: var(--text-secondary);
  stroke-width: 1.2;
  stroke-dasharray: 4 3;
  opacity: 0.5;
  transition: opacity 0.25s ease, stroke 0.15s ease;
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
  top: 0;
  left: 0;
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
