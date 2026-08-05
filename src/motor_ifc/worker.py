"""One-request JSON-RPC worker used by the process supervisor."""
from __future__ import annotations

import sys

from .rpc import MAX_RPC_LINE_BYTES, RpcFault, _response, handle_line, parse_line, valid_request_id


def main() -> None:
    raw = sys.stdin.buffer.readline(MAX_RPC_LINE_BYTES + 2)
    if not raw:
        return
    line = raw.decode("utf-8", errors="replace")
    request, error_response = parse_line(line)
    if error_response is not None:
        response = error_response
    elif request is None or "id" not in request or not valid_request_id(request.get("id"), allow_none=False):
        response = _response(None, fault=RpcFault(-32600, "Invalid Request"))
    else:
        response = handle_line(line)
    if response is not None:
        sys.stdout.write(response + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
