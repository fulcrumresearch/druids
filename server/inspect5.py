import asyncio
from morphcloud.api import MorphCloudClient

client = MorphCloudClient()


async def main():
    # Test with feal instance which has the most files
    inst = await client.instances.aget("morphvm_15dtk931")

    # Run each command separately, not via bash -c
    print("--- Test 1: simple echo ---")
    r = await inst.aexec(["echo", "MARKER_STRING"], timeout=10)
    print(f"stdout: '{r.stdout}'")

    print("--- Test 2: ls -la /app ---")
    r = await inst.aexec(["ls", "-la", "/app/"], timeout=10)
    print(f"stdout: '{r.stdout}'")

    print("--- Test 3: ls -la / ---")
    r = await inst.aexec(["ls", "-la", "/"], timeout=10)
    print(f"stdout: '{r.stdout}'")

    print("--- Test 4: cat /etc/os-release ---")
    r = await inst.aexec(["cat", "/etc/os-release"], timeout=10)
    print(f"stdout: '{r.stdout[:500]}'")

    print("--- Test 5: which find ---")
    r = await inst.aexec(["which", "find"], timeout=10)
    print(f"stdout: '{r.stdout}'")
    print(f"stderr: '{r.stderr}'")
    print(f"exit: {r.exit_code}")

    print("--- Test 6: ls -la /root ---")
    r = await inst.aexec(["ls", "-la", "/root/"], timeout=10)
    print(f"stdout: '{r.stdout}'")

    print("--- Test 7: find /app -type f ---")
    r = await inst.aexec(["find", "/app", "-type", "f"], timeout=10)
    print(f"stdout: '{r.stdout}'")

    print("--- Test 8: stat /app/feal.c ---")
    r = await inst.aexec(["stat", "/app/feal.c"], timeout=10)
    print(f"stdout: '{r.stdout}'")
    print(f"stderr: '{r.stderr}'")


asyncio.run(main())
