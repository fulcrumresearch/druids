import { ref, onUnmounted } from 'vue'
import type { Ref, ComputedRef } from 'vue'
import type { AgentRecentMessage, AgentState, TraceEvent, Topology } from '../types'

type SSEFrameCallback = (id: string | null, event: string, data: string) => void

export interface SessionStream {
  agents: Ref<string[]>
  agentState: Ref<Record<string, AgentState>>
  allEvents: Ref<Record<string, TraceEvent[]>>
  topology: Ref<Topology | null>
  programState: Ref<Record<string, unknown>>
  isConnected: Ref<boolean>
  isDone: Ref<boolean>
  connect: () => Promise<void>
  disconnect: () => void
}

/**
 * SSE stream composable for execution events.
 *
 * Connects to the execution's SSE endpoint and dispatches events to per-agent
 * state. Supports reconnection via Last-Event-ID and derives agent
 * active/idle status from event timestamps.
 */
export function useSessionStream(
  slugRef: Ref<string> | ComputedRef<string> | string,
  { raw = false } = {},
): SessionStream {
  const agents = ref<string[]>([])
  const agentState = ref<Record<string, AgentState>>({})
  const allEvents = ref<Record<string, TraceEvent[]>>({})
  const topology = ref<Topology | null>(null)
  const programState = ref<Record<string, unknown>>({})
  const isConnected = ref(false)
  const isDone = ref(false)

  let controller: AbortController | null = null
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let lastEventId = 0
  let statusPollTimer: ReturnType<typeof setInterval> | null = null

  const IDLE_THRESHOLD_MS = 5000

  function ensureAgent(name: string) {
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

  function dispatchEvent(id: string | null, event: string, data: string) {
    if (id) lastEventId = Number(id)

    if (event === 'done') {
      isDone.value = true
      return
    }

    let parsed: Record<string, unknown>
    try {
      parsed = JSON.parse(data)
    } catch {
      return
    }

    // Caption events from server-side summarizer
    if (parsed.type === 'client_event' && parsed.event === 'caption') {
      const eventData = parsed.data as Record<string, unknown> | undefined
      const target = eventData?.agent as string | undefined
      if (target) {
        ensureAgent(target)
        agentState.value[target].caption = eventData?.text as string || ''
        agentState.value = { ...agentState.value }
      }
      return
    }

    // Program state events from program via ctx.emit()
    if (parsed.type === 'client_event' && parsed.event === 'program_state') {
      const eventData = parsed.data as Record<string, unknown> | undefined
      if (eventData) {
        programState.value = { ...eventData }
      }
      return
    }

    // Topology events carry agents + edges for the whole execution
    if (parsed.type === 'topology') {
      topology.value = {
        agents: parsed.agents as string[],
        edges: parsed.edges as Topology['edges'],
      }
      for (const name of parsed.agents as string[]) {
        ensureAgent(name)
      }
      return
    }

    const agentName = parsed.agent as string | undefined
    if (!agentName) return

    ensureAgent(agentName)

    // Skip MCP-prefixed tool events (the native druids: version follows)
    const isMcpDupe = (parsed.type === 'tool_use' || parsed.type === 'tool_result')
      && parsed.tool && (parsed.tool as string).startsWith('mcp__')

    // Store event (skip MCP duplicates)
    const traceEvent = parsed as unknown as TraceEvent
    if (!isMcpDupe) {
      allEvents.value[agentName].push(traceEvent)
      allEvents.value = { ...allEvents.value }
    }

    // Update agent state
    const state = agentState.value[agentName]
    state.lastEventTs = Date.now()
    state.status = 'active'

    // Update recent messages (keep last 3)
    if (traceEvent.type === 'response_chunk') {
      const last = state.recentMessages[state.recentMessages.length - 1]
      if (last && last.type === 'response_chunk') {
        last.text = (last.text || '') + (traceEvent.text || '')
        if (last.text.length > 200) last.text = last.text.slice(-200)
        state.recentMessages = [...state.recentMessages]
      } else {
        state.recentMessages = [...state.recentMessages.slice(-2), { ...traceEvent } as AgentRecentMessage]
      }
    } else if ((traceEvent.type === 'tool_use' || traceEvent.type === 'tool_result') && !isMcpDupe) {
      state.recentMessages = [...state.recentMessages.slice(-2), traceEvent as AgentRecentMessage]
    }

    agentState.value = { ...agentState.value }
  }

  function parseSSEStream(text: string, onFrame: SSEFrameCallback) {
    const frames = text.split(/\n\n/)
    for (const frame of frames) {
      if (!frame.trim() || frame.trim().startsWith(':')) continue
      const lines = frame.split('\n')
      let id: string | null = null
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
    const headers: Record<string, string> = { Accept: 'text/event-stream' }
    if (lastEventId) headers['Last-Event-ID'] = String(lastEventId)

    const queryParams = raw ? '?raw=true' : ''
    let res: Response
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
      let chunk: ReadableStreamReadResult<Uint8Array>
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
    for (const [, state] of Object.entries(agentState.value)) {
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
    programState,
    isConnected,
    isDone,
    connect,
    disconnect,
  }
}
