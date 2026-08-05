# ADR 0003: Publish result artifacts as one recoverable set

**Status:** Accepted

## Decision

Compilation stages every artifact in a new sibling directory, reopens and validates the IFC, then publishes the complete immutable compile-result directory with one atomic rename. GLB conversion independently stages and validates its GLB, manifest, and diagnostics in a sibling directory, then publishes a separate immutable conversion-result directory with one atomic rename. A conversion never modifies or augments a compile result. Every requested result directory must not already exist.

## Consequences

A failed attempt does not expose partial new output or overwrite an existing result. Sibling staging keeps publication on one filesystem; symlink and reparse-point output paths are rejected.
