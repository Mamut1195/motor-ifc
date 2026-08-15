"""Public API orchestration, policy gates, and publication."""
from __future__ import annotations
import hashlib, importlib.util, json, os, shutil, tempfile
from pathlib import Path
from typing import Any, Literal
from pydantic import TypeAdapter, ValidationError
from . import compiler
from ._version import VERSION
from .contracts import ArchitectureEnvelope, AuthoringEnvelope, MepEnvelope, StructureEnvelope
from .diagnostics import DiagnosticCode, error
from .federation import build as _build_federation
from .identity import semantic_fingerprint
from .ids_validation import validate as _validate_ids
from .element_index import Projection as ElementIndexProjection
from .element_index import index as _index_ifc_elements
from .quantity_evidence import collect as _collect_quantity_evidence
from .models import Capabilities, CompileContext, CompileResult, ElementIndexResult, QuantityDecisions, QuantityEvidenceResult, FederationResult, IdsValidationResult, InspectionResult, ModelAuditResult, ModelRepairResult, QualityFacts, QualityScoreResult, ReaderExtractionResult, ReaderExtractionResultV2, ValidationPolicy, ValidationResult, ViewerConversionResult
from .quality_score import derive_verdict as _derive_quality_verdict
from .quality_score import score_index as _score_index
from .model_repair import audit as _audit_ifc
from .model_repair import repair as _repair_ifc
from .reader_extraction import extract as _extract_ifc
from .reader_extraction import extract_semantic as _extract_ifc_semantic
from .security import UnsafePathError, secure_new_output
from .viewer_conversion import convert as _convert_ifc_to_glb

_ADAPTER = TypeAdapter(AuthoringEnvelope)
_ENGINE_VERSION = VERSION

def capabilities() -> Capabilities:
    return Capabilities(engine_version=_ENGINE_VERSION, contract_versions=("authoring.v1","ids-validation.v1","viewer-conversion.v1","process-supervision.v1","reader-extraction.v1","reader-extraction.v2","model-audit.v1","model-repair.v1","element-index.v1","quality-score.v1","quantity-evidence.v1","quantity-decisions.v1"), validation_profiles=("building.architecture@1","building.structure@1","building.mep@1"), compilation_profiles=("building.architecture@1","building.structure@1","building.mep@1"), ifc_schemas=("IFC4",), ifcopenshell_available=importlib.util.find_spec("ifcopenshell") is not None,ids_validation_contract_versions=("ids-validation.v1",),ids_versions=("1.0",),ifctester_available=importlib.util.find_spec("ifctester") is not None,viewer_conversion_contract_versions=("viewer-conversion.v1",),viewer_formats=("glb-2.0",),supervision_contract_versions=("process-supervision.v1",),supervisor_default_workers=1,supervisor_max_workers=4,request_cancellation=True,reader_extraction_contract_versions=("reader-extraction.v1","reader-extraction.v2"),model_audit_contract_versions=("model-audit.v1",),model_repair_contract_versions=("model-repair.v1",),element_index_contract_versions=("element-index.v1",),quality_score_contract_versions=("quality-score.v1",),quantity_evidence_contract_versions=("quantity-evidence.v1",),quantity_decisions_contract_versions=("quantity-decisions.v1",))

def _parse(snapshot: Any, policy: ValidationPolicy):
    if policy.warnings_as_errors:
        return None, (error(DiagnosticCode.INVALID_CONTRACT,"policy","warnings_as_errors is not supported until warning-producing rules are implemented.","Use the default policy or a future advertised capability."),)
    encoded=json.dumps(snapshot.model_dump(mode="json") if hasattr(snapshot,"model_dump") else snapshot, separators=(",",":"), default=str).encode()
    if len(encoded)>policy.max_snapshot_bytes:
        return None, (error(DiagnosticCode.LIMIT_EXCEEDED,"contract","Snapshot exceeds the configured byte limit.","Reduce the snapshot or explicitly raise the policy limit."),)
    try: envelope=_ADAPTER.validate_python(snapshot)
    except ValidationError:
        return None, (error(DiagnosticCode.INVALID_CONTRACT,"contract","Snapshot does not conform to authoring.v1.","Validate against the checked-in JSON Schema.",json_pointer="/"),)
    count=len(envelope.payload.elements) if isinstance(envelope,ArchitectureEnvelope) else len(envelope.payload.members if envelope.discipline=="structure" else envelope.payload.components)
    diagnostics=[]
    if count>policy.max_elements: diagnostics.append(error(DiagnosticCode.LIMIT_EXCEEDED,"contract","Snapshot exceeds the configured element limit.","Split the model or explicitly raise the policy limit.",json_pointer="/payload"))
    if policy.require_approved_authority and not envelope.authority.approved: diagnostics.append(error(DiagnosticCode.UNAPPROVED_AUTHORITY,"authority","Authority snapshot is not approved.","Approve the source model in its authority engine.",json_pointer="/authority/approved"))
    return envelope, tuple(diagnostics)

def validate_snapshot(snapshot: Any, policy: ValidationPolicy | None=None) -> ValidationResult:
    _, diagnostics=_parse(snapshot,policy or ValidationPolicy())
    return ValidationResult(valid=not diagnostics,diagnostics=diagnostics)

def compile_snapshot(snapshot: Any, output_dir: str|Path, context: CompileContext|None=None) -> CompileResult:
    context=context or CompileContext(); envelope,diagnostics=_parse(snapshot,context.policy)
    if diagnostics: return CompileResult(success=False,diagnostics=diagnostics)
    if not isinstance(envelope,(ArchitectureEnvelope,StructureEnvelope,MepEnvelope)):
        return CompileResult(success=False,diagnostics=(error(DiagnosticCode.UNSUPPORTED_PROFILE,"compile",f"Compilation profile {envelope.profile} is not implemented.","Use capabilities() before submitting compilation."),))
    try: target=secure_new_output(output_dir)
    except (UnsafePathError,OSError):
        return CompileResult(success=False,diagnostics=(error(DiagnosticCode.UNSAFE_PATH,"io","Output directory is outside the secure publication boundary.","Use a real, non-symlink output directory without traversal."),))
    stage=Path(tempfile.mkdtemp(prefix=f".{target.name}.stage-",dir=target.parent)); fingerprint=semantic_fingerprint(envelope)
    try:
        artifact=f"{envelope.discipline}.ifc"; ifc_path=stage/artifact
        if isinstance(envelope,ArchitectureEnvelope):
            source_map=compiler.compile_architecture(envelope,ifc_path)
            expected_source_ids={envelope.payload.storey_source_id,*(item.source_id for item in envelope.payload.elements)}
        elif isinstance(envelope,StructureEnvelope):
            source_map=compiler.compile_structure(envelope,ifc_path)
            expected_source_ids={item.source_id for item in envelope.payload.members}
        else:
            source_map=compiler.compile_mep(envelope,ifc_path)
            expected_source_ids={item.source_id for item in envelope.payload.components}
        if set(source_map)!=expected_source_ids: raise RuntimeError("mandatory source map is incomplete")
        ifc_hash=hashlib.sha256(ifc_path.read_bytes()).hexdigest()
        versions={"engine":_ENGINE_VERSION,"contract":"authoring.v1","profile":envelope.profile,"ifcopenshell":compiler.runtime().version}
        manifest={"schema":"motor-ifc.compile-manifest.v1","model_id":str(envelope.model_id),"federation_id":str(envelope.federation_id),"discipline":envelope.discipline,"revision":envelope.revision,"artifact":artifact,"sha256":ifc_hash,"semantic_fingerprint":fingerprint,"coordinate_reference":envelope.coordinate_reference.model_dump(mode="json"),"versions":versions}
        documents={"manifest.json":manifest,"diagnostics.json":{"diagnostics":[]},"source-map.json":{"schema":"motor-ifc.source-map.v1","entries":source_map}}
        for name,value in documents.items(): (stage/name).write_text(json.dumps(value,sort_keys=True,indent=2)+"\n",encoding="utf-8")
        names=(artifact,"manifest.json","diagnostics.json","source-map.json")
        os.replace(stage,target)
        artifacts={name:target/name for name in names}
        return CompileResult(success=True,artifacts=artifacts,semantic_fingerprint=fingerprint,source_map=source_map,versions=versions)
    except compiler.IfcRuntimeUnavailable:
        return CompileResult(success=False,diagnostics=(error(DiagnosticCode.IFC_RUNTIME_UNAVAILABLE,"compile","IfcOpenShell runtime is unavailable.","Install the motor-ifc[ifc] optional dependency."),))
    except Exception:
        return CompileResult(success=False,diagnostics=(error(DiagnosticCode.COMPILATION_FAILED,"compile","IFC compilation or publication failed.","Inspect private stderr logs and correct the source snapshot or runtime."),))
    finally: shutil.rmtree(stage,ignore_errors=True)

def inspect_ifc(path: str|Path) -> InspectionResult:
    try:
        ifc=compiler.runtime(); model=ifc.open(str(Path(path)))
        counts={name:len(model.by_type(name)) for name in ("IfcProject","IfcSite","IfcBuilding","IfcBuildingStorey","IfcWall")}
        tree=[{"global_id":obj.GlobalId,"name":obj.Name,"type":obj.is_a()} for type_name in ("IfcSite","IfcBuilding","IfcBuildingStorey") for obj in model.by_type(type_name)]
        return InspectionResult(success=True,ifc_schema=model.schema,entity_counts=counts,spatial_tree=tree)
    except compiler.IfcRuntimeUnavailable:
        return InspectionResult(success=False,diagnostics=(error(DiagnosticCode.IFC_RUNTIME_UNAVAILABLE,"inspect","IfcOpenShell runtime is unavailable.","Install the motor-ifc[ifc] optional dependency."),))
    except Exception:
        return InspectionResult(success=False,diagnostics=(error(DiagnosticCode.IFC_REOPEN_FAILED,"inspect","IFC could not be opened.","Provide a readable IFC4 file."),))

def extract_ifc(path: str|Path) -> ReaderExtractionResult:
    return _extract_ifc(path)

def extract_ifc_semantic(path: str|Path, projection: Literal["rich","metadata","properties","quantities","materials"]="rich", output_dir: str|Path|None=None) -> ReaderExtractionResultV2:
    return _extract_ifc_semantic(path, projection, output_dir)

def index_ifc_elements(path: str|Path, projection: ElementIndexProjection="index", output_dir: str|Path|None=None, decisions: QuantityDecisions|dict[str,Any]|None=None) -> ElementIndexResult:
    ruling = None if decisions is None else (decisions if isinstance(decisions, QuantityDecisions) else QuantityDecisions.model_validate(decisions))
    return _index_ifc_elements(path, projection, output_dir, ruling)

def collect_quantity_evidence(path: str|Path) -> QuantityEvidenceResult:
    return _collect_quantity_evidence(path)

def score_ifc_quality(index: ElementIndexResult) -> QualityScoreResult:
    return _score_index(index)

def derive_quality_verdict(facts: QualityFacts | dict[str, Any]) -> QualityScoreResult:
    return _derive_quality_verdict(facts if isinstance(facts, QualityFacts) else QualityFacts.model_validate(facts))

def audit_ifc(path: str|Path) -> ModelAuditResult:
    return _audit_ifc(path)

def repair_ifc(path: str|Path, output_dir: str|Path) -> ModelRepairResult:
    return _repair_ifc(path, output_dir)

def validate_ifc(path: str|Path, policy: ValidationPolicy|None=None) -> ValidationResult:
    if policy is not None and policy != ValidationPolicy():
        return ValidationResult(valid=False,diagnostics=(error(DiagnosticCode.INVALID_CONTRACT,"policy","Non-default IFC validation policy is unsupported.","Use the default policy or a future advertised capability."),))
    result=inspect_ifc(path)
    if not result.success: return ValidationResult(valid=False,diagnostics=result.diagnostics)
    try:
        model=compiler.runtime().open(str(Path(path)))
        validator=importlib.import_module("ifcopenshell.validate")
        logger=validator.json_logger(); validator.validate(model,logger)
        valid=result.ifc_schema=="IFC4" and result.entity_counts.get("IfcProject")==1 and not any(item.get("level")=="error" for item in logger.statements)
    except Exception:
        valid=False
    return ValidationResult(valid=valid,diagnostics=() if valid else (error(DiagnosticCode.IFC_REOPEN_FAILED,"validate","IFC failed schema or supported-project validation.","Compile or repair the model with a conforming tool."),))

def build_federation(manifests:list[dict[str,Any]],request:dict[str,Any]|None=None)->FederationResult:
    return _build_federation(manifests,request)

def validate_ids(ifc_path: str|Path,ids_path: str|Path)->IdsValidationResult:
    return _validate_ids(ifc_path,ids_path)

def convert_ifc_to_glb(ifc_path: str|Path,result_dir: str|Path,glb_filename: str)->ViewerConversionResult:
    return _convert_ifc_to_glb(ifc_path,result_dir,glb_filename)
