"""Test-only supervisor construction with an injected hostile worker factory."""
import os
import subprocess
import sys

from motor_ifc.supervisor import Supervisor, _max_workers


def _spawn_test_worker() -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, "-m", "tests.supervisor_worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )


def main() -> None:
    try:
        supervisor = Supervisor(_max_workers(), sys.stdout, sys.stderr, _spawn=_spawn_test_worker)
    except ValueError:
        sys.stderr.write('{"event":"configuration_rejected"}\n')
        sys.stderr.flush()
        raise SystemExit(2)
    supervisor.run(sys.stdin.buffer)


if __name__ == "__main__":
    main()
