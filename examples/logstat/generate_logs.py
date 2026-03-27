"""Generate sample log data for logstat benchmarks.

Creates .jsonl files with realistic HTTP request logs. Includes ~5% duplicate
request_ids to exercise the deduplication path.
"""

from __future__ import annotations

import json
import os
import random
import sys
import uuid


ENDPOINTS = [
    "/api/users",
    "/api/users/123",
    "/api/orders",
    "/api/orders/456",
    "/api/orders/456/items",
    "/api/products",
    "/api/products/789",
    "/api/search",
    "/api/health",
    "/api/auth/login",
]

STATUSES = [200] * 85 + [201] * 5 + [400] * 3 + [404] * 3 + [500] * 2 + [503] * 2


def generate_entry(request_id: str | None = None) -> dict:
    endpoint = random.choice(ENDPOINTS)
    # Latency varies by endpoint — search and order items are slower
    base = 150 if "search" in endpoint else 50 if "items" in endpoint else 20
    latency = max(1.0, random.gauss(base, base * 0.4))
    return {
        "timestamp": f"2025-01-15T{random.randint(0, 23):02d}:{random.randint(0, 59):02d}:{random.randint(0, 59):02d}Z",
        "request_id": request_id or uuid.uuid4().hex[:12],
        "endpoint": endpoint,
        "method": random.choice(["GET"] * 7 + ["POST"] * 2 + ["DELETE"]),
        "status": random.choice(STATUSES),
        "latency_ms": round(latency, 2),
        "user_id": f"user_{random.randint(1, 500)}",
    }


def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 10_000
    out_dir = sys.argv[2] if len(sys.argv) > 2 else "logs"
    files = int(sys.argv[3]) if len(sys.argv) > 3 else 4

    os.makedirs(out_dir, exist_ok=True)
    per_file = count // files
    all_ids: list[str] = []

    for i in range(files):
        path = os.path.join(out_dir, f"access-{i:03d}.jsonl")
        with open(path, "w") as f:
            for _ in range(per_file):
                # 5% chance of duplicating a previous request_id
                dup_id = None
                if all_ids and random.random() < 0.05:
                    dup_id = random.choice(all_ids)
                entry = generate_entry(dup_id)
                all_ids.append(entry["request_id"])
                f.write(json.dumps(entry) + "\n")

    print(f"Generated {count} entries across {files} files in {out_dir}/")


if __name__ == "__main__":
    main()
