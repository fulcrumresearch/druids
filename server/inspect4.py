import asyncio
from morphcloud.api import MorphCloudClient

client = MorphCloudClient()


async def run(inst, cmd_str):
    """Run a bash -c command and return stdout."""
    r = await inst.aexec(["bash", "-c", cmd_str], timeout=15)
    return r.stdout, r.stderr


async def inspect_instance(inst_id, task_name):
    inst = await client.instances.aget(inst_id)
    print("=" * 60)
    print(f"{task_name} ({inst_id}) status={inst.status}")
    print("=" * 60)

    # 1. What is the working directory?
    out, _ = await run(inst, "pwd")
    print(f"CWD: {out.strip()}")

    # 2. List /app recursively with full details
    out, err = await run(inst, "ls -laR /app/ 2>&1")
    print(f"\n/app contents:\n{out[:3000]}")

    # 3. List /root recursively  
    out, err = await run(inst, "ls -laR /root/ 2>&1")
    print(f"\n/root contents:\n{out[:3000]}")

    # 4. Find recently modified files
    out, err = await run(inst, "find / -maxdepth 5 -type f -newer /etc/hostname 2>/dev/null")
    print(f"\nFiles newer than /etc/hostname:\n{out[:3000]}")

    # 5. Check /tmp
    out, err = await run(inst, "ls -laR /tmp/ 2>&1")
    print(f"\n/tmp contents:\n{out[:2000]}")

    # 6. Check if any Python files exist anywhere relevant
    out, err = await run(inst, "find /app /root /tmp /opt -name '*.py' 2>/dev/null")
    print(f"\nPython files:\n{out[:1000]}")

    # 7. Check bash history for what agent did
    out, err = await run(inst, "cat /root/.bash_history 2>/dev/null || echo 'no history'")
    print(f"\nBash history:\n{out[:3000]}")

    # 8. Check for any solution/output files
    out, err = await run(inst, "find /app /root /tmp -name 'solution*' -o -name 'output*' -o -name 'result*' -o -name 'answer*' 2>/dev/null")
    print(f"\nSolution/output files:\n{out[:1000]}")

    print("\n\n")


async def main():
    tasks = [
        ("morphvm_wy5i7mf3", "financial-document-processor"),
        ("morphvm_9m9dco48", "circuit-fibsqrt"),
        ("morphvm_15dtk931", "feal-linear-cryptanalysis"),
    ]
    for iid, name in tasks:
        try:
            await inspect_instance(iid, name)
        except Exception as e:
            print(f"{iid}: ERROR {e}")


asyncio.run(main())
