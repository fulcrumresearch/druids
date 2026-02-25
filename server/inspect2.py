import asyncio
from morphcloud.api import MorphCloudClient

client = MorphCloudClient()


async def inspect_instance(inst_id, task_name):
    inst = await client.instances.aget(inst_id)
    print(f"========================================")
    print(f"{task_name} ({inst_id}) status={inst.status}")
    print(f"========================================")

    # Check what the agent created - look at recently modified files across the whole FS
    cmds = [
        ("Recent files (last 24h)", "find / -type f -mtime -1 -not -path '/proc/*' -not -path '/sys/*' -not -path '/dev/*' -not -path '/run/*' 2>/dev/null | head -50"),
        ("/root contents", "ls -laR /root/ 2>/dev/null | head -60"),
        ("/app contents (recursive)", "ls -laR /app/ 2>/dev/null | head -80"),
        ("Any solution/output files", "find / -maxdepth 4 -type f \\( -name 'solution*' -o -name 'output*' -o -name 'result*' -o -name 'answer*' -o -name '*.py' -o -name 'Makefile' \\) -not -path '/proc/*' -not -path '/sys/*' -not -path '/usr/*' 2>/dev/null | head -30"),
        ("/tmp contents", "ls -la /tmp/ 2>/dev/null"),
        ("Check agent workdir", "ls -laR /opt/ 2>/dev/null | head -40"),
    ]

    for label, cmd in cmds:
        r = await inst.aexec(["bash", "-c", cmd], timeout=15)
        out = r.stdout.strip()
        if out:
            print(f"\n--- {label} ---")
            print(out[:2000])

    print()
    print()


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
