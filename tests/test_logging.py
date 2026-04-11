from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from druids import Context, LocalImage
from tests.helpers import FakeAgentClient, wait_for_server


def test_orchestrator_log_is_written(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    ctx = Context(image=LocalImage(tmp_path / "builder"), launch_mode="manual")
    builder = ctx.agent("builder", prompt="hello")

    @builder.on("finish")
    def finish() -> str:
        ctx.done("done")
        return "done"

    outcome: dict[str, object] = {}

    def runner() -> None:
        try:
            outcome["result"] = ctx.run(timeout=10)
        except Exception as exc:  # pragma: no cover
            outcome["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()

    deadline = time.time() + 5
    while ctx.server_url is None and time.time() < deadline:
        time.sleep(0.01)
    assert ctx.server_url is not None
    wait_for_server(ctx.server_url)

    client = FakeAgentClient(ctx.server_url, ctx.execution_id, "builder")
    client.start_events()
    client.register()
    client.next_event("message")
    client.tool_call("finish")

    thread.join(timeout=5)
    assert outcome["result"] == "done"

    assert ctx.log_path is not None
    records = [json.loads(line) for line in ctx.log_path.read_text().splitlines()]
    events = {record["event"] for record in records}
    assert "agent_created" in events
    assert "tool_call_dispatched" in events
    assert "done" in events
    assert "shutdown_complete" in events
