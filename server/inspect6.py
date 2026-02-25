import asyncio
from morphcloud.api import MorphCloudClient

client = MorphCloudClient()


async def run(inst, args):
    r = await inst.aexec(args, timeout=15)
    return r.stdout.strip()


async def inspect_instance(inst_id, task_name):
    inst = await client.instances.aget(inst_id)
    print("=" * 60)
    print(f"{task_name} ({inst_id}) status={inst.status}")
    print("=" * 60)

    # Root filesystem listing
    out = await run(inst, ["ls", "-la", "/"])
    print(f"\n/ listing:\n{out}")

    # /app contents with details
    out = await run(inst, ["ls", "-la", "/app/"])
    print(f"\n/app:\n{out}")

    # /root contents
    out = await run(inst, ["ls", "-la", "/root/"])
    print(f"\n/root:\n{out}")

    # /tests directory (if it exists based on feal instance)
    out = await run(inst, ["ls", "-laR", "/tests/"])
    if out:
        print(f"\n/tests:\n{out}")

    # /tmp contents
    out = await run(inst, ["ls", "-la", "/tmp/"])
    print(f"\n/tmp:\n{out}")

    # /opt contents
    out = await run(inst, ["ls", "-laR", "/opt/"])
    print(f"\n/opt:\n{out}")

    # Check for any node/npm installed agent (the ACP bridge)
    out = await run(inst, ["ls", "-la", "/opt/claude-code-acp/"])
    if out:
        print(f"\n/opt/claude-code-acp:\n{out[:500]}")

    # Check .cache for any Claude/agent artifacts
    out = await run(inst, ["ls", "-laR", "/root/.cache/"])
    if out:
        print(f"\n/root/.cache:\n{out[:1000]}")

    # Any files modified after the container started (Feb 6 08:04)
    out = await run(inst, ["find", "/app", "/root", "/tmp", "/tests", "-newer", "/app", "-type", "f"])
    if out:
        print(f"\nFiles newer than /app:\n{out[:2000]}")

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
