// Domain types for the Druids frontend.
// Derived from backend Python models and SSE trace format.

// --- Execution & agent status ---

export type ExecutionStatus = 'starting' | 'running' | 'stopped' | 'completed' | 'failed'

export type AgentStatus = 'active' | 'idle' | 'blocked' | 'disconnected'

// --- User ---

export interface User {
  id: string
  github_id: number
  github_login: string
  is_admin: boolean
  github_app_install_url?: string
}

// --- Dashboard ---

export interface Devbox {
  id: string
  repo_full_name: string
  has_snapshot: boolean
  setup_slug: string | null
  instance_id: string | null
  setup_completed_at: string | null
}

export interface Dashboard {
  user: Pick<User, 'id' | 'github_id' | 'github_login'>
  devboxes: Devbox[]
}

// --- Execution (list vs detail) ---

export interface ExecutionSummary {
  id: string
  slug: string
  spec: string
  repo_full_name: string
  status: ExecutionStatus
  error: string | null
  metadata: Record<string, unknown>
  branch_name: string | null
  pr_url: string | null
  started_at: string | null
}

export interface ExposedService {
  instance_id: string
  service_name: string
  port: number
  url: string
}

export interface Edge {
  from: string
  to: string
}

export interface ExecutionDetail {
  execution_id: string
  execution_slug: string
  spec: string
  repo_full_name: string
  status: ExecutionStatus
  error: string | null
  metadata: Record<string, unknown>
  branch_name: string | null
  pr_url: string | null
  started_at: string | null
  stopped_at: string | null
  agents: string[]
  edges: Edge[]
  exposed_services: ExposedService[]
  client_events: ClientEvent[]
}

// --- API keys ---

export interface ApiKey {
  id: string
  name: string
  prefix: string
  created_at: string
  last_used_at: string | null
}

export interface ApiKeyCreated {
  id: string
  name: string
  key: string
  prefix: string
  created_at: string
}

// --- Admin usage ---

export interface UsageStats {
  users: { total: number }
  repos_configured: number
  executions: {
    total: number
    by_status: Partial<Record<ExecutionStatus, number>>
  }
  tokens: {
    input: number
    output: number
    cache_read: number
    cache_creation: number
  }
  recent_executions: RecentExecution[]
}

export interface RecentExecution {
  slug: string
  status: ExecutionStatus
  repo_full_name: string
  started_at: string | null
  pr_url: string | null
  input_tokens: number
  output_tokens: number
  user_login: string
}

// --- SSE trace events (discriminated union) ---

export interface ConnectedEvent {
  type: 'connected'
  agent: string
  session_id: string
  ts: string
}

export interface DisconnectedEvent {
  type: 'disconnected'
  agent: string
  ts: string
}

export interface PromptEvent {
  type: 'prompt'
  agent: string
  text: string
  from?: string
  ts: string
}

export interface ResponseChunkEvent {
  type: 'response_chunk'
  agent: string
  text: string
  ts: string
}

export interface ToolUseEvent {
  type: 'tool_use'
  agent: string
  tool: string
  params: Record<string, unknown> | string
  ts: string
}

export interface ToolResultEvent {
  type: 'tool_result'
  agent: string
  tool: string
  result: string | null
  exit_code: number | null
  duration_secs: number | null
  ts: string
}

export interface TopologyEvent {
  type: 'topology'
  agents: string[]
  edges: Edge[]
  ts: string
}

export interface ClientEvent {
  type: 'client_event'
  event: string
  data: Record<string, unknown>
  ts: string
}

export interface ErrorEvent {
  type: 'error'
  agent: string | null
  error: string
  ts: string
}

export interface ExecutionStartedEvent {
  type: 'execution_started'
  agent: null
  task_id: string | null
  base_snapshot: string | null
  ts: string
}

export interface ExecutionStoppedEvent {
  type: 'execution_stopped'
  agent: null
  reason: string
  ts: string
}

export interface ProgramAddedEvent {
  type: 'program_added'
  agent: null
  name: string
  program_type: string
  instance_id: string | null
  ts: string
}

export interface InstanceStoppedEvent {
  type: 'instance_stopped'
  agent: null
  name: string
  instance_id: string
  ts: string
}

export type TraceEvent =
  | ConnectedEvent
  | DisconnectedEvent
  | PromptEvent
  | ResponseChunkEvent
  | ToolUseEvent
  | ToolResultEvent
  | TopologyEvent
  | ClientEvent
  | ErrorEvent
  | ExecutionStartedEvent
  | ExecutionStoppedEvent
  | ProgramAddedEvent
  | InstanceStoppedEvent

// --- Agent state (frontend-only, from useSessionStream) ---

export type AgentRecentMessage = ResponseChunkEvent | ToolUseEvent | ToolResultEvent

export interface AgentState {
  status: AgentStatus
  lastEventTs: number
  caption: string
  recentMessages: AgentRecentMessage[]
}

// --- Topology (frontend-only) ---

export interface Topology {
  agents: string[]
  edges: Edge[]
}

// --- Graph layout (frontend-only) ---

export interface Position {
  x: number
  y: number
}

export interface LayoutOptions {
  columnSpacing?: number
  rowSpacing?: number
  paddingX?: number
  paddingY?: number
}

// --- Docs structure ---

export interface DocPage {
  title: string
  file: string
}

export interface DocSection {
  label: string
  pages: Record<string, DocPage>
}
