# ADR 0002: Promise semantic reproducibility, not STEP byte identity

**Status:** Accepted

## Decision

GlobalIds derive from a fixed namespace plus authority producer, model ID, discipline, source ID, and semantic role. Authority producer and model ID define the identity scope; revision, ruleset version, and source hash do not participate, so the same semantic entity remains stable across revisions within that scope. A canonical JSON projection produces the semantic fingerprint. Results record runtime versions and the source-to-GlobalId map.

## Consequences

Equivalent snapshots within the same authority/model scope have stable semantic identity. Reusing a local source ID in another model or authority scope produces a different GlobalId. STEP headers and IfcOpenShell serialization may differ, so byte-for-byte IFC equality is not a product guarantee. A converted GLB manifest records the artifact SHA-256 as identity for that output instance, but GLB byte equality across processes or platforms is not promised.
