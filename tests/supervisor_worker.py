"""Hostile worker fixture injected only by tests.supervisor_runner."""
import json
import os
import signal
import sys
import time


request = json.loads(sys.stdin.readline())
method = request["method"]
pid_file = os.environ.get("MOTOR_IFC_TEST_PID_FILE")
if pid_file:
    with open(pid_file, "w", encoding="ascii") as stream:
        stream.write(str(os.getpid()))

if method == "test.crash":
    os._exit(7)
if method == "test.uncooperative":
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, lambda *_: None)
    else:
        signal.signal(signal.SIGTERM, lambda *_: None)
    time.sleep(30)
if method == "test.slow":
    time.sleep(30)
if method == "test.race":
    time.sleep(0.01)
if method == "test.stderr":
    sys.stderr.write("SECRET request body C:\\private\\model.ifc\nTraceback: forbidden\n")
    sys.stderr.flush()
if method == "test.large_stdout":
    sys.stdout.write("x" * 1_100_000)
    sys.stdout.flush()
    time.sleep(30)
if method == "test.large_stderr":
    sys.stderr.write("S" * 100_000)
    sys.stderr.flush()
    time.sleep(30)
if method == "test.malformed":
    print("not-json", flush=True)
    raise SystemExit
if method == "test.extra":
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": {"ok": True}}), flush=True)
    print("extra", flush=True)
    raise SystemExit
if method == "test.nan":
    print('{"jsonrpc":"2.0","id":1,"result":{"value":NaN}}', flush=True)
    raise SystemExit
if method == "test.wrong_id_bool":
    print('{"jsonrpc":"2.0","id":true,"result":{}}', flush=True)
    raise SystemExit
if method == "test.wrong_id_float":
    print('{"jsonrpc":"2.0","id":1.0,"result":{}}', flush=True)
    raise SystemExit

print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": {"ok": True}}, separators=(",", ":")), flush=True)
