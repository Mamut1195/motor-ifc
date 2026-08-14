# ADR 0008: Bounded result publication for the semantic reader

## Decision

Large `reader-extraction.v2` results are transported as **bounded immutable artifacts** published under the job root, not as inline RPC payloads. `extract_ifc_semantic(path, projection, output_dir=None)` (`reader.extract.v2`) gains an optional `output_dir`: when present, the complete result is streamed per-entity into `extraction.json` plus an `extraction-manifest.json` through staging and one atomic rename (the `model-repair.v1` / `viewer-conversion.v1` publication pattern); the inline response stays a small typed summary. Inline responses are themselves bounded by a reader-owned byte cap, so a reader-valid result can never contradict the sidecar/supervisor transport. The v2 total node budget moves from the flat 100 000 that rejected real models to `MAX_TOTAL_NODES_V2 = 5_000_000`; `reader-extraction.v1` is untouched and frozen, including its 100 000 total budget.

## Contract boundary

| Topic | Decision |
|---|---|
| Transport | `reader.extract.v2` with `output_dir` publishes an immutable directory; without it the complete result returns inline. Pagination, cursors, and streaming protocols are rejected for this contract. |
| Inline bound | `MAX_INLINE_RESULT_BYTES = 900_000`, below the 1 000 000-byte RPC line cap with envelope margin. Breach is checked incrementally per entity and fails atomically with `LIMIT_EXCEEDED` (2003) whose suggested action points at `output_dir` publication. Applies to v2 only. |
| Publication layout | `extraction.json` is the canonical inline document (same bytes the inline result would produce for the same input: `publication="none"`, empty `artifact_filenames`, no self-hash) plus `extraction-manifest.json` (`motor-ifc.reader-publication-manifest.v1`: contract, projection, source/extraction SHA-256, entity count, artifact names, engine/contract/ifcopenshell versions). |
| Atomicity | Staging directory with `.stage-` prefix beside the target, one `os.replace`, staging removed on every exit path. Budget, unsupported-value, or source-change failures mid-stream publish nothing and leave no residue. |
| Provenance | Successful results carry `source_sha256`; publications additionally carry `extraction_sha256` of the artifact bytes. Inline v2 results carry `source_sha256` too. |
| Budgets | `MAX_TOTAL_NODES_V2 = 5_000_000` flat (fits measured real models: CAND 11 MB/1190 objects and Schependomlaan 49 MB/3823 objects both exceeded the old 100 000 cap) with `MAX_NODES_PER_ENTITY = 10_000` unchanged. Failure stays atomic: `truncated=false`, no partial entities. |
| Determinism | Artifact bytes depend only on input bytes and contract version: sorted entities, sorted normalized keys, compact separators, no timestamps or paths inside the document; manifest sorts keys. |
| v1 | `reader-extraction.v1` keeps its exact behavior, budgets, and inline-only transport; it predates the transport contradiction at its own scale and remains frozen. |

## Non-goals

- No pagination, cursors, or NDJSON streaming: no caller-visible iteration state, no protocol change to the single-line stdout contract.
- No v1 publication and no inline byte cap for v1.
- No RSS/CPU/time budgets and no cancellation during extraction yet; the node budget bounds output, while validation time on large files remains unbudgeted until the resource-budget roadmap unit.
- No artifact signing or retention policy; the manifest records hashes and versions only.
- The reader still never truncates: over-budget extraction is atomic failure, never a partial document.

## Consequences

A reader-valid response can no longer die in transport: inline v2 results are bounded below the RPC line cap by the reader itself, and anything larger must travel as a published artifact whose summary response is kilobytes. Real dirty models repaired through `model-repair.v1` become extractable end-to-end (`quantities`/`rich`) under the supervisor. Per-entity streaming removes the full-document JSON string from publication peak memory; the inline mode still materializes entities, which the resource-budget unit will budget explicitly.

## Rollback

Remove the `output_dir` parameter and publication branch from `reader_extraction.py`, restore `_extract_pipeline` and `_Counter` to their single-budget form, drop the two additive `ReaderExtractionResultV2` fields (`source_sha256`, `extraction_sha256`) and the widened `publication` literal, revert the RPC params and dispatch, regenerate checked-in schemas, delete the publication tests, and remove this ADR. v1, compiler, IDS, viewer, audit/repair, and supervisor contracts are unaffected.
