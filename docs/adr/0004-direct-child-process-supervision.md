# ADR 0004: Supervise one direct child per correlated request

**Status:** Accepted

## Decision

`motor-ifc-supervisor` is a separate newline-delimited JSON-RPC executable. It remains responsive to stdin while one direct `motor_ifc.worker` child handles each valid request with a non-null ID. `motor-ifc-sidecar` remains the synchronous compatibility executable.

The request ID is the only job correlation authority. Active IDs are unique by decoded JSON type and value, so integer `1` and float `1.0` are distinct. There is no persistent queue or retry: one worker is allowed by default, an exact environment value can raise the bound to at most four, and excess requests fail deterministically.

`cancel_job` and its exact `job.cancel.v1` alias are supervisor-only notifications with exact params `{"id": <active-request-id>}`. The notification has no response. Both names share the same lifecycle path: cancellation wins or natural completion wins under one terminal-state lock, and the original request receives one response only. Cancellation targets only that direct worker and returns JSON-RPC error `-32800` after direct-child termination.

Termination sends the platform's graceful direct-child signal, waits 250 ms, then force-kills if necessary. Windows children use a new process group and `CTRL_BREAK_EVENT`. EOF stops admission and applies the same policy to every remaining direct child.

## Security and observability

The supervisor validates the existing 1,000,000-byte line and JSON-RPC request bounds before spawn. Worker argv is fixed and never enters a shell. Existing job-root and filesystem validation remains inside worker dispatch. Worker stdout is capped at 1,000,001 bytes including the line terminator and worker stderr at 65,536 bytes; overflow terminates the worker. Supervisor stdout is protocol-only; bounded structured lifecycle records go to stderr and exclude raw bodies, paths, environment values, child stderr, exception text, and stacks.

## Consequences

This vertical owns only direct Python workers, and those workers launch no descendants. Time and memory limits, descendant trees or Windows job objects, configurable termination policy, persistence, retries, and distributed cancellation remain unsupported. Removing the `job.cancel.v1` dispatch name, its tests, and its documentation rolls back the alias without changing `cancel_job`, the synchronous sidecar, or public IFC APIs.
