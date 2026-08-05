"""Bounded adaptation of caller-supplied buildingSMART IDS 1.0 validation."""
from __future__ import annotations
import importlib
from pathlib import Path
import xml.etree.ElementTree as ET
from .diagnostics import DiagnosticCode, error
from .models import IdsSpecificationResult, IdsValidationResult, IdsValidationSummary
from .security import UnsafePathError, secure_existing_input

CONTRACT_VERSION="ids-validation.v1"
IDS_NAMESPACE="http://standards.buildingsmart.org/IDS"
MAX_IFC_BYTES=100_000_000
MAX_IDS_BYTES=5_000_000
MAX_SPECIFICATIONS=100
MAX_REQUIREMENTS=1_000
MAX_CHECKS=100_000
MAX_TEXT_LENGTH=500

class IdsRuntimeUnavailable(RuntimeError): pass

class _UnsupportedXmlDeclaration(ValueError): pass

class _IdsTreeBuilder(ET.TreeBuilder):
    def doctype(self,name: str,pubid: str|None,system: str|None)->None:
        raise _UnsupportedXmlDeclaration

def runtime():
    try:
        ifc=importlib.import_module("ifcopenshell")
        tester=importlib.import_module("ifctester")
        reporter=importlib.import_module("ifctester.reporter")
    except ImportError as exc:
        raise IdsRuntimeUnavailable("IDS runtime is not installed") from exc
    if getattr(ifc,"version",None)!="0.8.5" or getattr(tester,"__version__",None)!="0.8.5":
        raise IdsRuntimeUnavailable("IDS runtime version is unsupported")
    return ifc,tester,reporter

def _failure(code: DiagnosticCode,stage: str,message: str,action: str)->IdsValidationResult:
    return IdsValidationResult(success=False,valid=False,diagnostics=(error(code,stage,message,action),))

def _text(value: object)->str:
    text=str(value or "")
    return text if len(text)<=MAX_TEXT_LENGTH else text[:MAX_TEXT_LENGTH]

def _preflight_ids(path: Path)->IdsValidationResult|None:
    try:
        xml=path.read_bytes()
        root=ET.fromstring(xml,parser=ET.XMLParser(target=_IdsTreeBuilder()))
    except _UnsupportedXmlDeclaration:
        return _failure(DiagnosticCode.INVALID_IDS,"ids-parse","IDS XML contains unsupported declarations.","Provide buildingSMART IDS 1.0 XML without DTD or entity declarations.")
    except (ET.ParseError,OSError,ValueError):
        return _failure(DiagnosticCode.INVALID_IDS,"ids-parse","IDS XML is malformed.","Provide a schema-valid buildingSMART IDS 1.0 XML file.")
    if root.tag!=f"{{{IDS_NAMESPACE}}}ids":
        return _failure(DiagnosticCode.UNSUPPORTED_IDS,"ids-parse","IDS XML is not the supported buildingSMART IDS 1.0 contract.","Provide IDS 1.0 XML using the buildingSMART IDS namespace.")
    schema_location=root.attrib.get("{http://www.w3.org/2001/XMLSchema-instance}schemaLocation","")
    if schema_location and "/1.0/ids.xsd" not in schema_location:
        return _failure(DiagnosticCode.UNSUPPORTED_IDS,"ids-parse","IDS XML declares an unsupported schema version.","Provide buildingSMART IDS 1.0 XML.")
    return None

def validate(ifc_path: str|Path,ids_path: str|Path)->IdsValidationResult:
    """Validate an IFC against caller-owned IDS requirements without publishing files."""
    try:
        safe_ifc=secure_existing_input(ifc_path,MAX_IFC_BYTES)
        safe_ids=secure_existing_input(ids_path,MAX_IDS_BYTES)
    except (UnsafePathError,OSError,TypeError) as exc:
        code=DiagnosticCode.LIMIT_EXCEEDED if "byte limit" in str(exc) else DiagnosticCode.UNSAFE_PATH
        return _failure(code,"ids-input","IDS validation input is outside the supported file boundary.","Provide bounded regular IFC and IDS files without symlink or reparse components.")
    preflight=_preflight_ids(safe_ids)
    if preflight: return preflight
    try: ifc,tester,reporter=runtime()
    except IdsRuntimeUnavailable:
        return _failure(DiagnosticCode.IDS_RUNTIME_UNAVAILABLE,"ids-runtime","IDS validation runtime is unavailable.","Install the motor-ifc[ids] optional dependency.")
    try:
        specs=tester.open(str(safe_ids),validate=True)
        loaded_specifications=len(specs.specifications)
        loaded_requirement_counts=tuple(len(spec.requirements) for spec in specs.specifications)
        loaded_requirements=sum(loaded_requirement_counts)
        if loaded_specifications>MAX_SPECIFICATIONS or loaded_requirements>MAX_REQUIREMENTS:
            return _failure(DiagnosticCode.LIMIT_EXCEEDED,"ids-validate","IDS exceeds supported report bounds.","Reduce the IDS to at most 100 specifications and 1,000 requirements.")
        model=ifc.open(str(safe_ifc))
        specs.validate(model)
        report=reporter.Json(specs).report()
    except Exception as exc:
        if exc.__class__.__name__ in {"IdsXmlValidationError","XMLSchemaValidationError","ParseError"}:
            return _failure(DiagnosticCode.INVALID_IDS,"ids-parse","IDS XML does not conform to buildingSMART IDS 1.0.","Validate the caller-supplied IDS against the IDS 1.0 XML Schema.")
        return _failure(DiagnosticCode.IDS_VALIDATION_FAILED,"ids-validate","IDS validation could not be completed.","Provide readable IFC and schema-valid buildingSMART IDS 1.0 inputs.")
    try:
        report_rows=report.get("specifications",())
        if not isinstance(report_rows,(list,tuple)):
            raise TypeError
        if len(report_rows)>MAX_SPECIFICATIONS:
            return _failure(DiagnosticCode.LIMIT_EXCEEDED,"ids-validate","IDS validation exceeds supported report bounds.","Reduce the IDS to at most 100 specifications and 1,000 requirements.")
        report_specifications=int(report.get("total_specifications",0))
        report_requirements=int(report.get("total_requirements",0))
        checks=int(report.get("total_checks",0))
        if report_specifications>MAX_SPECIFICATIONS:
            return _failure(DiagnosticCode.LIMIT_EXCEEDED,"ids-validate","IDS validation exceeds supported report bounds.","Reduce the IDS to at most 100 specifications and 1,000 requirements.")
        if not all(isinstance(item,dict) and isinstance(item.get("requirements",()),(list,tuple)) for item in report_rows):
            raise TypeError
        row_requirement_counts=tuple(len(item.get("requirements",())) for item in report_rows)
        row_requirements=sum(row_requirement_counts)
        if row_requirements>MAX_REQUIREMENTS or report_requirements>MAX_REQUIREMENTS:
            return _failure(DiagnosticCode.LIMIT_EXCEEDED,"ids-validate","IDS validation exceeds supported report bounds.","Reduce the IDS to at most 100 specifications and 1,000 requirements.")
        summary_counts=(
            report_specifications,int(report.get("total_specifications_pass",0)),int(report.get("total_specifications_fail",0)),
            report_requirements,int(report.get("total_requirements_pass",0)),int(report.get("total_requirements_fail",0)),
            checks,int(report.get("total_checks_pass",0)),int(report.get("total_checks_fail",0)),
        )
        row_checks=tuple(int(item.get("total_checks",0)) for item in report_rows)
        row_checks_passed=tuple(int(item.get("total_checks_pass",0)) for item in report_rows)
        row_checks_failed=tuple(int(item.get("total_checks_fail",0)) for item in report_rows)
    except Exception:
        return _failure(DiagnosticCode.IDS_VALIDATION_FAILED,"ids-validate","IDS validation report is invalid.","Retry with the supported IDS runtime and schema-valid inputs.")
    if checks>MAX_CHECKS or sum(row_checks)>MAX_CHECKS:
        return _failure(DiagnosticCode.LIMIT_EXCEEDED,"ids-validate","IDS validation exceeds supported check bounds.","Reduce IFC applicability or split the IDS validation request.")
    if (any(value<0 for value in summary_counts+row_checks+row_checks_passed+row_checks_failed)
            or len(report_rows)!=loaded_specifications or report_specifications!=loaded_specifications
            or row_requirement_counts!=loaded_requirement_counts or report_requirements!=loaded_requirements
            or summary_counts[1]+summary_counts[2]!=report_specifications
            or summary_counts[4]+summary_counts[5]!=report_requirements
            or summary_counts[7]+summary_counts[8]!=checks
            or any(passed+failed!=total for total,passed,failed in zip(row_checks,row_checks_passed,row_checks_failed))
            or sum(row_checks)!=checks or sum(row_checks_passed)!=summary_counts[7] or sum(row_checks_failed)!=summary_counts[8]):
        return _failure(DiagnosticCode.IDS_VALIDATION_FAILED,"ids-validate","IDS validation report is inconsistent.","Retry with the supported IDS runtime and schema-valid inputs.")
    try:
        rows=tuple(IdsSpecificationResult(index=index,name=_text(item.get("name")),passed=bool(item.get("status")),skipped=bool(item.get("is_skipped")),applicable_entities=int(item.get("total_applicable",0)),applicable_entities_passed=int(item.get("total_applicable_pass",0)),applicable_entities_failed=int(item.get("total_applicable_fail",0)),requirements=len(item.get("requirements",())),checks=int(item.get("total_checks",0)),checks_passed=int(item.get("total_checks_pass",0)),checks_failed=int(item.get("total_checks_fail",0))) for index,item in enumerate(report_rows,start=1))
        summary=IdsValidationSummary(specifications=summary_counts[0],specifications_passed=summary_counts[1],specifications_failed=summary_counts[2],requirements=summary_counts[3],requirements_passed=summary_counts[4],requirements_failed=summary_counts[5],checks=summary_counts[6],checks_passed=summary_counts[7],checks_failed=summary_counts[8])
        valid=bool(report.get("status"))
        ids_title=_text(report.get("title"))
    except Exception:
        return _failure(DiagnosticCode.IDS_VALIDATION_FAILED,"ids-validate","IDS validation report is invalid.","Retry with the supported IDS runtime and schema-valid inputs.")
    diagnostics=() if valid else (error(DiagnosticCode.IDS_REQUIREMENTS_FAILED,"ids-validate","IFC does not satisfy all caller-supplied IDS requirements.","Inspect the bounded specification results and correct the IFC or authoritative IDS."),)
    return IdsValidationResult(success=True,valid=valid,ids_title=ids_title,summary=summary,specifications=rows,diagnostics=diagnostics)
