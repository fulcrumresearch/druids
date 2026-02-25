import asyncio
from morphcloud.api import MorphCloudClient

client = MorphCloudClient()

CMD = (
    "echo '=== pwd ==='; pwd; "
    "echo '=== /home/user ==='; ls -la /home/user/; "
    "echo '=== find all files ==='; find /home/user -type f 2>/dev/null; "
    "echo '=== /app listing ==='; ls -la /app/ 2>/dev/null || echo 'no /app'; "
    "echo '=== find /root files ==='; find /root -type f -not -path '*/.*' 2>/dev/null | head -20; "
    "echo '=== DONE ==='"
)


async def inspect_instance(inst_id, task_name):
    inst = await client.instances.aget(inst_id)
    print(f"========================================")
    print(f"{task_name} ({inst_id}) status={inst.status}")
    print(f"========================================")

    r = await inst.aexec(["bash", "-c", CMD], timeout=15)
    print(r.stdout[:4000])
    if r.stderr:
        print(f"STDERR: {r.stderr[:500]}")
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
