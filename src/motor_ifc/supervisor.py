"""Bounded direct-child supervisor for newline-delimited JSON-RPC requests."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Callable, TextIO

from .rpc import MAX_RPC_LINE_BYTES, RpcFault, _response, parse_line, strict_json_loads, valid_request_id

DEFAULT_MAX_WORKERS = 1
MAX_WORKERS_LIMIT = 4
TERMINATION_GRACE_SECONDS = 0.25
DEFAULT_JOB_TIMEOUT_MS = 180_000
MAX_JOB_TIMEOUT_MS = 86_400_000
DEFAULT_MAX_RSS_BYTES = 4 * 1024 * 1024 * 1024
MAX_MAX_RSS_BYTES = 1024 ** 4
RESOURCE_SAMPLE_SECONDS = 0.05
CANCELLED = RpcFault(-32800, "Request cancelled")
JOB_TIMED_OUT = RpcFault(-32801, "Job timed out")
RESOURCE_BUDGET_EXCEEDED = RpcFault(-32802, "Job exceeded resource budget")
DUPLICATE_ID = RpcFault(-32010, "Request ID is already in flight")
OVERLOADED = RpcFault(-32011, "Supervisor capacity reached")
WORKER_FAILED = RpcFault(-32012, "Worker failed")
MAX_WORKER_STDOUT_BYTES = MAX_RPC_LINE_BYTES + 1
MAX_WORKER_STDERR_BYTES = 65_536
STAGE_MARKER = ".stage-"
TEMP_DIR_NAME = ".tmp.motor-ifc"
_RequestId = str | int | float
_RequestKey = tuple[type, _RequestId]
_SpawnWorker = Callable[[], subprocess.Popen[bytes]]


@dataclass
class _Job:
    request_id: _RequestId
    key: _RequestKey
    process: subprocess.Popen[bytes]
    thread: threading.Thread | None = None
    state: str = "active"
    termination_lock: threading.Lock = field(default_factory=threading.Lock)


def _request_token(request_id: object) -> str:
    encoded = json.dumps(request_id, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()[:12]


def _request_key(request_id: _RequestId) -> _RequestKey:
    return (type(request_id), request_id)


def _same_request_id(left: object, right: object) -> bool:
    return type(left) is type(right) and left == right


def _bounded_int_env(name: str, default: int, maximum: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    if not re.fullmatch(r"[1-9][0-9]*", value) or int(value) > maximum:
        raise ValueError(f"invalid {name}")
    return int(value)


def _job_timeout_ms() -> int:
    return _bounded_int_env("MOTOR_IFC_SUPERVISOR_JOB_TIMEOUT_MS", DEFAULT_JOB_TIMEOUT_MS, MAX_JOB_TIMEOUT_MS)


def _max_rss_bytes() -> int:
    return _bounded_int_env("MOTOR_IFC_SUPERVISOR_MAX_RSS_BYTES", DEFAULT_MAX_RSS_BYTES, MAX_MAX_RSS_BYTES)


def _max_workers() -> int:
    value = os.environ.get("MOTOR_IFC_SUPERVISOR_MAX_WORKERS")
    if value is None:
        return DEFAULT_MAX_WORKERS
    if not re.fullmatch(r"[1-4]", value):
        raise ValueError("invalid worker bound")
    return int(value)


_WINDOWS_MEMORY_PROBE: Any = None


def _windows_memory_probe() -> Any:
    global _WINDOWS_MEMORY_PROBE
    if os.name != "nt":
        return None
    if _WINDOWS_MEMORY_PROBE is None:
        try:
            import ctypes
            from ctypes import wintypes

            class _MemoryCounters(ctypes.Structure):
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
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            get_info = getattr(psapi, "K32GetProcessMemoryInfo", None) or getattr(psapi, "GetProcessMemoryInfo", None)
            if get_info is None:
                raise AttributeError("GetProcessMemoryInfo unavailable")
            get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MemoryCounters), wintypes.DWORD]
            get_info.restype = wintypes.BOOL
            _WINDOWS_MEMORY_PROBE = (kernel32, get_info, _MemoryCounters, ctypes)
        except (ImportError, OSError, AttributeError):
            _WINDOWS_MEMORY_PROBE = False
    return _WINDOWS_MEMORY_PROBE or None


def _process_rss(process: subprocess.Popen[bytes]) -> int | None:
    """Best-effort working-set size of the direct child; None when unmeasurable."""
    try:
        if os.name == "nt":
            probe = _windows_memory_probe()
            if probe is None:
                return None
            kernel32, get_info, memory_counters, ctypes = probe
            handle = kernel32.OpenProcess(0x1010, False, process.pid)
            if not handle:
                return None
            try:
                counters = memory_counters()
                counters.cb = ctypes.sizeof(counters)
                if not get_info(handle, ctypes.byref(counters), counters.cb):
                    return None
                return int(counters.WorkingSetSize)
            finally:
                kernel32.CloseHandle(handle)
        statm = Path(f"/proc/{process.pid}/statm").read_text(encoding="ascii").split()
        return int(statm[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError, AttributeError, TypeError):
        return None


def _clear_readonly(func: Any, path: str, exc: BaseException) -> None:
    # Reader/repair snapshots are chmod'd read-only (security.py), which blocks
    # removal on Windows with WinError 5; clear the write bit and retry once.
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def recover_job_root(root: str | Path) -> int:
    """Remove orphaned publication stages and contained temp directories beneath a job root."""
    removed = 0
    for dirpath, dirnames, _ in os.walk(str(root), topdown=False):
        for name in dirnames:
            if (name.startswith(".") and STAGE_MARKER in name) or name == TEMP_DIR_NAME:
                target = Path(dirpath) / name
                try:
                    shutil.rmtree(target, onexc=_clear_readonly)
                except OSError:
                    pass
                if not target.exists():
                    removed += 1
    return removed


def _spawn_worker() -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, "-m", "motor_ifc.worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )


class Supervisor:
    def __init__(
        self,
        max_workers: int,
        stdout: TextIO,
        stderr: TextIO,
        *,
        _spawn: _SpawnWorker | None = None,
        job_timeout_ms: int | None = None,
        max_rss_bytes: int | None = None,
    ) -> None:
        if not 1 <= max_workers <= MAX_WORKERS_LIMIT:
            raise ValueError("invalid worker bound")
        self.max_workers = max_workers
        self.stdout = stdout
        self.stderr = stderr
        self.job_timeout_ms = _job_timeout_ms() if job_timeout_ms is None else job_timeout_ms
        self.max_rss_bytes = _max_rss_bytes() if max_rss_bytes is None else max_rss_bytes
        self._spawn = _spawn or _spawn_worker
        self._jobs: dict[_RequestKey, _Job] = {}
        self._lock = threading.Lock()
        self._output_lock = threading.Lock()
        self._stopping = False

    def _log(self, event: str, request_id: object | None = None, **fields: object) -> None:
        record: dict[str, object] = {"event": event}
        if request_id is not None:
            record["request"] = _request_token(request_id)
        record.update(fields)
        line = json.dumps(record, separators=(",", ":"), ensure_ascii=True)
        if len(line.encode("ascii")) > 1024:
            line = '{"event":"lifecycle_record_rejected"}'
        with self._output_lock:
            self.stderr.write(line + "\n")
            self.stderr.flush()

    def _write(self, response: str) -> None:
        with self._output_lock:
            self.stdout.write(response + "\n")
            self.stdout.flush()

    def submit(self, line: str) -> None:
        request, error_response = parse_line(line)
        if error_response is not None:
            self._write(error_response)
            return
        assert request is not None
        if request["method"] in {"cancel_job", "job.cancel.v1"}:
            self._handle_cancel(request)
            return
        if "id" not in request:
            self._log("notification_ignored")
            return
        request_id = request.get("id")
        if not valid_request_id(request_id, allow_none=False):
            self._write(_response(None, fault=RpcFault(-32600, "Invalid Request")))
            return
        assert isinstance(request_id, (str, int, float)) and not isinstance(request_id, bool)
        key = _request_key(request_id)
        with self._lock:
            if self._stopping:
                return
            if key in self._jobs:
                self._write(_response(request_id, fault=DUPLICATE_ID))
                self._log("request_rejected", request_id, reason="duplicate")
                return
            if len(self._jobs) >= self.max_workers:
                self._write(_response(request_id, fault=OVERLOADED))
                self._log("request_rejected", request_id, reason="overload")
                return
            try:
                process = self._spawn()
            except OSError:
                self._write(_response(request_id, fault=WORKER_FAILED))
                self._log("worker_failed", request_id, reason="spawn")
                return
            job = _Job(request_id=request_id, key=key, process=process)
            self._jobs[key] = job
            canonical = json.dumps(request, separators=(",", ":"), ensure_ascii=True)
            thread = threading.Thread(target=self._collect, args=(job, canonical), daemon=True)
            job.thread = thread
            thread.start()
        self._log("worker_started", request_id, pid=process.pid)

    def _collect(self, job: _Job, request_line: str) -> None:
        stdout = bytearray()
        stderr = bytearray()
        overflow = threading.Event()

        def read_stream(stream: BinaryIO, destination: bytearray, limit: int) -> None:
            try:
                while True:
                    chunk = stream.read(min(65_536, limit + 1 - len(destination)))
                    if not chunk:
                        return
                    destination.extend(chunk)
                    if len(destination) > limit:
                        if not overflow.is_set():
                            overflow.set()
                            self._terminate_job(job)
                        return
            except (OSError, ValueError):
                overflow.set()
                self._terminate_job(job)

        assert job.process.stdout is not None
        assert job.process.stderr is not None
        stdout_thread = threading.Thread(
            target=read_stream,
            args=(job.process.stdout, stdout, MAX_WORKER_STDOUT_BYTES),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=read_stream,
            args=(job.process.stderr, stderr, MAX_WORKER_STDERR_BYTES),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()
        started = time.monotonic()
        try:
            assert job.process.stdin is not None
            job.process.stdin.write((request_line + "\n").encode("utf-8"))
            job.process.stdin.close()
        except (OSError, ValueError):
            overflow.set()
            self._terminate_job(job)
        reason = None
        rss_peak = 0
        while job.process.poll() is None:
            if (time.monotonic() - started) * 1000 > self.job_timeout_ms:
                reason = "timeout"
                self._terminate_job(job)
                break
            rss = _process_rss(job.process)
            if rss is not None:
                rss_peak = max(rss_peak, rss)
                if rss > self.max_rss_bytes:
                    reason = "resource"
                    self._terminate_job(job)
                    break
            time.sleep(RESOURCE_SAMPLE_SECONDS)
        stdout_thread.join()
        stderr_thread.join()
        elapsed_ms = int((time.monotonic() - started) * 1000)
        response = None
        if reason is None and not overflow.is_set():
            response = self._validated_worker_response(
                bytes(stdout), job.request_id, job.process.returncode
            )
        with self._lock:
            current = self._jobs.get(job.key)
            if current is not job or job.state != "active" or self._stopping:
                return
            job.state = "completed"
            del self._jobs[job.key]
        if overflow.is_set():
            self._write(_response(job.request_id, fault=WORKER_FAILED))
            self._log("worker_failed", job.request_id, reason="protocol", elapsed_ms=elapsed_ms)
        elif reason == "timeout":
            self._write(_response(job.request_id, fault=JOB_TIMED_OUT))
            self._log("worker_timeout", job.request_id, elapsed_ms=elapsed_ms, rss_peak=rss_peak or None)
        elif reason == "resource":
            self._write(_response(job.request_id, fault=RESOURCE_BUDGET_EXCEEDED))
            self._log("worker_resource", job.request_id, elapsed_ms=elapsed_ms, rss_peak=rss_peak)
        elif response is None:
            self._write(_response(job.request_id, fault=WORKER_FAILED))
            self._log("worker_failed", job.request_id, reason="protocol", elapsed_ms=elapsed_ms)
        else:
            self._write(response)
            self._log("worker_completed", job.request_id, elapsed_ms=elapsed_ms, rss_peak=rss_peak or None)

    @staticmethod
    def _validated_worker_response(stdout: bytes, request_id: object, returncode: int | None) -> str | None:
        if returncode != 0 or len(stdout) > MAX_WORKER_STDOUT_BYTES:
            return None
        try:
            decoded = stdout.decode("utf-8")
            lines = decoded.splitlines()
            if len(lines) != 1 or len(lines[0].encode("utf-8")) > MAX_RPC_LINE_BYTES:
                return None
            response = strict_json_loads(lines[0])
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, MemoryError, ValueError):
            return None
        response_id = response.get("id") if isinstance(response, dict) else None
        if (
            not isinstance(response, dict)
            or response.get("jsonrpc") != "2.0"
            or not valid_request_id(response_id, allow_none=False)
            or not _same_request_id(response_id, request_id)
        ):
            return None
        if ("result" in response) == ("error" in response):
            return None
        if "error" in response:
            error = response["error"]
            if (
                not isinstance(error, dict)
                or isinstance(error.get("code"), bool)
                or not isinstance(error.get("code"), int)
                or not isinstance(error.get("message"), str)
            ):
                return None
        return json.dumps(response, separators=(",", ":"), ensure_ascii=True, allow_nan=False)

    def _handle_cancel(self, request: dict[str, Any]) -> None:
        if "id" in request:
            self._write(_response(request.get("id"), fault=RpcFault(-32600, "Cancellation must be a notification")))
            return
        params = request.get("params")
        if not isinstance(params, dict) or set(params) != {"id"} or not valid_request_id(params.get("id"), allow_none=False):
            self._log("cancellation_rejected", reason="invalid_params")
            return
        request_id = params["id"]
        assert isinstance(request_id, (str, int, float)) and not isinstance(request_id, bool)
        key = _request_key(request_id)
        with self._lock:
            job = self._jobs.get(key)
            if job is None or job.state != "active":
                self._log("cancellation_ignored", request_id)
                return
            job.state = "cancelling"
        self._log("cancellation_requested", request_id)
        forced = self._terminate_job(job)
        with self._lock:
            if self._jobs.get(key) is job:
                job.state = "cancelled"
                del self._jobs[key]
        self._write(_response(request_id, fault=CANCELLED))
        self._log("worker_cancelled", request_id, forced=forced)

    @staticmethod
    def _terminate(process: subprocess.Popen[bytes]) -> bool:
        if process.poll() is not None:
            return False
        try:
            if os.name == "nt":
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                process.terminate()
            process.wait(timeout=TERMINATION_GRACE_SECONDS)
            return False
        except (OSError, subprocess.TimeoutExpired):
            if process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    pass
                try:
                    process.wait(timeout=TERMINATION_GRACE_SECONDS)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            return True

    def _terminate_job(self, job: _Job) -> bool:
        with job.termination_lock:
            return self._terminate(job.process)

    def shutdown(self) -> None:
        with self._lock:
            self._stopping = True
            jobs = list(self._jobs.values())
            for job in jobs:
                job.state = "stopping"
        forced = 0
        for job in jobs:
            forced += int(self._terminate_job(job))
        with self._lock:
            self._jobs.clear()
        for job in jobs:
            if job.thread is not None:
                job.thread.join(timeout=TERMINATION_GRACE_SECONDS)
        self._log("supervisor_stopped", active=len(jobs), forced=forced)

    def run(self, stdin: BinaryIO) -> None:
        try:
            while True:
                raw = stdin.readline(MAX_RPC_LINE_BYTES + 2)
                if not raw:
                    break
                if len(raw) > MAX_RPC_LINE_BYTES and not raw.endswith(b"\n"):
                    while raw and not raw.endswith(b"\n"):
                        raw = stdin.readline(MAX_RPC_LINE_BYTES + 2)
                    self._write(_response(None, fault=RpcFault(-32600, "Request too large")))
                    continue
                try:
                    line = raw.decode("utf-8")
                except UnicodeDecodeError:
                    self._write(_response(None, fault=RpcFault(-32700, "Parse error")))
                    continue
                self.submit(line)
        finally:
            self.shutdown()


def main() -> None:
    try:
        supervisor = Supervisor(_max_workers(), sys.stdout, sys.stderr)
    except ValueError:
        sys.stderr.write('{"event":"configuration_rejected"}\n')
        sys.stderr.flush()
        raise SystemExit(2)
    job_root = os.environ.get("MOTOR_IFC_JOB_ROOT")
    if job_root:
        removed = recover_job_root(job_root)
        if removed:
            supervisor._log("recovery", removed=removed)
    supervisor.run(sys.stdin.buffer)


if __name__ == "__main__":
    main()
