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
