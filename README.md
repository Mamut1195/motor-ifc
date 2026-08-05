# motor-ifc

`motor-ifc` is a private, deterministic compiler from approved MAMUT authority snapshots to IFC. It does **not** design buildings, size systems, infer missing engineering decisions, or execute arbitrary model code.

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

The package also exposes `validate_ifc`, `validate_ids`, `convert_ifc_to_glb`, `extract_ifc`, `inspect_ifc`, and `build_federation`. All operations return typed results with stable diagnostics. An unavailable or unsupported optional runtime is an explicit error, never a fake success.

## Rich reader extraction

```python
from motor_ifc import extract_ifc

result = extract_ifc("model.ifc")
```

`reader-extraction.v1` reads an IFC without publishing or leaving files. It returns occurrences in the exact `IfcObject` scope having a non-empty `GlobalId`, including qualifying spatial objects and products while excluding relationships, resources, anonymous representation items, and type definitions. Each entity contains `global_id`, concrete `ifc_class`, nullable `name`, `description`, `object_type`, and `tag`, plus occurrence and inherited type property sets and quantities from IfcOpenShell 0.8.5. Third-party `id` keys are removed recursively. Geometry is not extracted.

Values are recursively normalized to JSON null, boolean, string, integer, finite number, ordered array, and lexicographically ordered object keys. Entity order is `(global_id, ifc_class)`. Numeric values and source array order are preserved; v1 performs no unit conversion and makes no inferred unit claim. Dictionary keys share the string-length bound. Unsupported entity references, binary/non-finite/cyclic values, schema-invalid IFC, or any limit breach fail the whole extraction with no entities and `truncated: false`; data is never silently partial or stringified.

The fixed limits are 100 MB input, 10,000 entities, 1,000 combined property/quantity sets per entity, 10,000 normalized nodes per entity, 100,000 total normalized nodes, depth 16, strings and dictionary keys 1,000 characters, and arrays 1,000 items. Each scalar and each dict/list container is one node, including empty containers; the six metadata fields and all requested rich-section roots and descendants consume the same budgets. Extraction opens only a private read-only snapshot of the bounded source bytes, runs IfcOpenShell schema validation before filtering entities, and verifies the held original source identity, size, mtime, and SHA-256 before success. Every result contains `contract_version`, `success`, `source_schema`, `entity_count`, `entities`, `diagnostics`, `truncated`, `publication: "none"`, and empty `artifact_filenames`; it exposes no paths, timestamps, raw exceptions, STEP IDs, or IfcOpenShell objects.

The sidecar method is `reader.extract.v1` with exactly `{"ifc_path":"relative/model.ifc"}` beneath `MOTOR_IFC_JOB_ROOT`. Existing legacy method names `extract_metadata`, `extract_properties`, and `extract_quantities` accept those same exact params and are compatibility projections over the same engine. They return the same versioned envelope and metadata on every entity, adding only the requested rich section. No older response shape was documented or implemented, so this mapping introduces no conflicting compatibility claim. `inspect_ifc` remains unchanged.

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
