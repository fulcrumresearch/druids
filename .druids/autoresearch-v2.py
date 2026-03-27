"""Autoresearch v2 -- scientist-driven ML research with adaptive parallelism.

Improvements over v1:
- Scientist controls assistant count via spawn_assistant tool
- Periodic nudge keeps scientist active during long experiments
- Compact tool for context refresh on long runs
- Clearer separation: scientist runs baseline, assistants run everything else
- Periodic VM snapshots for crash recovery
- Resume from snapshot to continue after execution death

Usage:
  druids exec .druids/autoresearch-v2.py --devbox autoresearch-bench/ar-cc-starter \
    spec="minimize val_bpb" gpu_hours=4 nudge_minutes=10

Resume from a previous snapshot:
  druids exec .druids/autoresearch-v2.py --devbox ar-v2-snapshot-1710... \
    spec="minimize val_bpb" gpu_hours=4 resume=true
"""

from __future__ import annotations

import asyncio
import json
import time


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

SCIENTIST_SYSTEM = """\
You are an autonomous ML research scientist. Your job is to design \
experiments that minimize val_bpb (validation bits per byte) on a language \
model training task.

You have lab assistants who run experiments for you. You never modify \
train.py or run training yourself, except for the initial baseline run.

## How experiments work

Each experiment modifies train.py and runs it for 5 minutes on a Modal \
H100 GPU via `uv run modal run --env ar-cc-starter run_modal.py`. The \
output metric is val_bpb (lower is better). You specify what change to \
make in natural language, and a lab assistant implements it and runs it.

Experiments run on git branches off of research/best. When a result \
improves, the assistant opens a PR into research/best. You decide \
what to merge. The research/best branch always represents the current \
best known configuration.

## Setup (do this first)

1. Call read_state to see the current state.
2. Read train.py, prepare.py, and run_modal.py.
3. Create the research/best branch from main.
4. Write initial research files and commit them.
5. Run the baseline yourself: `uv run modal run --env ar-cc-starter \
run_modal.py 2>&1`. This is the ONLY experiment you run directly.
6. Record the baseline result via log_entry and update_observations.
7. Spawn at least one assistant, then use run_experiments for everything.

## Your tools

- read_state: Full research state as markdown.
- run_experiments: Submit a batch as JSON [{branch, change, hypothesis}]. \
Returns immediately; results arrive as [Result] messages.
- update_observations: Rewrite accumulated observations.
- update_plan: Rewrite your research plan.
- log_entry: Write a timestamped journal entry.
- check_budget: Budget and assistant status.
- spawn_assistant: Spawn an additional lab assistant. You decide how \
many based on budget and hypothesis diversity.
- compact: When your context feels full, call this. Commit all your \
research files first. You will be restarted with fresh context and \
can read your committed files to resume.

## Git workflow

You own research/best. Create it from main at the start. Lab assistants \
branch off it for experiments. When they get an improvement, they open \
a PR. You merge with `gh pr merge`.

## State as committed files

Maintain research state as files in research/ on the research/best \
branch. These are your primary output -- the human reads them \
asynchronously to understand what you've learned and where you're going. \
Write and commit regularly. Each commit is a temporal snapshot.

- research/dashboard.html -- A self-contained, styled HTML page that \
summarizes the entire research session at a glance: current best, \
experiment history table, key findings, what you're trying next, and \
budget status. This is the main thing the human looks at. Make it \
visually clear and information-dense. Update it after every batch of \
results.
- research/observations.md -- Accumulated scientific knowledge. What \
works, what doesn't, interaction effects, hypotheses confirmed/rejected. \
This is your lab notebook. Curate it -- remove stale observations, \
synthesize patterns, flag surprises.
- research/plan.md -- Your current research plan and reasoning. What \
experiments are queued, why you chose them, what you expect. Update \
this BEFORE launching each batch so the human can see your thinking.

The orchestrator snapshots your VM periodically for crash recovery. \
If the execution dies, the human can resume from the snapshot and \
your committed files are the handoff.

## Branching strategy

Budget determines parallelism:
- Abundant: spawn more assistants, explore multiple directions.
- Limited: converge on the most promising branch, one assistant.

## While waiting for results

You always have work to do:
- Update the dashboard with your current thinking.
- Curate observations.
- Analyze trends. Is a 0.002 bpb difference signal or noise?
- Think about interactions between findings.
- Read train.py for new angles.
- You will receive periodic [Status] nudges to remind you.

## Human interaction

The human checks in asynchronously. They read your committed files \
and may send messages with feedback or additional budget.

## Be a good scientist

- Simplicity criterion: prefer simpler code at equal performance.
- Control variables. Change one thing at a time when possible.
- Track what you learn from failures.
- When stuck, try something radical.
- NEVER STOP. The human may be asleep. Keep experimenting until \
budget runs out."""


ASSISTANT_SYSTEM = """\
You are a lab assistant running ML experiments on Modal H100 GPUs. \
You receive experiment assignments, implement code changes, run \
training, and report results.

## Workflow for each experiment

1. Read the assignment message: branch name, code change, hypothesis.
2. Clean your working tree: `git checkout . && git clean -fd`
3. Fetch latest: `git fetch origin`
4. Check out the branch. If new, create from research/best: \
`git checkout -b <branch> origin/research/best`
5. Read train.py. Implement the specified change.
6. Commit: `git add -A && git commit -m "<description>"`
7. Run on Modal: \
`uv run modal run --env ar-cc-starter run_modal.py 2>&1 | tee /tmp/run.log`
8. Extract results: `grep "^val_bpb:\\|^peak_vram_mb:" /tmp/run.log`
9. If empty, the run crashed. Read `tail -50 /tmp/run.log`.

## Reporting

Call submit_result with:
- branch: the branch name
- val_bpb: the value (use "0" if crashed)
- peak_vram_mb: peak VRAM in MB (use "0" if crashed)
- config: one-line description of the change
- keep: "true" if val_bpb improved over the branch's previous best
- crashed: "true" if the run crashed
- notes: brief observations

## Git rules

- If improved (keep=true): push and open a PR into research/best: \
`git push -u origin <branch> && gh pr create --base research/best \
--title "<description>" --body "<hypothesis and result>"`
- If not improved or crashed: `git reset --hard HEAD~1`

After reporting, wait for the next assignment."""


# ---------------------------------------------------------------------------
# Program
# ---------------------------------------------------------------------------

EXPERIMENT_HOURS = 5.0 / 60.0
SNAPSHOT_INTERVAL_MINUTES = 20


async def program(ctx, spec="", gpu_hours="4", nudge_minutes="10", resume="", snapshot_minutes="", **kwargs):
    """Autonomous ML research with scientist-controlled parallelism.

    On resume, run against a snapshot devbox with resume=true. The scientist's
    committed files and git branches are the durable state -- the program just
    needs the remaining budget (pass via gpu_hours).
    """
    working_dir = "/home/agent/repo"
    gpu_hours_total = float(gpu_hours)
    nudge_interval = float(nudge_minutes) * 60
    snapshot_interval = float(snapshot_minutes or SNAPSHOT_INTERVAL_MINUTES) * 60
    next_journal_id = 1
    pending = 0
    assistant_count = 0
    scientist_generation = 0
    is_resume = str(resume).lower() in ("true", "1", "yes")
    last_snapshot_time = time.time()

    state = {
        "goal": spec or "minimize val_bpb",
        "status": "running",
        "started_at": time.time(),
        "budget": {
            "gpu_hours_total": gpu_hours_total,
            "gpu_hours_used": 0.0,
        },
        "best": None,
        "branches": {},
        "observations": "",
        "plan": "",
        "journal": [],
    }

    def add_journal(text):
        nonlocal next_journal_id
        entry = {"id": next_journal_id, "time": time.time(), "text": text}
        state["journal"].append(entry)
        next_journal_id += 1
        return entry

    def budget_remaining():
        return state["budget"]["gpu_hours_total"] - state["budget"]["gpu_hours_used"]

    async def take_snapshot(label="periodic"):
        """Snapshot the scientist's VM. Returns devbox name or None."""
        nonlocal last_snapshot_time
        try:
            name = f"ar-v2-{label}-{int(time.time())}"
            devbox_name = await scientist.snapshot_machine(name)
            last_snapshot_time = time.time()
            add_journal(f"Snapshot: {devbox_name}")
            ctx.emit("snapshot", {"devbox_name": devbox_name})
            return devbox_name
        except Exception as e:
            add_journal(f"Snapshot failed: {e}")
            return None

    def state_markdown():
        s = state
        b = s["budget"]
        rem = b["gpu_hours_total"] - b["gpu_hours_used"]
        lines = [
            f"# Research State\n",
            f"## Goal\n{s['goal']}\n",
            f"## Budget\n- Total: {b['gpu_hours_total']:.1f}h"
            f"\n- Used: {b['gpu_hours_used']:.1f}h"
            f"\n- Remaining: {rem:.1f}h\n",
        ]
        if s["best"]:
            lines.append(
                f"## Best Result\n- val_bpb: {s['best']['bpb']:.6f}"
                f"\n- branch: {s['best']['branch']}"
                f"\n- config: {s['best']['description']}\n"
            )
        else:
            lines.append("## Best Result\nNo results yet.\n")

        lines.append("## Branches")
        for name, br in s["branches"].items():
            lines.append(f"\n### {name} [{br['status']}]")
            lines.append(f"Hypothesis: {br['hypothesis']}")
            if br.get("best_bpb"):
                lines.append(f"Branch best: {br['best_bpb']:.6f}")
            for run in br["runs"]:
                tag = "CRASH" if run.get("crashed") else ("KEEP" if run["keep"] else "DISCARD")
                lines.append(f"  [{tag}] bpb={run['val_bpb']:.6f} | {run['config']}")

        lines.append(f"\n## Observations\n{s['observations'] or 'None yet.'}\n")
        lines.append(f"## Plan\n{s['plan'] or 'No plan yet.'}\n")

        recent = s["journal"][-10:]
        lines.append("## Recent Journal")
        for e in recent:
            t = time.strftime("%H:%M", time.localtime(e["time"]))
            lines.append(f"\n### Entry {e['id']} ({t})\n{e['text']}")

        return "\n".join(lines)

    # -- Assistant management --

    idle_assistants: asyncio.Queue = asyncio.Queue()

    async def create_assistant():
        nonlocal assistant_count
        i = assistant_count
        assistant_count += 1

        asst = await ctx.agent(
            f"assistant-{i}",
            system_prompt=ASSISTANT_SYSTEM,
            prompt=f"You are lab assistant {i}. Wait for experiment assignments.",
            model="claude-opus-4-6",
            git="write",
            working_directory=working_dir,
        )

        @asst.on("submit_result")
        async def on_result(
            branch="",
            val_bpb="",
            peak_vram_mb="",
            config="",
            keep="",
            crashed="",
            notes="",
            caller=None,
        ):
            """Report experiment results."""
            nonlocal pending
            bpb = float(val_bpb) if val_bpb else 0.0
            vram = float(peak_vram_mb) if peak_vram_mb else 0.0
            is_keep = str(keep).lower() in ("true", "1", "yes")
            is_crash = str(crashed).lower() in ("true", "1", "yes")

            run = {
                "val_bpb": bpb,
                "peak_vram_mb": vram,
                "config": config,
                "keep": is_keep,
                "crashed": is_crash,
                "notes": notes,
                "time": time.time(),
            }

            if branch in state["branches"]:
                state["branches"][branch]["runs"].append(run)
                if is_keep and bpb > 0:
                    prev = state["branches"][branch].get("best_bpb")
                    if prev is None or bpb < prev:
                        state["branches"][branch]["best_bpb"] = bpb

            if is_keep and bpb > 0:
                if state["best"] is None or bpb < state["best"]["bpb"]:
                    state["best"] = {
                        "bpb": bpb,
                        "branch": branch,
                        "description": config,
                    }

            state["budget"]["gpu_hours_used"] += EXPERIMENT_HOURS
            tag = "CRASH" if is_crash else ("KEEP" if is_keep else "DISCARD")
            add_journal(f"{branch}: [{tag}] bpb={bpb:.6f} | {config}")
            pending -= 1

            # scientist is accessed from the outer scope — if compacted,
            # this automatically sends to the new scientist instance
            await scientist.send(
                f"[Result] {branch}: val_bpb={bpb:.6f} [{tag}] | {config}"
                + (f"\nVRAM: {vram:.0f}MB" if vram else "")
                + (f"\nNotes: {notes}" if notes else "")
                + f"\nBudget remaining: {budget_remaining():.1f}h"
                + f" | Pending: {pending}"
            )

            if caller:
                await idle_assistants.put(caller)

            if budget_remaining() <= 0:
                state["status"] = "waiting"
                await scientist.send(
                    "[System] Budget exhausted. Curate your observations, update the dashboard, and commit."
                )

            # Snapshot periodically for crash recovery
            if time.time() - last_snapshot_time >= snapshot_interval:
                await take_snapshot()

            return (
                f"Recorded. Best: "
                f"{state['best']['bpb']:.6f if state['best'] else '--'}. "
                f"Budget: {budget_remaining():.1f}h"
            )

        await idle_assistants.put(asst)
        return asst

    # -- Scientist tools (extracted so we can re-register on compact) --

    def register_scientist_tools(sci):
        @sci.on("read_state")
        async def on_read_state():
            """Read the full research state as markdown."""
            return state_markdown()

        @sci.on("run_experiments")
        async def on_run_experiments(experiments: str = ""):
            """Submit experiments. JSON list of {branch, change, hypothesis}.

            Returns immediately. Results arrive as [Result] messages."""
            nonlocal pending

            if budget_remaining() <= 0:
                return "Budget exhausted. Wait for human to add more."

            try:
                exps = json.loads(experiments)
            except json.JSONDecodeError:
                return "Invalid JSON. Expected: [{branch, change, hypothesis}, ...]"

            if not isinstance(exps, list):
                return "Expected a JSON list."

            launched = 0
            for exp in exps:
                branch = exp.get("branch", "")
                change = exp.get("change", "")
                hypothesis = exp.get("hypothesis", "")
                if not branch or not change:
                    continue

                if branch not in state["branches"]:
                    state["branches"][branch] = {
                        "hypothesis": hypothesis,
                        "status": "active",
                        "runs": [],
                        "best_bpb": None,
                    }

                try:
                    asst = idle_assistants.get_nowait()
                except asyncio.QueueEmpty:
                    break

                pending += 1
                launched += 1

                branch_info = ""
                br = state["branches"][branch]
                if br.get("best_bpb"):
                    branch_info = f"\nBranch current best: {br['best_bpb']:.6f}"

                await asst.send(
                    f"[Experiment]\n"
                    f"Branch: {branch}\n"
                    f"Change: {change}\n"
                    f"Hypothesis: {hypothesis}"
                    f"{branch_info}"
                    f"\nBase branch: research/best\n\n"
                    f"Implement the change, run the experiment, "
                    f"call submit_result."
                )

            idle = idle_assistants.qsize()
            return (
                f"Launched {launched} experiment(s). "
                f"{idle} assistant(s) idle, {pending} pending. "
                f"Results will arrive as [Result] messages."
            )

        @sci.on("update_observations")
        async def on_update_obs(observations: str = ""):
            """Rewrite the observations section of the research state."""
            state["observations"] = observations
            return "Observations updated."

        @sci.on("update_plan")
        async def on_update_plan(plan: str = ""):
            """Rewrite the plan section of the research state."""
            state["plan"] = plan
            return "Plan updated."

        @sci.on("log_entry")
        async def on_log_entry(text: str = ""):
            """Write a timestamped journal entry."""
            entry = add_journal(text)
            return f"Entry #{entry['id']} recorded."

        @sci.on("check_budget")
        async def on_check_budget():
            """Check remaining budget and assistant status."""
            return (
                f"GPU hours total: {state['budget']['gpu_hours_total']:.1f}\n"
                f"GPU hours used: {state['budget']['gpu_hours_used']:.1f}\n"
                f"GPU hours remaining: {budget_remaining():.1f}\n"
                f"Pending experiments: {pending}\n"
                f"Assistants: {assistant_count} total, "
                f"{idle_assistants.qsize()} idle"
            )

        @sci.on("spawn_assistant")
        async def on_spawn(reason: str = ""):
            """Spawn an additional lab assistant to run experiments in parallel.

            Call this when you have multiple independent hypotheses to test
            and budget to support parallel execution."""
            asst = await create_assistant()
            add_journal(f"Spawned assistant-{assistant_count - 1}: {reason}")
            return f"Assistant {assistant_count - 1} spawned. {idle_assistants.qsize()} idle, {assistant_count} total."

        @sci.on("compact")
        async def on_compact(summary: str = ""):
            """Request a context refresh. Commit all research files first.

            You will be restarted with fresh context. Your VM is snapshotted
            before restart so all installed packages and files are preserved.
            Read your committed files on research/best to resume."""
            nonlocal scientist, scientist_generation
            scientist_generation += 1
            add_journal(f"Scientist compacted (gen {scientist_generation}): {summary}")

            # Snapshot before respawning so the new scientist inherits
            # the full environment (packages, caches, etc.)
            await take_snapshot(f"compact-g{scientist_generation}")

            new_sci = await ctx.agent(
                f"scientist-g{scientist_generation}",
                system_prompt=SCIENTIST_SYSTEM,
                prompt=(
                    f"You have been compacted (generation {scientist_generation}). "
                    f"Your research goal: {state['goal']}\n\n"
                    f"Call read_state for the current structured state. "
                    f"Then read your committed files on research/best: "
                    f"research/dashboard.html, research/observations.md, "
                    f"research/plan.md. Resume where you left off.\n\n"
                    f"Previous scientist's summary: {summary}"
                ),
                model="claude-opus-4-6",
                git="write",
                working_directory=working_dir,
            )
            register_scientist_tools(new_sci)
            scientist = new_sci
            return "Compacting. You will be restarted with fresh context."

    # -- Create scientist --

    scientist = await ctx.agent(
        "scientist",
        system_prompt=SCIENTIST_SYSTEM,
        model="claude-opus-4-6",
        git="write",
        working_directory=working_dir,
        # prompt is set after potential state restore below
    )

    register_scientist_tools(scientist)

    # -- Resume or fresh start --
    if is_resume:
        add_journal("Execution resumed from snapshot.")
        await scientist.send(
            f"You are RESUMING a previous research session that was interrupted.\n\n"
            f"Your research goal: {spec or 'minimize val_bpb'}\n\n"
            f"Budget for this session: {gpu_hours_total} GPU-hours.\n\n"
            f"Your VM was restored from a snapshot. All your files, git branches, "
            f"and installed packages are intact. Read your committed files on "
            f"research/best (research/dashboard.html, research/observations.md, "
            f"research/plan.md) to orient yourself.\n\n"
            f"Do NOT re-run the baseline or recreate research/best. Spawn "
            f"assistant(s) and continue experimenting from where you left off."
        )
    else:
        await scientist.send(
            f"Your research goal: {spec or 'minimize val_bpb'}\n\n"
            f"Budget: {gpu_hours_total} GPU-hours.\n\n"
            "Follow the setup steps in your instructions. Start with "
            "read_state, read the codebase, create research/best, write "
            "initial files, run the baseline yourself, then spawn "
            "assistant(s) and use run_experiments for everything after."
        )
        add_journal(f"Research started. Goal: {state['goal']}. Budget: {gpu_hours_total}h.")

    # -- Periodic nudge --

    async def nudge_loop():
        while True:
            await asyncio.sleep(nudge_interval)
            try:
                best = state["best"]["bpb"] if state["best"] else None
                await scientist.send(
                    f"[Status] {time.strftime('%H:%M')} | "
                    f"Budget: {budget_remaining():.1f}h remaining | "
                    f"Pending: {pending} | "
                    f"Best: {best:.6f if best else 'none'} | "
                    f"Assistants: {idle_assistants.qsize()} idle / "
                    f"{assistant_count} total\n"
                    f"Update your dashboard and observations if needed."
                )
            except Exception:
                pass

    asyncio.create_task(nudge_loop())

    # -- Periodic snapshot loop --

    async def snapshot_loop():
        while True:
            await asyncio.sleep(snapshot_interval)
            await take_snapshot()

    asyncio.create_task(snapshot_loop())

    # -- Client events --

    @ctx.on_client_event("get_state")
    def on_get_state():
        """Return full experiment state."""
        return state

    @ctx.on_client_event("snapshot_now")
    async def on_snapshot_now():
        """Manually trigger a VM snapshot for crash recovery."""
        devbox_name = await take_snapshot("manual")
        if devbox_name:
            return {"devbox_name": devbox_name}
        return {"error": "snapshot failed"}

    @ctx.on_client_event("feedback")
    async def on_feedback(text="", extra_gpu_hours=""):
        """Send feedback to the scientist, optionally adding budget."""
        if extra_gpu_hours:
            extra = float(extra_gpu_hours)
            state["budget"]["gpu_hours_total"] += extra
            add_journal(f"Budget increased by {extra}h")
        msg = f"[Human Feedback] {text}"
        if extra_gpu_hours:
            msg += f"\nBudget +{extra_gpu_hours}h. Remaining: {budget_remaining():.1f}h"
        state["status"] = "running"
        await scientist.send(msg)
        return {"ack": True}

    await ctx.wait()
