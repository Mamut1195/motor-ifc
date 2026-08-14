"""Deterministic drop-only IFC model audit and repair.

The reader stays strict: models with EXPRESS schema errors enter through an explicit
audit -> repair -> extract pipeline. Audit classifies every validation defect with a
typed strategy; repair applies the whitelisted drop-instance strategy only (IFC
relationships are referenced solely through inverse attributes, so removing a
defective relationship never leaves a dangling stored reference), revalidates, and
publishes a repaired artifact plus manifest through staging and one atomic rename.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, BinaryIO, Literal

from ._version import VERSION
from .diagnostics import DiagnosticCode, error
from .models import ModelAuditResult, ModelDefect, ModelRepairResult
from .reader_extraction import (
    MAX_IFC_BYTES,
    ReaderRuntimeUnavailable,
    _open_source,
    _snapshot_source,
    _source_unchanged,
    runtime,
)
from .security import JobRootUnavailable, UnsafePathError, secure_existing_input, secure_new_output, temp_root

AUDIT_CONTRACT_VERSION = "model-audit.v1"
REPAIR_CONTRACT_VERSION = "model-repair.v1"
MAX_AUDIT_DEFECTS = 10_000
_ENTITY_COUNT_TYPES = ("IfcObject", "IfcElementQuantity", "IfcPropertySet", "IfcRelAssociatesMaterial", "IfcMaterial")
_INSTANCE_PATTERN = re.compile(r"#(\d+)=([A-Za-z0-9_]+)")


class _DefectsOverflow(ValueError):
    pass


def _failure(result_type: Any, code: DiagnosticCode, stage: str, message: str, action: str, **fields: Any) -> Any:
    return result_type(success=False, diagnostics=(error(code, stage, message, action),), **fields)


def _validate_model(model: Any) -> list[dict[str, Any]]:
    validator = importlib.import_module("ifcopenshell.validate")
    logger = validator.json_logger()
    validator.validate(model, logger)
    return [statement for statement in logger.statements if statement.get("level") == "error"]


def _resolve_instance(statement: dict[str, Any], model: Any) -> Any:
    instance = statement.get("instance")
    if hasattr(instance, "is_a"):
        return instance
    for text in (str(instance or ""), str(statement.get("message") or "")):
        match = _INSTANCE_PATTERN.search(text)
        if not match:
            continue
        try:
            candidate = model.by_id(int(match.group(1)))
        except Exception:
            return None
        if candidate is not None and str(candidate.is_a()).upper() == match.group(2).upper():
            return candidate
        return None
    return None


def _classify_defects(statements: list[dict[str, Any]], model: Any) -> list[ModelDefect]:
    if len(statements) > MAX_AUDIT_DEFECTS:
        raise _DefectsOverflow
    defects = []
    for statement in statements:
        attribute = statement.get("attribute") or None
        rule: Literal["missing-mandatory-attribute", "schema-rule"] = (
            "missing-mandatory-attribute" if statement.get("message") == "Attribute not optional" else "schema-rule"
        )
        instance = _resolve_instance(statement, model)
        if instance is None:
            defects.append(ModelDefect(step_id=None, ifc_class="unknown", attribute=attribute, rule=rule, repair_strategy="manual"))
            continue
        global_id = getattr(instance, "GlobalId", None)
        defects.append(ModelDefect(
            step_id=int(instance.id()),
            ifc_class=str(instance.is_a()),
            global_id=global_id if isinstance(global_id, str) else None,
            attribute=attribute,
            rule=rule,
            repair_strategy="drop-instance" if instance.is_a("IfcRelationship") else "manual",
        ))
    defects.sort(key=lambda defect: (defect.step_id if defect.step_id is not None else -1, defect.attribute or ""))
    return defects


def _execute(path: str | Path, output_dir: str | Path | None, repair_mode: bool) -> Any:
    result_type = ModelRepairResult if repair_mode else ModelAuditResult
    failed_code = DiagnosticCode.MODEL_REPAIR_FAILED if repair_mode else DiagnosticCode.MODEL_AUDIT_FAILED
    source_stream: BinaryIO | None = None
    try:
        safe_path = secure_existing_input(path, MAX_IFC_BYTES)
        source_stream, source_before = _open_source(safe_path)
    except (UnsafePathError, OSError, TypeError) as exc:
        code = DiagnosticCode.LIMIT_EXCEEDED if "byte limit" in str(exc) else DiagnosticCode.UNSAFE_PATH
        return _failure(result_type, code, "repair-input", "IFC input is outside the supported file boundary.", "Provide a bounded regular IFC file without symlink or reparse components.")
    try:
        ifc, _ = runtime()
    except ReaderRuntimeUnavailable:
        source_stream.close()
        return _failure(result_type, DiagnosticCode.IFC_RUNTIME_UNAVAILABLE, "repair-runtime", "IfcOpenShell 0.8.5 runtime is unavailable.", "Install the motor-ifc[ifc] optional dependency.")
    except Exception:
        source_stream.close()
        return _failure(result_type, failed_code, "repair-runtime", "IFC runtime could not be initialized.", "Install the supported motor-ifc[ifc] runtime and retry.")
    try:
        staging = temp_root()
    except JobRootUnavailable:
        source_stream.close()
        return _failure(result_type, DiagnosticCode.JOB_ROOT_UNAVAILABLE, "repair-input", "No job root is configured, so the private snapshot has nowhere contained to go.", "Set MOTOR_IFC_JOB_ROOT to a usable directory before auditing or repairing.")
    try:
        with tempfile.TemporaryDirectory(prefix="motor-ifc-repair-", dir=staging) as snapshot_dir:
            snapshot = Path(snapshot_dir) / "source.ifc"
            source_hash = _snapshot_source(source_stream, source_before, snapshot)
            model = ifc.open(str(snapshot))
            defects = _classify_defects(_validate_model(model), model)
            if not _source_unchanged(safe_path, source_stream, source_before, source_hash):
                raise RuntimeError("source IFC changed during audit")
            if not repair_mode:
                repairable = [defect for defect in defects if defect.repair_strategy == "drop-instance"]
                return ModelAuditResult(
                    success=True,
                    source_schema=str(model.schema),
                    source_sha256=source_hash,
                    valid=not defects,
                    defect_count=len(defects),
                    repairable_count=len(repairable),
                    manual_count=len(defects) - len(repairable),
                    repairable=not defects or len(repairable) == len(defects),
                    defects=tuple(defects),
                    entity_counts={name: len(model.by_type(name)) for name in _ENTITY_COUNT_TYPES},
                )
            if not defects:
                return ModelRepairResult(success=True, repaired=False, source_sha256=source_hash)
            if any(defect.repair_strategy == "manual" for defect in defects):
                return ModelRepairResult(
                    success=False,
                    remaining_defects=tuple(defects),
                    source_sha256=source_hash,
                    diagnostics=(error(DiagnosticCode.MODEL_NOT_REPAIRABLE, "repair-apply", "IFC contains defects that require manual repair.", "Correct the reported non-relationship defects in the authority tool and retry."),),
                )
            try:
                target = secure_new_output(output_dir)
            except (UnsafePathError, OSError, TypeError):
                return _failure(result_type, DiagnosticCode.UNSAFE_PATH, "repair-output", "Output directory is outside the secure publication boundary.", "Use a real, non-symlink output directory without traversal.")
            stage = Path(tempfile.mkdtemp(prefix=f".{target.name}.stage-", dir=target.parent))
            try:
                for step_id in sorted({defect.step_id for defect in defects if defect.step_id is not None}):
                    model.remove(model.by_id(step_id))
                remaining = _classify_defects(_validate_model(model), model)
                if remaining:
                    return ModelRepairResult(
                        success=False,
                        remaining_defects=tuple(remaining),
                        source_sha256=source_hash,
                        diagnostics=(error(DiagnosticCode.MODEL_REPAIR_FAILED, "repair-validate", "IFC still has schema defects after repair.", "Inspect the remaining defects; manual correction is required."),),
                    )
                artifact = "repaired.ifc"
                model.write(str(stage / artifact))
                repaired_hash = hashlib.sha256((stage / artifact).read_bytes()).hexdigest()
                manifest = {
                    "schema": "motor-ifc.repair-manifest.v1",
                    "source_sha256": source_hash,
                    "repaired_sha256": repaired_hash,
                    "fixes": [defect.model_dump(mode="json") for defect in defects],
                    "versions": {"engine": VERSION, "contract": REPAIR_CONTRACT_VERSION, "ifcopenshell": str(ifc.version)},
                }
                (stage / "repair-manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
                os.replace(stage, target)
                return ModelRepairResult(
                    success=True,
                    repaired=True,
                    defects_fixed=len(defects),
                    fixes=tuple(defects),
                    source_sha256=source_hash,
                    repaired_sha256=repaired_hash,
                    artifacts={"repaired.ifc": target / artifact, "repair-manifest.json": target / "repair-manifest.json"},
                    publication="immutable-directory",
                )
            finally:
                shutil.rmtree(stage, ignore_errors=True)
    except _DefectsOverflow:
        return _failure(result_type, DiagnosticCode.LIMIT_EXCEEDED, "repair-validate", "IFC audit exceeds the defect budget.", "Repair or split the IFC before auditing.")
    except Exception:
        return _failure(result_type, failed_code, "repair-apply" if repair_mode else "repair-validate", "IFC could not be safely audited or repaired.", "Provide a readable IFC file parseable by IfcOpenShell 0.8.5.")
    finally:
        source_stream.close()


def audit(path: str | Path) -> ModelAuditResult:
    """Audit one IFC for schema defects with typed repair strategies; read-only, publishes nothing."""
    return _execute(path, None, False)


def repair(path: str | Path, output_dir: str | Path) -> ModelRepairResult:
    """Repair droppable schema defects into a new immutable artifact; the source is never modified."""
    return _execute(path, output_dir, True)
