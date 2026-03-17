"""Refactor program -- theory-driven codebase restructuring.

Two phases, one long-lived program.

  theorist      Reads the codebase deeply: code, git history, tests, docs.
                Builds a theory of the codebase -- why things are shaped the
                way they are. Surfaces Problems: abstraction errors, missing
                simplicity, unnecessary repetition, naming that lies. Stays
                alive to answer questions.

  builder(s)    Spawned per problem on demand. The driver controls how many
                and what models (claude, codex). Each gets its own machine
                and branch. When a builder finishes, a prover on the same
                machine verifies behavioral equivalence.

The driver interacts through client events:

  get_problems  Returns all surfaced problems and their status.
  discuss       Send a message to the theorist about a problem.
  focus         Tell the theorist to look at a specific area.
  spawn         Add one builder to a problem. Choose model (claude/codex).
  assign        Convenience: spawn N builders at once with a model mix.
  assign_all    Spawn builders for every open problem.
  pick          Pick the winning builder. Marks its PR ready.
  input         Send a message directly to a builder.
"""


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

THEORIST_SYSTEM_PROMPT = """\
You are a theorist. You read codebases and build a theory of how they work: \
why things are shaped the way they are, what the constraints are, where the \
design breaks down.

Read deeply. Read the code, the git history, the tests, the docs. Use \
`git log --oneline -50` and `git log --all --oneline --graph` to understand \
the project's trajectory. Use `git blame` on files that look wrong to \
understand when and why they became that way. Read README.md, SETUP.md, \
CLAUDE.md, and any architecture docs.

You are not scanning for bugs. You are building a mental model of the \
codebase and identifying structural problems -- places where the design \
does not match the reality, where abstractions leak or lie, where \
complexity has accumulated without earning its keep.

## What counts as a Problem

A Problem is a structural issue that makes the codebase harder to work \
with than it needs to be. Examples:

- Repeated logic: three modules each implement their own version of the \
same thing. Not copy-paste duplication necessarily -- structural \
duplication where the same concept is expressed in multiple places \
without sharing.
- Abstraction errors: an interface that forces every caller to handle a \
case that only matters for one consumer. An abstraction that is more \
complex than the thing it abstracts.
- Naming that lies: a module called "utils" that contains core business \
logic. A function called "validate" that also transforms.
- Missing concepts: code that passes around raw dicts or tuples where a \
named type would make intent clear. Logic scattered across files that \
belongs together.
- Over-engineering: layers of indirection that serve no current purpose. \
Configurability that nothing configures. Generality that handles one case.
- Dead patterns: code paths that were relevant once but are now unreachable \
or irrelevant, still shaping the architecture around them.

## Surfacing problems

When you identify a problem, call the `surface_problem` tool with:
- title: a short name for the problem
- description: what is wrong, why it is wrong, and what you think the \
codebase would look like without this problem. Include file paths and \
code references.
- severity: "high", "medium", or "low". High means the problem actively \
causes confusion or bugs. Low means it is inelegant but harmless.

Do not surface cosmetic issues. Do not surface things that are a matter \
of taste. Surface things where you can explain concretely why the current \
structure is worse than the alternative.

Take your time. Read broadly before surfacing anything. A shallow scan \
that produces ten vague problems is less useful than a deep reading that \
produces three precise ones.

## Driver interaction

You may receive [Driver] messages at any time. The driver is a human who \
knows this codebase. They may steer you toward specific areas, disagree \
with your assessment, or ask you to explain your reasoning. Incorporate \
their feedback. If they say a problem is intentional, update your theory.

You may also receive [Focus] messages asking you to look at a specific \
area of the codebase. When you receive one, read that area deeply and \
surface any problems you find there."""


BUILDER_SYSTEM_PROMPT = """\
You are a refactoring agent. You receive a problem description. Your job \
is to restructure the code to fix the problem without changing behavior.

You are one of several competing builders working on the same problem \
independently. The driver will pick the best solution.

Read `SETUP.md` for build and test instructions. Read the codebase to \
understand existing patterns before writing anything.

## The refactoring contract

1. Behavior must not change. The system must do exactly what it did before.
2. Tests must pass after every commit. No exceptions.
3. No new features. No "while I'm here" improvements outside the problem scope.
4. Each commit is one self-contained transformation. Small steps.

## Git

You start on the main branch. Before any changes, create a feature branch:

  git checkout -b {branch_name}

All commits go on this branch. Push with `git push -u origin {branch_name}`.

## Workflow

1. Run the full test suite FIRST. Record the baseline. If tests already \
fail, note which ones and do not break anything new.
2. Read the code described in the problem. Understand it fully before \
changing it.
3. Plan your transformation as a sequence of small steps.
4. For each step:
   a. Make one structural change.
   b. Run the test suite.
   c. If tests pass, call the `step` tool with a description of what you did.
   d. If tests fail, fix the failure before calling `step`.
5. After all steps, open a draft PR and call `submit` with a summary.

## Step discipline

Each step must leave the codebase in a working state. Good steps:
- Extract a function or class
- Rename for clarity
- Move code to a better location
- Replace repeated logic with a shared implementation
- Remove dead code

Bad steps:
- Rewrite an entire module in one commit
- Change behavior "to make it cleaner"
- Add new abstractions that did not exist before (unless the plan calls for it)

## Draft PR

Open a draft PR after your first step:

  gh pr create --draft --title "<title>" --body "<wip description>"

Push after every step.

## Driver input

You may receive [Driver] messages. Incorporate their feedback."""


PROVER_SYSTEM_PROMPT = """\
You are a behavioral equivalence prover. You share a machine with a \
builder who just finished refactoring code. Your job: prove that the \
refactored code behaves identically to the original.

You do NOT review code quality. You do NOT care whether the refactoring \
is good. You care only whether behavior changed.

## Method

1. Read `SETUP.md` for how to build and run the system.
2. Check out `main`. Build and start the system. Exercise it: make HTTP \
requests, run CLI commands, query databases, read logs. Capture every \
output. Cover the happy path, edge cases, and error paths for the code \
that was changed.
3. Stop the system.
4. Check out the refactored branch (`{branch_name}`). Build and start \
the system. Run the exact same commands. Capture every output.
5. Compare. Ignore timestamps, PIDs, and other non-deterministic values. \
Focus on: response bodies, status codes, error messages, data shapes, \
side effects.

## What counts as a behavioral change

- Different HTTP response body or status code for the same request
- Different error message or error type
- Different side effects (database writes, file creation, log output)
- Different CLI output for the same command
- A feature that worked before and no longer works
- A new error that did not exist before

## What does NOT count

- Performance differences (unless extreme)
- Log formatting changes
- Different ordering of unordered collections (sets, dict keys)
- Whitespace or formatting in non-user-facing output

## Verdict

When done, call the `verdict` tool with:
- result: "equivalent" or "divergent"
- evidence: the commands you ran, the outputs you compared, and your \
conclusion. Show real output, not summaries. If divergent, show exactly \
which command produced different output and what changed."""


# ---------------------------------------------------------------------------
# Program
# ---------------------------------------------------------------------------


def _slugify(name: str) -> str:
    slug = name.lower().replace(" ", "-")
    slug = "".join(c for c in slug if c.isalnum() or c == "-")
    return slug[:50].strip("-") or "task"


async def program(ctx, spec="", repo_full_name=""):
    """Theory-driven refactoring. Long-lived, driver-controlled."""
    repo_full_name = repo_full_name or ctx.repo_full_name or ""
    working_dir = "/home/agent/repo"

    problems = {}  # id -> {title, description, severity, status, builders, verdicts}
    builders = {}  # builder_name -> {problem_id, agent, branch, model, number}
    builder_counter = 0
    problem_counter = 0

    # -- Theorist --

    theorist = await ctx.agent(
        "theorist",
        system_prompt=THEORIST_SYSTEM_PROMPT,
        prompt=(
            f"Analyze the codebase at {working_dir}.\n\n"
            + (f"Driver notes:\n\n{spec}\n\n" if spec else "")
            + "Read broadly. Build your theory. Surface problems as you find them."
        ),
        git="read",
        working_directory=working_dir,
    )

    # -- Theorist tools --

    @theorist.on("surface_problem")
    async def on_surface(title: str = "", description: str = "", severity: str = "medium"):
        """Surface a structural problem you found in the codebase."""
        nonlocal problem_counter
        problem_counter += 1
        pid = f"P{problem_counter}"
        problems[pid] = {
            "title": title,
            "description": description,
            "severity": severity,
            "status": "open",
            "builders": [],
            "verdicts": {},
        }
        await ctx.emit("problem", {"id": pid, "title": title, "severity": severity, "description": description})
        return f"Problem {pid} surfaced."

    # -- Builder spawning --

    async def spawn_one(problem_id, plan, model):
        """Spawn a single builder for a problem. Returns the builder name."""
        nonlocal builder_counter
        builder_counter += 1
        num = builder_counter

        prob = problems[problem_id]
        builder_name = f"builder-{num}"
        branch_name = f"druids/refactor-{_slugify(prob['title'])}-{num}"

        builder = await ctx.agent(
            builder_name,
            system_prompt=BUILDER_SYSTEM_PROMPT.format(branch_name=branch_name),
            prompt=(
                f"## Problem: {prob['title']}\n\n"
                f"{prob['description']}\n\n"
                + (f"## Plan\n\n{plan}\n\n" if plan else "")
                + "Fix the problem. Work in small steps. Run tests after every change."
            ),
            model=model,
            git="write",
            working_directory=working_dir,
        )

        builders[builder_name] = {
            "problem_id": problem_id,
            "agent": builder,
            "branch": branch_name,
            "model": model,
            "number": num,
        }
        prob["builders"].append(builder_name)
        prob["status"] = "in_progress"

        # -- Builder tools --

        @builder.on("submit")
        async def on_submit(summary: str = ""):
            """Submit when all refactoring steps are complete. Push and open a draft PR before calling this."""

            await ctx.emit(
                "builder_done",
                {
                    "builder": builder_name,
                    "problem_id": problem_id,
                    "summary": summary,
                    "branch": branch_name,
                    "model": model,
                },
            )

            # Spawn prover for behavioral equivalence
            prover_name = f"prover-{num}"
            prover = await ctx.agent(
                prover_name,
                system_prompt=PROVER_SYSTEM_PROMPT.format(branch_name=branch_name),
                prompt=(
                    f"The builder has finished refactoring on branch `{branch_name}`.\n\n"
                    f"## Problem that was fixed\n\n{prob['title']}: {prob['description']}\n\n"
                    f"## Builder summary\n\n{summary}\n\n"
                    f"Read `SETUP.md`. Check out `main`, run the system, capture behavior. "
                    f"Then check out `{branch_name}`, run the system, capture behavior. "
                    f"Compare and deliver your verdict."
                ),
                model="claude-sonnet-4-6",
                git="read",
                working_directory=working_dir,
                share_machine_with=builder,
            )

            @prover.on("verdict")
            async def on_verdict(result: str = "", evidence: str = ""):
                """Deliver behavioral equivalence verdict."""
                prob["verdicts"][builder_name] = {
                    "result": result,
                    "evidence": evidence,
                }
                await ctx.emit(
                    "verdict",
                    {
                        "builder": builder_name,
                        "problem_id": problem_id,
                        "result": result,
                        "evidence": evidence,
                    },
                )
                return "Verdict recorded."

            return "Submitted. Prover is verifying behavioral equivalence."

        await ctx.emit(
            "builder_spawned",
            {
                "builder": builder_name,
                "problem_id": problem_id,
                "model": model,
                "branch": branch_name,
            },
        )
        return builder_name

    # -- Client events (driver interface) --

    @ctx.on_client_event("get_problems")
    def get_problems():
        """Return all surfaced problems and their status."""
        return {
            "problems": {
                pid: {
                    "title": p["title"],
                    "description": p["description"],
                    "severity": p["severity"],
                    "status": p["status"],
                    "builders": p["builders"],
                    "verdicts": p["verdicts"],
                }
                for pid, p in problems.items()
            },
            "builders": {
                name: {
                    "problem_id": b["problem_id"],
                    "branch": b["branch"],
                    "model": b["model"],
                }
                for name, b in builders.items()
            },
        }

    @ctx.on_client_event("discuss")
    async def on_discuss(problem_id="", message=""):
        """Discuss a problem with the theorist."""
        prob = problems.get(problem_id)
        if not prob:
            return {"error": f"Unknown problem: {problem_id}"}
        await theorist.send(f"[Driver] Re: {prob['title']} ({problem_id})\n\n{message}")
        return {"ack": True}

    @ctx.on_client_event("focus")
    async def on_focus(area=""):
        """Tell the theorist to examine a specific area."""
        await theorist.send(f"[Focus] The driver wants you to look at: {area}")
        return {"ack": True}

    @ctx.on_client_event("spawn")
    async def on_spawn(problem_id="", model="claude", plan=""):
        """Spawn one builder on a problem. model: claude, codex, etc."""
        prob = problems.get(problem_id)
        if not prob:
            return {"error": f"Unknown problem: {problem_id}"}
        name = await spawn_one(problem_id, plan, model)
        return {"builder": name, "model": model}

    @ctx.on_client_event("assign")
    async def on_assign(problem_id="", plan="", claude="2", codex="1"):
        """Spawn multiple builders for a problem. Specify count per model."""
        prob = problems.get(problem_id)
        if not prob:
            return {"error": f"Unknown problem: {problem_id}"}

        spawned = []
        for _ in range(int(claude)):
            name = await spawn_one(problem_id, plan, "claude")
            spawned.append({"builder": name, "model": "claude"})
        for _ in range(int(codex)):
            name = await spawn_one(problem_id, plan, "codex")
            spawned.append({"builder": name, "model": "codex"})
        return {"spawned": spawned}

    @ctx.on_client_event("assign_all")
    async def on_assign_all(plan="", claude="2", codex="1"):
        """Spawn builders for every open problem."""
        assigned = []
        for pid, prob in problems.items():
            if prob["status"] == "open":
                spawned = []
                for _ in range(int(claude)):
                    name = await spawn_one(pid, plan, "claude")
                    spawned.append(name)
                for _ in range(int(codex)):
                    name = await spawn_one(pid, plan, "codex")
                    spawned.append(name)
                assigned.append({"problem_id": pid, "builders": spawned})
        return {"assigned": assigned}

    @ctx.on_client_event("pick")
    async def on_pick(problem_id="", winner=""):
        """Pick the winning builder. Marks its PR ready."""
        prob = problems.get(problem_id)
        if not prob:
            return {"error": f"Unknown problem: {problem_id}"}
        if winner not in builders:
            return {"error": f"Unknown builder: {winner}"}

        prob["status"] = "done"
        info = builders[winner]
        await info["agent"].exec("gh pr ready 2>/dev/null || true")

        await ctx.emit("picked", {"problem_id": problem_id, "winner": winner, "branch": info["branch"]})
        return {"status": "picked", "winner": winner, "branch": info["branch"]}

    @ctx.on_client_event("input")
    async def on_input(builder_name="", text=""):
        """Send a message to a specific builder."""
        info = builders.get(builder_name)
        if not info:
            return {"error": f"Unknown builder: {builder_name}"}
        await info["agent"].send(f"[Driver]: {text}")
        return {"ack": True}

    await ctx.emit("ready", {"message": "Refactor program running. Theorist is reading."})
    await ctx.wait()
