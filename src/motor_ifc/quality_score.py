"""Model quality: measurability and completeness, scored and judged.

This is not `model-audit.v1`. That contract reports EXPRESS schema conformance — is
this file a legal IFC. This one asks a different question: can the model be measured,
and how much of it carries the facts a quantity takeoff needs. A model can be
schema-perfect and score zero here, and a model with schema defects can be fully
measurable.

The score never refuses anything. `refuses_generation` is derived from `verdict`
alone, and only an error-severity code produces a refusing verdict. See ADR 0011 for
why a score floor is the wrong instrument and what it would break.

Reads no IFC and needs no optional runtime: it consumes an `element-index.v1` result,
or the materialized scalars a caller already persisted.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from .diagnostics import DiagnosticCode, error
from .models import (
    CoverageBucket,
    ElementIndexResult,
    QualityCoverage,
    QualityFacts,
    QualityIssue,
    QualityScoreResult,
    UncoveredGroup,
)

CONTRACT_VERSION = "quality-score.v1"

#: Calibrated 2026-07-29 against eight real public IFC files. It separates OK from
#: DEGRADED and nothing else — DEGRADED still generates. Inherited evidence, not
#: re-measured inside this engine; ADR 0011 carries the sample and the caveat.
QUALITY_SCORE_THRESHOLD = 70.0

#: Share of indexed elements without quantities at which the defect stops being local
#: and becomes the model's. One element without quantities is a modelling slip; most
#: of the model without them means most of the work is silently missing from the price.
UNMEASURED_REFUSAL_SHARE = 0.5

#: Element count above which model size is worth stating. Informational only.
LARGE_ELEMENT_COUNT = 10_000

MAX_ISSUES = 100_000

VERDICT_BLOCKED = "blocked"
VERDICT_DEGRADED = "degraded"
VERDICT_OK = "ok"
VERDICT_NOT_AUDITED = "not_audited"
VERDICT_NOT_APPLICABLE = "not_applicable"

#: The only verdicts that refuse. Neither is reachable from the score.
REFUSING_VERDICTS = (VERDICT_BLOCKED, VERDICT_NOT_AUDITED)

MODEL_NOT_MEASURABLE = "MODEL_NOT_MEASURABLE"

_EXPORTER_REMEDY = (
    "Revit ships 'Export base quantities' disabled, so it writes no IfcElementQuantity "
    "for walls, slabs or beams; enable it under Export > IFC > Modify setup. ARCHICAD "
    "can export every element as IfcBuildingElementProxy, which leaves nothing typed to "
    "measure; use a translator that preserves element types. Re-export with base "
    "quantities and upload the model again."
)

_ZERO_DIMENSIONS = ("area", "volume", "length", "weight")


#: Buckets that make up the headline denominator. `ambiguous` is reported beside it and
#: `non_geometric` is excluded outright.
BILLABLE_MEASURABILITY = ("structural", "countable", "container")

#: How deep a chain of containers is resolved. A container of containers is normal;
#: an unbounded walk over a malformed model is not.
MAX_CONTAINER_DEPTH = 8

MAX_UNCOVERED_GROUPS = 100


def _issue(code: str, severity: str, message: str, global_id: str | None = None, measurability: str | None = None) -> QualityIssue:
    return QualityIssue(code=code, severity=severity, message=message, global_id=global_id, measurability=measurability)


def _coverage(elements: tuple) -> tuple[QualityCoverage, dict[str, bool]]:
    """How much of what could have been measured actually was.

    Decomposition is read both ways, because a quantity lives at exactly one level of it:

    - a **whole** is covered by its own quantities or by all of its parts — the roof whose
      two slabs measure is not a gap, it is double counting avoided;
    - a **part** is covered by its own quantities or by any ancestor that measures — the
      257 layers inside a measured covering are not 257 gaps, they are one measured
      covering.

    Both derive from what each element declares, never from the other's derived value, so
    the two directions cannot talk each other into a coverage neither earned.

    A countable element is always covered: its existence is the quantity. Coverage does not
    demand a particular dimension either — which dimension an item is billed in is decided
    by the caller's unit, and picking one here is underdetermined on real models that carry
    area, length and volume all at once.
    """
    own: dict[str, bool] = {}
    part_of: dict[str, str] = {}
    parts_of: dict[str, list[str]] = defaultdict(list)
    for element in elements:
        if element.part_of_global_id:
            part_of[element.global_id] = element.part_of_global_id
            parts_of[element.part_of_global_id].append(element.global_id)
        if element.measurability == "countable":
            own[element.global_id] = True
        elif element.measurability == "non_geometric":
            own[element.global_id] = False
        else:
            # `spatial` included: a room is covered when it carries any of its own six
            # measures, exactly as a wall is covered by carrying one of its.
            own[element.global_id] = bool(element.quantities)

    covered = dict(own)
    for element in elements:
        if covered[element.global_id]:
            continue
        ancestor = part_of.get(element.global_id)
        for _ in range(MAX_CONTAINER_DEPTH):
            if ancestor is None:
                break
            if own.get(ancestor):
                covered[element.global_id] = True
                break
            ancestor = part_of.get(ancestor)

    containers = [element for element in elements if element.measurability == "container"]
    for _ in range(MAX_CONTAINER_DEPTH):
        settled = True
        for container in containers:
            if covered[container.global_id]:
                continue
            in_scope = [part for part in parts_of.get(container.global_id, ()) if part in covered]
            if in_scope and all(covered[part] for part in in_scope):
                covered[container.global_id] = True
                settled = False
        if settled:
            break

    buckets: dict[str, Counter] = defaultdict(Counter)
    uncovered: Counter = Counter()
    for element in elements:
        bucket = buckets[element.measurability]
        bucket["total"] += 1
        if covered[element.global_id]:
            bucket["covered"] += 1
        elif element.measurability != "non_geometric":
            uncovered[(element.ifc_class, element.measurability)] += 1

    def as_bucket(name: str) -> CoverageBucket:
        counts = buckets.get(name, Counter())
        total, hit = counts["total"], counts["covered"]
        return CoverageBucket(total=total, covered=hit, percent=round(hit / total * 100, 1) if total else None)

    billable_total = sum(buckets[name]["total"] for name in BILLABLE_MEASURABILITY)
    billable_covered = sum(buckets[name]["covered"] for name in BILLABLE_MEASURABILITY)
    groups = sorted(uncovered.items(), key=lambda item: (-item[1], item[0][0], item[0][1]))
    summary = QualityCoverage(
        billable_total=billable_total,
        billable_covered=billable_covered,
        coverage_percent=round(billable_covered / billable_total * 100, 1) if billable_total else None,
        structural=as_bucket("structural"),
        countable=as_bucket("countable"),
        container=as_bucket("container"),
        ambiguous=as_bucket("ambiguous"),
        spatial=as_bucket("spatial"),
        excluded_non_geometric=buckets["non_geometric"]["total"],
        uncovered_by_class=tuple(
            UncoveredGroup(ifc_class=ifc_class, measurability=measurability, count=count)
            for (ifc_class, measurability), count in groups[:MAX_UNCOVERED_GROUPS]
        ),
    )
    return summary, covered


def score_index(index: ElementIndexResult) -> QualityScoreResult:
    """Score and judge an `element-index.v1` result in one pass.

    Deriving the issues and the verdict from the same materialized elements is what
    makes them incapable of disagreeing: there is no second read that could see a
    different model.
    """
    if not index.success:
        return QualityScoreResult(
            success=False,
            diagnostics=(
                error(
                    DiagnosticCode.QUALITY_SCORE_FAILED,
                    "quality-score",
                    "The element index did not succeed, so there is nothing to score.",
                    "Index the model successfully before scoring it.",
                ),
            ),
        )
    if index.publication != "none":
        return QualityScoreResult(
            success=False,
            diagnostics=(
                error(
                    DiagnosticCode.QUALITY_SCORE_FAILED,
                    "quality-score",
                    "A published index carries no inline elements to score.",
                    "Index inline, or score the published artifact through derive_verdict with materialized scalars.",
                ),
            ),
        )

    issues: list[QualityIssue] = []
    # Coverage sees every indexed record; the score sees only the elements.
    #
    # Rooms are excluded from the scored population deliberately. Judging one by "has no
    # material assigned" is a category error, and folding Schependomlaan's 100 spaces into
    # its 3 331 elements would move a calibrated number by changing what is counted rather
    # than what is true.
    elements = tuple(entity for entity in index.entities if entity.measurability != "spatial")
    total_elements = len(elements)

    if not index.storeys:
        issues.append(_issue("NO_BUILDING_STOREYS", "error", "The IFC declares no building storeys (IfcBuildingStorey)."))
    if index.duplicate_global_id_count:
        # Model-level rather than per-element: the index keeps one record per GlobalId,
        # so the defect is a property of the file, and naming the survivors would
        # accuse the wrong elements.
        issues.append(
            _issue(
                "DUPLICATE_GUID",
                "error",
                f"{index.duplicate_global_id_count} element(s) repeat a GlobalId already used in this model.",
            )
        )
    if total_elements > LARGE_ELEMENT_COUNT:
        issues.append(_issue("LARGE_ELEMENT_COUNT", "info", f"The model carries {total_elements} indexed elements."))
    if not index.project_name:
        issues.append(_issue("MISSING_PROJECT_INFO", "info", "The IFC declares no project name."))
    if index.skipped_types:
        issues.append(
            _issue(
                "SKIPPED_ELEMENT_TYPES",
                "info",
                f"Schema {index.source_schema} does not declare: {', '.join(index.skipped_types)}.",
            )
        )

    coverage, covered = _coverage(index.entities)

    for element in elements:
        label = f'{element.ifc_class} "{element.name}"' if element.name else element.ifc_class
        # Raised for a real measurement gap, not for every silent quantity set. A layer
        # inside a measured covering and a door billed by the unit are both measurable;
        # warning on them 277 times buries the wall that genuinely cannot be measured.
        if not covered.get(element.global_id, False):
            issues.append(
                _issue("NO_QUANTITIES", "warning", f"{label} carries no measurable quantity.", element.global_id, element.measurability)
            )
        else:
            zero = any(
                quantity.value == 0 for quantity in element.quantities if quantity.dimension in _ZERO_DIMENSIONS
            )
            if zero:
                issues.append(_issue("ZERO_QUANTITY", "warning", f"{label} has a measured quantity of 0.", element.global_id))
        if not element.material:
            issues.append(_issue("NO_MATERIAL", "warning", f"{label} has no material assigned.", element.global_id))
        if not element.classification:
            issues.append(_issue("NO_CLASSIFICATION", "info", f"{label} has no classification.", element.global_id))
        if not element.storey_global_id:
            issues.append(_issue("NO_STOREY", "warning", f"{label} is not assigned to a storey.", element.global_id))
        if not element.name:
            issues.append(_issue("EMPTY_NAME", "info", f"{element.ifc_class} ({element.global_id}) has no name."))
        if len(issues) > MAX_ISSUES:
            return QualityScoreResult(
                success=False,
                diagnostics=(
                    error(
                        DiagnosticCode.LIMIT_EXCEEDED,
                        "quality-score",
                        f"Model quality issues exceed the {MAX_ISSUES} bound.",
                        "Reduce or split the model before scoring it.",
                    ),
                ),
            )

    # Model-level measurability. Deliberately a separate code rather than a severity
    # flip on NO_QUANTITIES: one element without quantities means the same thing
    # whatever the rest of the model looks like, and flipping severities would leave
    # the score untouched while making a local warning read as a blocker. The
    # zero-element case is the same defect at the limit, and it is the easy one to
    # miss — an empty model divides by a floor of 1 and scores a perfect 100.
    #
    # The share is read over billable coverage, not over every indexed row. The constant
    # is the calibrated one, unchanged; only the denominator got sharper, and it had to:
    # counting containers, grouping nodes and unit-billed fittings as unmeasured work is
    # what made fully measured models read as half empty. Verdicts are identical on every
    # model available here (ADR 0011).
    if coverage.billable_total == 0:
        issues.append(
            _issue(
                MODEL_NOT_MEASURABLE,
                "error",
                "The model has no billable building elements, so there is no quantity of work to measure. " + _EXPORTER_REMEDY,
            )
        )
    elif coverage.billable_covered / coverage.billable_total < UNMEASURED_REFUSAL_SHARE:
        missing = coverage.billable_total - coverage.billable_covered
        issues.append(
            _issue(
                MODEL_NOT_MEASURABLE,
                "error",
                f"{missing} of {coverage.billable_total} billable elements ({coverage.coverage_percent}% covered) "
                f"carry no measurable quantity; without quantities the work cannot be measured or priced. "
                + _EXPORTER_REMEDY,
            )
        )

    facts = QualityFacts(
        is_synthetic=False,
        audited=True,
        total_elements=total_elements,
        elements_with_issues=len(
            {issue.global_id for issue in issues if issue.global_id and issue.severity in ("error", "warning")}
        ),
        error_codes=tuple(sorted({issue.code for issue in issues if issue.severity == "error"})),
        model_level_error_messages=tuple(
            sorted({issue.message for issue in issues if issue.severity == "error" and issue.global_id is None})
        ),
        warning_codes=tuple(sorted({issue.code for issue in issues if issue.severity == "warning"})),
        total_issues=len(issues),
    )
    verdict = derive_verdict(facts)
    return verdict.model_copy(update={"issues": tuple(issues), "coverage": coverage})


def derive_verdict(facts: QualityFacts) -> QualityScoreResult:
    """Judge a model from materialized scalars, without re-reading or re-auditing it.

    This is the whole seam for a caller that persisted its issues: the same numbers the
    one-pass path computes, so both produce the same verdict by construction.
    """
    if facts.is_synthetic:
        # Not a relaxation of NOT_AUDITED: a synthetic model never had an audit step to
        # skip. Generation is allowed, but the caller has to declare it.
        return QualityScoreResult(success=True, verdict=VERDICT_NOT_APPLICABLE, score=None, threshold=QUALITY_SCORE_THRESHOLD)
    if not facts.audited:
        return QualityScoreResult(
            success=True,
            verdict=VERDICT_NOT_AUDITED,
            score=None,
            threshold=QUALITY_SCORE_THRESHOLD,
            refuses_generation=True,
        )

    total = facts.total_elements or 1
    score = round(((total - min(facts.elements_with_issues, total)) / total) * 100, 1)

    if facts.error_codes:
        return QualityScoreResult(
            success=True,
            verdict=VERDICT_BLOCKED,
            score=score,
            threshold=QUALITY_SCORE_THRESHOLD,
            refuses_generation=True,
            error_codes=facts.error_codes,
            error_messages=facts.model_level_error_messages,
            warning_codes=facts.warning_codes,
            total_issues=facts.total_issues,
        )

    # The only place the threshold is read. It separates OK from DEGRADED; both
    # generate. Nothing downstream of here can turn a low score into a refusal.
    verdict = VERDICT_DEGRADED if score < QUALITY_SCORE_THRESHOLD else VERDICT_OK
    return QualityScoreResult(
        success=True,
        verdict=verdict,
        score=score,
        threshold=QUALITY_SCORE_THRESHOLD,
        refuses_generation=verdict in REFUSING_VERDICTS,
        warning_codes=facts.warning_codes,
        total_issues=facts.total_issues,
    )
