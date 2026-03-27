"""logstat — summarize structured log files.

Reads JSON-lines log files and produces a report: request counts by endpoint,
p50/p95/p99 latency percentiles, and error rates.

Usage:
    python logstat.py logs/
"""

from __future__ import annotations

import os
import sys
import time

from dedup import deduplicate
from parser import parse_line
from report import build_report


def parse_logs(directory: str) -> list[dict]:
    """Read all .jsonl files in a directory and parse each line."""
    entries = []
    for filename in sorted(os.listdir(directory)):
        if not filename.endswith(".jsonl"):
            continue
        path = os.path.join(directory, filename)
        with open(path) as f:
            for line in f:
                entry = parse_line(line)
                if entry is not None:
                    entries.append(entry)
    return entries


def main():
    if len(sys.argv) < 2:
        print("Usage: python logstat.py <log-directory>", file=sys.stderr)
        sys.exit(1)

    log_dir = sys.argv[1]
    if not os.path.isdir(log_dir):
        print(f"Error: {log_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    t0 = time.perf_counter()

    entries = parse_logs(log_dir)
    t_parse = time.perf_counter()

    entries = deduplicate(entries)
    t_dedup = time.perf_counter()

    report = build_report(entries)
    t_report = time.perf_counter()

    print(f"{'Endpoint':<30} {'Count':>7} {'Err%':>6} {'p50':>8} {'p95':>8} {'p99':>8}")
    print("-" * 75)
    for ep, stats in sorted(report.items()):
        print(
            f"{ep:<30} {stats['count']:>7} {stats['error_rate']:>5.1f}% "
            f"{stats['p50']:>7.1f}ms {stats['p95']:>7.1f}ms {stats['p99']:>7.1f}ms"
        )

    total = t_report - t0
    print(f"\n--- Timing ---")
    print(f"Parse:       {t_parse - t0:.3f}s")
    print(f"Deduplicate: {t_dedup - t_parse:.3f}s")
    print(f"Report:      {t_report - t_dedup:.3f}s")
    print(f"Total:       {total:.3f}s")


if __name__ == "__main__":
    main()
