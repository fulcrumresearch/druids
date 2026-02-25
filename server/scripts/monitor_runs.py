"""Monitor terminal-bench runs and log status to a file.

Runs in a loop, writing status to /tmp/terminal-bench-monitor.log.
When a run finishes, writes a marker file so the orchestrating process
knows to kick off the next run.

Usage:
    python -u scripts/monitor_runs.py
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

RESULTS_FILE = Path("/tmp/terminal-bench-v2-run/results.json")
MONITOR_LOG = Path("/tmp/terminal-bench-monitor.log")
TRACES_DIR = Path(os.path.expanduser("~/.orpheus/executions"))
RESULTS_DIR = Path(__file__).parent.parent / "results"


def log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(MONITOR_LOG, "a") as f:
        f.write(line + "\n")


def get_results() -> dict:
    if RESULTS_FILE.exists():
        return json.loads(RESULTS_FILE.read_text())
    return {}


def get_active_traces() -> list[dict]:
    """Get traces that have been modified in the last 5 minutes."""
    now = time.time()
    active = []
    for f in TRACES_DIR.glob("*.jsonl"):
        if now - f.stat().st_mtime < 300:  # modified in last 5 min
            eid = f.stem
            events = [json.loads(l) for l in open(f)]
            connected = [e.get("agent") for e in events if e.get("type") == "connected"]
            tool_uses = sum(1 for e in events if e.get("type") == "tool_use")
            msgs = sum(1 for e in events if e.get("type") == "tool_use" and "send_message" in e.get("tool", ""))
            active.append({
                "id": eid,
                "events": len(events),
                "connected": connected,
                "tools": tool_uses,
                "messages": msgs,
            })
    return active


def summarize_results(agent_filter: str | None = None) -> str:
    results = get_results()
    if agent_filter:
        results = {k: v for k, v in results.items() if f"/{agent_filter}" in k}

    if not results:
        return "No results"

    passed = sum(1 for r in results.values() if r.get("passed"))
    failed = sum(1 for r in results.values() if r.get("status") == "failed")
    errors = sum(1 for r in results.values() if r.get("status") == "error")
    total = len(results)

    lines = [f"{passed}/{total} passed, {failed} failed, {errors} errors"]
    for key in sorted(results):
        r = results[key]
        icon = "PASS" if r.get("passed") else "FAIL"
        elapsed = r.get("elapsed", 0)
        lines.append(f"  {icon} {key:<55s} {elapsed:>6.0f}s")
    return "\n".join(lines)


def save_run_results(agent: str, label: str):
    """Save current results for a specific agent to the results dir."""
    results = get_results()
    agent_results = {k: v for k, v in results.items() if f"/{agent}" in k}
    if not agent_results:
        return

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = RESULTS_DIR / f"terminal-bench-v2-{label}-{date}.json"
    out.write_text(json.dumps(agent_results, indent=2))
    log(f"Saved {len(agent_results)} results to {out}")


def check_run_complete(agent: str, expected_tasks: int) -> bool:
    """Check if all tasks for an agent have finished."""
    results = get_results()
    agent_results = {k: v for k, v in results.items() if f"/{agent}" in k}
    return len(agent_results) >= expected_tasks


def write_status():
    """Write a comprehensive status snapshot."""
    log("--- Status Check ---")

    # Active traces
    active = get_active_traces()
    if active:
        log(f"Active executions: {len(active)}")
        for t in active:
            log(f"  {t['id']}: {t['events']} events, connected={t['connected']}, tools={t['tools']}, msgs={t['messages']}")
    else:
        log("No active executions")

    # Results by agent
    results = get_results()
    agents = set()
    for k in results:
        parts = k.split("/")
        if len(parts) == 2:
            agents.add(parts[1])

    for agent in sorted(agents):
        log(f"Results for {agent}:")
        log(summarize_results(agent))

    log("--- End Status ---")


if __name__ == "__main__":
    log("Monitor started")
    write_status()
