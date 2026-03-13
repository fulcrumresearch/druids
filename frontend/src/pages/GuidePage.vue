<script setup>
import { ref } from 'vue'

const expandedExample = ref(null)

function toggleExample(name) {
  expandedExample.value = expandedExample.value === name ? null : name
}

const examples = {
  basher: {
    filename: '.druids/basher.py',
    title: 'basher.py',
    desc: 'Finder agent scans for tasks, spawns implementor+reviewer pairs that iterate up to 3 review rounds before acceptance or rejection.',
    agents: ['finder', 'impl-N', 'review-N'],
    code: `async def program(ctx, repo_full_name="", task_name="", task_spec=""):
    active_tasks = {}
    task_counter = 0

    async def spawn_task(task_name, task_spec):
        nonlocal task_counter
        task_counter += 1
        rejections = 0

        implementor = await ctx.agent(
            f"impl-{task_counter}",
            system_prompt=IMPLEMENTOR_SYSTEM_PROMPT,
            prompt=f"## Task: {task_name}\\n\\n{task_spec}",
            git="write",
        )
        reviewer = await ctx.agent(
            f"review-{task_counter}",
            system_prompt=REVIEWER_SYSTEM_PROMPT,
            model="claude-sonnet-4-6",
            git="post",
            share_machine_with=implementor,
        )

        @implementor.on("submit_for_review")
        async def on_submit(summary=""):
            """Submit your implementation for review."""
            await reviewer.send(f"Review this implementation.\\n\\n{summary}")
            return "Submitted for review. Wait for feedback."

        @reviewer.on("approve_and_pr")
        async def on_approve(pr_url=""):
            """Signal the PR has been created."""
            ctx.done(f"PR created: {pr_url}")
            return "Task complete."

        @reviewer.on("send_feedback")
        async def on_feedback(feedback=""):
            """Send feedback to the implementor."""
            nonlocal rejections
            rejections += 1
            if rejections >= MAX_REVIEW_ROUNDS:
                ctx.done(f"Abandoned after {rejections} rounds.")
                return "Max review rounds reached."
            await implementor.send(f"Rejected ({rejections}/3):\\n{feedback}")
            return f"Feedback sent. ({rejections}/{MAX_REVIEW_ROUNDS})"

    if task_name and task_spec:
        await spawn_task(task_name, task_spec)
        return

    finder = await ctx.agent("finder", prompt="Start scanning...", git="post")

    @finder.on("spawn_task")
    async def on_spawn_task(task_name="", task_spec=""):
        """Spawn an implementor + reviewer pair."""
        await spawn_task(task_name, task_spec)
        return f"Spawned. Active tasks: {len(active_tasks)}."`,
  },
  build: {
    filename: '.druids/build.py',
    title: 'build.py',
    desc: 'Builder implements a spec on a feature branch. Critic reviews each commit for simplicity. Auditor verifies the builder\'s end-to-end testing was real.',
    agents: ['builder', 'critic', 'auditor'],
    code: `async def program(ctx, spec="", task_name="", repo_full_name=""):
    working_dir = "/home/agent/repo"
    branch_name = f"druids/{_slugify(task_name or 'build')}"
    rejections = 0
    critic = None

    builder = await ctx.agent(
        "builder",
        system_prompt=BUILDER_SYSTEM_PROMPT.format(branch_name=branch_name),
        prompt=f"## Spec: {task_name or 'Build task'}\\n\\n{spec}",
        git="write",
        working_directory=working_dir,
    )

    auditor = await ctx.agent(
        "auditor",
        system_prompt=AUDITOR_SYSTEM_PROMPT,
        git="post",
        working_directory=working_dir,
        share_machine_with=builder,
    )

    @builder.on("commit")
    async def on_commit(message: str = ""):
        """Commit staged changes and notify the critic."""
        nonlocal critic
        result = await builder.exec(f"git commit -m {shlex.quote(message)}")
        if result.exit_code != 0:
            return f"Commit failed:\\n{result.stderr}"
        await builder.exec("git push")
        if critic is None:
            critic = await ctx.agent(
                "critic",
                system_prompt=CRITIC_SYSTEM_PROMPT,
                git="read",
                working_directory=working_dir,
                share_machine_with=builder,
            )

            @critic.on("send_critique")
            async def on_critique(feedback: str = ""):
                await builder.send(f"[Critic] {feedback}")
                return "Feedback sent to builder."

        await critic.send(f"New commit: {message}. Run git diff HEAD~1.")
        return f"Committed and pushed.\\n{result.stdout}"

    @builder.on("submit_for_review")
    async def on_submit(summary: str = ""):
        """Submit verification for audit."""
        await auditor.send(
            f"Builder submitted for audit.\\n\\n"
            f"## Spec\\n\\n{spec}\\n\\n"
            f"## Verification summary\\n\\n{summary}"
        )
        return "Submitted for audit."

    @auditor.on("approve")
    async def on_approve(summary: str = ""):
        ctx.done(summary or "Build approved.")
        return "Done."

    @auditor.on("send_feedback")
    async def on_feedback(feedback: str = ""):
        nonlocal rejections
        rejections += 1
        if rejections >= MAX_AUDIT_ROUNDS:
            ctx.done(f"Abandoned after {rejections} audit rounds.")
            return "Max rounds reached."
        await builder.send(
            f"[Auditor] Rejected ({rejections}/{MAX_AUDIT_ROUNDS}):\\n{feedback}"
        )
        return f"Feedback sent ({rejections}/{MAX_AUDIT_ROUNDS})."`,
  },
  review: {
    filename: '.druids/review.py',
    title: 'review.py',
    desc: 'Demo agent reviews a pull request on real infrastructure. Sonnet monitor watches for lazy behavior and nudges.',
    agents: ['demo', 'monitor'],
    code: `async def program(ctx, pr_number="0", pr_title="", pr_body="", repo_full_name=""):
    demo = await ctx.agent(
        "demo",
        prompt=f"Demo PR #{pr_number} in {repo_full_name}.\\n\\n{pr_body}",
        system_prompt=SYSTEM_PROMPT,
        git="post",
    )

    monitor = await ctx.agent(
        "monitor",
        system_prompt=MONITOR_PROMPT,
        model="claude-sonnet-4-6",
        git="read",
        share_machine_with=demo,
    )

    @demo.on("finish")
    async def on_finish(summary=""):
        """Call when the review is posted. Ends the execution."""
        ctx.done(summary or "Review complete.")
        return "Done."`,
  },
  docalign: {
    filename: '.druids/doc-align.py',
    title: 'doc-align.py',
    desc: 'Auditor reads every doc, compares against source code, produces a report. Fixer rewrites stale docs. Reviewer verifies fixes match the code.',
    agents: ['auditor', 'fixer', 'reviewer'],
    code: `async def program(ctx, **kwargs):
    working_dir = "/home/agent/repo"
    branch_name = "druids/doc-alignment"
    audit_report = None
    rejections = 0

    auditor = await ctx.agent(
        "auditor",
        system_prompt=AUDITOR_SYSTEM_PROMPT,
        prompt=(
            f"Audit the following documentation files against the source code.\\n\\n"
            f"## Doc files\\n\\n{DOC_FILES}\\n\\n"
            f"## Source files\\n\\n{CODE_TO_CHECK}\\n\\n"
            f"Call submit_report when done."
        ),
        git="read",
        working_directory=working_dir,
    )

    @auditor.on("submit_report")
    async def on_report(report: str = ""):
        """Submit the audit report. Triggers the fixer agent."""
        nonlocal audit_report
        audit_report = report

        fixer = await ctx.agent(
            "fixer",
            system_prompt=FIXER_SYSTEM_PROMPT.format(branch_name=branch_name),
            prompt=f"## Audit Report\\n\\n{report}\\n\\nFix every issue.",
            git="write",
            working_directory=working_dir,
        )

        reviewer = await ctx.agent(
            "reviewer",
            system_prompt=REVIEWER_SYSTEM_PROMPT,
            model="claude-sonnet-4-6",
            git="post",
            working_directory=working_dir,
            share_machine_with=fixer,
        )

        @fixer.on("submit_for_review")
        async def on_submit(summary=""):
            await reviewer.send(f"Review the fixes.\\n\\n{summary}")
            return "Submitted for review."

        @reviewer.on("approve_and_pr")
        async def on_approve(pr_url=""):
            ctx.done(f"Docs aligned. PR: {pr_url}")
            return "Done."

        @reviewer.on("send_feedback")
        async def on_feedback(feedback=""):
            nonlocal rejections
            rejections += 1
            if rejections >= MAX_REVIEW_ROUNDS:
                ctx.done("Max review rounds reached.")
                return "Stopping."
            await fixer.send(f"Reviewer feedback:\\n{feedback}")
            return f"Sent. ({rejections}/{MAX_REVIEW_ROUNDS})"

        return "Report received. Fixer and reviewer spawned."`,
  },
  main: {
    filename: '.druids/main.py',
    title: 'main.py',
    desc: 'Spawns Claude and Codex agents in parallel on the same spec. Both implement independently. Completes when both submit.',
    agents: ['claude', 'codex'],
    code: `import asyncio

async def program(ctx, spec="", **kwargs):
    """Spawn a Claude and a Codex agent on the same spec in parallel."""

    common_prompt = f"""You are implementing a feature.
Read the spec carefully. Follow conventions in CLAUDE.md.
Create a feature branch, commit, push, and open a PR.
When done, call the submit tool with a summary.

## Spec

{spec}
"""

    claude, codex = await asyncio.gather(
        ctx.agent("claude", model="claude", prompt=common_prompt, git="write"),
        ctx.agent("codex", model="codex", prompt=common_prompt, git="write"),
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
  <div class="guide-page">
    <!-- Hero -->
    <div class="page-header">
      <h1>Programs</h1>
      <p class="section-desc">A program is a Python async function that creates agents, registers tool handlers, and lets the runtime dispatch events until the work is done. Three primitives, nothing else: <code>ctx.agent()</code> creates workers, <code>@agent.on()</code> registers tools, <code>ctx.done()</code> ends the execution.</p>
    </div>

    <!-- Minimal example -->
    <section class="guide-section">
      <h2>Minimal program</h2>
      <div class="code-block">
        <div class="code-header">
          <div class="dots"><span></span><span></span><span></span></div>
          minimal.py
        </div>
        <pre>async def program(ctx):
    executor = await ctx.agent("executor", prompt="Implement the feature.")

    @executor.on("submit")
    async def on_submit(summary=""):
        """Call when the work is complete."""
        ctx.done(summary)
        return "Done."</pre>
      </div>
      <p class="section-desc">Registering <code>@agent.on("submit")</code> gives the agent a <code>submit</code> tool. When the agent calls it, the handler fires. The return value goes back as the tool result. There is no distinction between "an event" and "a tool the agent can call."</p>
    </section>

    <!-- Real programs -->
    <section class="guide-section">
      <h2>Programs in production</h2>
      <p class="section-desc">These run in the Druids codebase today. Click to see the orchestration code.</p>

      <div class="example-grid">
        <div v-for="(ex, key) in examples" :key="key" class="example-card" :class="{ expanded: expandedExample === key }" @click="toggleExample(key)">
          <div class="example-card-header">
            <div>
              <h4>{{ ex.title }}</h4>
            </div>
          </div>
          <p>{{ ex.desc }}</p>
          <div class="agents">
            <span v-for="agent in ex.agents" :key="agent" class="agent-tag">{{ agent }}</span>
          </div>
        </div>
      </div>

      <div v-if="expandedExample" class="code-block mt-2">
        <div class="code-header">
          <div class="dots"><span></span><span></span><span></span></div>
          {{ examples[expandedExample].filename }}
        </div>
        <pre>{{ examples[expandedExample].code }}</pre>
      </div>
    </section>

    <!-- API Reference -->
    <section class="guide-section">
      <h2>API reference</h2>

      <div class="code-block">
        <div class="code-header">
          <div class="dots"><span></span><span></span><span></span></div>
          ctx API
        </div>
        <pre># --- Agent creation ---
agent = await ctx.agent(
    name,                          # unique name within this execution
    model="claude",                # "claude", "codex", or "claude-sonnet-4-6"
    prompt="...",                  # initial user prompt
    system_prompt="...",           # system prompt for the agent
    monitor_prompt="...",          # Sonnet monitor that watches this agent
    git="write",                   # "read", "post", or "write" (None = no git)
    working_directory="/home/agent",
    share_machine_with=other,     # share VM with another agent
    mcp_servers={...},             # external MCP servers with $SECRET refs
)

# --- Messaging ---
await agent.send("message")        # send a prompt to the agent

# --- Event handlers ---
@agent.on("tool_name")
async def handler(param: str) -> str:
    """Docstring becomes tool description."""
    return "result sent back to agent"

# --- Lifecycle ---
ctx.done(result)                   # end successfully
ctx.fail(reason)                   # end with failure

# --- Client events ---
@ctx.on_client_event("event_name")
def handle(text=""):
    return {"ack": True}

# --- Direct agent methods ---
result = await agent.exec("command")   # run shell command on agent VM
url = await agent.expose("web", 8080)  # expose port as public HTTPS URL</pre>
      </div>

      <h3 class="mt-3">Git access levels</h3>
      <table class="data-table">
        <thead>
          <tr><th>Level</th><th>Capabilities</th><th>Use case</th></tr>
        </thead>
        <tbody>
          <tr><td><code>read</code></td><td>Clone, checkout, pull</td><td>Finder agents, monitors</td></tr>
          <tr><td><code>post</code></td><td>Read + create PRs, post issues</td><td>Reviewer agents</td></tr>
          <tr><td><code>write</code></td><td>Read + create branches, commit, push</td><td>Implementor agents</td></tr>
          <tr><td><code>None</code></td><td>No git token, no repo clone</td><td>Utility agents, orchestrators</td></tr>
        </tbody>
      </table>

      <h3 class="mt-3">Template variables</h3>
      <p class="section-desc">Auto-substituted in <code>system_prompt</code> and <code>prompt</code> via Python <code>string.Template</code>.</p>
      <table class="data-table">
        <thead>
          <tr><th>Variable</th><th>Value</th></tr>
        </thead>
        <tbody>
          <tr><td><code>$execution_slug</code></td><td>Short human-readable execution identifier</td></tr>
          <tr><td><code>$agent_name</code></td><td>This agent's name</td></tr>
          <tr><td><code>$working_directory</code></td><td>Agent's working directory</td></tr>
          <tr><td><code>$branch_name</code></td><td>Auto-generated: <code>druids/{execution_slug}</code></td></tr>
          <tr><td><code>$spec</code></td><td>The spec string passed to the execution</td></tr>
        </tbody>
      </table>

      <h3 class="mt-3">MCP servers with secrets</h3>
      <p class="section-desc">MCP server configs can reference devbox secrets using <code>$VAR_NAME</code> syntax. Resolved from the encrypted secret store at startup.</p>
      <div class="code-block">
        <div class="code-header">
          <div class="dots"><span></span><span></span><span></span></div>
          mcp-secrets.py
        </div>
        <pre>agent = await ctx.agent(
    "finder",
    prompt="Scan Slack for bugs...",
    mcp_servers={
        "slack": {
            "url": "$SLACK_MCP_URL",
            "headers": {"Authorization": "Bearer $SLACK_BOT_TOKEN"},
        }
    },
)</pre>
      </div>
    </section>
  </div>
</template>

<style scoped>
.guide-page {
  max-width: 100%;
}

.guide-section {
  padding: 2.5rem 0;
}

.guide-section + .guide-section {
  border-top: 1px solid var(--border-light);
}

.section-desc {
  color: var(--text-secondary);
  max-width: 560px;
  margin-bottom: 1.5rem;
  line-height: 1.7;
  font-size: 0.85rem;
}

/* Code blocks */
.code-block {
  background: var(--bg-terminal);
  border: 1px solid var(--border);
  border-radius: 6px;
  overflow: hidden;
  margin: 1rem 0;
}

.code-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.6rem 1rem;
  border-bottom: 1px solid var(--border);
  font-size: 0.72rem;
  color: var(--text-dim);
}

.code-header .dots {
  display: flex;
  gap: 5px;
}

.code-header .dots span {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: rgba(200, 180, 150, 0.15);
}

.code-block pre {
  padding: 1rem;
  overflow-x: auto;
  font-family: var(--font-mono);
  font-size: clamp(0.72rem, 1.4vw, 0.8rem);
  line-height: 1.5;
  color: var(--text);
  white-space: pre-wrap;
  word-break: break-all;
}

code {
  font-family: var(--font-mono);
  font-size: 0.85em;
  background: var(--bg-terminal);
  padding: 0.15rem 0.4rem;
  border-radius: 3px;
  color: var(--text);
}

/* Example cards */
.example-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 1rem;
  margin: 1.5rem 0;
}

.example-card {
  background: var(--bg-card);
  border: 1px solid var(--border-light);
  border-radius: 6px;
  padding: clamp(1rem, 2vw, 1.5rem);
  cursor: pointer;
  transition: background 0.15s ease;
}

.example-card:hover {
  background: var(--bg-card-hover);
}

.example-card.expanded {
  border-color: var(--text-secondary);
}

.example-card h4 {
  font-size: 0.88rem;
  font-weight: 600;
  color: var(--text-bright);
  margin-bottom: 0.5rem;
}

.example-card p {
  font-size: 0.78rem;
  color: var(--text-secondary);
  line-height: 1.6;
}

.agents {
  display: flex;
  gap: 0.4rem;
  margin-top: 0.75rem;
  flex-wrap: wrap;
}

.agent-tag {
  font-size: 0.68rem;
  padding: 0.15rem 0.5rem;
  border-radius: 10px;
  border: 1px solid var(--border);
  color: var(--text-dim);
}

/* Responsive */
@media (max-width: 720px) {
  .example-grid {
    grid-template-columns: 1fr;
  }
}
</style>