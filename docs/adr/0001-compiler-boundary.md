# ADR 0001: Keep IFC compilation outside design authority

**Status:** Accepted

## Decision

`motor-ifc` compiles typed, approved snapshots. Domain engines remain authoritative for architecture, structural calculations, and MEP calculations. The compiler rejects missing or unsupported decisions rather than inventing or repairing them. For IDS, caller-supplied buildingSMART IDS 1.0 XML remains the requirements authority; motor-ifc only validates IFC against it and never authors, repairs, or infers IDS requirements.

## Consequences

Separate payload models prevent a universal optional-field model. Diagnostics and source identity mappings are mandatory. IDS validation returns only a bounded deterministic inline report and publishes no files, so compilation's immutable result directories remain untouched. IDS generation is outside the boundary.
