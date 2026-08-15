"""Operation inputs and results."""
from __future__ import annotations
from pathlib import Path
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field
from .diagnostics import Diagnostic

class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

class ValidationPolicy(FrozenModel):
    require_approved_authority: bool = True
    warnings_as_errors: bool = False
    max_snapshot_bytes: int = Field(default=10_000_000, ge=1, le=100_000_000)
    max_elements: int = Field(default=10_000, ge=0, le=100_000)

class CompileContext(FrozenModel):
    policy: ValidationPolicy = ValidationPolicy()

class Capabilities(FrozenModel):
    engine_version: str
    contract_versions: tuple[str, ...]
    validation_profiles: tuple[str, ...]
    compilation_profiles: tuple[str, ...]
    ifc_schemas: tuple[str, ...]
    ifcopenshell_available: bool
    ids_validation_contract_versions: tuple[str, ...] = ()
    ids_versions: tuple[str, ...] = ()
    ifctester_available: bool = False
    viewer_conversion_contract_versions: tuple[str, ...] = ()
    viewer_formats: tuple[str, ...] = ()
    supervision_contract_versions: tuple[str, ...] = ()
    supervisor_default_workers: int = 0
    supervisor_max_workers: int = 0
    request_cancellation: bool = False
    reader_extraction_contract_versions: tuple[str, ...] = ()
    model_audit_contract_versions: tuple[str, ...] = ()
    model_repair_contract_versions: tuple[str, ...] = ()
    element_index_contract_versions: tuple[str, ...] = ()
    quality_score_contract_versions: tuple[str, ...] = ()
    quantity_evidence_contract_versions: tuple[str, ...] = ()
    quantity_decisions_contract_versions: tuple[str, ...] = ()

class ValidationResult(FrozenModel):
    valid: bool
    diagnostics: tuple[Diagnostic, ...] = ()

class IdsValidationSummary(FrozenModel):
    specifications: int
    specifications_passed: int
    specifications_failed: int
    requirements: int
    requirements_passed: int
    requirements_failed: int
    checks: int
    checks_passed: int
    checks_failed: int

class IdsSpecificationResult(FrozenModel):
    index: int
    name: str
    passed: bool
    skipped: bool
    applicable_entities: int
    applicable_entities_passed: int
    applicable_entities_failed: int
    requirements: int
    checks: int
    checks_passed: int
    checks_failed: int

class IdsValidationResult(FrozenModel):
    contract_version: Literal["ids-validation.v1"] = "ids-validation.v1"
    success: bool
    valid: bool
    ids_title: str | None = None
    summary: IdsValidationSummary | None = None
    specifications: tuple[IdsSpecificationResult, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    publication: Literal["none"] = "none"
    artifact_filenames: tuple[str, ...] = ()

class CompileResult(FrozenModel):
    success: bool
    diagnostics: tuple[Diagnostic, ...] = ()
    artifacts: dict[str, Path] = Field(default_factory=dict)
    semantic_fingerprint: str | None = None
    source_map: dict[str, str] = Field(default_factory=dict)
    versions: dict[str, str] = Field(default_factory=dict)

class ViewerConversionResult(FrozenModel):
    contract_version: Literal["viewer-conversion.v1"] = "viewer-conversion.v1"
    success: bool
    diagnostics: tuple[Diagnostic, ...] = ()
    artifacts: dict[str, Path] = Field(default_factory=dict)
    source_ifc_sha256: str | None = None
    artifact_sha256: str | None = None
    versions: dict[str, str] = Field(default_factory=dict)
    publication: Literal["none", "immutable-directory"] = "none"

class InspectionResult(FrozenModel):
    success: bool
    diagnostics: tuple[Diagnostic, ...] = ()
    ifc_schema: str | None = None
    entity_counts: dict[str, int] = Field(default_factory=dict)
    spatial_tree: list[dict[str, Any]] = Field(default_factory=list)

class ReaderEntityMetadata(FrozenModel):
    global_id: str
    ifc_class: str
    name: str | None = None
    description: str | None = None
    object_type: str | None = None
    tag: str | None = None

class ReaderEntityProperties(ReaderEntityMetadata):
    properties: dict[str, Any] = Field(default_factory=dict)

class ReaderEntityQuantities(ReaderEntityMetadata):
    quantities: dict[str, Any] = Field(default_factory=dict)

class ReaderEntity(ReaderEntityMetadata):
    properties: dict[str, Any] = Field(default_factory=dict)
    quantities: dict[str, Any] = Field(default_factory=dict)

class ReaderExtractionResult(FrozenModel):
    contract_version: Literal["reader-extraction.v1"] = "reader-extraction.v1"
    success: bool
    source_schema: str | None = None
    entity_count: int = 0
    entities: tuple[ReaderEntityMetadata | ReaderEntityProperties | ReaderEntityQuantities | ReaderEntity, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    truncated: Literal[False] = False
    publication: Literal["none"] = "none"
    artifact_filenames: tuple[str, ...] = ()

class ReaderUnit(FrozenModel):
    source: Literal["quantity", "project", "unknown"]
    name: str | None = None
    symbol: str | None = None
    prefix: str | None = None
    unit_type: str | None = None

class ReaderQuantity(FrozenModel):
    name: str | None = None
    description: str | None = None
    ifc_class: str
    formula: str | None = None
    discrimination: str | None = None
    value: bool | int | float | str | None = None
    value_type: str | None = None
    unit: ReaderUnit | None = None
    normalized_value: float | None = None
    components: tuple["ReaderQuantity", ...] = ()

class ReaderQuantitySet(FrozenModel):
    global_id: str | None = None
    name: str | None = None
    description: str | None = None
    method_of_measurement: str | None = None
    source: Literal["occurrence", "type"]
    relation_global_id: str | None = None
    shadowed_by_occurrence: bool = False
    quantities: tuple[ReaderQuantity, ...] = ()

class ReaderMaterialLayer(FrozenModel):
    material_name: str | None = None
    thickness: float | None = None
    is_ventilated: bool | Literal["UNKNOWN"] | None = None
    priority: int | None = None
    category: str | None = None

class ReaderMaterialProfile(FrozenModel):
    name: str | None = None
    material_name: str | None = None
    category: str | None = None
    priority: int | None = None

class ReaderMaterialConstituent(FrozenModel):
    name: str | None = None
    description: str | None = None
    category: str | None = None
    fraction: float | None = None
    material_name: str | None = None

class ReaderMaterialAssociation(FrozenModel):
    source: Literal["occurrence", "type"]
    relation_global_id: str | None = None
    kind: Literal["material", "material_list", "layer_set", "layer_set_usage", "profile_set", "profile_set_usage", "constituent_set"]
    name: str | None = None
    description: str | None = None
    category: str | None = None
    materials: tuple[str, ...] = ()
    layers: tuple[ReaderMaterialLayer, ...] = ()
    profiles: tuple[ReaderMaterialProfile, ...] = ()
    constituents: tuple[ReaderMaterialConstituent, ...] = ()
    usage_direction: str | None = None
    usage_offset: float | None = None

class ReaderEntityQuantitiesV2(ReaderEntityMetadata):
    quantity_sets: tuple[ReaderQuantitySet, ...] = ()

class ReaderEntityMaterialsV2(ReaderEntityMetadata):
    material_associations: tuple[ReaderMaterialAssociation, ...] = ()

class ReaderEntityV2(ReaderEntityMetadata):
    properties: dict[str, Any] = Field(default_factory=dict)
    quantity_sets: tuple[ReaderQuantitySet, ...] = ()
    material_associations: tuple[ReaderMaterialAssociation, ...] = ()

class ReaderExtractionResultV2(FrozenModel):
    contract_version: Literal["reader-extraction.v2"] = "reader-extraction.v2"
    success: bool
    source_schema: str | None = None
    entity_count: int = 0
    entities: tuple[ReaderEntityMetadata | ReaderEntityProperties | ReaderEntityQuantitiesV2 | ReaderEntityMaterialsV2 | ReaderEntityV2, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    truncated: Literal[False] = False
    publication: Literal["none", "immutable-directory"] = "none"
    artifact_filenames: tuple[str, ...] = ()
    source_sha256: str | None = None
    extraction_sha256: str | None = None

#: `surface_area` is the coating and painting measure — `OuterSurfaceArea`,
#: `GrossSurfaceArea` and `NetSurfaceArea` are official IFC4 quantity names, and the area of
#: a wall's faces is not the area of its side. Only a caller decision can select it; no
#: built-in table does.
Dimension = Literal["area", "surface_area", "volume", "length", "count", "weight"]
MeasureKind = Literal["area", "volume", "length", "count", "weight"]

#: What a room measures. `Qto_SpaceBaseQuantities` defines six areas, not one, and each
#: bills against a different item — the specification defines NetPerimeter as the measure
#: "used for skirting boards". A space never reports the bare `area` an element reports.
SpaceDimension = Literal["floor_area", "wall_area", "ceiling_area", "perimeter", "height", "volume"]
QuantitySource = Literal["base_quantity", "vendor_quantity", "caller_decision", "existence", "fallback"]

#: What it would mean for an element to be measurable at all.
#:
#: Determined from structure — decomposition and geometry — before class, never from the
#: element's name. `container` and `non_geometric` exist because counting them as unmeasured
#: work is what makes a fully measured model look half empty: a container's parts carry its
#: quantities, and a grouping node has nothing to measure in the first place.
Measurability = Literal["structural", "countable", "container", "ambiguous", "non_geometric", "spatial"]

class ElementQuantity(FrozenModel):
    """One measure of one element, selected by name from the best available set.

    ``value`` is the number the file declares. ``normalized_value`` is that number in
    the SI unit ``ifcopenshell.util.unit`` derives for the measure, or ``None`` when it
    is not derivable — never a guess. ``unit`` says what ``value`` is in, so a
    normalized number never travels without its origin.
    """
    dimension: Dimension | SpaceDimension
    value: float
    normalized_value: float | None = None
    unit: ReaderUnit | None = None
    source_quantity_name: str | None = None
    selection_rank: int = 0
    #: Set only when a caller decision selected this name. A quantity a model chose must
    #: never travel indistinguishable from one buildingSMART named.
    decided_by: str | None = None

class ElementIndexRecord(ReaderEntityMetadata):
    storey_global_id: str | None = None
    storey_name: str | None = None
    material: str | None = None
    classification: str | None = None
    measurability: Measurability = "structural"
    #: The container this element decomposes from, when it has one. A caller joining
    #: quantities needs it: if a curtain wall and its members both carry numbers, summing
    #: both double-counts the same wall, and nothing else in the result reveals that.
    part_of_global_id: str | None = None
    decomposes_into: int = 0
    quantity_source: QuantitySource = "fallback"
    quantity_set_name: str | None = None
    quantities: tuple[ElementQuantity, ...] = ()
    properties: dict[str, Any] = Field(default_factory=dict)

class ElementIndexStorey(FrozenModel):
    global_id: str
    name: str | None = None
    elevation: float | None = None

class ElementIndexResult(FrozenModel):
    contract_version: Literal["element-index.v1"] = "element-index.v1"
    success: bool
    source_schema: str | None = None
    entity_count: int = 0
    project_name: str | None = None
    storeys: tuple[ElementIndexStorey, ...] = ()
    element_types: tuple[str, ...] = ()
    skipped_types: tuple[str, ...] = ()
    duplicate_global_id_count: int = 0
    entities: tuple[ElementIndexRecord, ...] = ()
    unresolved_unit_scale_count: int = 0
    diagnostics: tuple[Diagnostic, ...] = ()
    truncated: Literal[False] = False
    publication: Literal["none", "immutable-directory"] = "none"
    artifact_filenames: tuple[str, ...] = ()
    source_sha256: str | None = None
    extraction_sha256: str | None = None

class VocabularyEntry(FrozenModel):
    """One quantity name the built-in tables did not select, with what it would be worth.

    `competes_with` is what makes the entry decidable: it names the quantity already being
    taken for this measure, or is null when nothing measures that dimension at all. "There
    is a better name already in use" and "this is the only route to a dimension nobody is
    measuring" are different answers, and no reader can tell them apart from the name alone.
    """
    measure: MeasureKind
    name: str
    occurrences: int
    #: Distinct GlobalIds, never a sum: overlapping counts overstate reach.
    elements_affected: int
    quantity_sets: tuple[str, ...] = ()
    on_classes: tuple[str, ...] = ()
    sample_value: float | None = None
    sample_unit: ReaderUnit | None = None
    sample_normalized: float | None = None
    competes_with: str | None = None

class ElementGroupEvidence(FrozenModel):
    """A group of elements that needs a judgement no structural signal can make.

    Grouped rather than listed, and aggregated over the whole model rather than sampled: a
    bounded list of elements is alphabetically biased, and a reader that mistakes it for the
    population answers correctly about the wrong subset.
    """
    ifc_class: str
    measurability: Measurability
    material: str | None = None
    elements: int = 0
    #: A conclusion, not a score. False when the group carries nothing to decide, so a
    #: reader does not spend a cycle discovering that for itself.
    worth_deciding: bool = True
    object_type_samples: tuple[str, ...] = ()
    type_name: str | None = None
    property_sets: dict[str, Any] = Field(default_factory=dict)
    candidates: tuple[str, ...] = ()
    global_id_samples: tuple[str, ...] = ()
    #: Always true: the ids above are a sample of `elements`, never the population.
    is_sample: bool = True
    #: Share of the undecided population covered by this group and everything above it —
    #: the stop signal that says whether the next group is the last 2% or the first 40%.
    cumulative_percent: float = 0.0

class QuantityEvidenceResult(FrozenModel):
    """Everything a caller's model needs to decide what is a quantity, and nothing else.

    Read-only, inline-only and bounded by construction: it carries aggregates and samples,
    never the model. The engine asks the question; it never answers it and never calls a
    model of its own.
    """
    contract_version: Literal["quantity-evidence.v1"] = "quantity-evidence.v1"
    success: bool
    source_schema: str | None = None
    entity_count: int = 0
    vocabulary: tuple[VocabularyEntry, ...] = ()
    truncated_names: int = 0
    element_groups: tuple[ElementGroupEvidence, ...] = ()
    truncated_groups: int = 0
    diagnostics: tuple[Diagnostic, ...] = ()
    truncated: Literal[False] = False
    publication: Literal["none"] = "none"
    artifact_filenames: tuple[str, ...] = ()

class QuantityNameDecision(FrozenModel):
    """A caller's ruling on one quantity name.

    It may only name what the file already declares. `dimension: null` says the name is not
    a measurement at all — as load-bearing as a mapping, because it records that the name
    was judged rather than overlooked.
    """
    measure: MeasureKind
    name: str = Field(min_length=1, max_length=200)
    dimension: Dimension | None = None
    #: Restrict the ruling to these IFC classes; empty means every class.
    applies_to: tuple[str, ...] = Field(default=(), max_length=100)

class ElementGroupDecision(FrozenModel):
    """A caller's ruling on whether a class of elements is work to be billed."""
    ifc_class: str = Field(min_length=1, max_length=100)
    object_type_contains: str | None = Field(default=None, min_length=1, max_length=200)
    billable: bool = True

class QuantityDecisions(FrozenModel):
    """Caller-supplied authority over naming, never over numbers.

    The caller is the authority here exactly as it is for IDS requirements: the engine
    validates and applies this document, and never generates, infers or repairs one. Not a
    single field of this contract is numeric, so a model filling it in has nowhere to put an
    invented quantity — the value always comes from the file.
    """
    contract_version: Literal["quantity-decisions.v1"] = "quantity-decisions.v1"
    quantity_names: tuple[QuantityNameDecision, ...] = Field(default=(), max_length=1_000)
    element_groups: tuple[ElementGroupDecision, ...] = Field(default=(), max_length=1_000)
    #: Free-text provenance travelling with every quantity the document selects.
    decided_by: str | None = Field(default=None, min_length=1, max_length=200)

QualityVerdict = Literal["blocked", "degraded", "ok", "not_audited", "not_applicable"]

class QualityIssue(FrozenModel):
    """A model-quality finding. Data, not an operational diagnostic.

    ``global_id`` is null on model-level findings — the ones that diagnose the file
    rather than one of its elements. ``measurability`` lets a consumer separate a wall
    that should have been measured from a grouping node that never could be.
    """
    code: str
    severity: Literal["error", "warning", "info"]
    message: str
    global_id: str | None = None
    measurability: Measurability | None = None

class CoverageBucket(FrozenModel):
    total: int = 0
    covered: int = 0
    #: Null when the bucket is empty — a percentage of nothing is not 100 or 0.
    percent: float | None = None

class UncoveredGroup(FrozenModel):
    ifc_class: str
    measurability: Measurability
    count: int

class QualityCoverage(FrozenModel):
    """How much of what should be measurable actually is.

    The headline is `coverage_percent` over `structural`, `countable` and `container`.
    `ambiguous` is reported beside it, never inside it: `IfcBuildingElementProxy` is a
    coordinate marker in one file and roof trim in the next, and no structural signal
    separates them, so the operator decides rather than the engine guessing.
    `non_geometric` elements are excluded outright — they have nothing to measure.
    """
    billable_total: int = 0
    billable_covered: int = 0
    coverage_percent: float | None = None
    structural: CoverageBucket = CoverageBucket()
    countable: CoverageBucket = CoverageBucket()
    container: CoverageBucket = CoverageBucket()
    ambiguous: CoverageBucket = CoverageBucket()
    #: Rooms, reported beside the element headline and never inside it. Room surfaces bill
    #: finishes; element surfaces bill structure. A model can be complete on one and empty
    #: on the other, and one number covering both would hide exactly that.
    spatial: CoverageBucket = CoverageBucket()
    excluded_non_geometric: int = 0
    #: What is actually missing, grouped and ordered by size. The list a modeller acts on.
    uncovered_by_class: tuple[UncoveredGroup, ...] = ()

class QualityFacts(FrozenModel):
    """Materialized scalars a verdict is derivable from, without re-auditing.

    This is the whole input of ``derive_verdict``: it replaces the six queries the
    reference gate ran against its database, so a caller that persisted issues can
    re-derive the same verdict from its own rows.
    """
    is_synthetic: bool = False
    audited: bool = True
    total_elements: int = Field(default=0, ge=0)
    elements_with_issues: int = Field(default=0, ge=0)
    error_codes: tuple[str, ...] = ()
    model_level_error_messages: tuple[str, ...] = ()
    warning_codes: tuple[str, ...] = ()
    total_issues: int = Field(default=0, ge=0)

class QualityScoreResult(FrozenModel):
    """Score, verdict and codes.

    ``refuses_generation`` is derived from ``verdict`` alone. ``score`` and
    ``threshold`` are separate fields that no refusal reads: what a model cannot
    measure is refused by an error-severity code, never by this number (ADR 0011).
    """
    contract_version: Literal["quality-score.v1"] = "quality-score.v1"
    success: bool
    verdict: QualityVerdict | None = None
    score: float | None = None
    threshold: float | None = None
    refuses_generation: bool = False
    #: The number to act on. `score` is the calibrated legacy gate and mixes every
    #: element-level defect together; this is measurability alone, over a denominator
    #: that contains only what could have been measured.
    coverage: QualityCoverage | None = None
    error_codes: tuple[str, ...] = ()
    error_messages: tuple[str, ...] = ()
    warning_codes: tuple[str, ...] = ()
    total_issues: int = 0
    issues: tuple[QualityIssue, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    publication: Literal["none"] = "none"
    artifact_filenames: tuple[str, ...] = ()

class FederationResult(FrozenModel):
    success: bool
    diagnostics: tuple[Diagnostic, ...] = ()
    manifest: dict[str, Any] | None = None

class ModelDefect(FrozenModel):
    step_id: int | None = None
    ifc_class: str
    global_id: str | None = None
    attribute: str | None = None
    rule: Literal["missing-mandatory-attribute", "schema-rule"]
    repair_strategy: Literal["drop-instance", "manual"]

class ModelAuditResult(FrozenModel):
    contract_version: Literal["model-audit.v1"] = "model-audit.v1"
    success: bool
    source_schema: str | None = None
    source_sha256: str | None = None
    valid: bool = False
    defect_count: int = 0
    repairable_count: int = 0
    manual_count: int = 0
    repairable: bool = False
    defects: tuple[ModelDefect, ...] = ()
    entity_counts: dict[str, int] = Field(default_factory=dict)
    diagnostics: tuple[Diagnostic, ...] = ()
    truncated: Literal[False] = False
    publication: Literal["none"] = "none"
    artifact_filenames: tuple[str, ...] = ()

class ModelRepairResult(FrozenModel):
    contract_version: Literal["model-repair.v1"] = "model-repair.v1"
    success: bool
    repaired: bool = False
    defects_fixed: int = 0
    fixes: tuple[ModelDefect, ...] = ()
    remaining_defects: tuple[ModelDefect, ...] = ()
    source_sha256: str | None = None
    repaired_sha256: str | None = None
    artifacts: dict[str, Path] = Field(default_factory=dict)
    diagnostics: tuple[Diagnostic, ...] = ()
    publication: Literal["none", "immutable-directory"] = "none"
