"""Report generation. Bottleneck: sorts the full list per percentile call."""

from __future__ import annotations


def compute_percentiles(latencies: list[float], percentiles: list[int]) -> dict[str, float]:
    """Compute latency percentiles for a list of values."""
    result = {}
    for p in percentiles:
        sorted_vals = sorted(latencies)
        idx = int(len(sorted_vals) * p / 100)
        idx = min(idx, len(sorted_vals) - 1)
        result[f"p{p}"] = sorted_vals[idx]
    return result


def build_report(entries: list[dict]) -> dict:
    """Aggregate entries into a per-endpoint report."""
    endpoints: dict[str, list[float]] = {}
    errors: dict[str, int] = {}
    counts: dict[str, int] = {}

    for entry in entries:
        ep = entry.get("endpoint", "unknown")
        latency = entry.get("latency_ms")
        status = entry.get("status", 200)

        counts[ep] = counts.get(ep, 0) + 1
        if status >= 400:
            errors[ep] = errors.get(ep, 0) + 1
        if latency is not None:
            endpoints.setdefault(ep, []).append(latency)

    report = {}
    for ep, latencies in endpoints.items():
        report[ep] = {
            "count": counts.get(ep, 0),
            "errors": errors.get(ep, 0),
            "error_rate": round(errors.get(ep, 0) / max(counts.get(ep, 0), 1) * 100, 1),
            **compute_percentiles(latencies, [50, 95, 99]),
        }
    return report
