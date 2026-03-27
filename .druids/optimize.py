"""Optimize -- profile a codebase and spawn agents to fix bottlenecks."""

from __future__ import annotations


PROFILER_PROMPT = """\
You are a profiler. Read the code, run the benchmark, and find bottlenecks.

1. Generate logs if needed: `python3 generate_logs.py`
2. Run the benchmark: `python3 benchmark.py logs/`
3. For each bottleneck, call `spawn_optimizer` with the function name,
   file path, and a description of why it's slow.
   Call it once per bottleneck. Wait for each call to return.
4. When all optimizers report back, download each optimized file from
   the optimizer that fixed it. Run a final benchmark to confirm the
   aggregate speedup. Then call `submit_results` with before/after times."""

OPTIMIZER_PROMPT = """\
You are an optimizer. Fix the bottleneck described below.

1. Read the file and understand the function you need to optimize.
2. Run the benchmark to get the baseline.
3. Fix the function. Only change the function you're assigned.
4. Run the benchmark again to measure your improvement.
5. Call `submit_fix` with the before/after times and the path to the
   file you changed."""


async def program(ctx):
    working_dir = "/home/agent/repo"
    optimizers = {}

    profiler = await ctx.agent(
        "profiler",
        system_prompt=PROFILER_PROMPT,
        prompt=f"Profile the code in `{working_dir}`. Find bottlenecks and spawn optimizers.",
        working_directory=working_dir,
    )

    @profiler.on("spawn_optimizer")
    async def on_spawn_optimizer(function_name="", file_path="", description=""):
        """Spawn an optimizer agent for a bottleneck."""
        name = f"optimizer-{len(optimizers) + 1}"

        # Each optimizer gets its own VM (forked from the profiler's)
        optimizer = await profiler.fork(
            name,
            system_prompt=OPTIMIZER_PROMPT,
            prompt=f"Optimize `{function_name}` in `{file_path}`.\n\n{description}",
        )

        # Let the profiler download the fixed file from the optimizer
        ctx.connect(optimizer, profiler)

        optimizers[name] = {"function": function_name, "file": file_path}

        @optimizer.on("submit_fix")
        async def on_submit_fix(before_ms=0.0, after_ms=0.0, file_path="", summary=""):
            """Submit benchmark results and the file you changed."""
            optimizers[name]["before"] = before_ms
            optimizers[name]["after"] = after_ms
            await profiler.send(
                f"[{name}] `{function_name}`: {before_ms}ms -> {after_ms}ms. "
                f'Use download_file(sender="{name}", path="{file_path}") to get the fix. '
                f"{summary}"
            )

        return f"Spawned {name} for `{function_name}`."

    @profiler.on("submit_results")
    async def on_submit_results(before_total=0.0, after_total=0.0, summary=""):
        """Submit the final aggregate benchmark."""
        await ctx.done({"before_ms": before_total, "after_ms": after_total, "summary": summary})
