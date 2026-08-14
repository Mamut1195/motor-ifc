"""Ola 4 soak test: 8 h mixed workload through one supervised transport.

Mix per round: sequential inline, 2-concurrent publication, 4-concurrent dense
publication, mid-flight cancellation, and an overload rejection check. The
supervisor restarts every ``--restart-every`` rounds to exercise startup
recovery; residue, error counts, per-step timings, and supervisor RSS growth
are tracked.

Hardened (2026-08-06): an ``error`` response never crashes the harness — it is
recorded (round/case/code/message/step_ms/raw) in ``state["errors"]`` and in
``soak-errors.jsonl``. Contained temp residue (``.tmp.motor-ifc``) is swept at
the end of every round, and the heartbeat reports its residual size plus the
mean pair/quad step time of the rounds since the previous heartbeat so that a
throughput degradation (e.g. the rounds-46-52 slowdown) is visible in evidence.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import stat
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmarks.common import CORPUS, SupervisorClient, jsonl_append, stage_input
from benchmarks.run_benchmarks import MODELS, repaired_source

SOAK_MODELS = ("b02-community-ifc2x3", "b03-community-ifc4", "b04-oracle-ifc4", "b05-semantic-dense")
TMP_DIR_NAME = ".tmp.motor-ifc"


def _process_rss(pid: int) -> int | None:
    try:
        if os.name != "nt":
            statm = Path(f"/proc/{pid}/statm").read_text(encoding="ascii").split()
            return int(statm[1]) * os.sysconf("SC_PAGE_SIZE")
        from ctypes import wintypes

        class _Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        get_info = getattr(psapi, "K32GetProcessMemoryInfo", None) or getattr(psapi, "GetProcessMemoryInfo", None)
        if get_info is None:
            return None
        get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Counters), wintypes.DWORD]
        get_info.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(0x1010, False, pid)
        if not handle:
            return None
        try:
            counters = _Counters()
            counters.cb = ctypes.sizeof(counters)
            if not get_info(handle, ctypes.byref(counters), counters.cb):
                return None
            return int(counters.WorkingSetSize)
        finally:
            kernel32.CloseHandle(handle)
    except (OSError, ValueError, IndexError, AttributeError, TypeError):
        return None


def stage_residual(job_root: Path) -> int:
    stages = 0
    for dirpath, dirnames, _ in os.walk(str(job_root), topdown=False):
        stages += sum(1 for name in dirnames if name.startswith(".") and ".stage-" in name)
    return stages


def tmp_stats(job_root: Path) -> tuple[int, int]:
    """Return (entry count, total bytes) currently inside ``<job_root>/.tmp.motor-ifc``."""
    tmp = job_root / TMP_DIR_NAME
    if not tmp.is_dir():
        return 0, 0
    entries = 0
    total = 0
    for item in tmp.iterdir():
        entries += 1
        if item.is_dir() and not item.is_symlink():
            for dirpath, _dirnames, filenames in os.walk(str(item)):
                for name in filenames:
                    try:
                        total += (Path(dirpath) / name).stat().st_size
                    except OSError:
                        pass
        else:
            try:
                total += item.stat().st_size
            except OSError:
                pass
    return entries, total


def _clear_readonly(func: Any, path: str, exc: BaseException) -> None:
    # Snapshots are chmod'd read-only (security.py), which blocks os.unlink on
    # Windows with WinError 5; clear the write bit and retry once.
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def sweep_tmp(job_root: Path) -> int:
    """Remove every entry under ``<job_root>/.tmp.motor-ifc``; return the number removed.

    Safe between rounds: no workers are alive and each request snapshots into a
    fresh temp dir, so anything left behind is residue from a killed worker.
    """
    tmp = job_root / TMP_DIR_NAME
    if not tmp.is_dir():
        return 0
    removed = 0
    for item in list(tmp.iterdir()):
        try:
            if item.is_dir() and not item.is_symlink():
                shutil.rmtree(item, onexc=_clear_readonly)
            else:
                try:
                    item.unlink()
                except PermissionError:
                    os.chmod(item, stat.S_IWRITE)
                    item.unlink()
        except OSError:
            pass
        if not item.exists():
            removed += 1
    return removed


def _response_ok(response: dict[str, Any]) -> bool:
    result = response.get("result")
    return isinstance(result, dict) and result.get("success") is True


def _record_error(state: dict[str, Any], round_index: int, case: str, response: dict[str, Any], step_ms: dict[str, int], extra: dict[str, Any] | None = None) -> None:
    error = response.get("error") or {}
    record: dict[str, Any] = {
        "round": round_index,
        "case": case,
        "code": error.get("code"),
        "message": error.get("message"),
        "step_ms": step_ms,
        "raw": json.dumps(response, separators=(",", ":"), default=str)[:1024],
    }
    if extra:
        record.update(extra)
    state["errors"].append(record)


def run_round(client: SupervisorClient, job_root: Path, round_index: int, state: dict[str, Any]) -> dict[str, int]:
    step_ms: dict[str, int] = {}

    # 1) sequential inline
    t0 = time.monotonic()
    response = client.request(f"seq-{round_index}", "reader.extract.v2", {"ifc_path": "b02-community-ifc2x3.ifc", "projection": "quantities"})
    step_ms["seq"] = int((time.monotonic() - t0) * 1000)
    state["requests"] += 1
    if not _response_ok(response):
        _record_error(state, round_index, "sequential", response, dict(step_ms))

    # 2) two concurrent publications
    t0 = time.monotonic()
    for slot in range(2):
        client.submit(f"pair-{round_index}-{slot}", "reader.extract.v2", {"ifc_path": "b03-community-ifc4.ifc", "projection": "quantities", "output_dir": f"out-pair-{round_index}-{slot}"})
    for response in client.read_responses(2):
        state["requests"] += 1
        if not _response_ok(response):
            _record_error(state, round_index, "pair", response, {**step_ms, "pair": int((time.monotonic() - t0) * 1000)})
    step_ms["pair"] = int((time.monotonic() - t0) * 1000)

    # 3) four concurrent dense publications
    t0 = time.monotonic()
    for slot in range(4):
        client.submit(f"quad-{round_index}-{slot}", "reader.extract.v2", {"ifc_path": "b05-semantic-dense.ifc", "projection": "quantities", "output_dir": f"out-quad-{round_index}-{slot}"})
    for response in client.read_responses(4):
        state["requests"] += 1
        if not _response_ok(response):
            _record_error(state, round_index, "quad", response, {**step_ms, "quad": int((time.monotonic() - t0) * 1000)})
    step_ms["quad"] = int((time.monotonic() - t0) * 1000)

    # 4) cancellation mid-extraction (a -32800 terminal fault IS the expected success)
    t0 = time.monotonic()
    client.submit(f"victim-{round_index}", "reader.extract.v2", {"ifc_path": "b05-semantic-dense.ifc", "projection": "rich", "output_dir": f"out-victim-{round_index}"})
    time.sleep(2.0)
    client.notify_cancel(f"victim-{round_index}")
    response = client.read_responses(1)[0]
    step_ms["cancel"] = int((time.monotonic() - t0) * 1000)
    state["requests"] += 1
    if (response.get("error") or {}).get("code") == -32800:
        state["cancellations"] += 1
    else:
        _record_error(state, round_index, "cancel", response, dict(step_ms))

    # 5) overload: 5 concurrent on workers=4 -> exactly one -32011 rejection
    t0 = time.monotonic()
    for slot in range(5):
        client.submit(f"over-{round_index}-{slot}", "reader.extract.v2", {"ifc_path": "b02-community-ifc2x3.ifc", "projection": "quantities"})
    responses = client.read_responses(5)
    step_ms["overload"] = int((time.monotonic() - t0) * 1000)
    state["requests"] += 5
    rejected = sum(1 for item in responses if (item.get("error") or {}).get("code") == -32011)
    codes = [((item.get("error") or {}).get("code") if "error" in item else "result") for item in responses]
    if rejected != 1:
        _record_error(
            state, round_index, "overload",
            {"error": {"code": None, "message": f"expected exactly one -32011 rejection, got {rejected}"}},
            dict(step_ms), extra={"codes": codes},
        )
    else:
        for item in responses:
            if (item.get("error") or {}).get("code") != -32011 and not _response_ok(item):
                _record_error(state, round_index, "overload-unexpected", item, dict(step_ms), extra={"codes": codes})

    # 6) sweep contained temp residue left by killed workers (safe: no live workers here)
    state["tmp_swept"] = state.get("tmp_swept", 0) + sweep_tmp(job_root)

    return step_ms


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default=time.strftime("%Y-%m-%d"))
    parser.add_argument("--duration", type=int, default=8 * 3600)
    parser.add_argument("--restart-every", type=int, default=200)
    args = parser.parse_args()

    from benchmarks.run_benchmarks import evidence_path, results_root

    evidence = evidence_path(args.run_id, "soak")
    cache = results_root(args.run_id) / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    job_root = results_root(args.run_id) / "soak-jobroot"
    job_root.mkdir(parents=True, exist_ok=True)
    for model_id in SOAK_MODELS:
        source = repaired_source(cache, model_id) if MODELS[model_id].get("repair") else MODELS[model_id]["source"]
        stage_input(job_root, source)

    state: dict[str, Any] = {"requests": 0, "errors": [], "cancellations": 0, "restarts": 0, "tmp_swept": 0}
    step_samples: list[dict[str, int]] = []
    started = time.monotonic()
    deadline = started + args.duration
    round_index = 0
    client = SupervisorClient(job_root, workers=4)
    cold_rss = _process_rss(client.process.pid)
    warm_baseline_rss: int | None = None
    last_supervisor_rss = cold_rss
    last_heartbeat = started
    jsonl_append(evidence, {"event": "soak_start", "duration_s": args.duration, "cold_supervisor_rss": cold_rss})
    try:
        while time.monotonic() < deadline:
            step_ms = run_round(client, job_root, round_index, state)
            step_samples.append(step_ms)
            round_index += 1
            if round_index % args.restart_every == 0:
                events = client.close()
                recovery_before = stage_residual(job_root)
                client = SupervisorClient(job_root, workers=4)
                state["restarts"] += 1
                warm_baseline_rss = None
                jsonl_append(evidence, {
                    "event": "soak_restart",
                    "round": round_index,
                    "stages_found": recovery_before,
                    "recovery_events": sum(1 for event in events if event.get("event") == "recovery"),
                    "new_cold_supervisor_rss": _process_rss(client.process.pid),
                })
            now = time.monotonic()
            if now - last_heartbeat >= 300:
                tmp_entries, tmp_bytes = tmp_stats(job_root)
                # keep the job root bounded: remove output dirs older than this heartbeat
                removed = 0
                for item in job_root.iterdir():
                    if item.is_dir() and item.name.startswith("out-"):
                        shutil.rmtree(item, ignore_errors=True)
                        removed += 1
                last_supervisor_rss = _process_rss(client.process.pid)
                if warm_baseline_rss is None:
                    warm_baseline_rss = last_supervisor_rss
                pair_vals = [sample["pair"] for sample in step_samples if "pair" in sample]
                quad_vals = [sample["quad"] for sample in step_samples if "quad" in sample]
                jsonl_append(evidence, {
                    "event": "heartbeat",
                    "round": round_index,
                    "elapsed_s": int(now - started),
                    "requests": state["requests"],
                    "errors": len(state["errors"]),
                    "cancellations": state["cancellations"],
                    "restarts": state["restarts"],
                    "supervisor_rss": last_supervisor_rss,
                    "supervisor_rss_growth_pct": round((last_supervisor_rss - warm_baseline_rss) / warm_baseline_rss * 100, 1) if warm_baseline_rss else None,
                    "stages_present": stage_residual(job_root),
                    "tmp_entries": tmp_entries,
                    "tmp_bytes": tmp_bytes,
                    "output_dirs_removed": removed,
                    "avg_pair_ms": int(sum(pair_vals) / len(pair_vals)) if pair_vals else None,
                    "avg_quad_ms": int(sum(quad_vals) / len(quad_vals)) if quad_vals else None,
                })
                step_samples.clear()
                last_heartbeat = now
    finally:
        events = client.close()
        summary = {
            "event": "soak_end",
            "rounds": round_index,
            "requests": state["requests"],
            "errors": len(state["errors"]),
            "cancellations": state["cancellations"],
            "restarts": state["restarts"],
            "stages_remaining": stage_residual(job_root),
            "tmp_entries_remaining": tmp_stats(job_root)[0],
            "tmp_swept_total": state.get("tmp_swept", 0),
            "duration_s": int(time.monotonic() - started),
            "last_supervisor_rss": last_supervisor_rss,
            "worker_terminal_events": sum(1 for event in events if event.get("event", "").startswith("worker_")),
        }
        jsonl_append(evidence, summary)
        if state["errors"]:
            error_path = evidence.with_name("soak-errors.jsonl")
            for error in state["errors"][:200]:
                jsonl_append(error_path, error)
        print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
