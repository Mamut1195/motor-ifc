"""Ola 4 benchmark phases executed through the real supervisor transport.

Subcommands (each writes JSONL evidence under ``benchmarks/results/<run-id>/``):
  hardware                        hardware + runtime fingerprint
  per-model --model-id ID         1 cold + `--warm` warm sequential runs
  determinism --runs N            N sequential + N concurrent extractions, hash equality
  concurrency                     1/2/4 concurrent batches on the dense model + overload
  cancellation                    cancel mid-extraction under load
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmarks.common import (
    CORPUS,
    FIXTURES,
    SupervisorClient,
    hardware_fingerprint,
    jsonl_append,
    request_token,
    sha256,
    stage_input,
)

MODELS: dict[str, dict] = {
    "b01-pcert-ifc4": {"source": CORPUS / "models/b01-pcert-ifc4.ifc", "projection": "quantities", "publish": False},
    "b02-community-ifc2x3": {"source": CORPUS / "models/b02-community-ifc2x3.ifc", "projection": "quantities", "publish": False},
    "b03-community-ifc4": {"source": CORPUS / "models/b03-community-ifc4.ifc", "projection": "quantities", "publish": True},
    "b04-oracle-ifc4": {"source": CORPUS / "models/b04-oracle-ifc4.ifc", "projection": "rich", "publish": False},
    "b04-oracle-ifc2x3": {"source": CORPUS / "models/b04-oracle-ifc2x3.ifc", "projection": "rich", "publish": False},
    "b04-oracle-ifc4x3": {"source": CORPUS / "models/b04-oracle-ifc4x3.ifc", "projection": "rich", "publish": False},
    "b05-semantic-dense": {"source": CORPUS / "models/b05-semantic-dense.ifc", "projection": "quantities", "publish": True},
    "b06-relation-dense": {"source": CORPUS / "models/b06-relation-dense.ifc", "projection": "metadata", "publish": False},
    "b07-geometry-dense": {"source": CORPUS / "models/b07-geometry-dense.ifc", "projection": "metadata", "publish": False},
    "real-cand-11m": {"source": FIXTURES / "CAND_aleman_11M.ifc", "projection": "rich", "publish": True, "repair": True},
    "real-schependomlaan-49m": {"source": FIXTURES / "IFC_Schependomlaan.ifc", "projection": "rich", "publish": True, "repair": True},
}


def results_root(run_id: str) -> Path:
    root = Path(__file__).resolve().parent / "results" / run_id
    root.mkdir(parents=True, exist_ok=True)
    return root


def evidence_path(run_id: str, phase: str) -> Path:
    return results_root(run_id) / f"{phase}.jsonl"


def repaired_source(cache: Path, model_id: str) -> Path:
    """Repair a dirty real fixture once per benchmark run; returns the repaired artifact."""
    entry = MODELS[model_id]
    cached = cache / f"{model_id}-repaired.ifc"
    if cached.exists():
        return cached
    job_root = cache / f"repair-{model_id}"
    job_root.mkdir(parents=True, exist_ok=True)
    relative = stage_input(job_root, entry["source"])
    client = SupervisorClient(job_root)
    try:
        response = client.request("repair", "model.repair.v1", {"ifc_path": relative, "output_dir": "repaired"})
        assert response["result"]["success"], response
    finally:
        client.close()
    shutil.copy2(job_root / "repaired" / "repaired.ifc", cached)
    return cached


def extraction_params(model_id: str, relative: str, output_dir: str | None) -> dict:
    entry = MODELS[model_id]
    params = {"ifc_path": relative, "projection": entry["projection"]}
    if output_dir is not None:
        params["output_dir"] = output_dir
    return params


def run_extraction(client: SupervisorClient, job_root: Path, model_id: str, source: Path, index: int, mode: str) -> dict:
    entry = MODELS[model_id]
    relative = stage_input(job_root, source)
    publish = entry["publish"]
    output_dir = f"out-{index}" if publish else None
    response = client.request(f"{model_id}-{index}", "reader.extract.v2", extraction_params(model_id, relative, output_dir))
    result = response["result"]
    completed = client.events_for("worker_completed", f"{model_id}-{index}")
    record = {
        "phase": "per-model",
        "model_id": model_id,
        "projection": entry["projection"],
        "mode": mode,
        "index": index,
        "success": result["success"],
        "entity_count": result.get("entity_count"),
        "client_elapsed_ms": response["_client_elapsed_ms"],
        "worker_elapsed_ms": completed[0].get("elapsed_ms") if completed else None,
        "rss_peak": completed[0].get("rss_peak") if completed else None,
        "publish": publish,
    }
    if publish and result["success"]:
        artifact = job_root / output_dir / "extraction.json"
        record["artifact_bytes"] = artifact.stat().st_size
        record["extraction_sha256"] = result.get("extraction_sha256")
    if not result["success"]:
        record["diagnostics"] = [diagnostic["code"] for diagnostic in result.get("diagnostics", [])]
    return record


def phase_per_model(args) -> None:
    entry = MODELS[args.model_id]
    cache = results_root(args.run_id) / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    source = repaired_source(cache, args.model_id) if entry.get("repair") else entry["source"]
    source_hash = sha256(source)
    for index in range(args.start, args.start + args.count):
        mode = "cold" if index == 0 else "warm"
        job_root = Path(tempfile.mkdtemp(prefix=f"bench-{args.model_id}-{index}-"))
        client = SupervisorClient(job_root)
        try:
            record = run_extraction(client, job_root, args.model_id, source, index, mode)
            client.close()
        finally:
            shutil.rmtree(job_root, ignore_errors=True)
        record["source_sha256"] = source_hash
        jsonl_append(evidence_path(args.run_id, "per-model"), record)
        print(json.dumps({"done": args.model_id, "index": index, "mode": mode, "elapsed_ms": record["client_elapsed_ms"]}), flush=True)


def phase_determinism(args) -> None:
    model_id = args.model_id
    entry = MODELS[model_id]
    cache = results_root(args.run_id) / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    source = repaired_source(cache, model_id) if entry.get("repair") else entry["source"]
    hashes: list[str] = []
    job_root = Path(tempfile.mkdtemp(prefix="bench-det-"))
    client = SupervisorClient(job_root, workers=4)
    try:
        for index in range(args.runs):
            record = run_extraction(client, job_root, model_id, source, index, "sequential")
            assert record["success"], record
            hashes.append(record["extraction_sha256"] if entry["publish"] else json.dumps(record))
            jsonl_append(evidence_path(args.run_id, "determinism"), record | {"scheme": "sequential"})
        # Concurrent: batches of 4 in one supervisor with workers=4.
        batch = 0
        produced = 0
        while produced < args.runs:
            count = min(4, args.runs - produced)
            ids = []
            for slot in range(count):
                index = produced + slot
                relative = stage_input(job_root, source)
                output_dir = f"conc-{batch}-{slot}" if entry["publish"] else None
                client.submit(f"conc-{batch}-{slot}", "reader.extract.v2", extraction_params(model_id, relative, output_dir))
                ids.append(f"conc-{batch}-{slot}")
            responses = client.read_responses(count)
            for response in responses:
                result = response["result"]
                assert result["success"], response
                hashes.append(result.get("extraction_sha256") if entry["publish"] else json.dumps(result.get("entity_count")))
                jsonl_append(evidence_path(args.run_id, "determinism"), {
                    "phase": "determinism",
                    "model_id": model_id,
                    "scheme": "concurrent",
                    "id": response["id"],
                    "success": True,
                    "extraction_sha256": result.get("extraction_sha256"),
                })
            produced += count
            batch += 1
    finally:
        client.close()
        shutil.rmtree(job_root, ignore_errors=True)
    distinct = set(hashes)
    summary = {"phase": "determinism", "model_id": model_id, "runs": args.runs, "distinct_hashes": len(distinct), "match": len(distinct) == 1}
    jsonl_append(evidence_path(args.run_id, "determinism"), summary)
    print(json.dumps(summary), flush=True)


def phase_concurrency(args) -> None:
    model_id = args.model_id
    entry = MODELS[model_id]
    cache = results_root(args.run_id) / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    source = repaired_source(cache, model_id) if entry.get("repair") else entry["source"]
    for workers in (1, 2, 4):
        for batch in range(args.batches):
            job_root = Path(tempfile.mkdtemp(prefix=f"bench-conc-{workers}-{batch}-"))
            client = SupervisorClient(job_root, workers=4)
            try:
                relative = stage_input(job_root, source)
                started = time.monotonic()
                ids = []
                for slot in range(workers):
                    output_dir = f"w{workers}-b{batch}-s{slot}" if entry["publish"] else None
                    request_id = f"w{workers}-b{batch}-s{slot}"
                    client.submit(request_id, "reader.extract.v2", extraction_params(model_id, relative, output_dir))
                    ids.append(request_id)
                responses = client.read_responses(workers)
                wall_ms = int((time.monotonic() - started) * 1000)
                for response in responses:
                    completed = client.events_for("worker_completed", response["id"])
                    jsonl_append(evidence_path(args.run_id, "concurrency"), {
                        "phase": "concurrency",
                        "model_id": model_id,
                        "workers": workers,
                        "batch": batch,
                        "id": response["id"],
                        "success": response["result"]["success"],
                        "wall_ms": wall_ms,
                        "worker_elapsed_ms": completed[0].get("elapsed_ms") if completed else None,
                        "rss_peak": completed[0].get("rss_peak") if completed else None,
                    })
                client.close()
            finally:
                shutil.rmtree(job_root, ignore_errors=True)
            print(json.dumps({"done": "concurrency", "workers": workers, "batch": batch, "wall_ms": wall_ms}), flush=True)
    # Overload: 5 concurrent on workers=4 -> fifth rejected with -32011 before overassignment.
    job_root = Path(tempfile.mkdtemp(prefix="bench-overload-"))
    client = SupervisorClient(job_root, workers=4)
    try:
        relative = stage_input(job_root, source)
        for slot in range(5):
            client.submit(f"overload-{slot}", "reader.extract.v2", extraction_params(model_id, relative, f"over-{slot}" if entry["publish"] else None))
        responses = client.read_responses(5)
        codes = {response["id"]: response.get("error", {}).get("code") if "error" in response else "ok" for response in responses}
        summary = {"phase": "concurrency", "case": "overload", "workers": 4, "submitted": 5, "codes": codes, "rejected_with_overload": -32011 in codes.values()}
        jsonl_append(evidence_path(args.run_id, "concurrency"), summary)
        client.close()
    finally:
        shutil.rmtree(job_root, ignore_errors=True)
    print(json.dumps(summary), flush=True)


def phase_cancellation(args) -> None:
    model_id = args.model_id
    entry = MODELS[model_id]
    cache = results_root(args.run_id) / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    source = repaired_source(cache, model_id) if entry.get("repair") else entry["source"]
    job_root = Path(tempfile.mkdtemp(prefix="bench-cancel-"))
    client = SupervisorClient(job_root, workers=2)
    try:
        relative = stage_input(job_root, source)
        output_dir = "cancel-out" if entry["publish"] else None
        started = time.monotonic()
        client.submit("victim", "reader.extract.v2", extraction_params(model_id, relative, output_dir))
        time.sleep(args.delay_seconds)
        client.notify_cancel("victim")
        responses = client.read_responses(1)
        cancel_ms = int((time.monotonic() - started) * 1000)
        assert responses[0]["error"]["code"] == -32800, responses[0]
        cancelled_events = client.events_for("worker_cancelled", "victim")
        # The supervisor stays usable after cancellation.
        follow_up = client.request("after", "reader.extract.v2", extraction_params(model_id, relative, "after-out" if entry["publish"] else None))
        record = {
            "phase": "cancellation",
            "model_id": model_id,
            "cancel_observed_ms": cancel_ms,
            "cancelled_code": responses[0]["error"]["code"],
            "forced": cancelled_events[0].get("forced") if cancelled_events else None,
            "follow_up_success": follow_up["result"]["success"],
            "residue_entries": sorted(item.name for item in job_root.iterdir()),
        }
        jsonl_append(evidence_path(args.run_id, "cancellation"), record)
        client.close()
    finally:
        shutil.rmtree(job_root, ignore_errors=True)
    print(json.dumps(record), flush=True)


def phase_hardware(args) -> None:
    fingerprint = hardware_fingerprint()
    path = results_root(args.run_id) / "hardware.json"
    path.write_text(json.dumps(fingerprint, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(fingerprint), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["hardware", "per-model", "determinism", "concurrency", "cancellation"])
    parser.add_argument("--run-id", default=time.strftime("%Y-%m-%d"))
    parser.add_argument("--model-id", default="b05-semantic-dense")
    parser.add_argument("--count", type=int, default=11)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--batches", type=int, default=5)
    parser.add_argument("--delay-seconds", type=float, default=2.0)
    args = parser.parse_args()
    {
        "hardware": phase_hardware,
        "per-model": phase_per_model,
        "determinism": phase_determinism,
        "concurrency": phase_concurrency,
        "cancellation": phase_cancellation,
    }[args.phase](args)


if __name__ == "__main__":
    main()
