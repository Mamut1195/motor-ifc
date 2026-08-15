"""Stable, safe diagnostics."""
from enum import IntEnum
from typing import Literal
from pydantic import BaseModel, ConfigDict

class DiagnosticCode(IntEnum):
    INVALID_CONTRACT = 2000
    UNSUPPORTED_PROFILE = 2001
    UNAPPROVED_AUTHORITY = 2002
    LIMIT_EXCEEDED = 2003
    INVALID_REFERENCE = 2100
    UNSUPPORTED_ELEMENT = 2101
    IFC_RUNTIME_UNAVAILABLE = 2200
    COMPILATION_FAILED = 2201
    IFC_REOPEN_FAILED = 2300
    UNSAFE_PATH = 2400
    PUBLICATION_FAILED = 2401
    JOB_ROOT_UNAVAILABLE = 2402
    FEDERATION_MISMATCH = 2500
    IDS_RUNTIME_UNAVAILABLE = 2600
    INVALID_IDS = 2601
    UNSUPPORTED_IDS = 2602
    IDS_VALIDATION_FAILED = 2603
    IDS_REQUIREMENTS_FAILED = 2604
    VIEWER_CONVERSION_FAILED = 2700
    READER_EXTRACTION_FAILED = 2800
    UNSUPPORTED_READER_VALUE = 2801
    MODEL_AUDIT_FAILED = 2900
    MODEL_REPAIR_FAILED = 2901
    MODEL_NOT_REPAIRABLE = 2902
    ELEMENT_INDEX_FAILED = 3000
    UNRESOLVED_UNIT_SCALE = 3001
    QUALITY_SCORE_FAILED = 3100
    QUANTITY_EVIDENCE_FAILED = 3200
    INVALID_QUANTITY_DECISIONS = 3201
    UNMATCHED_QUANTITY_DECISION = 3202

class Diagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    severity: Literal["error", "warning", "info"]
    code: int
    stage: str
    message: str
    suggested_action: str
    json_pointer: str | None = None
    source_id: str | None = None
    global_id: str | None = None
    ifc_class: str | None = None

def error(code: DiagnosticCode, stage: str, message: str, action: str, **context: object) -> Diagnostic:
    return Diagnostic(severity="error", code=int(code), stage=stage, message=message, suggested_action=action, **context)

def warning(code: DiagnosticCode, stage: str, message: str, action: str, **context: object) -> Diagnostic:
    """A successful operation reporting something the caller must not ignore.

    Reserved for facts an operation can state truthfully but cannot resolve, where
    silence would read as "nothing to see". `element-index.v1` uses it for a
    quantity whose unit will not normalize: dropping the SI value without saying so
    is the failure mode that produces order-of-magnitude errors no later check finds.
    """
    return Diagnostic(severity="warning", code=int(code), stage=stage, message=message, suggested_action=action, **context)
