# ADR 0007: Model audit and drop-only repair

## Decision

Add an explicit audit → repair → extract pipeline for IFC models that fail the reader's strict schema gate. `model-audit.v1` (`audit_ifc`, `model.audit.v1`) reports every EXPRESS validation defect with a typed repair strategy. `model-repair.v1` (`repair_ifc`, `model.repair.v1`) applies the whitelisted `drop-instance` strategy only, revalidates, and publishes a repaired `.ifc` plus `repair-manifest.json` through staging and one atomic rename. The source file is never modified; the reader contracts stay strict and unchanged.

## Contract boundary

| Topic | Decision |
|---|---|
| Strategies | Whitelist with exactly one automatic strategy: `drop-instance`, allowed only for `IfcRelationship` subtypes. IFC relationships are referenced solely through inverse attributes, so removal never leaves a dangling stored reference. Every other defect class is `manual`. |
| Defect identity | Each defect carries `step_id` (when resolvable), `ifc_class`, `global_id` when the instance is rooted, the failing `attribute`, and a coarse `rule` (`missing-mandatory-attribute` / `schema-rule`). WHERE-rule statements resolve their instance by parsing `#id=ClassName` and verifying the class against `model.by_id`. |
| Repair outcome | All-or-nothing: the artifact publishes only when every defect is droppable and revalidation is clean. Manual defects or a dirty revalidation return typed failure with `remaining_defects` and publish nothing. |
| Publication | New immutable directory with `repaired.ifc` and `repair-manifest.json` (`motor-ifc.repair-manifest.v1`: source/repaired SHA-256, applied fixes, versions). A defect-free model returns `repaired=false` and publishes nothing. |
| Budgets | Input byte limit shared with the reader; `MAX_AUDIT_DEFECTS = 10,000` with atomic `LIMIT_EXCEEDED` beyond it. |
| Determinism | Defects sort by `(step_id, attribute)`; fixes drop by unique sorted `step_id`; results carry no timestamps. The repaired artifact preserves the original header. |

## Non-goals

- No invented values or references: no default filling, no semantic reconstruction, no geometry repair.
- No in-place source mutation and no repair of files IfcOpenShell cannot parse.
- No quarantine/tolerant mode in the reader contracts; extraction still requires a schema-clean model.
- No validation time/memory budgets yet; on a 49 MB real model validation costs 40-130 s and remains unbudgeted until the resource-budget roadmap unit.
- New automatic strategies require an amendment to this ADR.

## Consequences

Real-world models with defective relationships (observed: 8 `IfcRelSpaceBoundary` without `RelatedBuildingElement` in an 11 MB IFC4; 1 defective `IfcRelConnectsPathElements` in a 49 MB IFC2X3) become extractable after an auditable, evidence-producing repair. Removing space boundaries or connection paths does not alter quantities, properties, or materials, which are the reader's domain; the manifest records every removal so downstream consumers can judge impact.

## Rollback

Remove `model_repair.py`, the audit/repair DTOs and diagnostic codes, API/RPC dispatch, capability advertisement, checked-in schemas, associated tests, and this documentation. Reader, compiler, IDS, viewer, and supervisor contracts remain independent.
