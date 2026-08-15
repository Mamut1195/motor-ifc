# ADR 0012: The engine asks; the caller's model rules on names, never on numbers

## Decision

Add `quantity-evidence.v1` through `collect_quantity_evidence` and `quantity.evidence.v1`,
and `quantity-decisions.v1` as an optional caller-supplied document on `element.index.v1`
(`decisions_path`).

**No model of any kind runs inside this engine.** It gains no network dependency; the only
hard dependency remains `pydantic`. What it gains is a way to state the question it cannot
answer, and to apply an answer as authority over *naming*.

## Why a table cannot close this

The selection tables of ADR 0011 take quantities by name, conservatively, and drop what they
do not recognise. Measured on the pinned corpus, that is most of the numbers:

| Model | Quantities declared | Dropped as unrecognised | Distinct names |
|---|---|---|---|
| Schependomlaan (49 MB) | 153 815 | 128 310 | 536 |
| CAND (11 MB) | 36 002 | 30 849 | 231 |
| gymzaal | — | — | 12 |

Most of that is rightly dropped — `Home Offset` ×1 519, `Elevation to Project Zero` ×3 444,
`Number of Doors` ×1 519. But the same list holds `Net Surface Area on the Outside Face`
×1 419 and `Net Surface Area on the Inside Face` ×1 419, which are the paint areas of a
wall's two faces, and `Surface Area` ×3 444.

Separating those two lists is not tabulation. They are exporter dialects, in several
languages, different per project; ArchiCAD alone contributes an English-Dutch vocabulary
that no enumeration written in advance would have contained. The same holds for
`IfcBuildingElementProxy`: in PCERT the proxies are survey markers, in gymzaal they are
`Fascia:41_ROF_daktrim aluminium` — aluminium roof trim, real work — and in Schependomlaan
27 of them carry quantities. No structural signal separates them; reading the name and the
property sets does.

That is a judgement, and it is the one thing a language model is genuinely better at than
this engine. So the engine states it and gets out of the way.

## Contract boundary

| Topic | Decision |
|---|---|
| Division of authority | The caller owns the ruling, the engine owns the reading — the same split `ids-validation.v1` already draws with caller-supplied requirements. The engine never generates, infers or repairs a decisions document. |
| The question | `quantity-evidence.v1` reports a **vocabulary** of names the tables dropped and **element groups** a ruling could change. Read-only, inline-only, `publication: "none"`. |
| `competes_with` | Names the quantity already selected for that measure, or null when nothing measures that dimension. "There is a better name in use" and "this is the only route to a dimension nobody measures" are different answers, and no reader can tell them apart from a name alone. |
| Reach | `elements_affected` is a union of GlobalIds, never a sum of occurrences. Downstream, summing overlapping counts overstated coverage by +26% to +864%. |
| Scope of the ask | Only groups a ruling could change: a container is measured through its parts and a countable element through its own existence, so neither is undecided. Listing them would bury the thirteen proxies that are. |
| Ordering | Groups sort by `worth_deciding` then size, and carry `cumulative_percent` — the stop signal that says whether the next group is the last 2% or the first 40%. Everything with nothing to decide sinks and says so. |
| Truncation | Bounded at 200 names and 200 groups, ordered by reach, with the tail declared as a count. A reader that mistakes a truncated list for the population rules on the wrong half. |
| **No numbers** | Not one field of `quantity-decisions.v1` is numeric, asserted by a test that walks the generated JSON Schema. A model filling it in has nowhere to put an invented quantity: the value always comes from the file. |
| Authority limits | A ruling maps a declared name to a dimension, or marks it not-a-measurement (`dimension: null`, as load-bearing as a mapping). It adds dimensions the tables never claimed and **overrides none they did** — caller authority covers the names the engine declined, not the ones buildingSMART settled. |
| Provenance | Every quantity a ruling selects carries `decided_by`, and an element that had nothing else reports `quantity_source: "caller_decision"`. A number a model chose never travels indistinguishable from one a standard named. |
| Unmatched rulings | A ruling that matches nothing in the model raises a warning (3202). Silence would be a decision the operator believes applied and is not. |
| `surface_area` | A new element dimension, reachable only by ruling. `OuterSurfaceArea`, `GrossSurfaceArea` and `NetSurfaceArea` are official IFC4 quantity names, and the area of a wall's faces is not the area of its side. |
| Transport | `decisions_path` is a bounded JSON under `MOTOR_IFC_JOB_ROOT`, resolved by the same `rpc_input` policy as the caller's IDS, and rejected as `-32602` with diagnostic 3201 when malformed. |
| Absent document | Without decisions the behaviour is byte-identical to the frozen one, asserted by test. |

## Non-goals

- **No language model, no network, no provider abstraction.** The engine ships the question
  and the answer's schema. Which model answers, at what cost, under what prompt, is the
  caller's business entirely.
- **No proposal store and no confirmation workflow.** The consuming application already has
  a propose→confirm cycle with a 24-hour TTL and mandatory human confirmation; the engine
  consumes an already-confirmed document and stays stateless.
- **No suggestion of its own.** The engine does not propose that `Fläche` is probably an
  area. Guessing semantics is precisely what the conservative tables exist to avoid; a
  ruling is a decision someone made, not a default someone inherited.
- **No change to the score or its threshold.** Decided quantities are marked so a consumer
  can separate them; the calibration measures the same thing it did.

## Consequences

The loop closes and is visible. On Schependomlaan a single ruling —
`Net Surface Area on the Outside Face → surface_area` — recovers that dimension on **1 419
elements**, every one of them stamped with who decided it, and the numbers are still the
file's, zeros included. The evidence payload for that model is 200 names with 54 declared as
truncated, which is a request a caller can afford to send.

The shared reader pipeline gained one addition: a builder may return `None`, meaning an
aggregate contract that streams every entity and emits none. v1, v2 and `element-index.v1`
never return `None` and are unaffected.

## Rollback

Remove `quantity_evidence.py`, the evidence and decision DTOs, `surface_area` and
`caller_decision` from their unions, `_decided` in `element_index.py`, the 3200 diagnostic
block, the `quantity.evidence.v1` dispatch and `decisions_path` param, the two capability
fields, the two checked-in schemas, `tests/test_quantity_evidence.py`, and this document.
Restore the pipeline's unconditional `entities` kwarg. Every other contract is independent.
