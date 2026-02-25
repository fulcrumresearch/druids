import asyncio
from morphcloud.api import MorphCloudClient

client = MorphCloudClient()


async def run(inst, args):
    r = await inst.aexec(args, timeout=15)
    return r.stdout


async def inspect_instance(inst_id, task_name):
    inst = await client.instances.aget(inst_id)
    print("=" * 60)
    print(f"{task_name} ({inst_id})")
    print("=" * 60)

    # Check /tests directory (seen on feal instance earlier)
    out = await run(inst, ["ls", "-laR", "/tests/"])
    print(f"/tests:\n{out[:2000]}")

    # Check if agent created any files under /app (recursively with sizes)
    out = await run(inst, ["find", "/app", "-type", "f"])
    print(f"\nAll files under /app:\n{out[:2000]}")

    # Check /root for any hidden dirs with content
    out = await run(inst, ["find", "/root", "-type", "f"])
    print(f"\nAll files under /root:\n{out[:2000]}")

    # Look for any file created/modified on Feb 6 after 08:05 (agent would have run then)
    # Use stat on a few files to check modification times
    out = await run(inst, ["find", "/", "-maxdepth", "1", "-type", "d"])
    print(f"\nTop-level directories:\n{out[:500]}")

    # Check the feal instance specifically for /tests
    if "feal" in task_name:
        out = await run(inst, ["ls", "-la", "/tests/"])
        print(f"\n/tests listing:\n{out[:1000]}")
        out = await run(inst, ["cat", "/tests/test.sh"])
        print(f"\n/tests/test.sh:\n{out[:2000]}")

    # Check for any recently modified files in /app using stat
    out = await run(inst, ["stat", "/app/"])
    print(f"\n/app stat:\n{out[:500]}")
    
    # Check the root directory (/) modified time
    out = await run(inst, ["stat", "/"])
    print(f"\n/ stat:\n{out[:500]}")

    print("\n")


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
