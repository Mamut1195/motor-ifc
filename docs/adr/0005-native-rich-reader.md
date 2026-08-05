# ADR 0005: Native bounded rich reader

## Decision

Expose `reader-extraction.v1` through `extract_ifc` and `reader.extract.v1`. Keep `inspect_ifc` unchanged and map the three previously unsupported legacy extraction names to explicit projections over the same engine.

## Contract boundary

| Topic | Decision |
|---|---|
| Scope | `IfcObject` occurrences with a non-empty `GlobalId`; spatial objects and products qualify, type definitions and non-objects do not. |
| Data | Bounded metadata, inherited/occurrence psets, and quantities; no geometry. |
| Runtime | Exactly IfcOpenShell 0.8.5; `get_psets` is adapted, never exposed raw. |
| Units | Preserve reported numbers; no conversion or inferred unit. |
| Determinism | Entities sort by `(global_id, ifc_class)` and every object key sorts lexicographically. |
| Completeness | Any schema error, unsupported value, or bound failure returns no entities with `truncated=false`. |
| Filesystem | Open one secure regular file read-only, extract only from a private bounded snapshot, recheck original identity/size/mtime/hash before success, and publish nothing. |

The bounds are 100 MB, 10,000 entities, 1,000 combined sets per entity, 10,000 normalized nodes per entity, 100,000 normalized nodes total, depth 16, 1,000-character strings and dictionary keys, and 1,000-item arrays. Every scalar and dict/list container is one node, including empty containers; metadata fields and requested rich-section roots and descendants share those budgets.

## Consequences

The stable DTO does not leak internal STEP IDs, runtime objects, exception text, paths, timestamps, or unstable third-party dictionary details. Unsupported values fail rather than being converted with `str` or `repr`. The legacy names now have an honest documented shape because they previously returned only an unsupported-method fault.

## Rollback

Remove `reader_extraction.py`, reader DTOs/diagnostics/API exports, reader RPC dispatch and legacy projections, associated tests, capability advertisement, and this documentation. Compilation, IDS, GLB conversion, inspection, sidecar framing, and supervisor lifecycle remain independent.
