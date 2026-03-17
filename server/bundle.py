#!/usr/bin/env python3
"""Bundle assets into druids_server/_bundled/ for wheel builds.

Run this before `uv build` to include frontend, bridge, and client wheel
in the server package. Safe to run repeatedly -- it cleans _bundled/ first.
"""

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent
SERVER = ROOT / "server"
BUNDLED = SERVER / "druids_server" / "_bundled"


def main():
    # Clean previous bundle
    if BUNDLED.exists():
        shutil.rmtree(BUNDLED)
    BUNDLED.mkdir()

    # Frontend
    frontend_dist = ROOT / "frontend" / "dist"
    if not frontend_dist.is_dir():
        print("Building frontend...")
        subprocess.run(["npm", "run", "build"], cwd=ROOT / "frontend", check=True)
    print(f"Bundling frontend from {frontend_dist}")
    shutil.copytree(frontend_dist, BUNDLED / "frontend")

    # Bridge
    bridge_src = ROOT / "bridge" / "bridge.py"
    bridge_dest = BUNDLED / "bridge"
    bridge_dest.mkdir()
    print(f"Bundling bridge from {bridge_src}")
    shutil.copy2(bridge_src, bridge_dest / "bridge.py")

    # Client wheel
    client_dist = ROOT / "client" / "dist"
    if not list(client_dist.glob("druids-*.whl")):
        print("Building client wheel...")
        subprocess.run(["uv", "build"], cwd=ROOT / "client", check=True)
    wheel_dest = BUNDLED / "client_wheel"
    wheel_dest.mkdir()
    for whl in client_dist.glob("druids-*.whl"):
        print(f"Bundling {whl.name}")
        shutil.copy2(whl, wheel_dest / whl.name)

    print(f"Bundle complete: {BUNDLED}")


if __name__ == "__main__":
    main()
