import type { AgentRecentMessage, AgentStatus, Edge, TraceEvent } from './types'

export const agents: { name: string }[] = [
  { name: 'task-planner' },
  { name: 'api-gateway-builder' },
  { name: 'frontend-ui-exec' },
  { name: 'integration-tests' },
  { name: 'code-reviewer' },
  { name: 'docs-gen' },
]

export const recentMessages: Record<string, AgentRecentMessage[]> = {
  'task-planner': [
    { type: 'response_chunk', agent: 'task-planner', text: 'Backend schema looks good. Frontend, go ahead and start on the table component.', ts: '2024-03-10T00:00:50Z' },
    { type: 'tool_use', agent: 'task-planner', tool: 'mcp__druids__send_message', params: { agent: 'frontend-ui-exec', text: 'you can start building the task list page now' }, ts: '2024-03-10T00:00:22Z' },
  ],
  'api-gateway-builder': [
    { type: 'tool_use', agent: 'api-gateway-builder', tool: 'bash', params: 'cd server && uv run python -m pytest tests/api/test_tasks.py -x', ts: '2024-03-10T00:00:50Z' },
    { type: 'tool_result', agent: 'api-gateway-builder', tool: 'bash', result: 'PASSED (3 tests in 0.42s)', exit_code: 0, duration_secs: 0.42, ts: '2024-03-10T00:00:55Z' },
    { type: 'response_chunk', agent: 'api-gateway-builder', text: 'Tests pass. Adding the PATCH endpoint next.', ts: '2024-03-10T00:01:00Z' },
  ],
  'frontend-ui-exec': [
    { type: 'tool_use', agent: 'frontend-ui-exec', tool: 'write', params: 'frontend/src/pages/TasksPage.vue', ts: '2024-03-10T00:00:40Z' },
    { type: 'response_chunk', agent: 'frontend-ui-exec', text: 'Added sortable columns for name, status, and created date.', ts: '2024-03-10T00:00:45Z' },
  ],
  'integration-tests': [
    { type: 'response_chunk', agent: 'integration-tests', text: 'Waiting for api-gateway-builder to push the tasks endpoint before I can write integration tests.', ts: '2024-03-10T00:00:05Z' },
  ],
  'code-reviewer': [
    { type: 'response_chunk', agent: 'code-reviewer', text: 'No PRs in the queue yet. Will review as they come in.', ts: '2024-03-10T00:00:05Z' },
  ],
  'docs-gen': [
    { type: 'tool_use', agent: 'docs-gen', tool: 'write', params: 'docs/api/tasks.md', ts: '2024-03-10T00:00:40Z' },
    { type: 'response_chunk', agent: 'docs-gen', text: 'Documenting the POST /api/tasks request and response schema.', ts: '2024-03-10T00:00:45Z' },
  ],
}

export const edges: Edge[] = [
  { from: 'task-planner', to: 'api-gateway-builder' },
  { from: 'task-planner', to: 'frontend-ui-exec' },
  { from: 'task-planner', to: 'docs-gen' },
  { from: 'api-gateway-builder', to: 'integration-tests' },
  { from: 'api-gateway-builder', to: 'code-reviewer' },
  { from: 'frontend-ui-exec', to: 'code-reviewer' },
]

export const chatMessages: Record<string, TraceEvent[]> = {
  'task-planner': [
    { type: 'connected', agent: 'task-planner', session_id: 'demo-1', ts: '2024-03-10T00:00:00Z' },
    { type: 'response_chunk', agent: 'task-planner', text: 'Starting execution. Let me review the spec and figure out the task breakdown.', ts: '2024-03-10T00:00:01Z' },
    { type: 'tool_use', agent: 'task-planner', tool: 'Read File', params: 'spec.md', ts: '2024-03-10T00:00:10Z' },
    { type: 'tool_result', agent: 'task-planner', tool: 'Read File', result: '# Task Management Feature\n\nAdd CRUD endpoints for tasks...', exit_code: null, duration_secs: null, ts: '2024-03-10T00:00:11Z' },
    { type: 'response_chunk', agent: 'task-planner', text: 'The spec calls for a tasks API with list, create, update, and delete. I will split this across backend and frontend agents.', ts: '2024-03-10T00:00:15Z' },
    { type: 'tool_use', agent: 'task-planner', tool: 'mcp__druids__send_message', params: { agent: 'api-gateway-builder', text: 'please implement POST /api/tasks and GET /api/tasks endpoints. Use the existing database session pattern.' }, ts: '2024-03-10T00:00:20Z' },
    { type: 'tool_use', agent: 'task-planner', tool: 'mcp__druids__send_message', params: { agent: 'frontend-ui-exec', text: 'build a TasksPage.vue with a sortable table. Wait for backend to push the API first.' }, ts: '2024-03-10T00:00:22Z' },
    { type: 'tool_use', agent: 'task-planner', tool: 'mcp__druids__send_message', params: { agent: 'docs-gen', text: 'draft openapi spec for the tasks endpoints as backend builds them.' }, ts: '2024-03-10T00:00:24Z' },
    { type: 'response_chunk', agent: 'task-planner', text: 'Delegated work to all builders. Waiting for PRs to review.', ts: '2024-03-10T00:00:30Z' },
    { type: 'tool_use', agent: 'task-planner', tool: 'Read File', params: 'server/druids_server/api/routes/__init__.py', ts: '2024-03-10T00:00:40Z' },
    { type: 'tool_result', agent: 'task-planner', tool: 'Read File', result: 'from .executions import router as executions_router\n...', exit_code: null, duration_secs: null, ts: '2024-03-10T00:00:41Z' },
    { type: 'response_chunk', agent: 'task-planner', text: 'Backend schema looks good. Frontend, go ahead and start on the table component.', ts: '2024-03-10T00:00:50Z' },
  ],
  'api-gateway-builder': [
    { type: 'connected', agent: 'api-gateway-builder', session_id: 'demo-2', ts: '2024-03-10T00:00:00Z' },
    { type: 'prompt', agent: 'api-gateway-builder', text: 'please implement POST /api/tasks and GET /api/tasks endpoints. Use the existing database session pattern.', from: 'task-planner', ts: '2024-03-10T00:00:20Z' },
    { type: 'response_chunk', agent: 'api-gateway-builder', text: 'Got it. Let me look at the existing route patterns first.', ts: '2024-03-10T00:00:25Z' },
    { type: 'tool_use', agent: 'api-gateway-builder', tool: 'Read File', params: 'server/druids_server/api/routes/executions.py', ts: '2024-03-10T00:00:30Z' },
    { type: 'tool_result', agent: 'api-gateway-builder', tool: 'Read File', result: '@router.post("/executions")\nasync def create_execution(...', exit_code: null, duration_secs: null, ts: '2024-03-10T00:00:31Z' },
    { type: 'response_chunk', agent: 'api-gateway-builder', text: 'Following the same pattern. Creating the tasks router with Pydantic models for request validation.', ts: '2024-03-10T00:00:35Z' },
    { type: 'tool_use', agent: 'api-gateway-builder', tool: 'Write File', params: 'server/druids_server/api/routes/tasks.py', ts: '2024-03-10T00:00:40Z' },
    { type: 'tool_result', agent: 'api-gateway-builder', tool: 'Write File', result: 'File written (47 lines)', exit_code: null, duration_secs: null, ts: '2024-03-10T00:00:41Z' },
    { type: 'tool_use', agent: 'api-gateway-builder', tool: 'Terminal', params: 'cd server && uv run python -m pytest tests/api/test_tasks.py -x', ts: '2024-03-10T00:00:50Z' },
    { type: 'tool_result', agent: 'api-gateway-builder', tool: 'Terminal', result: '3 passed in 0.42s', exit_code: null, duration_secs: null, ts: '2024-03-10T00:00:55Z' },
    { type: 'response_chunk', agent: 'api-gateway-builder', text: 'Tests pass. Adding the PATCH endpoint next.', ts: '2024-03-10T00:01:00Z' },
    { type: 'tool_use', agent: 'api-gateway-builder', tool: 'Edit File', params: 'server/druids_server/api/routes/tasks.py:28', ts: '2024-03-10T00:01:05Z' },
  ],
  'frontend-ui-exec': [
    { type: 'connected', agent: 'frontend-ui-exec', session_id: 'demo-3', ts: '2024-03-10T00:00:00Z' },
    { type: 'prompt', agent: 'frontend-ui-exec', text: 'build a TasksPage.vue with a sortable table. Wait for backend to push the API first.', from: 'task-planner', ts: '2024-03-10T00:00:22Z' },
    { type: 'response_chunk', agent: 'frontend-ui-exec', text: 'Understood. Let me check the existing page patterns while I wait.', ts: '2024-03-10T00:00:25Z' },
    { type: 'tool_use', agent: 'frontend-ui-exec', tool: 'Read File', params: 'frontend/src/pages/HomePage.vue', ts: '2024-03-10T00:00:30Z' },
    { type: 'tool_result', agent: 'frontend-ui-exec', tool: 'Read File', result: '<script setup>\nimport { ref, onMounted } from \'vue\'\n...', exit_code: null, duration_secs: null, ts: '2024-03-10T00:00:31Z' },
    { type: 'response_chunk', agent: 'frontend-ui-exec', text: 'Good, I see the pattern. Using script setup with ref and onMounted for data fetching. The table uses the .data-table class.', ts: '2024-03-10T00:00:35Z' },
    { type: 'tool_use', agent: 'frontend-ui-exec', tool: 'Write File', params: 'frontend/src/pages/TasksPage.vue', ts: '2024-03-10T00:00:40Z' },
    { type: 'tool_result', agent: 'frontend-ui-exec', tool: 'Write File', result: 'File written (89 lines)', exit_code: null, duration_secs: null, ts: '2024-03-10T00:00:41Z' },
    { type: 'response_chunk', agent: 'frontend-ui-exec', text: 'Added sortable columns for name, status, and created date. The sort uses a computed property that reacts to the selected column and direction.', ts: '2024-03-10T00:00:45Z' },
    { type: 'tool_use', agent: 'frontend-ui-exec', tool: 'Read File', params: 'frontend/src/router.js', ts: '2024-03-10T00:00:50Z' },
    { type: 'tool_use', agent: 'frontend-ui-exec', tool: 'Edit File', params: 'frontend/src/router.js:15', ts: '2024-03-10T00:00:55Z' },
    { type: 'response_chunk', agent: 'frontend-ui-exec', text: 'Route added. Now adding the sidebar link.', ts: '2024-03-10T00:00:58Z' },
  ],
  'integration-tests': [
    { type: 'connected', agent: 'integration-tests', session_id: 'demo-4', ts: '2024-03-10T00:00:00Z' },
    { type: 'response_chunk', agent: 'integration-tests', text: 'Waiting for api-gateway-builder to push the tasks endpoint before I can write integration tests.', ts: '2024-03-10T00:00:05Z' },
    { type: 'response_chunk', agent: 'integration-tests', text: 'Still waiting. Backend is working on PATCH now, will write tests once the full CRUD is ready.', ts: '2024-03-10T00:01:00Z' },
  ],
  'code-reviewer': [
    { type: 'connected', agent: 'code-reviewer', session_id: 'demo-5', ts: '2024-03-10T00:00:00Z' },
    { type: 'response_chunk', agent: 'code-reviewer', text: 'No PRs in the queue yet. Will review as they come in.', ts: '2024-03-10T00:00:05Z' },
  ],
  'docs-gen': [
    { type: 'connected', agent: 'docs-gen', session_id: 'demo-6', ts: '2024-03-10T00:00:00Z' },
    { type: 'prompt', agent: 'docs-gen', text: 'draft openapi spec for the tasks endpoints as backend builds them.', from: 'task-planner', ts: '2024-03-10T00:00:24Z' },
    { type: 'response_chunk', agent: 'docs-gen', text: 'On it. Let me check what endpoints are being built.', ts: '2024-03-10T00:00:28Z' },
    { type: 'tool_use', agent: 'docs-gen', tool: 'Read File', params: 'server/druids_server/api/routes/tasks.py', ts: '2024-03-10T00:00:35Z' },
    { type: 'tool_result', agent: 'docs-gen', tool: 'Read File', result: '@router.post("/tasks")\nasync def create_task(...', exit_code: null, duration_secs: null, ts: '2024-03-10T00:00:36Z' },
    { type: 'tool_use', agent: 'docs-gen', tool: 'Write File', params: 'docs/api/tasks.md', ts: '2024-03-10T00:00:40Z' },
    { type: 'tool_result', agent: 'docs-gen', tool: 'Write File', result: 'File written (62 lines)', exit_code: null, duration_secs: null, ts: '2024-03-10T00:00:41Z' },
    { type: 'response_chunk', agent: 'docs-gen', text: 'Documenting the POST /api/tasks request and response schema. Will update as more endpoints land.', ts: '2024-03-10T00:00:45Z' },
    { type: 'tool_use', agent: 'docs-gen', tool: 'Edit File', params: 'docs/api/tasks.md:30', ts: '2024-03-10T00:00:50Z' },
    { type: 'response_chunk', agent: 'docs-gen', text: 'Added GET /api/tasks documentation with query parameter descriptions for filtering and pagination.', ts: '2024-03-10T00:00:55Z' },
  ],
}

export const agentStates: Record<string, { status: AgentStatus; caption: string }> = {
  'task-planner': { status: 'idle', caption: 'waiting for builder PRs' },
  'api-gateway-builder': { status: 'active', caption: 'writing handler for POST /api/tasks' },
  'frontend-ui-exec': { status: 'active', caption: 'adding sort controls to task table' },
  'integration-tests': { status: 'blocked', caption: 'waiting for api-gateway-builder' },
  'code-reviewer': { status: 'disconnected', caption: 'disconnected' },
  'docs-gen': { status: 'active', caption: 'drafting openapi spec for tasks' },
}
