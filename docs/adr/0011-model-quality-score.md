# ADR 0011: Element index and a model quality score that never refuses

## Decision

Add `element-index.v1` through `index_ifc_elements` and `element.index.v1`, and
`quality-score.v1` through `score_ifc_quality` / `derive_quality_verdict` and
`quality.score.v1`.

`element-index.v1` is a projection over the shared reader pipeline, not a second
reader. `reader-extraction.v2` reports every quantity set losslessly and states that
collision detection belongs to its consumer (ADR 0006); this contract is that
consumer. It selects one value per dimension per element, by name, in a declared order
of preference, and reports which set and which quantity name won. Unit resolution is
`reader_extraction._resolve_quantity_unit` — the same `ifcopenshell.util.unit` path v2
uses — never a second scale table.

`quality-score.v1` reads no IFC. It consumes an index result, or the materialized
scalars a caller already persisted, and returns issues, a score and a verdict.

## The threshold, and why it is not a floor

Calibrated 2026-07-29 against eight real public IFC files by running the reference
index, audit and gate over each. Measured scores:

```
AC20-Institute-Var-2      100.0  usable
AC20-FZK-Haus              95.1  usable
IFC_Schependomlaan         83.4  usable
---------------------------------------- 70.0
PCERT_Building-Arch_IFC4   38.5  partial (46.2% unmeasured)
PCERT_Building-Struct_IFC4 26.7  partial (33.3% unmeasured)
Duplex_A                    0.0  no element quantities at all
Esplanades                  0.0  no element quantities at all
ThatOpen_school_str         0.0  no element quantities at all
```

70.0 sits inside the observed 38.5 → 83.4 gap and separated the sample correctly, so it
stays. Its job is unchanged by that evidence: it decides DEGRADED against OK, and
DEGRADED still generates.

**This score is not a quantity metric and must never become the refusal instrument.**
`IFC_Schependomlaan` loses 16.6 points to missing materials and storeys on a model
whose elements carry base quantities 100% of the time, so a score floor would refuse a
perfectly measurable model — and would still admit a model whose only defect is that
nothing can be measured at all. What a model cannot measure is refused by
`MODEL_NOT_MEASURABLE`, an error-severity code, not by this number.

### Evidence status of the table, and what the scope did to it

Three of the eight rows are reproducible from files available here. All three were first
reproduced **to the decimal** over the reference application's 20-class scope. Aligning the
scope with buildingSMART then changed the population two of them are scored over:

| Row | Reference | Reproduced at 20 classes | Under the aligned scope | Why |
|---|---|---|---|---|
| `IFC_Schependomlaan` 83.4 | 83.4 | 83.4 (3 331 rows) | **77.1** (3 621 rows) | 277 `IfcBuildingElementPart` layers plus 13 rows the narrow scope never looked at |
| `PCERT_Building-Arch_IFC4` 38.5 | 38.5 | 38.5 (13 rows) | **35.7** (14 rows) | one `IfcChimney`, with a material and a storey but no quantities |
| `Duplex_A` 0.0 | 0.0 | 0.0 | **0.0** | no new classes present |

**Every verdict is unchanged** — OK, DEGRADED, BLOCKED respectively, and the same for the two
models outside the table. That is the property that had to survive: a score is only
comparable within a fixed scope, so the number moving is the scope being stated, while what
the threshold *decides* is untouched. Both numbers sit on the same side of 70.0.

The other five files — `AC20-Institute-Var-2`, `AC20-FZK-Haus`,
`PCERT_Building-Struct_IFC4`, `Esplanades`, `ThatOpen_school_str` — are **not present
on disk**, so their rows travel as **inherited evidence from the reference
application, dated and not re-measured here**. That is stated rather than papered over:
recovering those five public files, pinning them in the corpus and re-measuring all
eight is named follow-up work, not a silent assumption. No fixture was fabricated to
stand in for them.

`UNMEASURED_REFUSAL_SHARE = 0.5` carries the same status. The reference measured a
strongly bimodal sample — 0%, 0%, 0% for the usable models and 100% for the three with
no element quantities, with only 46.2% and 33.3% in between — and nothing at all
between 46.2% and 100%. A simple majority is the cut that can be stated as a rule
("most of the model cannot be measured") rather than a number fitted to one sample.

## What gets indexed, on buildingSMART's authority

buildingSMART states which entities carry quantities by defining a `Qto_*BaseQuantities`
template for them, and that set is the answer to "what should a takeoff look at". It is not
a matter of interpretation: IfcOpenShell ships the official templates
(`ifcopenshell/util/schema/Pset_IFC4_ADD2.ifc`), the same pinned dependency this engine
already runs on. IFC4 ADD2 defines **93 of them**.

The scope is derived from that set rather than invented. Three branch supertypes reach every
billable one — `IfcBuiltElement`/`IfcBuildingElement` (renamed in IFC4X3, so both are named),
`IfcDistributionElement` and `IfcElementComponent` — because `by_type` already returns every
subtype. `IfcSpace` is indexed separately as its own family.

| Deliberately out of scope | Reason |
|---|---|
| `IfcOpeningElement`, `IfcProjectionElement` | Modifiers of a host element. buildingSMART is explicit that an opening *"should not be linked directly to the spatial structure ... `ContainedInStructure` shall be NIL"*: its quantity exists to **deduct** from the wall it cuts, never to be billed alone. |
| `IfcSite`, `IfcBuilding`, `IfcBuildingStorey` | Accumulators. Their area already contains everything inside them, so billing them double-counts the whole model. |
| Construction resources (labour, material, equipment) | Cost and schedule side, not model geometry. |

`IfcStair` and `IfcRamp` have **no** `Qto_` template while `IfcStairFlight` and
`IfcRampFlight` do. buildingSMART confirms by omission what the container rule already
handles: a stair is an aggregate of flights, and its quantities live in them.

A test reads those templates, expands subtypes and asserts the engine's scope equals the
official set minus the exclusions above. The list is checkable against the standard rather
than asserted, and a future entity gaining a `Qto_` template fails the suite instead of
passing unnoticed.

## Rooms measure rooms

`Qto_SpaceBaseQuantities` does not define "an area": it defines **six**, plus perimeter,
height and volume. Each bills against a different item, and the specification says so —
`NetPerimeter` is defined as the measure *"used for skirting boards"*. Schependomlaan carries
the full vocabulary on all 100 of its rooms.

| Dimension | Accepted names, preferred first | Bills |
|---|---|---|
| `floor_area` | NetFloorArea, GrossFloorArea | flooring, screed |
| `wall_area` | NetWallArea, GrossWallArea | paint, tiling |
| `ceiling_area` | NetCeilingArea, GrossCeilingArea | ceilings |
| `perimeter` | NetPerimeter, GrossPerimeter | skirting |
| `height` | Height, FinishCeilingHeight, FinishFloorHeight | — |
| `volume` | NetVolume, GrossVolume | conditioning |

Element dimensions and room dimensions are disjoint: a room never reports the bare `area` a
wall reports, and a test asserts it both ways. Folding a room's floor area into an element's
`area` would price wall paint against floor tiles.

Rooms sit in their own `spatial` bucket, outside the headline denominator and outside the
scored population. A room has no material and is not "in" a storey the way a wall is, so
judging one by element rules is a category error; and folding Schependomlaan's 100 rooms
into its element score would move a number by changing what is counted rather than what is
true.

**`GSA BIM Area` is accepted; BOMA is refused.** IFC2X3 defines no `Qto_` template at all
(verified: zero in `Pset_IFC2X3.ifc`), so in that schema a vendor name is all there is —
Duplex_A carries its 21 room areas only as `GSA BIM Area`, a floor area from the GSA BIM
Guide. `SpaceNetFloorAreaBOMA` and `SpaceUsableFloorAreaBOMA` are named in the tables and
explicitly rejected, because absence alone would read as an oversight and invite someone to
add them: **BOMA measures rentable area, not work.** Its rules add and deduct floor area by
leasing criteria — cores, shared circulation, pro-rated commons — that correspond to no metre
anyone builds. Taking it as `floor_area` would bill surface nobody executes.

## Measurability, and why coverage needs its own denominator

A count of elements without quantities is not a count of unmeasured work. Measured across
five models — 4 440 elements, including two real projects of 11 MB and 49 MB — the raw
share is contaminated by three things that are not gaps:

| Contaminant | Measured example | Why it is not a gap |
|---|---|---|
| Container | PCERT's `IfcRoof "house - roof"` carries no geometry and decomposes into the two slabs that do measure. gymzaal's 27 `IfcCurtainWall` decompose into its 186 measured `IfcMember`. | Its parts hold its quantities; counting it reports double-count avoidance as a defect |
| Non-geometric | PCERT's `Group#18` / `Group#19`: empty `Representation` | A grouping node has nothing to measure |
| Countable | gymzaal's 22 `IfcSanitaryTerminal` with no quantity set | Billed per unit; existence is the quantity |

`element-index.v1` therefore classifies every element, from **structure before class and
never from its name**:

1. `IsDecomposedBy` non-empty → `container`
2. no `Representation` → `non_geometric`
3. `is_a` one of `IfcDoor`, `IfcWindow`, `IfcFlowTerminal`, `IfcFlowFitting` → `countable`
4. `IfcBuildingElementProxy` → `ambiguous`
5. otherwise → `structural`

A name rule was rejected deliberately. It would have to know that PCERT's `origin` and
`geo-reference` are survey markers while its `sand bedding` is real work — in one language,
from one exporter. All three carry geometry and all three are proxies, so all three land in
`ambiguous` and are reported rather than guessed. That bucket is genuinely mixed: 27 of
Schependomlaan's proxies carry quantities, 13 of gymzaal's are roof trim that does not.

`quality-score.v1` then computes coverage: `structural` and `ambiguous` are covered by
carrying any recognized quantity, `countable` always, and a `container` by its own
quantities or by all of its in-scope parts, resolved to a bounded depth of 8. Coverage never
demands a particular dimension — which dimension an item is billed in follows the caller's
unit, and choosing one here is underdetermined on real models that carry area, length and
volume at once.

Decomposition is read **both ways**, because a quantity lives at exactly one level of it. A
whole is covered by its own quantities or by all of its parts — the roof whose two slabs
measure is double counting avoided, not a gap. A part is covered by its own quantities or by
any ancestor that measures — the 257 layers inside a measured covering in Schependomlaan are
one measured covering, not 257 gaps. Both derive from what each element declares and never
from the other's derived value, so the two directions cannot talk each other into a coverage
neither earned.

| Model | Raw rows measured | **Billable coverage** | Rooms | Score |
|---|---|---|---|---|
| Schependomlaan (49 MB) | 100 % | **100 %** (3 594/3 594) | 100/100 | 77.1 |
| CAND (11 MB) | 100 % | **100 %** (448/448) | 82/82 | 100.0 |
| gymzaal | 87 % | **99.8 %** (477/478) | 48/48 | 48.7 |
| PCERT | 54 % | **88.9 %** (8/9) | 0/2 | 35.7 |
| Duplex_A | 0 % | **24.2 %** (38/157) | 21/21 | 0.0 |

PCERT reads 88.9 % rather than 100 % because the aligned scope found its chimney: a real
element carrying a material and a storey but no geometry and no quantities. That is why
`non_geometric` is restricted to `IfcBuildingElementProxy` — the class defined by having no
declared semantics — instead of any class lacking a body. A chimney nobody can measure is
information; `Group#18` is not.

`MODEL_NOT_MEASURABLE` now fires on billable coverage rather than the raw share, with
`UNMEASURED_REFUSAL_SHARE = 0.5` unchanged: the constant did not move, the denominator got
sharper. Verdicts are identical on every model available here — Duplex refuses, the other
four do not — and a test asserts that equivalence rather than assuming it.

The 70.0 threshold itself is **untouched**. `NO_QUANTITIES` is raised for a real measurement
gap — an element coverage reports as uncovered — rather than for every silent quantity set:
warning 277 times about layers inside a measured covering buries the one wall that genuinely
cannot be measured, which is the failure this contract exists to surface.

## Contract boundary

| Topic | Decision |
|---|---|
| Scope | The 20 most general building element classes. `by_type` already returns every subtype, so naming a class and its subtype would yield the same entity twice; `*StandardCase` entities arrive through their parent. A class the loaded schema does not declare is recorded in `skipped_types`, never swallowed. |
| Selection | One value per dimension, by name, most preferred first. Net before gross: net is deducted for openings, the conservative basis for costing. A canonical `BaseQuantities` / `Qto_*BaseQuantities` set wins over a vendor set **entirely** — mixing them would pair one exporter's net area with another's gross volume. Nested `IfcPhysicalComplexQuantity` sets are descended into. |
| Absence | An element with nothing readable is `quantity_source: "fallback"` with **no** quantities. Never a measured zero: several vendor quantities are legitimately 0.0 because they measure something else. |
| Units | Delegated to `ifcopenshell.util.unit` through the v2 resolver. `value` is the declared number and `unit` says what it is in; `normalized_value` is the SI value only when derivable. `count` is dimensionless and reported as already normalized. |
| Unresolved units | A selected quantity with no derivable SI value raises a `warning`-severity diagnostic (`UNRESOLVED_UNIT_SCALE`, 3001) carrying its `global_id`, and increments `unresolved_unit_scale_count`. The count is never truncated; the warnings are bounded at 100. A dropped SI value that says nothing is the defect that produces order-of-magnitude errors no later check finds. |
| Duplicates | The index keeps one record per `GlobalId` and reports `duplicate_global_id_count`. `quality-score.v1` raises `DUPLICATE_GUID` at model level, not per element: naming the survivors would accuse the wrong elements. |
| Measurability | Five states from structure before class, never from the element's name. Each record also carries `part_of_global_id` and `decomposes_into`, so a caller summing a container and its parts can see the double count that nothing else in the result would reveal. |
| Derived counts | A `countable` element with no quantity set reports `count = 1` under `quantity_source: "existence"`. Marked distinctly on purpose: the number was counted, not measured, and a derived number must never travel indistinguishable from one the exporter wrote. |
| Coverage buckets | `structural`, `countable` and `container` form the headline denominator. `ambiguous` is reported beside it, never inside it. `non_geometric` is excluded outright. An empty bucket reports `percent: null` — a percentage of nothing is neither 100 nor 0. |
| Limits | Material and classification summaries 300 characters, 1 000 storeys, 1 000 distinct classes, 100 000 quality issues. Byte, entity, node, depth, string and array bounds are inherited from the reader unchanged. |
| Determinism | Entities sort by `(global_id, ifc_class)` as v1 and v2; storeys by `global_id`; element types and skipped types sorted; dimensions in a fixed order; issue codes and messages sorted before they become facts. |
| Refusal | `refuses_generation` derives from `verdict` alone, and only `BLOCKED` (an error code) or `NOT_AUDITED` (no audit ran) refuse. The threshold is read in exactly one place, the branch separating OK from DEGRADED. |
| Verdict parity | The one-pass path over an index and the scalar path over persisted rows compute the same numbers, so they cannot disagree about the same model. |
| Transport | Inline under the reader's byte cap, otherwise one bounded immutable artifact under job root. The published `extraction.json` is byte-identical to the canonical inline document, so it round-trips into the DTO and can be scored without re-reading the model. |

## Non-goals

- No mapping, APU, tenant, budget or unit-catalogue knowledge. The index reports the
  dimensions an element actually has; deciding which dimension an item is priced in
  belongs to the caller, and the join belongs to the web layer.
- No aggregation of mapped quantity rows. Stripped of APU, tenant and unit identity —
  all of which this engine is forbidden to know — that operation is a group-by and a
  sum whose meaning lives entirely on the other side of the boundary.
- No `IfcConversionBasedUnit` claim. `ifcopenshell.util.unit` resolves it internally,
  but no known-answer case here exercises an imperial model, so no support is claimed.
- No overlap with `model-audit.v1`. That contract reports EXPRESS schema conformance;
  this one reports measurability and completeness. A model can be schema-perfect and
  score zero here, and a schema-defective model can be fully measurable.
- No geometry-derived quantities: only declared values, as ADR 0006 already fixed.
- No change to `reader-extraction.v1`, `reader-extraction.v2`, `ids-validation.v1`,
  `viewer-conversion.v1`, `model-audit.v1` or `model-repair.v1`.

## Consequences

A quantity now arrives as one number per dimension with the set and the name that
produced it, so a takeoff reads a value somebody measured rather than whichever
quantity happened to come last. The millimetre defect — a 6 m wall stored as 6000 and
billed as 6 000 linear metres, wearing a base-quantity provenance label that every
later check passes — is a pinned known-answer case on an official file
(`b01-pcert-ifc4.ifc` declares `LENGTHUNIT` as MILLI METRE while its areas and volumes
are already in metres). Mutating the normalization propagation drops exactly the two
tests that cover it and no others.

The shared reader pipeline gained two optional callbacks (`prepare`, `finalize`) with
defaults that reproduce the frozen behaviour, so v1 and v2 outputs are unchanged and
their schemas regenerate without diff.

Warnings now exist in `diagnostics.py`. Before this ADR the engine emitted only
errors, because no rule produced a warning; the unresolved-unit case is the first
thing an operation can state truthfully and cannot resolve.

## Rollback

Remove `element_index.py`, `quality_score.py`, their DTOs in `models.py`, the 3000/3100
diagnostic codes and the `warning` factory, the `element.index.v1` and
`quality.score.v1` dispatch entries and their param models, the two capability fields,
the two checked-in schemas, `tests/test_element_index.py`,
`tests/test_quality_score.py`, the README sections, and this document. Restore
`_extract_pipeline`'s hardcoded scope and contract version. Every other contract is
independent and unaffected.
