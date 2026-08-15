# motor-ifc

`motor-ifc` is a deterministic compiler from approved MAMUT authority snapshots to IFC. It does **not** design buildings, size systems, infer missing engineering decisions, or execute arbitrary model code.

## Scope — read this before adopting

Claims here are graduated by tier against measured evidence, never by aspiration:

| Capability | Tier | State |
|---|---|---|
| Audit-grade quantity and material extraction (`reader.extract.v2`) | S/M | **Ready.** This is what the engine is for. |
| The same, on large models | L/XL | **Unevidenced.** Largest measured: 49 MB, 3,823 objects, ~1M nodes. |
| Element index with resolved quantities (`element.index.v1`) | S/M | **Ready.** Scope derived from buildingSMART's own `Qto_` templates and checked against them; known-answer cases on pinned corpus models; exercised end to end on a 49 MB model through publication. |
| Quantity evidence for a caller's model (`quantity.evidence.v1`) | S/M | **Ready.** Deterministic, bounded, no model and no network inside the engine. One ruling recovers a dimension on 1,419 elements of a 49 MB model (ADR 0012). |
| Model quality score (`quality.score.v1`) | — | **Ready, threshold partly inherited.** Three of its eight calibration files are reproducible here; five are not on disk, and the widened scope re-states two of the three. Every verdict is unchanged. See ADR 0011. |
| IFC authoring (`authoring.compile.v1`) | — | **Narrow verticals only**, see the paragraph below. Not a general authoring engine. |
| IDS validation (`ids.validate.v1`) | — | Real `ifctester` integration, exercised end to end by `tests/test_ids_validation_live.py` (marker `ids`). |
| Geometry / viewer conversion | — | Caveated: it produces a GLB, and its fidelity is not claimed. |

Largest model measured end to end: 49 MB, 3,823 objects, ~1M normalized nodes. Anything beyond that is untested, not "expected to work".

This is **not** a universal BIM engine. Becoming a general IFC engine would take geometric and spatial structure compilation, connected and typed MEP with materials and flow, and L/XL benchmarks without timeouts. None of that is done.

Every path that stages a private snapshot needs `MOTOR_IFC_JOB_ROOT`. Leaving it unset means the system temp directory, which is a deliberate default for standalone library use; setting it to something unusable is an explicit `JOB_ROOT_UNAVAILABLE` error, never a silent fallback that puts the snapshot outside the sandbox you asked for.

## Quick path

```powershell
python -m pip install -e ".[test,ifc]"
python -m pytest --basetemp=.tmp_pytest
motor-ifc-sidecar
motor-ifc-supervisor
```

The current vertical slices accept `authoring.v1` with `building.architecture@1` (one storey and rectangular walls), `building.structure@1` (type-only beams, columns, plates, and foundations), or `building.mep@1` (type-only generic distribution elements for approved HVAC, plumbing, and electrical components). A successful compile publishes one new immutable result directory containing the discipline IFC, `manifest.json`, `diagnostics.json`, and `source-map.json`. MEP products preserve approved source identity and name; the `system` value contributes to deterministic semantic identity but does not select an IFC product subtype. The MEP contract does not identify ducts, pipes, cables, terminals, equipment, distribution systems, ports, connections, placement, geometry, materials, sizes, or flow, so none are inferred.

## Public API

```python
from motor_ifc import capabilities, validate_snapshot, compile_snapshot
```

The package also exposes `validate_ifc`, `validate_ids`, `convert_ifc_to_glb`, `extract_ifc`, `extract_ifc_semantic`, `index_ifc_elements`, `collect_quantity_evidence`, `score_ifc_quality`, `derive_quality_verdict`, `audit_ifc`, `repair_ifc`, `inspect_ifc`, and `build_federation`. All operations return typed results with stable diagnostics. An unavailable or unsupported optional runtime is an explicit error, never a fake success.

## Rich reader extraction

```python
from motor_ifc import extract_ifc

result = extract_ifc("model.ifc")
```

`reader-extraction.v1` reads an IFC without publishing or leaving files. It returns occurrences in the exact `IfcObject` scope having a non-empty `GlobalId`, including qualifying spatial objects and products while excluding relationships, resources, anonymous representation items, and type definitions. Each entity contains `global_id`, concrete `ifc_class`, nullable `name`, `description`, `object_type`, and `tag`, plus occurrence and inherited type property sets and quantities from IfcOpenShell 0.8.5. Third-party `id` keys are removed recursively. Geometry is not extracted.

Values are recursively normalized to JSON null, boolean, string, integer, finite number, ordered array, and lexicographically ordered object keys. Entity order is `(global_id, ifc_class)`. Numeric values and source array order are preserved; v1 performs no unit conversion and makes no inferred unit claim. Dictionary keys share the string-length bound. Unsupported entity references, binary/non-finite/cyclic values, schema-invalid IFC, or any limit breach fail the whole extraction with no entities and `truncated: false`; data is never silently partial or stringified.

The fixed limits are 100 MB input, 10,000 entities, 1,000 combined property/quantity sets per entity, 10,000 normalized nodes per entity, 100,000 total normalized nodes, depth 16, strings and dictionary keys 1,000 characters, and arrays 1,000 items. Each scalar and each dict/list container is one node, including empty containers; the six metadata fields and all requested rich-section roots and descendants consume the same budgets. Extraction opens only a private read-only snapshot of the bounded source bytes, runs IfcOpenShell schema validation before filtering entities, and verifies the held original source identity, size, mtime, and SHA-256 before success. Every result contains `contract_version`, `success`, `source_schema`, `entity_count`, `entities`, `diagnostics`, `truncated`, `publication: "none"`, and empty `artifact_filenames`; it exposes no paths, timestamps, raw exceptions, STEP IDs, or IfcOpenShell objects.

The sidecar method is `reader.extract.v1` with exactly `{"ifc_path":"relative/model.ifc"}` beneath `MOTOR_IFC_JOB_ROOT`. Existing legacy method names `extract_metadata`, `extract_properties`, and `extract_quantities` accept those same exact params and are compatibility projections over the same engine. They return the same versioned envelope and metadata on every entity, adding only the requested rich section. No older response shape was documented or implemented, so this mapping introduces no conflicting compatibility claim. `inspect_ifc` remains unchanged.

## Semantic reader extraction

```python
from motor_ifc import extract_ifc_semantic

result = extract_ifc_semantic("model.ifc", "quantities")
```

`reader-extraction.v2` adds lossless semantic quantities and material associations while `reader-extraction.v1` stays byte-stable. Quantity sets are traversed from occurrence `IfcRelDefinesByProperties` and relating-type `HasPropertySets` rather than the flattened helper: each set keeps its identity, `method_of_measurement`, occurrence/type `source`, and `relation_global_id`, and each quantity keeps its IFC class, declared value with measure type, `formula` when the schema exposes it, and recursive components for complex quantities. Units resolve in three explicit levels — the quantity `Unit` attribute, else the project unit, else `source: "unknown"` — recording name, symbol, SI prefix, and unit type, with `normalized_value` set to the SI value only when derivable. Sets and quantities are ordered lists, never name-keyed dictionaries: duplicates are preserved, and a type set shadowed by a same-named occurrence set carries `shadowed_by_occurrence: true`. Materials cover `IfcMaterial`, material lists, layer sets and usages, profile sets and usages, and constituent sets on both occurrence and type. Projections are `rich`, `metadata`, `properties`, `quantities`, and `materials`; the same snapshot, validation, node budgets, determinism, and atomic-failure rules as v1 apply. The sidecar method is `reader.extract.v2` with `{"ifc_path":"relative/model.ifc","projection":"rich"}`; `projection` is optional. Geometry-derived quantities, material property sets, and unit algebra beyond declared/project plus SI normalization are explicit non-goals (ADR 0006).

## Element index

```python
from motor_ifc import index_ifc_elements

result = index_ifc_elements("model.ifc")
```

`element-index.v1` reduces a model to one measured number per dimension per element. What it
indexes is derived from buildingSMART rather than chosen: the entities for which IFC4 ADD2
defines a `Qto_*BaseQuantities` template, reached through three branch supertypes. Openings
and projections are excluded because their quantity deducts from a host and buildingSMART
requires their `ContainedInStructure` to be NIL; sites, buildings and storeys because they
already contain everything inside them; construction resources because they are cost, not
geometry. A test reads those official templates and fails if the scope drifts from them.
`reader-extraction.v2` reports every quantity set losslessly and leaves collision
detection to its consumer (ADR 0006); this contract is that consumer, and it is a
projection over the same reader pipeline rather than a second reader. `by_type` already returns every subtype, so `*StandardCase` entities arrive through their
parent, and a class group whose every schema alias is missing is recorded in `skipped_types`
instead of aborting the read. Each
record carries `global_id`, `ifc_class`, name, storey, a bounded material summary,
classification, the winning quantity set, and the selected quantities. The result adds
`source_schema`, `project_name`, `storeys`, `element_types`, `skipped_types` and
`duplicate_global_id_count`.

Each record also carries a `measurability` — `structural`, `countable`, `container`,
`ambiguous` or `non_geometric` — decided from decomposition and geometry before class, and
never from the element's name. A `countable` element with no quantity set reports
`count = 1` under `quantity_source: "existence"`, marked distinctly because that number was
counted rather than measured. `part_of_global_id` and `decomposes_into` expose the
decomposition both ways, so a caller summing a curtain wall and its members can see the
double count.

Rooms are indexed as their own family. `Qto_SpaceBaseQuantities` defines six areas rather
than one, so a space reports `floor_area`, `wall_area`, `ceiling_area`, `perimeter`, `height`
and `volume` — flooring, paint, ceilings and the perimeter the specification names for
skirting boards. Room dimensions and element dimensions are disjoint: a room never reports
the bare `area` a wall reports. `GSA BIM Area` is accepted as a vendor floor area because
IFC2X3 defines no quantity template at all, while `SpaceNetFloorAreaBOMA` and
`SpaceUsableFloorAreaBOMA` are named and refused — BOMA measures rentable area under leasing
rules, not work anyone executes.

Quantities are selected by name in a declared order of preference, never by whichever
one came last: net before gross, because net is deducted for openings and is the
conservative basis for costing. A canonical `BaseQuantities` or `Qto_*BaseQuantities`
set wins over a vendor set entirely, so one exporter's net area is never paired with
another's gross volume, and sets nested under `IfcPhysicalComplexQuantity` are
descended into. An element with nothing readable is `quantity_source: "fallback"` with
no quantities at all — never a measured zero, since several vendor quantities are
legitimately 0.0 because they measure something else. Units resolve through
`ifcopenshell.util.unit`: `value` is the declared number, `unit` says what it is in,
and `normalized_value` is the SI value only when derivable. `count` is dimensionless
and reported as already normalized. A selected quantity whose unit will not resolve
produces a `warning`-severity `UNRESOLVED_UNIT_SCALE` diagnostic carrying its
`global_id` and increments `unresolved_unit_scale_count`; a dropped SI value that says
nothing is the defect that produces order-of-magnitude errors no later check finds.

The fixed limits are 300 characters for material and classification summaries, 1,000
storeys, 1,000 distinct element classes, and 100 inline unit-scale warnings — the
warning count itself is never truncated. Input bytes, entity count, per-entity and
total nodes, depth, string length and array length are the reader's, unchanged.
Entities sort by `(global_id, ifc_class)`, storeys by `global_id`, and dimensions in a
fixed order, so two runs of the same file produce the same document. Any limit breach
fails the whole index with no entities and `truncated: false`.

Projections are `index` (default) and `rich`, which adds single-value property sets.
Results travel inline under the reader's byte cap; anything larger fails with a typed
diagnostic pointing at publication, and an `output_dir` publishes one immutable
directory containing `extraction.json` and `extraction-manifest.json`. That artifact is
byte-identical to the canonical inline document, so it validates straight back into
`ElementIndexResult` and can be scored without re-reading the model. The sidecar method
is `element.index.v1` with `{"ifc_path":"relative/model.ifc","projection":"index","output_dir":null}`;
`projection` and `output_dir` are optional and both paths stay contained beneath
`MOTOR_IFC_JOB_ROOT` (ADR 0011).

## Quantity evidence, and caller-decided quantities

```python
from motor_ifc import collect_quantity_evidence, index_ifc_elements

question = collect_quantity_evidence("model.ifc")
result = index_ifc_elements("model.ifc", decisions=confirmed_rulings)
```

The selection tables are conservative, so most of a real file's numbers are dropped:
Schependomlaan declares 153,815 quantities and 128,310 of them sit under 536 names no table
claims. Most of that is correctly discarded — `Home Offset`, `Elevation to Project Zero` —
but the same list holds `Net Surface Area on the Outside Face` 1,419 times, which is the
paint area of a wall's outer face. Separating the two cannot be tabulated in advance: they
are exporter dialects, in several languages, different per project.

`quantity-evidence.v1` states that question instead of answering it. It reports a
**vocabulary** of dropped names — with occurrences, the classes carrying them, a sample
value with its resolved unit, how many distinct elements the name would reach, and
`competes_with`, the quantity already selected for that measure or null when nothing
measures that dimension at all — and **element groups** a ruling could change, with their
object types, type name and property sets. Only what a ruling could move is listed: a
container is measured through its parts and a countable element through its existence, so
neither is undecided. Groups sort by whether they are worth deciding and carry a cumulative
percentage as a stop signal. Bounded at 200 names and 200 groups ordered by reach, with the
tail declared as a count.

`quantity-decisions.v1` is the answer, supplied by the caller exactly as IDS requirements
are: the engine validates and applies it and never generates one. A ruling maps a declared
name to a dimension or marks it not a measurement, and may rule an element class billable.
It adds dimensions the tables never claimed and overrides none they did. **Not one field of
the contract is numeric** — a test walks the generated JSON Schema to prove it — so a model
filling it in has nowhere to put an invented quantity; the value always comes from the file.
Everything a ruling selects carries `decided_by`, and a ruling matching nothing in the model
raises a warning rather than passing silently. Without a document the behaviour is
byte-identical to the frozen one.

**No language model runs inside this engine, and it gains no network dependency.** The
sidecar methods are `quantity.evidence.v1` with exactly `{"ifc_path":"relative/model.ifc"}`
and `element.index.v1` with an optional `decisions_path` bounded at 5 MB beneath
`MOTOR_IFC_JOB_ROOT`. Which model answers, at what cost, under what prompt, is the caller's
business entirely (ADR 0012).

## Model quality score

```python
from motor_ifc import index_ifc_elements, score_ifc_quality, derive_quality_verdict

report = score_ifc_quality(index_ifc_elements("model.ifc"))
```

`quality-score.v1` answers a different question from `model-audit.v1`. That contract
reports EXPRESS schema conformance — whether the file is a legal IFC. This one reports
measurability and completeness: whether the model carries the facts a quantity takeoff
needs. A model can be schema-perfect and score zero here, and a schema-defective model
can be fully measurable. It reads no IFC and needs no optional runtime.

`score_ifc_quality` takes an index result and returns issues, a score and a verdict in
one pass. `derive_quality_verdict` takes the materialized scalars instead — element
totals, distinct error and warning codes, model-level error messages, issue count — so
a caller that persisted its issues can re-derive the same verdict without re-auditing.
Both paths compute the same numbers, so they cannot disagree about the same model. The
five verdicts are `ok`, `degraded`, `blocked`, `not_audited`, and `not_applicable` for
a synthetic model that never had an audit step to skip.

**The score never refuses anything.** `refuses_generation` derives from `verdict`
alone, and only `blocked` — which requires an error-severity code — or `not_audited`
refuse. The threshold of 70.0 is read in exactly one place, the branch separating `ok`
from `degraded`, and `degraded` still generates. A model with 100% base quantities and
missing materials loses real points and still passes; a model that cannot be measured
is refused by `MODEL_NOT_MEASURABLE` even with a high score. Three of the eight
calibration files behind that threshold are reproduced here to the decimal (83.4, 38.5,
0.0); the other five are not on disk, so their rows travel as inherited, dated,
un-remeasured evidence rather than as a claim. See ADR 0011 for the table, the warning
it carries, and why a score floor would refuse a perfectly measurable model.

**Coverage is the number to act on.** `score` mixes every element-level defect together
and stays as calibrated; `coverage` reports measurability alone, over a denominator that
holds only what could have been measured. A count of elements without quantities is not a
count of unmeasured work: a *container* like a roof or a curtain wall keeps its quantities
in the parts it decomposes into, a *non-geometric* grouping node has nothing to measure,
and a *countable* door or fitting is billed per unit whether or not the exporter wrote a
number. Decomposition counts both ways: a whole is covered by its parts, and a part by any
whole that measures, so the 257 layers inside a measured covering are one covering rather
than 257 gaps. Rooms report in a `spatial` bucket of their own, beside the element headline
and never inside it. Each element is classified from structure — decomposition and geometry — before
class, and never from its name; `IfcBuildingElementProxy` stays explicitly *ambiguous* and
is reported beside the headline rather than inside it. The result gives per-bucket totals,
`coverage_percent`, and `uncovered_by_class`: the grouped, ordered list of what is actually
missing. Measured on the pinned corpus: Schependomlaan and CAND 100%, gymzaal 99.8%, PCERT 88.9%,
Duplex 24.2%, where the raw element share read 100/100/87/54/0.

`MODEL_NOT_MEASURABLE` fires on billable coverage rather than the raw share, with the same
calibrated 0.5 majority — the constant did not move, the denominator got sharper, and the
verdicts are identical on every model available.

The fixed bounds are 100,000 issues per model, 100 reported uncovered groups, and a
container chain resolved 8 deep; exceeding the issue bound fails the scoring. The
contract is read-only: `publication` is `"none"` and `artifact_filenames` is empty. The
sidecar method is `quality.score.v1` with exactly `{"facts":{...}}` and needs no job
root, because it touches no files — that path returns a verdict without coverage, which
needs the elements themselves.

## Model audit and repair

```python
from motor_ifc import audit_ifc, repair_ifc

report = audit_ifc("model.ifc")
result = repair_ifc("model.ifc", "repair-result")
```

Real IFC files often fail the reader's strict schema gate. `model-audit.v1` reports every EXPRESS validation defect with `step_id`, `ifc_class`, `global_id` when rooted, failing `attribute`, coarse `rule`, and a typed `repair_strategy`, plus structural counts and the source SHA-256. It is read-only and publishes nothing. `model-repair.v1` applies the whitelisted `drop-instance` strategy only — defective `IfcRelationship` instances, which are referenced solely through inverse attributes — then revalidates and publishes one new immutable directory containing `repaired.ifc` and `repair-manifest.json` (`motor-ifc.repair-manifest.v1`: source/repaired SHA-256, applied fixes, versions). The source is never modified, values or references are never invented, non-relationship defects are reported as `manual` and fail the repair without publication, and a defect-free model returns `repaired: false` with no artifacts (ADR 0007). The sidecar methods are `model.audit.v1` with exactly `{"ifc_path":"relative/model.ifc"}` and `model.repair.v1` with exactly `{"ifc_path":"relative/model.ifc","output_dir":"relative/repair-result"}`, both contained beneath `MOTOR_IFC_JOB_ROOT`.

## IDS validation

Install the isolated IDS runtime with `python -m pip install ".[ids]"`. This pins `ifctester==0.8.5` and `ifcopenshell==0.8.5`; non-IDS consumers do not receive IfcTester or its reporting/XML dependencies.

```python
from motor_ifc import validate_ids

result = validate_ids("model.ifc", "caller-requirements.ids")
```

`ids-validation.v1` validates IFC against caller-supplied buildingSMART IDS 1.0 XML. The caller remains the requirements authority; motor-ifc does not generate, repair, or infer IDS requirements. `success` reports whether validation completed, while `valid` reports whether all IDS specifications passed. The inline result includes a bounded aggregate summary and one bounded row per specification. It excludes third-party exception text, absolute paths, entity details, and the unstable raw IfcTester report.

The fixed bounds are 100 MB per IFC, 5 MB per IDS, 100 specifications, 1,000 requirements, 100,000 checks, and 500 characters for returned title/name text. DTD and entity declarations are rejected before the optional parser runs. IDS validation is read-only: `publication` is `"none"`, `artifact_filenames` is empty, no report filename exists, and no compile result directory is created or mutated.

The sidecar exposes one matching method: `ids.validate.v1` with exactly `{"ifc_path":"relative/model.ifc","ids_path":"relative/requirements.ids"}`. Both paths must be existing relative regular files beneath `MOTOR_IFC_JOB_ROOT`; absolute paths, traversal, symlink/reparse components, missing files, oversized files, and extra fields are rejected.

## GLB conversion

Install `motor-ifc[ifc]`, which provides the required IfcOpenShell 0.8.5 runtime. Conversion uses its in-process `GltfSerializer`; no executable or shell command is invoked.

```python
from motor_ifc import convert_ifc_to_glb

result = convert_ifc_to_glb("model.ifc", "viewer-result", "building.glb")
```

`viewer-conversion.v1` reads a caller-owned IFC and publishes a separate new immutable conversion-result directory. It never modifies or augments a compile-result directory. Success publishes exactly three files: the caller-named `.glb`, `manifest.json`, and `diagnostics.json`. No source map is fabricated.

`manifest.json` uses schema `motor-ifc.viewer-conversion-manifest.v1` and contains `contract_version`, source filename/SHA-256, artifact filename/media type/byte count/SHA-256, and engine/IfcOpenShell versions. Artifact SHA-256 identifies that output instance; GLB bytes are not promised to be reproducible across processes or platforms. `diagnostics.json` is exactly `{"diagnostics": []}` on success.

The GLB filename must be one platform-safe basename ending in `.glb`: at most 120 Unicode characters and 255 UTF-8 bytes, with no separators, traversal, control characters, Windows-special characters, trailing dot/space, or reserved device name. API paths are at most 500 characters, source IFC is at most 100 MB, and generated GLB is at most 500 MB when measured after serialization. Existing destinations, symlink/reparse components, non-regular or malformed sources, unsupported runtime versions, and partial publication are rejected.

The sidecar method is `viewer.convert.v1` with exactly `{"ifc_path":"relative/model.ifc","result_dir":"relative/viewer-result","glb_filename":"building.glb"}`. Both paths remain contained beneath `MOTOR_IFC_JOB_ROOT`; the source must exist and the result must not. Extra fields are rejected.

## Safety boundary

- Each output path must be a new immutable job-result directory; existing files, directories, symlinks, and reparse points are rejected.
- Compilation and GLB conversion stage sibling directories on the same volume and publish each complete artifact set with one atomic rename.
- RPC compilation requires `MOTOR_IFC_JOB_ROOT` and a relative output path beneath that root; symlink/reparse ancestors are rejected.
- Snapshot bytes, element counts, paths, and revisions are bounded and validated.
- Both newline-delimited JSON-RPC executables bound input lines at 1,000,000 bytes and reserve stdout for protocol responses. `motor-ifc-sidecar` remains the synchronous compatibility entry point. `motor-ifc-supervisor` is the responsive isolated-worker entry point described below.
- Semantic fingerprints are reproducible. Raw IFC SHA-256 values identify individual artifacts but may differ across equivalent compiles because IfcOpenShell STEP headers can vary.
- Network isolation, time/memory limits, descendant process-tree control, retries, persistence, IDS generation, geometric/spatial structure compilation, and richer typed or connected MEP compilation remain roadmap work.

## Process supervisor

Run `motor-ifc-supervisor` when the host needs cancellable request isolation. Each valid ordinary JSON-RPC request with a non-null string or finite numeric `id` starts one direct child using the fixed argv `sys.executable -m motor_ifc.worker`. Numeric IDs are type-sensitive: JSON integer `1` and JSON float `1.0` are distinct for duplicate detection, response correlation, and cancellation. The child validates and handles exactly that request, emits exactly one response line, and exits. The existing filesystem and `MOTOR_IFC_JOB_ROOT` checks execute inside that child. Notifications other than cancellation are not executed by the supervisor; use `motor-ifc-sidecar` where synchronous notification compatibility is required.

The correlation and cancellation contract is exact:

```json
{"jsonrpc":"2.0","method":"cancel_job","params":{"id":"request-42"}}
{"jsonrpc":"2.0","method":"job.cancel.v1","params":{"id":"request-42"}}
```

`cancel_job` and its exact `job.cancel.v1` alias are supervisor-only notifications. For either name, `params` must contain only the same valid non-null request ID, and the notification emits no response of its own. If that ID is active, only its direct worker is targeted and the original request receives exactly one `-32800` `Request cancelled` error after the child exits. A completion/cancellation race produces either the natural response or cancellation, never both. Duplicate active IDs receive `-32010`; capacity rejection receives `-32011`; worker spawn, crash, or protocol failure receives `-32012`. IDs become reusable after terminal cleanup.

Concurrency defaults to one active worker. `MOTOR_IFC_SUPERVISOR_MAX_WORKERS` may be exactly `1`, `2`, `3`, or `4`; absent means `1`, and any other value rejects startup with exit code 2. There is no queue or retry. On cancellation and EOF shutdown, the supervisor sends a graceful direct-child signal, waits a fixed 250 ms, then force-kills a still-running child. On Windows the graceful signal is `CTRL_BREAK_EVENT` to a new process group. This contract owns only the direct Python worker, which launches no descendants.

Lifecycle records are compact JSON lines on stderr with an event name, an optional 12-character hash of the request ID, and bounded status fields. They never contain raw request bodies, paths, environment values, child stderr, exception text, or stacks. Worker stdout is capped at 1,000,001 bytes including its line terminator; worker stderr is capped at 65,536 bytes. Overflow terminates the child and returns `-32012` without forwarding child output. Supervisor stdout contains protocol response lines only. EOF stops admission, terminates all direct children with the same policy, suppresses unfinished shutdown responses, cleans up, and exits.

See [`docs/adr`](docs/adr) for decisions and [`docs/PROVENANCE.md`](docs/PROVENANCE.md) for the clean-room boundary.

## License

Apache License 2.0 — see [`LICENSE`](LICENSE). Copyright 2026 MAMUT.

Domain knowledge was translated, never copied, from private MAMUT
applications; those repositories are not covered by this license and nothing
was committed back to them. See `docs/PROVENANCE.md`.
