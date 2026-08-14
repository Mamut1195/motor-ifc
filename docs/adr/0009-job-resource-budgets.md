# ADR 0009: Job resource budgets and residue recovery

## Decision

Every supervised job runs under a hard wall-clock timeout and a working-set (RSS) budget enforced by the supervisor, and the supervisor owns residue recovery for its job root. `Supervisor` samples each direct child every 50 ms: exceeding `MOTOR_IFC_SUPERVISOR_JOB_TIMEOUT_MS` (default 180 000 ms, the tier-M initial proposal) terminates the child and answers `-32801 Job timed out`; exceeding `MOTOR_IFC_SUPERVISOR_MAX_RSS_BYTES` (default 4 GiB per job) terminates it and answers `-32802 Job exceeded resource budget`. Reader/repair snapshot temp directories move under `<job-root>/.tmp.motor-ifc` when a job root is configured, and supervisor startup sweeps orphaned `*.stage-*` publication stages and the contained temp directory from the job root. `model.inspect.v1` and `model.validate.v1` are unified with the rest of the RPC surface: job-root containment and the shared 100 MB input byte limit. All budgets are internal CI proposals, not SLOs or contractual guarantees.

## Contract boundary

| Topic | Decision |
|---|---|
| Timeout | Per-job wall clock measured in the supervisor from worker spawn. Default 180 s; env override validated as a positive integer ≤ 24 h, invalid configuration exits 2 with `configuration_rejected`. Terminal fault `-32801`, worker terminated through the existing graceful-then-force path. |
| RSS budget | Direct-child working set sampled every 50 ms via PSAPI `GetProcessMemoryInfo` on Windows (`/proc/pid/statm` where available; unmeasurable platforms disable enforcement, never crash). Default 4 GiB per job. Terminal fault `-32802`. Tree budgeting for descendants remains out of scope with the existing direct-child supervision model. |
| Fault ordering | Overflow keeps precedence (`-32012 Worker failed`), then timeout, then resource budget, then protocol validation; cancellation still claims the terminal response through the existing race resolution. |
| Containment | Worker snapshot temps use `<MOTOR_IFC_JOB_ROOT>/.tmp.motor-ifc` when the job root is configured and writable; otherwise the system temp default. Publication stages already live beside their job-root targets. |
| Recovery | On supervisor startup with a configured job root: remove every directory named `*.stage-*` (hidden staging marker) at any depth and the `.tmp.motor-ifc` directory; log one bounded `recovery` event with the removal count. Idempotent; regular files and non-marker directories are never touched. |
| Instrumentation | Terminal lifecycle events carry `elapsed_ms` and `rss_peak` (null when unmeasurable); event names: `worker_completed`, `worker_failed`, `worker_timeout`, `worker_resource`, `worker_cancelled`. Records stay ≤ 1024 bytes, redacted, numeric-only additions. |
| Boundary unification | `model.inspect.v1` / `model.validate.v1` now resolve `path` through `rpc_input` under the job root with the 100 MB limit, exactly like the reader; missing job root, traversal, oversize, and extra params are `-32602`. The legacy adapter subset keeps direct paths by documented contract. |
| IDS/viewer | Their time/memory exposure is bounded by the same job-level timeout and RSS budget; no per-operation budgets are added. |

## Non-goals

- No descendant-tree or job-object control; the RSS reading covers the direct child only (consistent with ADR 0004's supervision scope).
- No disk-quota enforcement (temporals ≤ 2× input + output remains a benchmark gate, not an enforced bound); recovery bounds residue, quotas await a dedicated unit.
- No per-operation budgets inside IDS validation or GLB serialization; the job-level budgets subsume them.
- No CPU-time limits and no NUMA/affinity policy.
- Budgets are not product SLOs: they are internal CI ceilings to be recalibrated on reference hardware with the fixed corpus (Ola 3-4).

## Consequences

Cancellation and timeout become observable and testable end-to-end: a 300 ms configured timeout terminates a stalled worker with `-32801` in under 2 s, and an over-budget worker dies with `-32802` leaving no live child. Force-killed jobs can no longer strand publication stages or snapshot copies inside the job root: the next supervised start removes them deterministically. Measured evidence on this workstation: CAND rich peaks at ~215 MB RSS / 22.7 s; Schependomlaan rich peaks at ~645 MB RSS / 86.9 s — both far inside the 4 GiB/180 s initial budgets, with validation still dominating runtime. RPC consumers of `model.inspect.v1`/`model.validate.v1` must send job-root-relative paths; absolute or out-of-root paths are now rejected.

## Rollback

Remove the timeout/RSS parameters, sampling loop, `-32801`/`-32802` faults, `_process_rss`/`_windows_memory_probe`, `recover_job_root` and the startup sweep, the `temp_root` indirection (restore system-temp `TemporaryDirectory` calls), and revert `model.inspect.v1`/`model.validate.v1` dispatch to direct paths; delete the associated tests and this ADR. Reader, compiler, IDS, viewer, audit/repair contracts and the cancellation lifecycle are unaffected.
