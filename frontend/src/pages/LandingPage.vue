<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface GraphNode { id: string; label: string; x: number; y: number; w: number }
interface GraphEdge { id: string; from: string; to: string; label: string; curve: number }
interface AnimStep { nodes: string[]; edges: string[]; label: string }
interface ChatMsg { type: string; text?: string; from?: string; label?: string; params?: string; result?: string }

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const landingRef = ref<HTMLElement | null>(null)
const scrolled = ref(false)
const currentStep = ref(0)
const selectedAgent = ref<string | null>('opt1')
let timer: ReturnType<typeof setInterval> | null = null

// ---------------------------------------------------------------------------
// Program example: performance optimizer
// ---------------------------------------------------------------------------

const programCode = `async def program(ctx):
    # Create a profiler agent on a remote VM
    profiler = await ctx.agent(
        "profiler",
        prompt="Profile the app. Find the slowest functions.",
    )

    results = {}

    # The profiler calls this event for each bottleneck it finds
    @profiler.on("found_bottleneck")
    async def on_bottleneck(name="", analysis=""):
        opt = await ctx.agent(
            f"opt-{len(results) + 1}",
            prompt=f"Optimize: {name}\\n\\n{analysis}",
        )

        # Each optimizer calls this when it's done
        @opt.on("submit")
        async def on_submit(speedup=""):
            results[name] = speedup

    # The profiler calls this when it's finished profiling
    @profiler.on("all_done")
    async def on_done():
        await ctx.done(results)`

// ---------------------------------------------------------------------------
// Syntax highlighting (manual, no deps)
// ---------------------------------------------------------------------------

function highlightPython(code: string): string {
  const lines = code.split('\n')
  return lines.map((line) => {
    const tokens: { type: 'code' | 'str' | 'comment'; text: string }[] = []
    let remaining = line
    while (remaining.length > 0) {
      const commentIdx = findUnquotedHash(remaining)
      if (commentIdx >= 0) {
        if (commentIdx > 0) tokens.push({ type: 'code', text: remaining.slice(0, commentIdx) })
        tokens.push({ type: 'comment', text: remaining.slice(commentIdx) })
        remaining = ''
        break
      }
      const strMatch = remaining.match(/^(.*?)(f?"[^"]*"|f?'[^']*')/)
      if (strMatch) {
        if (strMatch[1]) tokens.push({ type: 'code', text: strMatch[1] })
        tokens.push({ type: 'str', text: strMatch[2] })
        remaining = remaining.slice(strMatch[0].length)
        continue
      }
      tokens.push({ type: 'code', text: remaining })
      remaining = ''
    }

    return tokens.map((t) => {
      const esc = escHtml(t.text)
      if (t.type === 'str') return `<span class="hl-str">${esc}</span>`
      if (t.type === 'comment') return `<span class="hl-comment">${esc}</span>`
      let h = esc
      const ph: string[] = []
      const hold = (html: string) => { ph.push(html); return `%%PH${ph.length - 1}%%PH` }
      h = h.replace(/(@\w+(?:\.\w+)*)/g, (m) => hold(`<span class="hl-dec">${m}</span>`))
      h = h.replace(/\b(def)\s+(\w+)/g, (_, d, n) => `${hold(`<span class="hl-kw">${d}</span>`)} ${hold(`<span class="hl-fn">${n}</span>`)}`)
      h = h.replace(/\b(async|await|if|for|nonlocal|return|import|from|class|and|or|not|in|is|None|True|False)\b/g, (m) => hold(`<span class="hl-kw">${m}</span>`))
      h = h.replace(/(?<![a-zA-Z_])(\d+)(?![a-zA-Z_])/g, (m) => hold(`<span class="hl-num">${m}</span>`))
      h = h.replace(/%%PH(\d+)%%PH/g, (_, i) => ph[Number(i)])
      return h
    }).join('')
  }).join('\n')
}

function findUnquotedHash(line: string): number {
  let inSingle = false
  let inDouble = false
  for (let i = 0; i < line.length; i++) {
    const ch = line[i]
    if (ch === '"' && !inSingle) inDouble = !inDouble
    else if (ch === "'" && !inDouble) inSingle = !inSingle
    else if (ch === '#' && !inSingle && !inDouble) return i
  }
  return -1
}

function escHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

const highlightedCode = computed(() => highlightPython(programCode))

// ---------------------------------------------------------------------------
// Agent graph
// ---------------------------------------------------------------------------

const graph = {
  viewBox: '0 0 500 220',
  nodes: [
    { id: 'profiler', label: 'profiler', x: 90, y: 110, w: 100 },
    { id: 'opt1', label: 'opt-1', x: 380, y: 35, w: 80 },
    { id: 'opt2', label: 'opt-2', x: 380, y: 110, w: 80 },
    { id: 'opt3', label: 'opt-3', x: 380, y: 185, w: 80 },
  ] as GraphNode[],
  edges: [
    { id: 'e1', from: 'profiler', to: 'opt1', label: 'spawn', curve: 0 },
    { id: 'e2', from: 'profiler', to: 'opt2', label: 'spawn', curve: 0 },
    { id: 'e3', from: 'profiler', to: 'opt3', label: 'spawn', curve: 0 },
  ] as GraphEdge[],
  steps: [
    { nodes: ['profiler'], edges: [], label: '[1/9] Profiler benchmarking the app' },
    { nodes: ['profiler'], edges: ['e1'], label: '[2/9] found_bottleneck: db queries' },
    { nodes: ['profiler', 'opt1'], edges: ['e2'], label: '[3/9] found_bottleneck: api handler' },
    { nodes: ['profiler', 'opt1', 'opt2'], edges: ['e3'], label: '[4/9] found_bottleneck: render loop' },
    { nodes: ['opt1', 'opt2', 'opt3'], edges: [], label: '[5/9] Optimizers working in parallel' },
    { nodes: ['opt1', 'opt2', 'opt3'], edges: [], label: '[6/9] opt-1 submits: 2.3x speedup' },
    { nodes: ['opt2', 'opt3'], edges: [], label: '[7/9] opt-2 submits: 1.8x speedup' },
    { nodes: ['opt3'], edges: [], label: '[8/9] opt-3 submits: 4.1x speedup' },
    { nodes: ['opt1', 'opt2', 'opt3'], edges: [], label: '[9/9] all_done: ctx.done(results)' },
  ] as AnimStep[],
}

const currentAnim = computed(() => graph.steps[currentStep.value])

function findNode(id: string): GraphNode {
  return graph.nodes.find((n) => n.id === id)!
}

function getEdgePath(edge: GraphEdge): string {
  const from = findNode(edge.from)
  const to = findNode(edge.to)
  return `M${from.x},${from.y} L${to.x},${to.y}`
}

function getEdgeLabelPos(edge: GraphEdge): { x: number; y: number } {
  const from = findNode(edge.from)
  const to = findNode(edge.to)
  return { x: (from.x + to.x) / 2, y: (from.y + to.y) / 2 - 8 }
}

function isNodeActive(id: string): boolean {
  return currentAnim.value.nodes.includes(id)
}

function isEdgeActive(id: string): boolean {
  return currentAnim.value.edges.includes(id)
}

function selectNode(id: string) {
  selectedAgent.value = selectedAgent.value === id ? null : id
}

// ---------------------------------------------------------------------------
// Per-agent mock chat messages
// ---------------------------------------------------------------------------

const agentChats: Record<string, { status: string; messages: ChatMsg[] }> = {
  profiler: {
    status: 'idle',
    messages: [
      { type: 'status', text: 'connected' },
      { type: 'response', text: 'Running benchmarks on the application...' },
      { type: 'tool', label: 'BASH', params: 'python -m cProfile -o profile.dat app.py' },
      { type: 'tool-result', label: 'BASH result', result: 'Profiling complete. 3 hotspots identified.' },
      { type: 'response', text: 'Found 3 bottlenecks:\n1. DB query layer (42%)\n2. API handler (28%)\n3. Render loop (18%)' },
      { type: 'tool', label: 'found_bottleneck', params: 'name="db queries", analysis="N+1 queries in user loader"' },
      { type: 'tool', label: 'found_bottleneck', params: 'name="api handler", analysis="json.dumps on every response"' },
      { type: 'tool', label: 'found_bottleneck', params: 'name="render loop", analysis="redundant re-renders"' },
    ],
  },
  opt1: {
    status: 'active',
    messages: [
      { type: 'status', text: 'connected' },
      { type: 'prompt', from: 'profiler', text: 'Optimize: DB query layer. N+1 queries in user loader consuming 42% of request time.' },
      { type: 'response', text: 'Let me examine the current query patterns...' },
      { type: 'tool', label: 'READ FILE', params: 'server/db/queries.py' },
      { type: 'tool-result', label: 'READ FILE result', result: 'class QueryBuilder:\n    def get_users(self, filters):\n        return self.db.execute(\n            "SELECT * FROM users WHERE ..."' },
      { type: 'response', text: 'I see N+1 queries in the user loader. Batching with a JOIN and adding an index.' },
      { type: 'tool', label: 'EDIT FILE', params: 'server/db/queries.py' },
      { type: 'tool-result', label: 'EDIT FILE result', result: 'Applied 2 edits to server/db/queries.py' },
    ],
  },
  opt2: {
    status: 'active',
    messages: [
      { type: 'status', text: 'connected' },
      { type: 'prompt', from: 'profiler', text: 'Optimize: API request handler. Serialization overhead in response middleware.' },
      { type: 'response', text: 'Analyzing the request pipeline...' },
      { type: 'tool', label: 'READ FILE', params: 'server/api/middleware.py' },
      { type: 'tool-result', label: 'READ FILE result', result: 'def serialize_response(data):\n    return json.dumps(data, default=str)' },
      { type: 'response', text: 'The serializer is calling json.dumps on every response. Switching to orjson for 3x faster serialization.' },
    ],
  },
  opt3: {
    status: 'active',
    messages: [
      { type: 'status', text: 'connected' },
      { type: 'prompt', from: 'profiler', text: 'Optimize: Template render loop. Redundant re-renders in the dashboard component.' },
      { type: 'response', text: 'Looking at the render path...' },
      { type: 'tool', label: 'READ FILE', params: 'frontend/src/pages/Dashboard.vue' },
      { type: 'tool-result', label: 'READ FILE result', result: '<template>\n  <div v-for="item in items">\n    <HeavyComponent :data="item" />' },
      { type: 'response', text: 'Adding v-memo and extracting the heavy computation into a cached computed property.' },
    ],
  },
}

const selectedChat = computed(() => {
  if (!selectedAgent.value) return null
  return agentChats[selectedAgent.value] || null
})

const selectedAgentLabel = computed(() => {
  if (!selectedAgent.value) return ''
  const node = graph.nodes.find((n) => n.id === selectedAgent.value)
  return node?.label || selectedAgent.value
})

// ---------------------------------------------------------------------------
// Features data
// ---------------------------------------------------------------------------

const features = [
  { title: 'Multi-agent collaboration', desc: 'Agents talk to each other through events you define. A builder commits, a critic reviews, an auditor verifies, all coordinated by your program.' },
  { title: 'Runs on real infrastructure', desc: 'Each agent gets a full sandboxed VM with your repo cloned and dependencies installed. Executions run for hours or days, surviving disconnects.' },
  { title: 'Steerable', desc: 'Message any agent while it runs. Inspect program state, redirect work, send feedback. You stay in the loop without blocking the process.' },
]

// ---------------------------------------------------------------------------
// Animation & scroll
// ---------------------------------------------------------------------------

function restartTimer() {
  if (timer) clearInterval(timer)
  timer = setInterval(() => {
    currentStep.value = (currentStep.value + 1) % graph.steps.length
  }, 2200)
}

function handleScroll() {
  if (landingRef.value) {
    scrolled.value = landingRef.value.scrollTop > 80
  }
}

onMounted(() => {
  restartTimer()
  landingRef.value?.addEventListener('scroll', handleScroll, { passive: true })
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
  landingRef.value?.removeEventListener('scroll', handleScroll)
})
</script>

<template>
  <div ref="landingRef" class="landing">

    <!-- ── Nav ── -->
    <nav :class="['nav', { scrolled }]">
      <a href="/" class="brand">Druids</a>
      <div class="nav-links">
        <a href="/docs" class="nav-link">Docs</a>
        <a href="https://github.com/fulcrumresearch/druids" target="_blank" rel="noopener" class="nav-link">GitHub</a>
        <a href="/api/oauth/login" class="nav-signin btn btn-primary btn-sm">Sign in</a>
      </div>
    </nav>

    <!-- ── 1. Hero (image only, with title + button) ── -->
    <section class="hero">
      <div class="hero-bg" aria-hidden="true"></div>
      <div class="hero-fade" aria-hidden="true"></div>
      <div class="hero-inner">
        <h1>The right way to write<br>agent software.</h1>
        <div class="hero-actions">
          <a href="/api/oauth/login" class="btn btn-hero">Get started</a>
        </div>
      </div>
    </section>

    <!-- ── 2. Subtitle (large, clear, below the image) ── -->
    <section class="section">
      <div class="container">
        <p class="subtitle">
          Druids is a batteries-included library to coordinate and<br>
          deploy coding agents across machines.
        </p>
      </div>
    </section>

    <!-- ── 3. You write / Druids runs ── -->
    <section class="section">
      <div class="container split-block">
        <div class="split-left">
          <h3>You write the program</h3>
          <p>
            Python functions that specify agents, their goals, and how
            they talk to each other. Programs are state machines: you define
            the state, the events that modify it, and how agents branch
            and recover from errors.
          </p>
        </div>
        <div class="split-arrow" aria-hidden="true">&rarr;</div>
        <div class="split-right">
          <h3>Druids runs the infrastructure</h3>
          <p>
            Sandboxed VMs, git branches, agent lifecycle, message routing,
            and execution tracing. Because every dependency is explicit,
            Druids knows what can run concurrently and parallelizes
            automatically.
          </p>
        </div>
      </div>
    </section>

    <!-- ── 4. Feature Cards ── -->
    <section class="section">
      <div class="container">
        <div class="feature-grid">
          <div v-for="f in features" :key="f.title" class="feature-card">
            <h4>{{ f.title }}</h4>
            <p>{{ f.desc }}</p>
          </div>
        </div>
      </div>
    </section>

    <!-- ── 5. Program Example ── -->
    <section class="section">
      <div class="container">
        <h2>Example program</h2>
        <p class="section-desc">
          A profiler agent benchmarks the application, identifies bottlenecks,
          then spawns optimizer agents that each fix one problem in parallel.
        </p>

        <!-- Code block -->
        <div class="code-block">
          <div class="code-header">
            <div class="code-dots"><span></span><span></span><span></span></div>
            .druids/optimize.py
          </div>
          <!-- eslint-disable-next-line vue/no-v-html -->
          <pre v-html="highlightedCode"></pre>
        </div>

        <!-- Hint + step indicator above diagram -->
        <div class="diagram-header">
          <span class="diagram-hint">Click an agent to view its chat</span>
          <span class="diagram-step">{{ currentStep + 1 }} / {{ graph.steps.length }}</span>
        </div>

        <!-- Interactive execution view: graph + chat side by side -->
        <div class="exec-view">
          <!-- Graph panel (left) -->
          <div class="exec-graph">
            <!-- Legend -->
            <div class="graph-legend">
              <span class="legend-item"><span class="legend-dot active"></span> active</span>
              <span class="legend-item"><span class="legend-dot idle"></span> idle</span>
              <span class="legend-item"><span class="legend-line"></span> event</span>
            </div>

            <svg
              :viewBox="graph.viewBox"
              xmlns="http://www.w3.org/2000/svg"
              class="graph-svg"
              preserveAspectRatio="xMidYMid meet"
            >
              <g
                v-for="edge in graph.edges"
                :key="edge.id"
                :class="['g-edge', { active: isEdgeActive(edge.id) }]"
              >
                <path :id="'path-' + edge.id" :d="getEdgePath(edge)" fill="none" stroke-width="1.5" />
                <text
                  v-if="edge.label"
                  :x="getEdgeLabelPos(edge).x"
                  :y="getEdgeLabelPos(edge).y"
                  text-anchor="middle"
                  class="edge-label"
                >{{ edge.label }}</text>
              </g>

              <template v-for="edge in graph.edges" :key="'dot-' + edge.id">
                <circle
                  v-if="isEdgeActive(edge.id)"
                  :key="currentStep + '-' + edge.id"
                  r="3.5"
                  class="travel-dot"
                >
                  <animateMotion dur="1.2s" repeatCount="indefinite" fill="freeze">
                    <mpath :href="'#path-' + edge.id" />
                  </animateMotion>
                </circle>
              </template>

              <!-- Clickable nodes -->
              <g
                v-for="node in graph.nodes"
                :key="node.id"
                :class="['g-node', { active: isNodeActive(node.id), selected: selectedAgent === node.id }]"
                style="cursor: pointer"
                @click="selectNode(node.id)"
              >
                <rect :x="node.x - node.w / 2" :y="node.y - 16" :width="node.w" height="32" rx="2" />
                <text :x="node.x" :y="node.y + 4" text-anchor="middle" class="node-label">{{ node.label }}</text>
              </g>
            </svg>

            <div class="graph-status">
              <span class="status-dot"></span>
              {{ currentAnim.label }}
            </div>
          </div>

          <!-- Chat panel (right) -->
          <div :class="['exec-chat', { open: selectedAgent }]">
            <template v-if="selectedChat">
              <div class="pc-header">
                <span class="pc-name">{{ selectedAgentLabel }}</span>
                <button class="pc-close" @click="selectedAgent = null">&times;</button>
              </div>
              <div class="pc-messages">
                <div v-for="(msg, i) in selectedChat.messages" :key="i" :class="'pc-msg pc-' + msg.type">
                  <div v-if="msg.type === 'status'" class="pc-status-text">{{ msg.text }}</div>
                  <template v-else-if="msg.type === 'prompt'">
                    <div class="pc-from">{{ msg.from }}</div>
                    <div class="pc-body">{{ msg.text }}</div>
                  </template>
                  <template v-else-if="msg.type === 'response'">
                    <div class="pc-response">{{ msg.text }}</div>
                  </template>
                  <template v-else-if="msg.type === 'tool'">
                    <div class="pc-tool-label">{{ msg.label }}</div>
                    <div class="pc-tool-params">{{ msg.params }}</div>
                  </template>
                  <template v-else-if="msg.type === 'tool-result'">
                    <div class="pc-tool-label result">{{ msg.label }}</div>
                    <div class="pc-tool-result">{{ msg.result }}</div>
                  </template>
                </div>
              </div>
              <div class="pc-input">
                <input type="text" :placeholder="`Message ${selectedAgentLabel}...`" disabled />
                <button disabled>Send</button>
              </div>
            </template>
            <div v-else class="exec-chat-empty">
              Select an agent to view its chat
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- ── 6. Blog Posts ── -->
    <section class="section">
      <div class="container">
        <h2>From the blog</h2>
        <div class="blog-grid">
          <a href="https://www.fulcrum.inc/2026/03/05/more-is-different-for-intelligence.html" target="_blank" rel="noopener" class="card blog-card">
            <h4>More is Different for Intelligence</h4>
            <p>Why software changed the world, and why agents will change it again.</p>
            <span class="blog-date">March 2026</span>
          </a>
          <a href="https://fulcrum.inc/2026/03/16/the-bitter-lesson-for-software.html" target="_blank" rel="noopener" class="card blog-card">
            <h4>The Bitter Lesson for Software</h4>
            <p>Why general methods that leverage computation win in the end.</p>
            <span class="blog-date">March 2026</span>
          </a>
        </div>
      </div>
    </section>

    <!-- ── 7. CTA Banner ── -->
    <section class="cta-banner">
      <div class="container cta-inner">
        <h2>Ready to get started?</h2>
        <div class="cta-actions">
          <a href="/api/oauth/login" class="btn btn-primary">Get started</a>
          <a href="/docs" class="cta-docs-link">Read the docs</a>
        </div>
      </div>
    </section>

    <!-- ── 8. Footer ── -->
    <footer class="footer">
      <div class="container footer-inner">
        <span class="footer-brand">Druids</span>
        <span class="footer-sep">/</span>
        <span>A <a href="https://fulcrum.inc">Fulcrum</a> product</span>
      </div>
    </footer>
  </div>
</template>

<style scoped>
/* ── Layout ── */
.landing {
  width: 100%;
  min-height: 100vh;
  height: 100vh;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
}

.container {
  max-width: 960px;
  margin: 0 auto;
  width: 100%;
  padding: 0 clamp(1.25rem, 4vw, 2rem);
}

.section { padding: clamp(1.5rem, 3vw, 2.25rem) 0; }

.section h2 {
  font-family: var(--font-serif);
  font-style: italic;
  font-weight: 400;
  font-size: clamp(1.15rem, 2.5vw, 1.4rem);
  color: var(--text-bright);
  margin-bottom: 0.6rem;
}

.section-desc {
  font-size: 0.85rem;
  color: var(--text-secondary);
  line-height: 1.7;
  max-width: 560px;
  margin-bottom: 1.25rem;
}

/* ── Nav ── */
.nav {
  position: fixed;
  top: 0; left: 0; right: 0;
  z-index: 100;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.9rem clamp(1.25rem, 4vw, 2rem);
  background: rgba(0, 0, 0, 0.4);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  transition: background 0.3s ease, backdrop-filter 0.3s ease;
}

.nav.scrolled {
  background: var(--bg);
  backdrop-filter: none;
  -webkit-backdrop-filter: none;
  box-shadow: 0 1px 0 var(--border-light);
}

.brand {
  font-family: var(--font-serif);
  font-style: italic;
  font-size: 1.35rem;
  color: #fff;
  text-shadow: 0 1px 6px rgba(0,0,0,0.3);
  transition: color 0.3s, text-shadow 0.3s;
}
.nav.scrolled .brand { color: var(--text-bright); text-shadow: none; }

.nav-links { display: flex; gap: clamp(1rem, 3vw, 1.75rem); align-items: center; }
.nav-link {
  color: rgba(255,255,255,0.9); font-size: 0.72rem;
  text-transform: uppercase; letter-spacing: 0.06em; transition: color 0.3s;
}
.nav-link:hover { color: #fff; opacity: 1; }
.nav.scrolled .nav-link { color: var(--text-secondary); }
.nav.scrolled .nav-link:hover { color: var(--text-bright); }
.nav-signin { text-shadow: none; }

/* ── Hero ── */
.hero {
  position: relative;
  min-height: 68vh;
  padding: clamp(6rem, 14vw, 10rem) clamp(1.25rem, 4vw, 2rem) clamp(4rem, 8vw, 6rem);
  display: flex; align-items: center; justify-content: center;
  text-align: center; overflow: hidden;
}
.hero-bg {
  position: absolute; inset: 0;
  background: url('/hero-bg.jpg') center 30% / cover no-repeat;
  pointer-events: none;
}
.hero-fade {
  position: absolute; inset: 0;
  background: linear-gradient(to bottom, rgba(0,0,0,0.35) 0%, rgba(0,0,0,0.15) 40%, transparent 60%, var(--bg) 100%);
  pointer-events: none;
}
.hero-inner { position: relative; z-index: 1; max-width: 640px; }
.hero h1 {
  font-family: var(--font-serif); font-style: italic; font-weight: 400;
  font-size: clamp(2rem, 5.5vw, 3.2rem); line-height: 1.1;
  letter-spacing: -0.025em; color: #fff;
  text-shadow: 0 2px 16px rgba(0,0,0,0.4); margin-bottom: 1.5rem;
}
.hero-actions { display: flex; justify-content: center; }
.btn-hero {
  display: inline-flex; align-items: center;
  padding: 0.6rem 1.8rem; font-family: var(--font-mono);
  font-size: 0.82rem; letter-spacing: 0.03em; border-radius: 2px;
  border: none; cursor: pointer;
  background: var(--bg); color: var(--text-bright); transition: opacity 0.2s;
}
.btn-hero:hover { opacity: 0.85; }

/* ── Subtitle ── */
.subtitle {
  font-family: var(--font-serif);
  font-style: italic;
  font-size: clamp(1.2rem, 3vw, 1.7rem);
  line-height: 1.45;
  color: var(--text-bright);
}

/* ── Split block: you write / druids runs ── */
.split-block {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  gap: 1.5rem;
  align-items: start;
}

.split-arrow {
  font-size: 1.5rem;
  color: var(--text-dim);
  padding-top: 1.8rem;
  user-select: none;
}

.split-block h3 {
  font-family: var(--font-mono);
  font-style: normal;
  font-size: 0.82rem;
  font-weight: 600;
  color: var(--text-bright);
  margin-bottom: 0.5rem;
}

.split-block p {
  font-size: 0.82rem;
  color: var(--text-secondary);
  line-height: 1.75;
}

/* ── Feature Cards ── */
.feature-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 1rem;
}

.feature-card {
  padding: clamp(1rem, 2vw, 1.25rem);
  background: var(--bg-terminal);
  border-radius: 2px;
}

.feature-card h4 {
  font-family: var(--font-mono); font-size: 0.8rem;
  font-weight: 600; color: var(--text-bright); margin-bottom: 0.4rem;
}

.feature-card p { font-size: 0.78rem; color: var(--text-secondary); line-height: 1.65; }

/* ── Code Block ── */
.code-block {
  background: var(--bg-terminal);
  border: 1px dotted var(--border); border-radius: 2px;
  overflow: hidden; margin-bottom: 1rem;
}
.code-header {
  display: flex; align-items: center; gap: 0.5rem;
  padding: 0.45rem 0.85rem;
  border-bottom: 1px dotted var(--border);
  font-size: 0.7rem; color: var(--text-dim);
}
.code-dots { display: flex; gap: 4px; }
.code-dots span { width: 7px; height: 7px; border-radius: 50%; background: rgba(0,0,0,0.08); }
.code-block pre {
  padding: 0.85rem 1rem; overflow-x: auto;
  font-family: var(--font-mono); font-size: 0.76rem;
  line-height: 1.55; color: var(--text); white-space: pre; max-height: 340px;
}

/* Syntax highlighting */
:deep(.hl-kw) { color: #7a5c3a; font-weight: 500; }
:deep(.hl-str) { color: #4a7a4a; }
:deep(.hl-dec) { color: #6a5a8a; }
:deep(.hl-comment) { color: var(--text-dim); }
:deep(.hl-fn) { color: #3a6a7a; }
:deep(.hl-num) { color: #5a7a5a; }

/* ── Diagram header (hint + step counter) ── */
.diagram-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 0.5rem;
  font-size: 0.72rem;
}

.diagram-hint {
  color: var(--text-secondary);
}

.diagram-step {
  font-family: var(--font-mono);
  color: var(--text-dim);
  font-size: 0.68rem;
}

/* ── Execution View: graph + chat side by side ── */
.exec-view {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0;
  border: 1px dotted var(--border);
  border-radius: 2px;
  overflow: hidden;
  min-height: 360px;
}

.exec-graph {
  background: var(--bg-terminal);
  padding: 0.6rem 0.85rem;
  display: flex;
  flex-direction: column;
}

/* Legend */
.graph-legend {
  display: flex;
  gap: 1rem;
  font-size: 0.62rem;
  color: var(--text-dim);
  margin-bottom: 0.4rem;
}

.legend-item {
  display: flex;
  align-items: center;
  gap: 0.3rem;
}

.legend-dot {
  width: 7px; height: 7px;
  border-radius: 50%;
}

.legend-dot.active { background: var(--text-bright); }
.legend-dot.idle { background: var(--border); }

.legend-line {
  width: 16px;
  border-top: 1.5px dashed var(--text-dim);
}

.graph-svg { flex: 1; width: 100%; max-height: 240px; }

.g-node rect {
  fill: var(--bg-terminal); stroke: var(--border); stroke-dasharray: 3 3;
  transition: stroke 0.3s;
}
.g-node .node-label {
  font-family: var(--font-mono); font-size: 11px; fill: var(--text-dim);
  transition: fill 0.3s;
}
.g-node.active rect { stroke: var(--text-bright); }
.g-node.active .node-label { fill: var(--text-bright); font-weight: 600; }
.g-node.selected rect { stroke: var(--text-bright); stroke-dasharray: none; fill: rgba(0,0,0,0.04); }
.g-node.selected .node-label { fill: var(--text-bright); font-weight: 600; }
.g-node:hover rect { stroke: var(--text-secondary); }

.g-edge path { stroke: var(--border); stroke-dasharray: 4 3; transition: stroke 0.3s; }
.g-edge .edge-label { font-family: var(--font-mono); font-size: 9px; fill: var(--text-dim); }
.g-edge.active path { stroke: var(--text); stroke-dasharray: 6 4; animation: dash-flow 0.7s linear infinite; }
.g-edge.active .edge-label { fill: var(--text); }
.travel-dot { fill: var(--text-bright); opacity: 0.7; }

@keyframes dash-flow { from { stroke-dashoffset: 0; } to { stroke-dashoffset: -10; } }

.graph-status {
  display: flex; align-items: center; gap: 0.4rem;
  margin-top: 0.5rem; padding-top: 0.4rem;
  border-top: 1px dotted var(--border-light);
  font-size: 0.72rem; color: var(--text-secondary);
}

.status-dot {
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--green); flex-shrink: 0;
  animation: pulse 2s ease-in-out infinite;
}
@keyframes pulse { 0%, 100% { opacity: 0.35; } 50% { opacity: 1; } }

/* ── Chat panel (right side of exec view) ── */
.exec-chat {
  background: #2c2722;
  color: #c4b9a8;
  display: flex;
  flex-direction: column;
  border-left: 1px dotted rgba(255,255,255,0.08);
}

.exec-chat-empty {
  display: flex; align-items: center; justify-content: center;
  height: 100%; font-size: 0.78rem; color: #6b6356;
  padding: 2rem; text-align: center;
}

.pc-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 0.6rem 0.85rem;
  border-bottom: 1px dotted rgba(255,255,255,0.1);
}
.pc-name { font-size: 0.78rem; font-weight: 600; color: #e8dfd0; }
.pc-close {
  background: none; border: none; color: #8a7e6e;
  font-size: 1.1rem; cursor: pointer; padding: 0 0.2rem; line-height: 1;
}
.pc-close:hover { color: #e8dfd0; }

.pc-messages {
  flex: 1; overflow-y: auto;
  padding: 0.6rem 0.85rem;
  display: flex; flex-direction: column; gap: 0.4rem;
}

.pc-msg { font-size: 0.72rem; line-height: 1.5; max-width: 95%; word-break: break-word; }

.pc-status-text {
  font-size: 0.6rem; text-transform: uppercase; letter-spacing: 0.05em;
  color: #2d7a3e; text-align: center; padding: 0.2rem 0;
}
.pc-from {
  font-size: 0.6rem; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.04em; color: #8a7e6e; margin-bottom: 0.1rem;
}
.pc-body {
  color: #e0d8ca; padding: 0.4rem 0.6rem;
  background: rgba(200,180,150,0.1); border-radius: 2px; white-space: pre-wrap;
}
.pc-response { color: #d4c9b8; white-space: pre-wrap; padding: 0.4rem 0.6rem; }
.pc-tool-label {
  font-size: 0.6rem; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.04em; color: #8a7e6e;
}
.pc-tool-label.result { color: #6b8a5e; }
.pc-tool-params, .pc-tool-result {
  font-size: 0.65rem; font-family: var(--font-mono);
  color: #9a8f7f; white-space: pre-wrap; word-break: break-all;
  padding: 0.2rem 0.35rem; background: rgba(0,0,0,0.15);
  border-radius: 2px; margin-top: 0.15rem;
  overflow: hidden; max-height: 3.5rem;
}
.pc-tool-result { color: #7a9a6a; }

.pc-input {
  display: flex; gap: 0.35rem;
  padding: 0.5rem 0.85rem;
  border-top: 1px dotted rgba(255,255,255,0.1);
}
.pc-input input {
  flex: 1; background: rgba(0,0,0,0.2);
  border: 1px dotted rgba(255,255,255,0.1);
  border-radius: 2px; color: #6b6356;
  font-family: var(--font-mono); font-size: 0.7rem;
  padding: 0.35rem 0.5rem; outline: none; width: auto;
}
.pc-input button {
  background: none; border: 1px dotted rgba(255,255,255,0.1);
  border-radius: 2px; color: #6b6356; font-size: 0.7rem;
  cursor: default; padding: 0.25rem 0.5rem;
}

/* ── Blog Cards ── */
.blog-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
.blog-card { display: block; text-decoration: none; transition: background 0.15s; }
.blog-card:hover { background: var(--bg-card-hover); opacity: 1; }
.blog-card h4 {
  font-family: var(--font-mono); font-size: 0.82rem;
  font-weight: 600; color: var(--text-bright); margin-bottom: 0.3rem;
}
.blog-card p { font-size: 0.78rem; color: var(--text-secondary); line-height: 1.6; margin-bottom: 0.4rem; }
.blog-date { font-size: 0.68rem; color: var(--text-dim); }

/* ── CTA Banner ── */
.cta-banner { background: var(--bg-terminal); padding: clamp(2rem, 4vw, 3rem) 0; text-align: center; }
.cta-inner h2 {
  font-family: var(--font-serif); font-style: italic; font-weight: 400;
  font-size: clamp(1.1rem, 2.5vw, 1.3rem); color: var(--text-bright); margin-bottom: 1rem;
}
.cta-actions { display: flex; gap: 1rem; align-items: center; justify-content: center; }
.cta-docs-link {
  font-size: 0.82rem; color: var(--text-secondary);
  border-bottom: 1px dotted var(--border); padding-bottom: 1px;
}
.cta-docs-link:hover { color: var(--text-bright); opacity: 1; }

/* ── Footer ── */
.footer { padding: clamp(1.5rem, 3vw, 2rem) 0; border-top: 1px dotted var(--border-light); }
.footer-inner { font-size: 0.75rem; color: var(--text-secondary); }
.footer-brand { font-family: var(--font-serif); font-style: italic; }
.footer-sep { margin: 0 0.5rem; opacity: 0.4; }
.footer a { color: var(--text-secondary); border-bottom: 1px dotted var(--border-light); }

/* ── Responsive ── */
@media (max-width: 768px) {
  .hero { min-height: 60vh; }
  .hero h1 { font-size: clamp(1.6rem, 7vw, 2.2rem); }
  .split-block { grid-template-columns: 1fr; gap: 1rem; }
  .split-arrow { display: none; }
  .feature-grid { grid-template-columns: 1fr; }
  .exec-view { grid-template-columns: 1fr; }
  .exec-chat { border-left: none; border-top: 1px dotted rgba(255,255,255,0.08); min-height: 280px; }
  .blog-grid { grid-template-columns: 1fr; }
}
</style>
