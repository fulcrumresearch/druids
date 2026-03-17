<script setup lang="ts">
import { ref } from 'vue'

interface ProgramExample {
  title: string
  desc: string
  agents: string[]
  code: string
}

const expandedProgram = ref<string | null>(null)

function toggleProgram(key: string) {
  expandedProgram.value = expandedProgram.value === key ? null : key
}

const programs: Record<string, ProgramExample> = {
  build: {
    title: 'build.py',
    desc: 'Builder implements a spec. Critic reviews each commit for simplicity. Auditor verifies the tests are real. Three agents, iterating until all three are satisfied.',
    agents: ['builder', 'critic', 'auditor'],
    code: `async def program(ctx, spec="", task_name="", ...):
    builder = await ctx.agent(
        "builder",
        system_prompt=BUILDER_SYSTEM_PROMPT,
        prompt=f"## Spec\\n\\n{spec}",
        git="write",
    )

    auditor = await ctx.agent(
        "auditor",
        system_prompt=AUDITOR_SYSTEM_PROMPT,
        git="post",
        share_machine_with=builder,
    )

    @builder.on("commit")
    async def on_commit(message: str = ""):
        result = await builder.exec(f"git commit -m {shlex.quote(message)}")
        await builder.exec("git push")
        # Spawn critic on first commit
        if critic is None:
            critic = await ctx.agent("critic", git="read", ...)
            @critic.on("send_critique")
            async def on_critique(feedback=""):
                await builder.send(f"[Critic] {feedback}")
        await critic.send(f"New commit: {message}")
        return f"Committed.\\n{result.stdout}"

    @builder.on("submit_for_review")
    async def on_submit(summary=""):
        await auditor.send(f"Builder submitted for audit.\\n\\n{summary}")

    @auditor.on("approve")
    async def on_approve(summary=""):
        ctx.done(summary)

    @auditor.on("send_feedback")
    async def on_feedback(feedback=""):
        await builder.send(f"[Auditor] Rejected: {feedback}")`,
  },
  basher: {
    title: 'basher.py',
    desc: 'Finder agent scans for tasks, then spawns implementor+reviewer pairs that iterate up to 3 rounds before acceptance or rejection.',
    agents: ['finder', 'impl-N', 'review-N'],
    code: `async def program(ctx, ...):
    async def spawn_task(task_name, task_spec):
        implementor = await ctx.agent(
            f"impl-{task_counter}",
            prompt=f"## Task: {task_name}\\n\\n{task_spec}",
            git="write",
        )
        reviewer = await ctx.agent(
            f"review-{task_counter}",
            model="claude-sonnet-4-6",
            git="post",
            share_machine_with=implementor,
        )

        @implementor.on("submit_for_review")
        async def on_submit(summary=""):
            await reviewer.send(f"Review this.\\n\\n{summary}")

        @reviewer.on("approve_and_pr")
        async def on_approve(pr_url=""):
            ctx.done(f"PR created: {pr_url}")

        @reviewer.on("send_feedback")
        async def on_feedback(feedback=""):
            if rejections >= MAX_REVIEW_ROUNDS:
                ctx.done(f"Abandoned after {rejections} rounds.")
            await implementor.send(f"Rejected:\\n{feedback}")

    finder = await ctx.agent("finder", prompt="Start scanning...")

    @finder.on("spawn_task")
    async def on_spawn_task(task_name="", task_spec=""):
        await spawn_task(task_name, task_spec)`,
  },
  bon: {
    title: 'main.py',
    desc: 'Spawns a Claude and a Codex agent in parallel on the same spec. Both implement independently. Completes when both submit.',
    agents: ['claude', 'codex'],
    code: `async def program(ctx, spec="", **kwargs):
    claude, codex = await asyncio.gather(
        ctx.agent("claude", model="claude", prompt=spec, git="write"),
        ctx.agent("codex", model="codex", prompt=spec, git="write"),
    )

    results = {}

    @claude.on("submit")
    async def on_claude_submit(summary=""):
        results["claude"] = summary
        if len(results) == 2:
            ctx.done(results)

    @codex.on("submit")
    async def on_codex_submit(summary=""):
        results["codex"] = summary
        if len(results) == 2:
            ctx.done(results)`,
  },
}
</script>

<template>
  <div class="landing-page">
    <nav class="landing-nav">
      <a href="/" class="landing-brand">Druids</a>
      <div class="landing-nav-links">
        <a href="/docs" class="landing-nav-link">Docs</a>
        <a href="https://github.com/fulcrumresearch/druids" target="_blank" rel="noopener" class="landing-nav-link">GitHub</a>
        <a href="/api/oauth/login" class="btn btn-primary">Sign in</a>
      </div>
    </nav>

    <div class="landing-hero">
      <div class="landing-hero-bg" aria-hidden="true"></div>
      <div class="landing-hero-inner">
        <p class="landing-tagline">
          <span class="landing-headline">Your own agent cloud.</span>
        </p>
        <div class="landing-actions">
          <a href="/api/oauth/login" class="btn btn-primary">Get started</a>
          <a href="/docs" class="landing-docs-link">Read the docs</a>
        </div>
      </div>
    </div>

    <div class="landing-section">
      <div class="landing-content">
        <p>
          Druids is a batteries-included library to coordinate and deploy
          coding agents across machines (currently in beta).
        </p>
        <p>
          You do this by writing Python functions in which you declaratively
          specify agents &mdash; their machines, their goals, and how they can
          talk to each other. The program lets you define events that agents
          can call to controllably modify state.
        </p>
        <p>
          Druids lets you build processes to reliably run large numbers of
          agents. We've found it useful for detecting issues in code, launching
          many parallel agents on software tasks, and new kinds of agent
          software we'll share soon.
        </p>
      </div>
    </div>

    <div class="landing-section">
      <div class="landing-content">
        <h2>Example programs</h2>
      </div>
      <div class="landing-programs">
        <div class="program-grid">
          <div
            v-for="(prog, key) in programs"
            :key="key"
            class="program-card"
            :class="{ expanded: expandedProgram === key }"
            @click="toggleProgram(key)"
          >
            <h4>{{ prog.title }}</h4>
            <p>{{ prog.desc }}</p>
            <div class="program-agents">
              <span v-for="agent in prog.agents" :key="agent" class="program-agent-tag">{{ agent }}</span>
            </div>
          </div>
        </div>
        <div v-if="expandedProgram" class="landing-code-block">
          <div class="landing-code-header">
            <div class="landing-dots"><span></span><span></span><span></span></div>
            .druids/{{ programs[expandedProgram].title }}
          </div>
          <pre>{{ programs[expandedProgram].code }}</pre>
        </div>
      </div>
    </div>

    <footer class="landing-footer">
      <div class="landing-footer-inner">
        <div>
          <span class="landing-footer-brand">Druids</span>
          <span class="landing-footer-sep">&mdash;</span>
          <span>A <a href="https://fulcrum.inc">Fulcrum</a> product</span>
        </div>
      </div>
    </footer>
  </div>
</template>

<style scoped>
.landing-page {
  width: 100%;
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
  height: 100vh;
}

/* Nav */
.landing-nav {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  z-index: 100;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 1.25rem max(clamp(1.5rem, 5vw, 3rem), calc((100vw - 960px) / 2 + clamp(1.5rem, 5vw, 3rem)));
  background: var(--bg);
  font-size: clamp(0.72rem, 1.6vw, 0.8rem);
}

.landing-nav a {
  text-decoration: none;
}

.landing-brand {
  font-family: var(--font-serif);
  font-style: italic;
  font-size: clamp(1.05rem, 2.5vw, 1.2rem);
  font-weight: 400;
  letter-spacing: -0.02em;
  color: var(--text-bright);
}

.landing-nav-links {
  display: flex;
  gap: clamp(1.25rem, 3vw, 2rem);
  align-items: center;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

.landing-nav-link {
  color: var(--text-secondary);
  text-decoration: none;
  font-size: clamp(0.68rem, 1.4vw, 0.75rem);
}

.landing-nav-link:hover {
  color: var(--text);
}

.landing-docs-link {
  color: rgba(255, 255, 255, 0.85);
  text-decoration: none;
  font-size: 0.85rem;
  border-bottom: 1px dotted rgba(255, 255, 255, 0.5);
  padding-bottom: 1px;
  text-shadow: 0 1px 4px rgba(0, 0, 0, 0.4);
}

.landing-docs-link:hover {
  color: #fff;
  border-bottom-color: rgba(255, 255, 255, 0.8);
}

/* Hero */
.landing-hero {
  position: relative;
  height: 80vh;
  min-height: 500px;
  padding: clamp(3rem, 8vw, 5rem) clamp(1.5rem, 5vw, 3rem) clamp(2rem, 5vw, 4rem);
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  overflow: hidden;
}

.landing-hero-bg {
  position: absolute;
  inset: 0;
  background: url('/hero-bg.jpg') center 30% / cover no-repeat;
  pointer-events: none;
}

.landing-hero::after {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(to bottom, transparent 60%, var(--bg) 100%);
  pointer-events: none;
}

.landing-hero-inner {
  position: relative;
  z-index: 1;
  width: 100%;
  max-width: 720px;
}

.landing-headline {
  font-family: var(--font-serif);
  font-style: italic;
  font-size: clamp(1.8rem, 4.5vw, 2.4rem);
  color: #fff;
  line-height: 1;
  display: block;
  margin-bottom: 0.75rem;
  letter-spacing: -0.02em;
  text-shadow: 0 1px 8px rgba(0, 0, 0, 0.5);
}

.landing-tagline {
  font-size: clamp(0.88rem, 2vw, 1rem);
  color: var(--text);
  margin-bottom: 2rem;
  line-height: 1.7;
  max-width: 520px;
}

.landing-actions {
  display: flex;
  gap: 1rem;
  align-items: center;
}

/* Sections */
.landing-section {
  max-width: 960px;
  margin: 0 auto;
  padding: clamp(1rem, 3vw, 1.5rem) clamp(1.5rem, 5vw, 3rem);
  width: 100%;
}

.landing-content {
  max-width: 640px;
}

.landing-content h2 {
  font-style: normal;
  font-weight: 600;
  font-size: clamp(1.3rem, 3vw, 1.6rem);
  margin-top: 0.5rem;
  margin-bottom: 0.75rem;
}

.landing-content p {
  margin-bottom: 0.75rem;
  color: var(--text);
}

.landing-content code {
  font-family: var(--font-mono);
  font-size: 0.85em;
  background: rgba(0, 0, 0, 0.04);
  padding: 0.1rem 0.3rem;
  border-radius: 2px;
}

/* Program cards */
.landing-programs {
  max-width: 720px;
}

.program-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 0.75rem;
  margin-top: 1rem;
}

.program-card {
  border: 1px dotted var(--border);
  border-radius: 2px;
  padding: 1rem 1.25rem;
  cursor: pointer;
  transition: background 0.15s ease;
}

.program-card:hover {
  background: var(--bg-card-hover);
}

.program-card.expanded {
  border-color: var(--text-secondary);
}

.program-card h4 {
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--text-bright);
  margin-bottom: 0.4rem;
}

.program-card p {
  font-size: 0.75rem;
  color: var(--text-secondary);
  line-height: 1.6;
}

.program-agents {
  display: flex;
  gap: 0.35rem;
  margin-top: 0.6rem;
  flex-wrap: wrap;
}

.program-agent-tag {
  font-size: 0.65rem;
  padding: 0.1rem 0.45rem;
  border-radius: 10px;
  border: 1px dotted var(--border);
  color: var(--text-dim);
}

.landing-code-block {
  background: var(--bg-terminal);
  border: 1px dotted var(--border);
  border-radius: 2px;
  overflow: hidden;
  margin-top: 0.75rem;
}

.landing-code-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 1rem;
  border-bottom: 1px dotted var(--border);
  font-size: 0.72rem;
  color: var(--text-dim);
}

.landing-dots {
  display: flex;
  gap: 4px;
}

.landing-dots span {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.08);
}

.landing-code-block pre {
  padding: 1rem 1.25rem;
  overflow-x: auto;
  font-family: var(--font-mono);
  font-size: clamp(0.72rem, 1.4vw, 0.8rem);
  line-height: 1.5;
  color: var(--text);
  white-space: pre;
}

/* Footer */
.landing-footer {
  max-width: 960px;
  margin: 0 auto;
  padding: clamp(2rem, 5vw, 2.5rem) clamp(1.5rem, 5vw, 3rem);
  border-top: 1px dotted var(--border-light);
  margin-top: auto;
  width: 100%;
}

.landing-footer-inner {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 0.78rem;
  color: var(--text-secondary);
}

.landing-footer-brand {
  font-family: var(--font-serif);
  font-style: italic;
}

.landing-footer-sep {
  margin: 0 0.5rem;
  opacity: 0.4;
}

.landing-footer a {
  color: var(--text-secondary);
  border-bottom: 1px dotted var(--border-light);
}

@media (max-width: 720px) {
  .landing-actions {
    flex-direction: column;
    align-items: flex-start;
  }

  .landing-footer-inner {
    flex-direction: column;
    align-items: flex-start;
    gap: 1rem;
  }

  .program-grid {
    grid-template-columns: 1fr;
  }
}
</style>
