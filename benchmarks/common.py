"""Shared helpers for the Ola 4 benchmark harness.

Benchmarks execute through the real ``motor_ifc.supervisor`` transport: every
measurement is a JSON-RPC request whose worker lifecycle events (``elapsed_ms``,
``rss_peak``) are captured from redacted stderr. Evidence is written as JSONL.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).parents[1]
CORPUS = REPO / "corpus"
# Extra IFC fixtures from the consuming application, which is a separate
# private repository. Point MOTOR_IFC_BENCH_FIXTURES at its IFC test fixtures
# to include them; without it the benchmarks run on `corpus/` alone.
FIXTURES = Path(os.environ.get("MOTOR_IFC_BENCH_FIXTURES", CORPUS / "models"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def request_token(request_id: object) -> str:
    encoded = json.dumps(request_id, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()[:12]


def hardware_fingerprint() -> dict[str, Any]:
    import ifcopenshell

    return {
        "machine": platform.node(),
        "os": platform.platform(),
        "cpu": platform.processor(),
        "logical_cores": os.cpu_count(),
        "python": sys.version.split()[0],
        "ifcopenshell": str(ifcopenshell.version),
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


class SupervisorClient:
    """One supervised process: request/response by id plus stderr event capture."""

    def __init__(self, job_root: Path, *, workers: int = 1, timeout_ms: int | None = None) -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(REPO / "src")
        env["MOTOR_IFC_JOB_ROOT"] = str(job_root)
        env["MOTOR_IFC_SUPERVISOR_MAX_WORKERS"] = str(workers)
        if timeout_ms is not None:
            env["MOTOR_IFC_SUPERVISOR_JOB_TIMEOUT_MS"] = str(timeout_ms)
        self.process = subprocess.Popen(
            [sys.executable, "-m", "motor_ifc.supervisor"],
            cwd=REPO,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=env,
        )
        self.events: list[dict[str, Any]] = []
        self._event_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()

    def _drain_stderr(self) -> None:
        assert self.process.stderr is not None
        for line in self.process.stderr:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                event = {"event": "unparseable_stderr"}
            self.events.append(event)
            self._event_queue.put(event)

    def _send(self, payload: dict[str, Any]) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
        self.process.stdin.flush()

    def request(self, request_id: Any, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        started = time.monotonic()
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
        assert self.process.stdout is not None
        line = self.process.stdout.readline()
        elapsed_ms = int((time.monotonic() - started) * 1000)
        response = json.loads(line)
        response["_client_elapsed_ms"] = elapsed_ms
        return response

    def submit(self, request_id: Any, method: str, params: dict[str, Any] | None = None) -> None:
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})

    def notify_cancel(self, request_id: Any) -> None:
        self._send({"jsonrpc": "2.0", "method": "job.cancel.v1", "params": {"id": request_id}})

    def read_responses(self, count: int, timeout: float = 900.0) -> list[dict[str, Any]]:
        assert self.process.stdout is not None
        responses = []
        deadline = time.monotonic() + timeout
        while len(responses) < count:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("supervisor responses incomplete")
            line = self.process.stdout.readline()
            if not line:
                raise EOFError("supervisor stdout closed")
            responses.append(json.loads(line))
        return responses

    def events_for(self, event_name: str, request_id: Any) -> list[dict[str, Any]]:
        token = request_token(request_id)
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            matches = [event for event in self.events if event.get("event") == event_name and event.get("request") == token]
            if matches:
                return matches
            time.sleep(0.02)
        return [event for event in self.events if event.get("event") == event_name and event.get("request") == token]

    def close(self) -> list[dict[str, Any]]:
        if self.process.stdin is not None and not self.process.stdin.closed:
            self.process.stdin.close()
        self.process.wait(timeout=60)
        self._stderr_thread.join(timeout=5)
        return self.events


def jsonl_append(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, separators=(",", ":")) + "\n")


def stage_input(job_root: Path, source: Path) -> str:
    """Copy a model into the job root (reparse/symlink inputs are forbidden)."""
    job_root.mkdir(parents=True, exist_ok=True)
    target = job_root / source.name
    if not target.exists() or target.stat().st_size != source.stat().st_size:
        target.write_bytes(source.read_bytes())
    return source.name
