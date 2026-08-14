# ADR 0006: Semantic quantities and materials contract

## Decision

Add `reader-extraction.v2` through `extract_ifc_semantic` and `reader.extract.v2` with projections `rich`, `metadata`, `properties`, `quantities`, and `materials`. `reader-extraction.v1` stays byte-stable: its flattened `get_psets` dictionaries, diagnostics, and projections are unchanged, and both versions share the same secure-open, private-snapshot, schema-validation, entity-ordering, node-budget, and atomic-failure pipeline.

## Contract boundary

| Topic | Decision |
|---|---|
| Quantities | Traversed from `IfcRelDefinesByProperties` (occurrence) and the relating type `HasPropertySets` (type), never via `get_psets` flattening. Each set keeps `global_id`, `name`, `description`, `method_of_measurement`, `source`, `relation_global_id`, and its quantities in STEP declaration order. |
| Quantity records | Keep IFC class, `formula` when the schema exposes it (IFC4), declared value plus measure type, `discrimination` for `IfcPhysicalComplexQuantity`, and recursive `components` for complex quantities. |
| Units | Three explicit levels: the quantity `Unit` attribute (`source="quantity"`), else the project unit for the measure class (`source="project"`), else `source="unknown"` with null fields. Name, symbol, SI prefix, and unit type are recorded; `normalized_value` is the SI value only when derivable through `ifcopenshell.util.unit` and is never invented. |
| Duplicates | Sets and quantities are ordered lists, never name-keyed dictionaries. Duplicate names across or within sets are all preserved; collision detection belongs to the consumer. |
| Precedence | Occurrence and type sets are both emitted with their source. A type set whose name collides with an occurrence set carries `shadowed_by_occurrence=true`; nothing is discarded. |
| Materials | `IfcRelAssociatesMaterial` traversed on occurrence and type with both sources visible. Supported forms: `IfcMaterial`, `IfcMaterialList`, `IfcMaterialLayerSet`(+`Usage`), `IfcMaterialProfileSet`(+`Usage`), `IfcMaterialConstituentSet`. Layers, profiles, and constituents keep their own attributes; usage keeps direction and offset. Unsupported relating-material forms fail atomically. |
| Determinism | Entities sort as v1; sets sort by `(source, name, global_id)`; associations by `(source, relation_global_id)`; quantities keep declaration order; dictionary keys sort lexicographically. |
| Completeness | Same node budgets as v1; semantic sections consume the shared per-entity and total counters, and any bound or unsupported value fails atomically with `truncated=false`. |

## Non-goals

- No geometry-derived quantities and no material-derived quantities; only declared values are reported.
- No material property sets (mechanical, thermal, or other psets hanging off materials); only material identity and composition.
- No unit algebra beyond the declared or project unit plus SI normalization through `ifcopenshell.util.unit`; no imperial/SI reinterpretation claims.
- No streaming, pagination, or limit changes; transport alignment and resource budgets remain separate roadmap units.
- No `reader-extraction.v1` behavior change; v1 consumers are unaffected.

## Consequences

A quantity now carries origin, unit, normalization, and measurement method, making it auditable instead of a bare number. Unknown units are explicit (`source="unknown"`, null normalization) rather than silently assumed. Consumers that only need the legacy flattened shape keep v1; consumers that need audit-grade semantics opt into v2 through capabilities.

## Rollback

Remove the v2 DTOs, `extract_semantic` and its helpers, `extract_ifc_semantic`, the `reader.extract.v2` dispatch, the v2 capability advertisement, the checked-in `reader-extraction-v2` schema, associated tests, and this documentation. `reader-extraction.v1` and every other contract remain independent.
