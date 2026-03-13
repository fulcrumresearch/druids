import { ref, onUnmounted } from 'vue'

/**
 * SSE stream composable for execution events.
 *
 * Connects to the execution's SSE endpoint and dispatches events to per-agent
 * state. Supports reconnection via Last-Event-ID and derives agent
 * active/idle status from event timestamps.
 *
 * @param {import('vue').Ref<string>} slugRef - reactive execution slug
 * @param {object} options
 * @param {boolean} options.raw - pass raw=true to get individual response chunks
 * @returns {{ agents, agentState, allEvents, isConnected, isDone }}
 */
export function useSessionStream(slugRef, { raw = false } = {}) {
  const agents = ref([])
  const agentState = ref({})
  const allEvents = ref({})
  const topology = ref(null)
  const isConnected = ref(false)
  const isDone = ref(false)

  let controller = null
  let reconnectTimer = null
  let lastEventId = 0
  let statusPollTimer = null

  const IDLE_THRESHOLD_MS = 5000 // tunable: 5s without events -> idle

  function ensureAgent(name) {
    if (!agentState.value[name]) {
      agentState.value[name] = {
        status: 'idle',
        lastEventTs: 0,
        caption: '',
        recentMessages: [],
      }
      agentState.value = { ...agentState.value }
    }
    if (!allEvents.value[name]) {
      allEvents.value[name] = []
      allEvents.value = { ...allEvents.value }
    }
    if (!agents.value.includes(name)) {
      agents.value = [...agents.value, name]
    }
  }

  function dispatchEvent(id, event, data) {
    if (id) lastEventId = id

    if (event === 'done') {
      isDone.value = true
      return
    }

    let parsed
    try {
      parsed = JSON.parse(data)
    } catch {
      return
    }

    // Caption events from server-side summarizer (no agent field at top level)
    if (parsed.type === 'client_event' && parsed.event === 'caption') {
      const target = parsed.data?.agent
      if (target) {
        ensureAgent(target)
        agentState.value[target].caption = parsed.data.text
        agentState.value = { ...agentState.value }
      }
      return
    }

    // Topology events carry agents + edges for the whole execution
    if (parsed.type === 'topology') {
      topology.value = { agents: parsed.agents, edges: parsed.edges }
      for (const name of parsed.agents) {
        ensureAgent(name)
      }
      return
    }

    const agentName = parsed.agent
    if (!agentName) return

    ensureAgent(agentName)

    // Skip MCP-prefixed tool events (the native druids: version follows)
    const isMcpDupe = (parsed.type === 'tool_use' || parsed.type === 'tool_result')
      && parsed.tool && parsed.tool.startsWith('mcp__')

    // Store event (skip MCP duplicates)
    if (!isMcpDupe) {
      allEvents.value[agentName].push(parsed)
      allEvents.value = { ...allEvents.value }
    }

    // Update agent state
    const state = agentState.value[agentName]
    state.lastEventTs = Date.now()
    state.status = 'active'

    // Update recent messages (keep last 3)
    if (parsed.type === 'response_chunk') {
      // Accumulate text into the last entry if it's also a response_chunk
      const last = state.recentMessages[state.recentMessages.length - 1]
      if (last && last.type === 'response_chunk') {
        last.text = (last.text || '') + (parsed.text || '')
        // Trim to last ~200 chars to avoid unbounded growth
        if (last.text.length > 200) last.text = last.text.slice(-200)
        state.recentMessages = [...state.recentMessages]
      } else {
        state.recentMessages = [...state.recentMessages.slice(-2), { ...parsed }]
      }
    } else if ((parsed.type === 'tool_use' || parsed.type === 'tool_result') && !isMcpDupe) {
      state.recentMessages = [...state.recentMessages.slice(-2), parsed]
    }

    agentState.value = { ...agentState.value }
  }

  function parseSSEStream(text, onFrame) {
    const frames = text.split(/\n\n/)
    for (const frame of frames) {
      if (!frame.trim() || frame.trim().startsWith(':')) continue
      const lines = frame.split('\n')
      let id = null
      let event = 'message'
      let data = ''
      for (const line of lines) {
        if (line.startsWith('id:')) id = line.slice(3).trim()
        else if (line.startsWith('event:')) event = line.slice(6).trim()
        else if (line.startsWith('data:')) data = line.slice(5).trim()
      }
      if (data || event === 'done') onFrame(id, event, data)
    }
  }

  async function connect() {
    const slug = typeof slugRef === 'string' ? slugRef : slugRef.value
    if (!slug) return

    disconnect()

    controller = new AbortController()
    const headers = { Accept: 'text/event-stream' }
    if (lastEventId) headers['Last-Event-ID'] = String(lastEventId)

    const queryParams = raw ? '?raw=true' : ''
    let res
    try {
      res = await fetch(`/api/executions/${slug}/stream${queryParams}`, {
        method: 'GET',
        credentials: 'same-origin',
        headers,
        signal: controller.signal,
      })
    } catch {
      if (controller?.signal.aborted) return
      scheduleReconnect()
      return
    }

    if (!res.ok || !res.body) {
      if (!controller?.signal.aborted) scheduleReconnect()
      return
    }

    isConnected.value = true
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      let chunk
      try {
        chunk = await reader.read()
      } catch {
        if (controller?.signal.aborted) return
        break
      }
      if (chunk.done) break
      buffer += decoder.decode(chunk.value, { stream: true })

      const boundary = buffer.lastIndexOf('\n\n')
      if (boundary !== -1) {
        const complete = buffer.slice(0, boundary + 2)
        buffer = buffer.slice(boundary + 2)
        parseSSEStream(complete, dispatchEvent)
      }
    }

    if (buffer.trim()) {
      parseSSEStream(buffer, dispatchEvent)
    }

    isConnected.value = false
    if (!controller?.signal.aborted && !isDone.value) {
      scheduleReconnect()
    }
  }

  function disconnect() {
    if (controller) {
      controller.abort()
      controller = null
    }
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    isConnected.value = false
  }

  function scheduleReconnect() {
    reconnectTimer = setTimeout(() => connect(), 2000)
  }

  // Poll agent status: mark agents as idle if no events for IDLE_THRESHOLD_MS
  function pollStatus() {
    const now = Date.now()
    let changed = false
    for (const [name, state] of Object.entries(agentState.value)) {
      if (state.status === 'active' && now - state.lastEventTs > IDLE_THRESHOLD_MS) {
        state.status = 'idle'
        changed = true
      }
    }
    if (changed) {
      agentState.value = { ...agentState.value }
    }
  }

  statusPollTimer = setInterval(pollStatus, 1000)

  onUnmounted(() => {
    disconnect()
    if (statusPollTimer) clearInterval(statusPollTimer)
  })

  return {
    agents,
    agentState,
    allEvents,
    topology,
    isConnected,
    isDone,
    connect,
    disconnect,
  }
}
