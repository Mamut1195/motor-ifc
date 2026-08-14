# ADR 0010: Immutable benchmark corpus and independent correctness oracle

## Decision

The repository carries an immutable benchmark corpus under `corpus/` registered by `corpus/MANIFEST.json` (`motor-ifc.corpus-manifest.v1`), plus an independent correctness oracle for the semantic reader. Pinned models are byte-fixed by SHA-256; generated models (B04-B07) come from deterministic generators that normalize the STEP `FILE_NAME` timestamp and derive every GlobalId from an explicit integer seed, and a freshness test regenerates them byte-for-byte. The oracle's expected documents are built from the generator constants plus the official `ifcopenshell.util.unit` utility — never from `motor_ifc` code — and the reader's extraction must equal them 100% for every oracle case.

## Corpus layout

| ID | Kind | Content | Status |
|---|---|---|---|
| B01-pcert-ifc4 | official | buildingSMART PCERT baseline IFC4 (local copy of the budgeting-app fixture) | pinned |
| B02-community-ifc2x3 | community | Duplex Apartment (buildingsmart-community LFS fetch) | pinned |
| B03-community-ifc4 | community | Example project location / gymzaal (buildingsmart-community LFS fetch) | pinned |
| B04-oracle-{ifc4,ifc2x3,ifc4x3} | oracle | fixed quantity/material semantics per schema, with pinned expected JSON | pinned |
| B05-semantic-dense | stress | 2 000 walls × 40 qtos × 8 props, minimal geometry (~1M nodes, fits v2 budget) | pinned |
| B06-relation-dense | stress | 500 walls × 20 `IfcRelSpaceBoundary` fan-out | pinned |
| B07-geometry-dense | stress | 100 walls with extrusions + 64-triangle tessellations | pinned |
| B08-adversarial | adversarial | test-time builders: entity/array budgets N/N+1, depth bomb, garbage, truncated file | generated-at-test-time |
| real-cand-11m / real-schependomlaan-49m | real | pointers with pinned SHA-256 to the budgeting-app fixtures (not copied into the repo) | pointer |

## Contract boundary

| Topic | Decision |
|---|---|
| Immutability | A pinned file's SHA-256 in the manifest is authoritative; the manifest-integrity test fails on any byte drift. Regeneration is the only sanctioned mutation path and must reproduce identical bytes. |
| Determinism | Generators use no clocks (FILE_NAME timestamp rewritten to a fixed value) and no random sources (GlobalIds = `compress(seed)`). Entity ordering, normalized keys, and separators are already canonical in the reader contract. |
| Oracle independence | Expected documents derive from generator constants + `ifcopenshell.util.unit` (the official unit utility), not from `motor_ifc`. The rebuildability test compares checked-in expected files against a fresh in-memory rebuild. |
| Schema coverage | Oracle models exist for IFC4, IFC2X3, and IFC4X3; the oracle pins the IFC2X3 scope difference (`IfcProject` is an `IfcObject` there) and the attribute absences (`Formula`, material `Category`, layer `Name`/`Priority`, constituents). |
| Community provenance | B02/B03 are downloaded from the buildingsmart-community LFS store; their bytes are pinned immediately and the manifest records source path plus a license caveat (verify upstream terms before external redistribution). |
| Adversarial scope | B08 exercises documented bounds only (entity budget, array budget, depth, hostile bytes). Unsupported-value classes that EXPRESS cannot represent (raw bytes, non-finite floats, cycles) stay covered by the fake-runtime unit tests. |

## Non-goals

- No cryptographic signatures: "signed expected JSON" is realized as SHA-256 pins in the manifest; no key infrastructure.
- No license redistribution analysis beyond the manifest caveat; community models stay in-repo for internal benchmarking.
- No geometry-correctness oracle: B07 stresses parse/validate cost, not tessellation content; geometry-derived quantities remain an ADR 0006 non-goal.
- No CI hardware normalization yet: runtimes recorded so far are single-workstation evidence, not gate thresholds (Ola 4).

## Consequences

Correctness and coverage gates become testable on every run: the suite asserts 100% oracle equality (6 cases), manifest integrity, generator byte-reproducibility, and the measured behavior of every corpus member — including real-world transport behavior (B03 quantities is 1.02 MB and therefore publishes instead of returning inline). The corpus also surfaced contract-relevant schema facts that are now pinned: IFC2X3 project scoping, `IfcMaterialLayer`/`IfcMaterial` attribute absences, `IfcRelSpaceBoundary.PhysicalOrVirtualBoundary` mandatory in IFC4, and `IfcComplexProperty.UsageName` mandatory.

## Rollback

Delete `corpus/` (models, generators, expected documents, manifest) and `tests/test_corpus.py`. Nothing in `src/motor_ifc` depends on the corpus; all other contracts and suites are unaffected.
