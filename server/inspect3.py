import asyncio
from morphcloud.api import MorphCloudClient

client = MorphCloudClient()


async def main():
    inst = await client.instances.aget("morphvm_15dtk931")
    print(f"status={inst.status}")

    # Try a very simple command first
    r = await inst.aexec(["echo", "hello world"], timeout=15)
    print(f"echo test: stdout='{r.stdout}' stderr='{r.stderr}' exit={r.exit_code}")

    # Try uname
    r = await inst.aexec(["uname", "-a"], timeout=15)
    print(f"uname: stdout='{r.stdout}' stderr='{r.stderr}' exit={r.exit_code}")

    # Try cat on a known file
    r = await inst.aexec(["cat", "/app/ciphertexts.txt"], timeout=15)
    print(f"cat ciphertexts.txt: stdout='{r.stdout[:200]}' exit={r.exit_code}")

    # Check the ExecResult type
    print(f"Result type: {type(r)}")
    print(f"Result dir: {[a for a in dir(r) if not a.startswith('_')]}")


asyncio.run(main())
