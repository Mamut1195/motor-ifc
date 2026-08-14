import ctypes
import hashlib
import io
import json
import os
from pathlib import Path
import queue
import stat
import subprocess
import sys
import threading
import time
import uuid

import pytest

import motor_ifc.supervisor as supervisor_module
from motor_ifc.supervisor import Supervisor, recover_job_root


ROOT = Path(__file__).parents[1]


class Harness:
    def __init__(self, tmp_path, *, test_worker=True, workers=None, env_extra=None):
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join((str(ROOT / "src"), str(ROOT)))
        self.pid_file = tmp_path / "worker.pid"
        env["MOTOR_IFC_TEST_PID_FILE"] = str(self.pid_file)
        if workers is not None:
            env["MOTOR_IFC_SUPERVISOR_MAX_WORKERS"] = workers
        if env_extra:
            env.update(env_extra)
        self.process = subprocess.Popen(
            [sys.executable, "-m", "tests.supervisor_runner" if test_worker else "motor_ifc.supervisor"],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=env,
        )
        self.lines = queue.Queue()
        self.stderr_lines = []
        self.reader = threading.Thread(target=self._read_stdout, daemon=True)
        self.stderr_reader = threading.Thread(target=self._read_stderr, daemon=True)
        self.reader.start()
        self.stderr_reader.start()

    def _read_stdout(self):
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.lines.put(line.rstrip("\n"))

    def _read_stderr(self):
        assert self.process.stderr is not None
        for line in self.process.stderr:
            self.stderr_lines.append(line)

    def send(self, method, request_id=1, params=None, *, notification=False):
        request = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notification:
            request["id"] = request_id
        self.send_raw(json.dumps(request, separators=(",", ":")))

    def send_raw(self, line):
        assert self.process.stdin is not None
        self.process.stdin.write(line + "\n")
        self.process.stdin.flush()

    def response(self, timeout=3):
        return json.loads(self.lines.get(timeout=timeout))

    def wait_for_pid(self):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if self.pid_file.exists():
                value = self.pid_file.read_text(encoding="ascii")
                if value.isdecimal():
                    return int(value)
            time.sleep(0.01)
        raise AssertionError("worker PID was not published")

    def close(self):
        if self.process.stdin is not None and not self.process.stdin.closed:
            self.process.stdin.close()
        self.process.wait(timeout=5)
        self.reader.join(timeout=1)
        self.stderr_reader.join(timeout=1)
        return "".join(self.stderr_lines)

    def diagnostics(self):
        return {
            "returncode": self.process.poll(),
            "stdout_queue": list(self.lines.queue),
            "stderr": "".join(self.stderr_lines),
        }

    def kill(self):
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=3)


@pytest.fixture
def harnesses():
    active = []
    yield active
    for harness in active:
        harness.kill()


def start(harnesses, tmp_path, **kwargs):
    harness = Harness(tmp_path, **kwargs)
    harnesses.append(harness)
    return harness


def process_is_active(pid):
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        return bool(ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def test_supervisor_runs_real_one_shot_worker_and_keeps_stdout_protocol_only(harnesses, tmp_path):
    harness = start(
        harnesses,
        tmp_path,
        test_worker=False,
        env_extra={
            "MOTOR_IFC_SUPERVISOR_TESTING": "1",
            "MOTOR_IFC_SUPERVISOR_TEST_WORKER_MODULE": "tests.supervisor_worker",
        },
    )
    harness.send("engine.capabilities.v1", "capabilities")
    response = harness.response()
    assert response["id"] == "capabilities"
    assert response["result"]["engine_version"] == "0.1.0"
    stderr = harness.close()
    assert harness.lines.empty()
    assert all(json.loads(line)["event"] for line in stderr.splitlines())


def test_supervisor_executes_reader_method_without_special_case(harnesses, tmp_path):
    (tmp_path / "malformed.ifc").write_text("not IFC", encoding="ascii")
    harness = start(harnesses, tmp_path, test_worker=False, env_extra={"MOTOR_IFC_JOB_ROOT": str(tmp_path)})
    harness.send("reader.extract.v1", "reader", {"ifc_path": "malformed.ifc"})
    response = harness.response()
    assert response["id"] == "reader"
    assert response["result"]["contract_version"] == "reader-extraction.v1"
    assert response["result"]["success"] is False
    assert response["result"]["diagnostics"][0]["code"] == 2800
    harness.close()


def make_dense_ifc(path, walls):
    ifc = pytest.importorskip("ifcopenshell")
    model = ifc.file(schema="IFC4")
    guid = ifc.guid.new
    point = model.create_entity("IfcCartesianPoint", Coordinates=[0.0, 0.0, 0.0])
    axis = model.create_entity("IfcAxis2Placement3D", Location=point)
    context = model.create_entity(
        "IfcGeometricRepresentationContext", ContextType="Model", CoordinateSpaceDimension=3, WorldCoordinateSystem=axis
    )
    assignment = model.create_entity("IfcUnitAssignment", Units=[model.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Name="METRE")])
    model.create_entity("IfcProject", GlobalId=guid(), Name="Project", RepresentationContexts=[context], UnitsInContext=assignment)
    for index in range(walls):
        wall = model.create_entity("IfcWall", GlobalId=ifc.guid.compress(uuid.UUID(int=index).hex), Name=f"Wall {index}")
        quantities = [
            model.create_entity("IfcQuantityLength", Name=f"Length {slot}", LengthValue=float(index + slot)) for slot in range(10)
        ]
        qset = model.create_entity("IfcElementQuantity", GlobalId=guid(), Name=f"Qto {index}", Quantities=quantities)
        model.create_entity("IfcRelDefinesByProperties", GlobalId=guid(), RelatedObjects=[wall], RelatingPropertyDefinition=qset)
    model.write(str(path))


@pytest.mark.ifcopenshell
def test_supervisor_transports_large_reader_result_as_publication(harnesses, tmp_path):
    make_dense_ifc(tmp_path / "dense.ifc", walls=400)
    harness = start(harnesses, tmp_path, test_worker=False, env_extra={"MOTOR_IFC_JOB_ROOT": str(tmp_path)})
    # Inline mode is bounded by the reader itself: typed failure instead of a transport death.
    harness.send("reader.extract.v2", "inline", {"ifc_path": "dense.ifc"})
    inline = harness.response(timeout=60)
    assert inline["id"] == "inline"
    assert inline["result"]["success"] is False
    assert inline["result"]["diagnostics"][0]["code"] == 2003
    assert "output_dir" in inline["result"]["diagnostics"][0]["suggested_action"]
    # The same extraction publishes a bounded artifact and the response stays small.
    harness.send("reader.extract.v2", "published", {"ifc_path": "dense.ifc", "output_dir": "published"})
    published = harness.response(timeout=60)
    assert published["id"] == "published"
    assert published["result"]["success"] is True
    assert published["result"]["publication"] == "immutable-directory"
    assert published["result"]["entity_count"] == 400
    assert len(json.dumps(published, separators=(",", ":"))) < 10_000
    extraction = tmp_path / "published" / "extraction.json"
    manifest = tmp_path / "published" / "extraction-manifest.json"
    assert extraction.is_file() and manifest.is_file()
    assert published["result"]["extraction_sha256"] == hashlib.sha256(extraction.read_bytes()).hexdigest()
    document = json.loads(extraction.read_text(encoding="utf-8"))
    assert document["entity_count"] == 400
    assert len(document["entities"]) == 400
    stderr = harness.close()
    assert harness.lines.empty()
    assert all(json.loads(line)["event"] for line in stderr.splitlines())


def test_protocol_errors_and_null_ids_do_not_spawn(harnesses, tmp_path):
    harness = start(harnesses, tmp_path)
    harness.send_raw("{")
    assert harness.response()["error"]["code"] == -32700
    harness.send_raw('{"jsonrpc":"2.0","id":null,"method":"test.success"}')
    assert harness.response()["error"]["code"] == -32600
    stderr = harness.close()
    assert "worker_started" not in stderr


@pytest.mark.parametrize(
    "line",
    [
        '{"jsonrpc":"2.0","id":1,"method":"test.success","params":null}',
        '{"jsonrpc":"2.0","id":1,"method":"test.success","params":7}',
        '{"jsonrpc":"2.0","id":1,"method":"test.success","params":{"nested":NaN}}',
        '{"jsonrpc":"2.0","id":1,"method":"test.success","params":[Infinity]}',
    ],
)
def test_invalid_json_and_scalar_params_never_spawn(harnesses, tmp_path, line):
    harness = start(harnesses, tmp_path)
    harness.send_raw(line)
    assert harness.response()["error"]["code"] in {-32700, -32600}
    stderr = harness.close()
    assert "worker_started" not in stderr
    assert not harness.pid_file.exists()


def test_invalid_utf8_never_spawns_worker():
    stdout = io.StringIO()
    spawned = False

    def forbidden_spawn():
        nonlocal spawned
        spawned = True
        raise AssertionError("invalid UTF-8 reached spawn")

    supervisor = Supervisor(1, stdout, io.StringIO(), _spawn=forbidden_spawn)
    supervisor.run(io.BytesIO(b'{"jsonrpc":"2.0","id":1,"method":"test.success","params":{"x":"\xff"}}\n'))
    assert json.loads(stdout.getvalue())["error"]["code"] == -32700
    assert spawned is False


def test_duplicate_ids_and_overload_are_deterministic(harnesses, tmp_path):
    harness = start(harnesses, tmp_path)
    harness.send("test.slow", "active")
    harness.wait_for_pid()
    harness.send("test.success", "active")
    assert harness.response()["error"]["code"] == -32010
    harness.send("test.success", "other")
    assert harness.response()["error"]["code"] == -32011
    harness.send("cancel_job", params={"id": "active"}, notification=True)
    assert harness.response()["error"]["code"] == -32800
    harness.close()


def test_valid_concurrency_override_allows_bounded_parallel_workers(harnesses, tmp_path):
    harness = start(harnesses, tmp_path, workers="2")
    harness.send("test.slow", "first")
    harness.send("test.slow", "second")
    harness.send("test.success", "third")
    assert harness.response()["error"]["code"] == -32011
    harness.send("cancel_job", params={"id": "first"}, notification=True)
    harness.send("cancel_job", params={"id": "second"}, notification=True)
    assert {harness.response()["id"], harness.response()["id"]} == {"first", "second"}
    harness.close()


@pytest.mark.parametrize("method", ["cancel_job", "job.cancel.v1"])
def test_cancellation_is_silent_for_notification_and_id_is_reusable(harnesses, tmp_path, method):
    harness = start(harnesses, tmp_path)
    harness.send("test.slow", 7)
    pid = harness.wait_for_pid()
    harness.send(method, params={"id": 7}, notification=True)
    cancelled = harness.response()
    assert cancelled == {"jsonrpc": "2.0", "id": 7, "error": {"code": -32800, "message": "Request cancelled"}}
    assert not process_is_active(pid)
    harness.pid_file.unlink()
    harness.send("test.success", 7)
    assert harness.response()["result"] == {"ok": True}
    harness.close()
    assert harness.lines.empty()


@pytest.mark.parametrize("method", ["cancel_job", "job.cancel.v1"])
def test_completion_cancellation_race_emits_one_terminal_response(harnesses, tmp_path, method):
    harness = start(harnesses, tmp_path)
    for request_id in range(100):
        harness.send("test.race", request_id)
        harness.send(method, params={"id": request_id}, notification=True)
        try:
            response = harness.response(timeout=5)
            assert response["id"] == request_id
            assert response.get("result") == {"ok": True} or response.get("error", {}).get("code") == -32800
        except (AssertionError, queue.Empty) as error:
            pytest.fail(f"race iteration {request_id}: {error!r}; {harness.diagnostics()}")
    harness.close()
    assert harness.lines.empty()


def test_numeric_id_types_are_distinct_for_correlation_and_cancellation(harnesses, tmp_path):
    harness = start(harnesses, tmp_path, workers="2")
    harness.send("test.slow", 1)
    harness.send("test.slow", 1.0)
    harness.send("cancel_job", params={"id": 1}, notification=True)
    first = harness.response()
    harness.send("cancel_job", params={"id": 1.0}, notification=True)
    second = harness.response()
    assert type(first["id"]) is int
    assert type(second["id"]) is float
    harness.close()


@pytest.mark.parametrize("method", ["test.wrong_id_bool", "test.wrong_id_float", "test.nan", "test.extra", "test.malformed"])
def test_invalid_worker_responses_are_replaced_with_worker_failure(harnesses, tmp_path, method):
    harness = start(harnesses, tmp_path)
    harness.send(method, 1)
    response = harness.response()
    assert response == {"jsonrpc": "2.0", "id": 1, "error": {"code": -32012, "message": "Worker failed"}}
    harness.close()
    assert harness.lines.empty()


@pytest.mark.parametrize("method", ["test.large_stdout", "test.large_stderr"])
def test_worker_output_overflow_is_terminated_and_redacted(harnesses, tmp_path, method):
    harness = start(harnesses, tmp_path)
    harness.send(method, method)
    pid = harness.wait_for_pid()
    response = harness.response(timeout=5)
    assert response["error"]["code"] == -32012
    assert not process_is_active(pid)
    stderr = harness.close()
    assert "SSSS" not in stderr
    assert harness.lines.empty()


@pytest.mark.skipif(os.name != "nt", reason="Windows direct-child termination contract")
def test_uncooperative_windows_child_is_force_killed(harnesses, tmp_path):
    harness = start(harnesses, tmp_path)
    harness.send("test.uncooperative", "force")
    pid = harness.wait_for_pid()
    time.sleep(0.05)
    harness.send("cancel_job", params={"id": "force"}, notification=True)
    assert harness.response()["error"]["code"] == -32800
    assert not process_is_active(pid)
    stderr = harness.close()
    assert any(json.loads(line).get("forced") is True for line in stderr.splitlines())


def test_eof_terminates_active_child_without_protocol_noise(harnesses, tmp_path):
    harness = start(harnesses, tmp_path)
    harness.send("test.slow", "shutdown")
    pid = harness.wait_for_pid()
    stderr = harness.close()
    assert harness.lines.empty()
    assert not process_is_active(pid)
    assert json.loads(stderr.splitlines()[-1])["event"] == "supervisor_stopped"


@pytest.mark.parametrize("value", ["0", "5", "01", "x", " 1"])
def test_invalid_concurrency_environment_exits_cleanly(harnesses, tmp_path, value):
    harness = start(harnesses, tmp_path, workers=value)
    stderr = harness.close()
    assert harness.process.returncode == 2
    assert harness.lines.empty()
    assert stderr == '{"event":"configuration_rejected"}\n'


def test_worker_stderr_is_discarded_and_lifecycle_is_redacted(harnesses, tmp_path):
    harness = start(harnesses, tmp_path)
    harness.send("test.stderr", "redaction", {"secret": "raw-body-secret"})
    assert harness.response()["result"] == {"ok": True}
    stderr = harness.close()
    assert "SECRET" not in stderr
    assert "Traceback" not in stderr
    assert "raw-body-secret" not in stderr
    assert str(tmp_path) not in stderr


def test_worker_crash_returns_stable_typed_error(harnesses, tmp_path):
    harness = start(harnesses, tmp_path)
    harness.send("test.crash", "crash")
    response = harness.response()
    assert response == {"jsonrpc": "2.0", "id": "crash", "error": {"code": -32012, "message": "Worker failed"}}
    harness.close()


def test_invalid_cancellation_params_are_silent(harnesses, tmp_path):
    harness = start(harnesses, tmp_path)
    harness.send("cancel_job", params={"job_id": 1}, notification=True)
    with pytest.raises(queue.Empty):
        harness.lines.get(timeout=0.1)
    stderr = harness.close()
    assert "cancellation_rejected" in stderr


@pytest.mark.parametrize(
    "params",
    [{}, {"job_id": 1}, {"id": None}, {"id": True}, {"id": 1, "extra": True}, [1]],
)
def test_job_cancel_alias_rejects_non_exact_params_without_spawning(harnesses, tmp_path, params):
    harness = start(harnesses, tmp_path)
    harness.send_raw(json.dumps({"jsonrpc": "2.0", "method": "job.cancel.v1", "params": params}))
    with pytest.raises(queue.Empty):
        harness.lines.get(timeout=0.1)
    stderr = harness.close()
    assert "cancellation_rejected" in stderr
    assert "worker_started" not in stderr


def test_job_cancel_alias_request_form_is_rejected_without_spawning(harnesses, tmp_path):
    harness = start(harnesses, tmp_path)
    harness.send("job.cancel.v1", "cancel-request", {"id": "active"})
    response = harness.response()
    assert response == {
        "jsonrpc": "2.0",
        "id": "cancel-request",
        "error": {"code": -32600, "message": "Cancellation must be a notification"},
    }
    stderr = harness.close()
    assert "worker_started" not in stderr


def test_job_cancel_alias_ignores_unknown_and_stale_ids(harnesses, tmp_path):
    harness = start(harnesses, tmp_path)
    harness.send("job.cancel.v1", params={"id": "unknown"}, notification=True)
    with pytest.raises(queue.Empty):
        harness.lines.get(timeout=0.1)
    harness.send("test.success", "stale")
    assert harness.response()["result"] == {"ok": True}
    harness.send("job.cancel.v1", params={"id": "stale"}, notification=True)
    with pytest.raises(queue.Empty):
        harness.lines.get(timeout=0.1)
    stderr = harness.close()
    assert stderr.count("cancellation_ignored") == 2


def test_job_timeout_terminates_worker_with_typed_fault(harnesses, tmp_path):
    harness = start(harnesses, tmp_path, env_extra={"MOTOR_IFC_SUPERVISOR_JOB_TIMEOUT_MS": "300"})
    harness.send("test.slow", "late")
    pid = harness.wait_for_pid()
    started = time.monotonic()
    response = harness.response(timeout=5)
    assert response == {"jsonrpc": "2.0", "id": "late", "error": {"code": -32801, "message": "Job timed out"}}
    assert time.monotonic() - started < 2.0
    assert not process_is_active(pid)
    stderr = harness.close()
    events = [json.loads(line) for line in stderr.splitlines()]
    timeout_event = next(event for event in events if event["event"] == "worker_timeout")
    assert timeout_event["elapsed_ms"] >= 300
    assert harness.lines.empty()


@pytest.mark.parametrize("value", ["0", "abc", " 300", "100000000000"])
def test_invalid_job_timeout_environment_exits_cleanly(harnesses, tmp_path, value):
    harness = start(harnesses, tmp_path, env_extra={"MOTOR_IFC_SUPERVISOR_JOB_TIMEOUT_MS": value})
    stderr = harness.close()
    assert harness.process.returncode == 2
    assert harness.lines.empty()
    assert stderr == '{"event":"configuration_rejected"}\n'


def test_rss_budget_terminates_worker_with_typed_fault(monkeypatch):
    spawned = {}

    def spawn_sleeper():
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
        spawned["process"] = process
        return process

    monkeypatch.setattr(supervisor_module, "_process_rss", lambda process: 2_000_000_000)
    stdout = io.StringIO()
    stderr = io.StringIO()
    supervisor = Supervisor(1, stdout, stderr, _spawn=spawn_sleeper, job_timeout_ms=60_000, max_rss_bytes=1_000_000_000)
    supervisor.submit('{"jsonrpc":"2.0","id":1,"method":"test.success"}')
    deadline = time.monotonic() + 5
    while not stdout.getvalue() and time.monotonic() < deadline:
        time.sleep(0.01)
    response = json.loads(stdout.getvalue().strip())
    assert response == {"jsonrpc": "2.0", "id": 1, "error": {"code": -32802, "message": "Job exceeded resource budget"}}
    events = [json.loads(line) for line in stderr.getvalue().splitlines()]
    resource_event = next(event for event in events if event["event"] == "worker_resource")
    assert resource_event["rss_peak"] >= 1_000_000_000
    supervisor.shutdown()
    assert spawned["process"].wait(timeout=5) is not None


def test_recovery_removes_stage_and_temp_residue(tmp_path):
    root = tmp_path / "jobroot"
    nested = root / "a" / "b"
    nested.mkdir(parents=True)
    stage = root / "a" / ".out.stage-123"
    stage.mkdir()
    (stage / "extraction.json").write_text("{}", encoding="utf-8")
    nested_stage = nested / ".deep.stage-9"
    nested_stage.mkdir()
    temp = root / ".tmp.motor-ifc"
    (temp / "motor-ifc-reader-x").mkdir(parents=True)
    keep_file = root / "input.ifc"
    keep_file.write_text("IFC", encoding="ascii")
    keep_dir = root / "results"
    keep_dir.mkdir()
    dot_other = root / ".hidden"
    dot_other.mkdir()
    assert recover_job_root(root) == 3
    assert not stage.exists() and not nested_stage.exists() and not temp.exists()
    assert keep_file.is_file() and keep_dir.is_dir() and dot_other.is_dir()
    assert recover_job_root(root) == 0
    assert recover_job_root(root / "missing") == 0


def test_recovery_removes_readonly_snapshot_residue(tmp_path):
    # Reader/repair snapshots are chmod'd read-only (security.py); on Windows a
    # plain rmtree fails with WinError 5, so recovery must clear the bit.
    root = tmp_path / "jobroot"
    root.mkdir()
    temp = root / ".tmp.motor-ifc"
    snapshot = temp / "motor-ifc-reader-x"
    snapshot.mkdir(parents=True)
    snapshot_file = snapshot / "source.ifc"
    snapshot_file.write_text("IFC", encoding="ascii")
    snapshot_file.chmod(stat.S_IREAD)
    try:
        assert recover_job_root(root) == 1
        assert not temp.exists()
    finally:
        if snapshot_file.exists():
            snapshot_file.chmod(stat.S_IWRITE)


def test_supervisor_recovers_job_root_residue_on_startup(harnesses, tmp_path):
    stage = tmp_path / ".out.stage-dead"
    stage.mkdir()
    (stage / "partial.json").write_text("{}", encoding="utf-8")
    temp = tmp_path / ".tmp.motor-ifc"
    temp.mkdir()
    harness = start(harnesses, tmp_path, test_worker=False, env_extra={"MOTOR_IFC_JOB_ROOT": str(tmp_path)})
    harness.send("engine.capabilities.v1", "caps")
    assert harness.response()["result"]["engine_version"] == "0.1.0"
    assert not stage.exists() and not temp.exists()
    stderr = harness.close()
    assert any(json.loads(line).get("event") == "recovery" for line in stderr.splitlines())
