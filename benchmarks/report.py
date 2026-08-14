"""Aggregate benchmark JSONL evidence into report.md + gates.json."""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, round(pct / 100.0 * (len(ordered) - 1))))
    return ordered[rank]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent / "results" / args.run_id
    hardware = json.loads((root / "hardware.json").read_text(encoding="utf-8")) if (root / "hardware.json").exists() else {}
    per_model = read_jsonl(root / "per-model.jsonl")
    determinism = read_jsonl(root / "determinism.jsonl")
    concurrency = read_jsonl(root / "concurrency.jsonl")
    cancellation = read_jsonl(root / "cancellation.jsonl")
    soak = read_jsonl(root / "soak.jsonl")

    lines: list[str] = []
    lines.append(f"# motor-ifc — Ola 4 benchmark results ({args.run_id})")
    lines.append("")
    lines.append(f"Hardware: {hardware.get('machine')} — {hardware.get('cpu')} — {hardware.get('logical_cores')} cores — {hardware.get('os')} — Python {hardware.get('python')} — IfcOpenShell {hardware.get('ifcopenshell')}.")
    lines.append("Transport: real `motor_ifc.supervisor` per request (worker elapsed/RSS from redacted stderr lifecycle events). Cold = first request of a fresh supervisor; warm = subsequent requests.")
    lines.append("")

    # Per-model table
    lines.append("## Per-model (1 cold + 10 warm sequential)")
    lines.append("")
    lines.append("| model | projection | publish | cold ms | warm p50 ms | warm p95 ms | rss peak p95 MB | entities | artifact KB |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    models = sorted({record["model_id"] for record in per_model})
    gates: dict[str, object] = {"per_model": {}}
    for model_id in models:
        records = [record for record in per_model if record["model_id"] == model_id and record["success"]]
        cold = [record["client_elapsed_ms"] for record in records if record["mode"] == "cold"]
        warm = [record["client_elapsed_ms"] for record in records if record["mode"] == "warm"]
        rss = [record["rss_peak"] / (1024 * 1024) for record in records if record.get("rss_peak")]
        artifacts = [record["artifact_bytes"] for record in records if record.get("artifact_bytes")]
        entity_counts = {record.get("entity_count") for record in records}
        projection = records[0]["projection"] if records else "-"
        publish = "yes" if records and records[0].get("publish") else "no"
        lines.append(
            f"| {model_id} | {projection} | {publish} | {cold[0] if cold else '-'} "
            f"| {int(statistics.median(warm)) if warm else '-'} | {int(percentile(warm, 95)) if warm else '-'} "
            f"| {round(percentile(rss, 95)) if rss else '-'} | {entity_counts.pop() if len(entity_counts) == 1 else entity_counts} "
            f"| {round(artifacts[-1] / 1024) if artifacts else '-'} |"
        )
        gates["per_model"][model_id] = {
            "runs": len(records),
            "cold_ms": cold[0] if cold else None,
            "warm_p50_ms": int(statistics.median(warm)) if warm else None,
            "warm_p95_ms": int(percentile(warm, 95)) if warm else None,
            "rss_peak_p95_mb": round(percentile(rss, 95)) if rss else None,
        }
    lines.append("")

    # Determinism
    det_summaries = [record for record in determinism if record.get("phase") == "determinism" and "distinct_hashes" in record]
    lines.append("## Determinism")
    lines.append("")
    for record in det_summaries:
        lines.append(f"- {record['model_id']}: {record['runs']} sequential + {record['runs']} concurrent runs → {record['distinct_hashes']} distinct hash(es) → {'PASS' if record['match'] else 'FAIL'}")
        gates["determinism"] = {"match": record["match"], "runs": record["runs"], "distinct": record["distinct_hashes"]}
    lines.append("")

    # Concurrency
    conc_records = [record for record in concurrency if record.get("workers") and record.get("success") is not None and record.get("case") != "overload"]
    overload = next((record for record in concurrency if record.get("case") == "overload"), None)
    lines.append("## Concurrency (dense model B05, workers=4 supervisor, global budget)")
    lines.append("")
    lines.append("| workers | batches | worker p50 ms | worker p95 ms | wall p50 ms |")
    lines.append("|---|---|---|---|---|")
    p95_by_workers: dict[int, float] = {}
    for workers in (1, 2, 4):
        subset = [record for record in conc_records if record["workers"] == workers]
        worker_times = [record["worker_elapsed_ms"] for record in subset if record.get("worker_elapsed_ms")]
        walls = sorted({record["wall_ms"] for record in subset})
        if worker_times:
            p95_by_workers[workers] = percentile(worker_times, 95)
        lines.append(
            f"| {workers} | {len(subset) // workers} "
            f"| {int(statistics.median(worker_times)) if worker_times else '-'} "
            f"| {int(percentile(worker_times, 95)) if worker_times else '-'} "
            f"| {int(statistics.median(walls)) if walls else '-'} |"
        )
    if 1 in p95_by_workers and 4 in p95_by_workers:
        ratio = p95_by_workers[4] / p95_by_workers[1]
        lines.append("")
        lines.append(f"p95(4)/p95(1) = {ratio:.2f} (gate ≤ 2.50 → {'PASS' if ratio <= 2.5 else 'FAIL'})")
        gates["concurrency"] = {"p95_by_workers_ms": p95_by_workers, "ratio": round(ratio, 3), "gate": ratio <= 2.5}
    if overload:
        lines.append("")
        lines.append(f"Overload: 5 concurrent on 4 workers → rejected with -32011: {'PASS' if overload.get('rejected_with_overload') else 'FAIL'} (codes: {overload.get('codes')})")
        gates["overload"] = overload.get("rejected_with_overload")
    lines.append("")

    # Cancellation
    lines.append("## Cancellation under load")
    lines.append("")
    for record in cancellation:
        response_latency = record["cancel_observed_ms"] - 2000 if record.get("cancel_observed_ms") else None
        lines.append(
            f"- {record['model_id']}: -32800 observed at {record['cancel_observed_ms']} ms after submit (notification at ~2000 ms → response latency ~{response_latency} ms), "
            f"forced={record.get('forced')}, follow-up success={record.get('follow_up_success')}"
        )
        gates["cancellation"] = {"observed_ms": record["cancel_observed_ms"], "response_latency_ms": response_latency, "gate_le_2000ms": (response_latency or 0) <= 2000}
    lines.append("")

    # Soak
    soak_end = next((record for record in soak if record.get("event") == "soak_end"), None)
    heartbeats = [record for record in soak if record.get("event") == "heartbeat"]
    restarts = [record for record in soak if record.get("event") == "soak_restart"]
    lines.append("## Soak")
    lines.append("")
    if soak_end:
        growth_values = [record["supervisor_rss_growth_pct"] for record in heartbeats if record.get("supervisor_rss_growth_pct") is not None]
        max_growth = max(growth_values) if growth_values else None
        lines.append(
            f"- rounds={soak_end['rounds']}, requests={soak_end['requests']}, errors={soak_end['errors']}, cancellations={soak_end['cancellations']}, "
            f"restarts={soak_end['restarts']}, duration={soak_end['duration_s']} s, stages remaining={soak_end['stages_remaining']}, "
            f"max supervisor RSS growth={max_growth}% (gate < 10%)"
        )
        gates["soak"] = {
            "rounds": soak_end["rounds"],
            "requests": soak_end["requests"],
            "errors": soak_end["errors"],
            "restarts": soak_end["restarts"],
            "stages_remaining": soak_end["stages_remaining"],
            "max_rss_growth_pct": max_growth,
            "gate_growth_lt_10pct": (max_growth is not None and max_growth < 10),
            "gate_zero_errors": soak_end["errors"] == 0,
            "gate_zero_stages": soak_end["stages_remaining"] == 0,
        }
    else:
        lines.append("- soak not yet executed")
    lines.append("")

    (root / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (root / "gates.json").write_text(json.dumps(gates, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"report written to {root / 'report.md'}")


if __name__ == "__main__":
    main()
