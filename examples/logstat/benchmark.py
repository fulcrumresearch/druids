"""Benchmark runner for logstat.

Runs logstat 5 times and reports median timing for each phase.
Outputs JSON so agents can parse it programmatically.
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time


# Add parent to path so we can import logstat
sys.path.insert(0, os.path.dirname(__file__))

from dedup import deduplicate
from logstat import parse_logs
from report import build_report


def run_benchmark(log_dir: str, runs: int = 5) -> dict:
    """Run the full pipeline multiple times and return median timings."""
    timings = {"parse": [], "deduplicate": [], "report": [], "total": []}

    for _ in range(runs):
        t0 = time.perf_counter()
        entries = parse_logs(log_dir)
        t1 = time.perf_counter()

        deduped = deduplicate(entries)
        t2 = time.perf_counter()

        build_report(deduped)
        t3 = time.perf_counter()

        timings["parse"].append(t1 - t0)
        timings["deduplicate"].append(t2 - t1)
        timings["report"].append(t3 - t2)
        timings["total"].append(t3 - t0)

    result = {}
    for phase, values in timings.items():
        result[phase] = {
            "median_s": round(statistics.median(values), 4),
            "runs": [round(v, 4) for v in values],
        }
    return result


def main():
    log_dir = sys.argv[1] if len(sys.argv) > 1 else "logs"
    runs = int(sys.argv[2]) if len(sys.argv) > 2 else 5

    if not os.path.isdir(log_dir):
        print(f"Error: {log_dir} not found. Run generate_logs.py first.", file=sys.stderr)
        sys.exit(1)

    print(f"Benchmarking logstat on {log_dir}/ ({runs} runs)...", file=sys.stderr)
    result = run_benchmark(log_dir, runs)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
