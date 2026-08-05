# ADR 0002: Promise semantic reproducibility, not STEP byte identity

**Status:** Accepted

## Decision

GlobalIds derive from a fixed namespace plus discipline, source ID, and semantic role. A canonical JSON projection produces the semantic fingerprint. Results record runtime versions and the source-to-GlobalId map.

## Consequences

Equivalent snapshots have stable semantic identity. STEP headers and IfcOpenShell serialization may differ, so byte-for-byte IFC equality is not a product guarantee. A converted GLB manifest records the artifact SHA-256 as identity for that output instance, but GLB byte equality across processes or platforms is not promised.
