"""Two agents collaborating on a code change.

The author writes code to a file in a shared working dir. The reviewer
reads it from the same dir, and either ``approve``s or sends feedback.
On feedback, the author edits and pings again.

Code travels via the shared filesystem, not through chat — the
``message`` tool is only used to signal "ready" / "please change X".

Watch the conversation while it runs with ``ramure connect author`` or
``ramure connect reviewer`` in another terminal.
"""

import asyncio
import tempfile
from pathlib import Path

from ramure import LocalImage, agent, agent_process, connect, done, fail, wait


AUTHOR_PROMPT = """\
You are a careful Python author. The user gives you a task.

You and the reviewer share a working directory (your cwd). Workflow:
  1. Read existing files with bash / read.
  2. Write the code with edit / write.
  3. Quickly sanity-check it (e.g. `python -c "..."`).
  4. Notify the reviewer: call ``message`` with receiver='reviewer' and
     text='ready: <path>' plus a one-line summary.
  5. If the reviewer asks for changes, edit and ping again.
"""

REVIEWER_PROMPT = """\
You are a strict reviewer. You share a working directory with the author.

When the author pings you with a path:
  * Read the file. Sanity-check it (e.g. `python -m doctest <path>`).
  * If it's correct and clean: call ``approve`` with the path.
  * If it needs work: call ``message`` with receiver='author' and
    text=<short, specific feedback>. Reference file:line where useful.
  * If the spec is unworkable or the author isn't converging: call
    ``give_up`` with a short reason.
"""


@agent_process(timeout=300)
async def code_review(task: str) -> str:
    # throwaway dir so the demo never touches your cwd; both agents
    # share it, so the author's writes are immediately readable by the
    # reviewer
    workdir = Path(tempfile.mkdtemp(prefix="ramure-review-"))
    image = LocalImage(workdir=workdir)

    author = await agent("author", system_prompt=AUTHOR_PROMPT, image=image)
    reviewer = await agent("reviewer", system_prompt=REVIEWER_PROMPT, image=image)

    # installs `message` / `send_file` / `download_file` on both sides;
    # we only use `message` here — the code itself moves via the fs
    connect(author, reviewer)

    @reviewer.on("approve")
    async def on_approve(path: str) -> str:
        """Approve the file at ``path``."""
        # done() resolves wait() below with the approved file's contents
        done((workdir / path).read_text())
        return "Approved."

    @reviewer.on("give_up")
    async def on_give_up(reason: str) -> str:
        """Abandon the review. Use only if the spec is unworkable."""
        # `failed` event with a `gave_up:` prefix lets a parent supervisor
        # distinguish semantic failure from timeout / exception
        fail(f"gave_up: {reason}")
        return "Recorded."

    # kick things off; from here it's agent-to-agent until approve / give_up
    await author.send(
        f"Task:\n\n{task}\n\nWrite the code, then notify the reviewer."
    )
    return await wait()


if __name__ == "__main__":
    task = (
        "Write `parse_duration(s: str) -> int` in `util.py`. It accepts "
        "strings like '1h30m', '45s', '2h', '90m' and returns the total "
        "number of seconds. Reject malformed input with a ValueError. "
        "Include a short docstring and basic doctest."
    )
    print(asyncio.run(code_review(task)))
