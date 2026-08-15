"""Bounded, deterministic, read-only IFC object extraction."""
from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Any, BinaryIO, Literal

from ._version import VERSION
from .diagnostics import DiagnosticCode, error
from .models import (
    ReaderEntity,
    ReaderEntityMaterialsV2,
    ReaderEntityMetadata,
    ReaderEntityProperties,
    ReaderEntityQuantities,
    ReaderEntityQuantitiesV2,
    ReaderEntityV2,
    ReaderExtractionResult,
    ReaderExtractionResultV2,
    ReaderMaterialAssociation,
    ReaderQuantitySet,
)
from .security import JobRootUnavailable, UnsafePathError, secure_existing_input, secure_new_output, temp_root

CONTRACT_VERSION = "reader-extraction.v1"
CONTRACT_VERSION_V2 = "reader-extraction.v2"
IFCOPENSHELL_VERSION = "0.8.5"
MAX_IFC_BYTES = 100_000_000
MAX_ENTITIES = 10_000
MAX_SETS_PER_ENTITY = 1_000
MAX_NODES_PER_ENTITY = 10_000
MAX_TOTAL_NODES = 100_000
MAX_TOTAL_NODES_V2 = 5_000_000
MAX_INLINE_RESULT_BYTES = 900_000
MAX_DEPTH = 16
MAX_STRING_LENGTH = 1_000
MAX_ARRAY_ITEMS = 1_000
PUBLICATION_ARTIFACT = "extraction.json"
PUBLICATION_MANIFEST = "extraction-manifest.json"
PUBLICATION_MANIFEST_SCHEMA = "motor-ifc.reader-publication-manifest.v1"

Projection = Literal["rich", "metadata", "properties", "quantities"]
ProjectionV2 = Literal["rich", "metadata", "properties", "quantities", "materials"]


class ReaderRuntimeUnavailable(RuntimeError):
    pass


class _BoundExceeded(ValueError):
    pass


class _InlineBoundExceeded(ValueError):
    pass


class _UnsupportedValue(ValueError):
    pass


class _SchemaInvalid(ValueError):
    pass


class _Counter:
    def __init__(self, total_budget: int) -> None:
        self.entity = 0
        self.total = 0
        self.total_budget = total_budget

    def node(self) -> None:
        self.entity += 1
        self.total += 1
        if self.entity > MAX_NODES_PER_ENTITY or self.total > self.total_budget:
            raise _BoundExceeded

    def ensure(self, additional: int) -> None:
        if self.entity + additional > MAX_NODES_PER_ENTITY or self.total + additional > self.total_budget:
            raise _BoundExceeded


def runtime() -> tuple[Any, Any]:
    try:
        ifc = importlib.import_module("ifcopenshell")
        element = importlib.import_module("ifcopenshell.util.element")
    except ImportError as exc:
        raise ReaderRuntimeUnavailable from exc
    if getattr(ifc, "version", None) != IFCOPENSHELL_VERSION:
        raise ReaderRuntimeUnavailable
    return ifc, element


def _failure(result_type: Any, code: DiagnosticCode, stage: str, message: str, action: str) -> Any:
    return result_type(
        success=False,
        diagnostics=(error(code, stage, message, action),),
    )


def _file_identity(info: os.stat_result) -> tuple[int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _open_source_stream(path: Path) -> BinaryIO:
    if os.name != "nt":
        return path.open("rb")

    import ctypes
    import msvcrt
    from ctypes import wintypes

    create_file = ctypes.WinDLL("kernel32", use_last_error=True).CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    handle = create_file(os.fspath(path), 0x80000000, 0x00000001, None, 3, 0x08000000, None)
    if handle == ctypes.c_void_p(-1).value:
        code = ctypes.get_last_error()
        raise OSError(code, ctypes.FormatError(code), path)
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except Exception:
        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(handle)
        raise
    try:
        return os.fdopen(descriptor, "rb")
    except Exception:
        os.close(descriptor)
        raise


def _open_source(path: Path) -> tuple[BinaryIO, os.stat_result]:
    stream = _open_source_stream(path)
    try:
        baseline = os.fstat(stream.fileno())
        if not stat.S_ISREG(baseline.st_mode) or baseline.st_size > MAX_IFC_BYTES:
            raise UnsafePathError("input exceeds the byte limit")
        if _file_identity(path.stat()) != _file_identity(baseline):
            raise UnsafePathError("input identity changed during secure open")
        return stream, baseline
    except Exception:
        stream.close()
        raise


def _snapshot_source(stream: BinaryIO, baseline: os.stat_result, target: Path) -> str:
    digest = hashlib.sha256()
    total = 0
    stream.seek(0)
    with target.open("xb") as snapshot:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            total += len(block)
            if total > MAX_IFC_BYTES:
                raise UnsafePathError("input exceeds the byte limit")
            digest.update(block)
            snapshot.write(block)
    if total != baseline.st_size or _file_identity(os.fstat(stream.fileno())) != _file_identity(baseline):
        raise RuntimeError("source IFC changed during snapshot")
    target.chmod(stat.S_IREAD)
    return digest.hexdigest()


def _source_unchanged(path: Path, stream: BinaryIO, baseline: os.stat_result, expected_hash: str) -> bool:
    try:
        if _file_identity(os.fstat(stream.fileno())) != _file_identity(baseline) or _file_identity(path.stat()) != _file_identity(baseline):
            return False
        digest = hashlib.sha256()
        total = 0
        stream.seek(0)
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            total += len(block)
            if total > MAX_IFC_BYTES:
                return False
            digest.update(block)
        return total == baseline.st_size and digest.hexdigest() == expected_hash and _file_identity(os.fstat(stream.fileno())) == _file_identity(baseline)
    except OSError:
        return False


def _validate_schema(model: Any) -> None:
    validator = importlib.import_module("ifcopenshell.validate")
    logger = validator.json_logger()
    validator.validate(model, logger)
    if any(item.get("level") == "error" for item in logger.statements):
        raise _SchemaInvalid


def _normalize(value: Any, counter: _Counter, depth: int = 0, active: set[int] | None = None) -> Any:
    if depth > MAX_DEPTH:
        raise _BoundExceeded
    if value is None or isinstance(value, (bool, str, int, float)):
        if isinstance(value, str) and len(value) > MAX_STRING_LENGTH:
            raise _BoundExceeded
        if isinstance(value, float) and not math.isfinite(value):
            raise _UnsupportedValue
        counter.node()
        return value
    if not isinstance(value, (dict, list, tuple)):
        raise _UnsupportedValue
    identity = id(value)
    active = active or set()
    if identity in active:
        raise _UnsupportedValue
    active.add(identity)
    try:
        counter.node()
        if isinstance(value, dict):
            member_count = 0
            for key in value:
                if not isinstance(key, str):
                    raise _UnsupportedValue
                if len(key) > MAX_STRING_LENGTH:
                    raise _BoundExceeded
                if key != "id":
                    member_count += 1
            counter.ensure(member_count)
            return {
                key: _normalize(value[key], counter, depth + 1, active)
                for key in sorted(value)
                if key != "id"
            }
        if len(value) > MAX_ARRAY_ITEMS:
            raise _BoundExceeded
        counter.ensure(len(value))
        return [_normalize(item, counter, depth + 1, active) for item in value]
    finally:
        active.remove(identity)


def _metadata_value(value: Any, counter: _Counter) -> str | None:
    if value is None:
        counter.node()
        return None
    if not isinstance(value, str):
        raise _UnsupportedValue
    if len(value) > MAX_STRING_LENGTH:
        raise _BoundExceeded
    counter.node()
    return value


def _entity_metadata(item: Any, counter: _Counter) -> dict[str, Any]:
    return {
        "global_id": _metadata_value(item.GlobalId, counter),
        "ifc_class": _metadata_value(item.is_a(), counter),
        "name": _metadata_value(getattr(item, "Name", None), counter),
        "description": _metadata_value(getattr(item, "Description", None), counter),
        "object_type": _metadata_value(getattr(item, "ObjectType", None), counter),
        "tag": _metadata_value(getattr(item, "Tag", None), counter),
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _json_fields(fields: dict[str, Any]) -> str:
    """Serialize extra result fields in caller-declared order.

    Insertion order, never sorted: the streamed artifact must come out byte-identical
    to the inline document, and that document's key order is the DTO's field order.
    """
    if not fields:
        return ""
    return "," + ",".join(
        json.dumps(key) + ":" + json.dumps(_jsonable(value), separators=(",", ":"))
        for key, value in fields.items()
    )


def _default_scope(model: Any) -> tuple[list[Any], dict[str, Any]]:
    return [item for item in model.by_type("IfcObject") if isinstance(getattr(item, "GlobalId", None), str) and item.GlobalId], {}


def _extract_pipeline(
    path: str | Path,
    builder: Any,
    result_type: Any,
    label: str,
    total_budget: int,
    projection: str | None = None,
    output_dir: str | Path | None = None,
    inline_byte_cap: int | None = None,
    prepare: Any = None,
    finalize: Any = None,
) -> Any:
    """Shared bounded pipeline: secure open, snapshot, validation, ordered entities, atomic errors.

    ``prepare`` selects the entity scope and returns result fields knowable before the
    entity loop; ``finalize`` returns the fields only knowable after it. Both default to
    the frozen reader behaviour, so v1 and v2 are unaffected.
    """
    source_stream: BinaryIO | None = None
    try:
        safe_path = secure_existing_input(path, MAX_IFC_BYTES)
        source_stream, source_before = _open_source(safe_path)
    except (UnsafePathError, OSError, TypeError) as exc:
        code = DiagnosticCode.LIMIT_EXCEEDED if "byte limit" in str(exc) else DiagnosticCode.UNSAFE_PATH
        return _failure(result_type, code, "reader-input", "IFC input is outside the supported file boundary.", "Provide a bounded regular IFC file without symlink or reparse components.")
    try:
        ifc, element_util = runtime()
    except ReaderRuntimeUnavailable:
        source_stream.close()
        return _failure(result_type, DiagnosticCode.IFC_RUNTIME_UNAVAILABLE, "reader-runtime", "IfcOpenShell 0.8.5 runtime is unavailable.", "Install the motor-ifc[ifc] optional dependency.")
    except Exception:
        source_stream.close()
        return _failure(result_type, DiagnosticCode.READER_EXTRACTION_FAILED, "reader-runtime", "IFC reader runtime could not be initialized.", "Install the supported motor-ifc[ifc] runtime and retry.")
    try:
        staging = temp_root()
    except JobRootUnavailable:
        source_stream.close()
        return _failure(result_type, DiagnosticCode.JOB_ROOT_UNAVAILABLE, "reader-input", "No job root is configured, so the private snapshot has nowhere contained to go.", "Set MOTOR_IFC_JOB_ROOT to a usable directory before extracting.")
    try:
        with tempfile.TemporaryDirectory(prefix="motor-ifc-reader-", dir=staging) as snapshot_dir:
            snapshot = Path(snapshot_dir) / "source.ifc"
            source_hash = _snapshot_source(source_stream, source_before, snapshot)
            model = ifc.open(str(snapshot))
            _validate_schema(model)
            scoped, pre_fields = (prepare or _default_scope)(model)
            if len(scoped) > MAX_ENTITIES:
                raise _BoundExceeded
            scoped.sort(key=lambda item: (item.GlobalId, item.is_a()))
            counter = _Counter(total_budget)
            if output_dir is None:
                result_entities = []
                inline_size = 0
                for item in scoped:
                    counter.entity = 0
                    entity = builder(model, element_util, item, counter)
                    if entity is None:
                        # An aggregate contract streams every entity to accumulate and emits
                        # none of them. Nothing else returns None.
                        continue
                    if inline_byte_cap is not None:
                        inline_size += len(json.dumps(entity.model_dump(mode="json"), separators=(",", ":")).encode("utf-8")) + 1
                        if inline_size > inline_byte_cap:
                            raise _InlineBoundExceeded
                    result_entities.append(entity)
                if not _source_unchanged(safe_path, source_stream, source_before, source_hash):
                    raise RuntimeError("source IFC changed during extraction")
                return result_type(
                    success=True,
                    source_schema=str(model.schema),
                    entity_count=len(scoped),
                    **({"entities": tuple(result_entities)} if "entities" in result_type.model_fields else {}),
                    **pre_fields,
                    **(finalize() if finalize is not None else {}),
                    **({"source_sha256": source_hash} if "source_sha256" in result_type.model_fields else {}),
                )
            try:
                target = secure_new_output(output_dir)
            except (UnsafePathError, OSError, TypeError):
                return _failure(result_type, DiagnosticCode.UNSAFE_PATH, "reader-output", "Output directory is outside the secure publication boundary.", "Use a real, non-symlink output directory without traversal.")
            stage = Path(tempfile.mkdtemp(prefix=f".{target.name}.stage-", dir=target.parent))
            try:
                with (stage / PUBLICATION_ARTIFACT).open("w", encoding="utf-8") as artifact_stream:
                    artifact_stream.write(
                        '{"contract_version":' + json.dumps(label)
                        + ',"success":true,"source_schema":' + json.dumps(str(model.schema))
                        + ',"entity_count":' + str(len(scoped))
                        + _json_fields(pre_fields)
                        + ',"entities":['
                    )
                    separator = ""
                    for item in scoped:
                        counter.entity = 0
                        entity = builder(model, element_util, item, counter)
                        artifact_stream.write(separator + json.dumps(entity.model_dump(mode="json"), separators=(",", ":")))
                        separator = ","
                    post_fields = finalize() if finalize is not None else {"diagnostics": []}
                    artifact_stream.write(
                        ']' + _json_fields(post_fields)
                        + ',"truncated":false,"publication":"none","artifact_filenames":[]'
                        ',"source_sha256":' + json.dumps(source_hash)
                        + ',"extraction_sha256":null}'
                    )
                extraction_hash = hashlib.sha256((stage / PUBLICATION_ARTIFACT).read_bytes()).hexdigest()
                manifest = {
                    "schema": PUBLICATION_MANIFEST_SCHEMA,
                    "contract": label,
                    "projection": projection,
                    "source_sha256": source_hash,
                    "extraction_sha256": extraction_hash,
                    "entity_count": len(scoped),
                    "artifacts": [PUBLICATION_ARTIFACT, PUBLICATION_MANIFEST],
                    "versions": {"engine": VERSION, "contract": label, "ifcopenshell": str(ifc.version)},
                }
                (stage / PUBLICATION_MANIFEST).write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
                if not _source_unchanged(safe_path, source_stream, source_before, source_hash):
                    raise RuntimeError("source IFC changed during extraction")
                os.replace(stage, target)
                return result_type(
                    success=True,
                    source_schema=str(model.schema),
                    entity_count=len(scoped),
                    publication="immutable-directory",
                    artifact_filenames=(PUBLICATION_ARTIFACT, PUBLICATION_MANIFEST),
                    source_sha256=source_hash,
                    extraction_sha256=extraction_hash,
                    **pre_fields,
                    **{key: value for key, value in post_fields.items() if key != "diagnostics" or value},
                )
            finally:
                shutil.rmtree(stage, ignore_errors=True)
    except _SchemaInvalid:
        return _failure(result_type, DiagnosticCode.READER_EXTRACTION_FAILED, "reader-validate", "IFC failed schema validation.", "Repair the IFC with a schema-conforming tool before extraction.")
    except _BoundExceeded:
        return _failure(result_type, DiagnosticCode.LIMIT_EXCEEDED, "reader-extract", f"IFC extraction exceeds {label} bounds.", "Reduce or split the IFC before extraction.")
    except _InlineBoundExceeded:
        return _failure(result_type, DiagnosticCode.LIMIT_EXCEEDED, "reader-extract", f"IFC extraction exceeds {label} inline bounds.", "Extract with an output_dir to publish the result as an immutable artifact, or reduce the projection scope.")
    except _UnsupportedValue:
        return _failure(result_type, DiagnosticCode.UNSUPPORTED_READER_VALUE, "reader-normalize", f"IFC extraction contains a value unsupported by {label}.", "Remove unsupported entity references, binary, non-finite, or cyclic property values.")
    except Exception:
        return _failure(result_type, DiagnosticCode.READER_EXTRACTION_FAILED, "reader-extract", "IFC could not be safely extracted.", "Provide a readable schema-valid IFC file supported by IfcOpenShell 0.8.5.")
    finally:
        source_stream.close()


def extract(path: str | Path, projection: Projection = "rich") -> ReaderExtractionResult:
    """Extract stable object metadata and selected rich sections without publication."""

    def builder(model: Any, element_util: Any, item: Any, counter: _Counter) -> Any:
        metadata = _entity_metadata(item, counter)
        properties: dict[str, Any] = {}
        quantities: dict[str, Any] = {}
        if projection in {"rich", "properties"}:
            raw_properties = element_util.get_psets(item, psets_only=True)
            if not isinstance(raw_properties, dict):
                raise _UnsupportedValue
            properties = _normalize(raw_properties, counter)
        if projection in {"rich", "quantities"}:
            raw_quantities = element_util.get_psets(item, qtos_only=True)
            if not isinstance(raw_quantities, dict):
                raise _UnsupportedValue
            quantities = _normalize(raw_quantities, counter)
        if len(properties) + len(quantities) > MAX_SETS_PER_ENTITY:
            raise _BoundExceeded
        if projection == "rich":
            return ReaderEntity(**metadata, properties=properties, quantities=quantities)
        if projection == "properties":
            return ReaderEntityProperties(**metadata, properties=properties)
        if projection == "quantities":
            return ReaderEntityQuantities(**metadata, quantities=quantities)
        return ReaderEntityMetadata(**metadata)

    return _extract_pipeline(path, builder, ReaderExtractionResult, CONTRACT_VERSION, MAX_TOTAL_NODES)


_QUANTITY_MEASURES = {
    "IfcQuantityLength": ("LengthValue", "IfcLengthMeasure"),
    "IfcQuantityArea": ("AreaValue", "IfcAreaMeasure"),
    "IfcQuantityVolume": ("VolumeValue", "IfcVolumeMeasure"),
    "IfcQuantityCount": ("CountValue", "IfcCountMeasure"),
    "IfcQuantityWeight": ("WeightValue", "IfcMassMeasure"),
    "IfcQuantityTime": ("TimeValue", "IfcTimeMeasure"),
}

_MATERIAL_KINDS = {
    "IfcMaterial": "material",
    "IfcMaterialList": "material_list",
    "IfcMaterialLayerSet": "layer_set",
    "IfcMaterialLayerSetUsage": "layer_set_usage",
    "IfcMaterialProfileSet": "profile_set",
    "IfcMaterialProfileSetUsage": "profile_set_usage",
    "IfcMaterialConstituentSet": "constituent_set",
}


def _unit_util() -> Any:
    try:
        return importlib.import_module("ifcopenshell.util.unit")
    except ImportError as exc:
        raise ReaderRuntimeUnavailable from exc


def _plain_scalar(value: Any) -> Any:
    raw = getattr(value, "wrappedValue", value)
    if raw is None or isinstance(raw, (bool, str, int, float)):
        return raw
    raise _UnsupportedValue


def _resolve_quantity_unit(quantity: Any, value: Any, measure: str, model: Any, unit_util: Any) -> tuple[dict[str, Any], float | None]:
    unit_type = None
    try:
        unit_type = unit_util.get_measure_unit_type(measure)
    except Exception:
        unit_type = None
    explicit = getattr(quantity, "Unit", None)
    if explicit is not None:
        symbol = None
        try:
            symbol = unit_util.get_unit_symbol(explicit)
        except Exception:
            symbol = None
        normalized = None
        try:
            si_name = unit_util.si_type_names.get(getattr(explicit, "UnitType", None))
            if si_name is not None and isinstance(value, (int, float)) and not isinstance(value, bool):
                normalized = float(unit_util.convert(float(value), getattr(explicit, "Prefix", None), explicit.Name, None, si_name))
        except Exception:
            normalized = None
        unit = {
            "source": "quantity",
            "name": _plain_scalar(getattr(explicit, "Name", None)),
            "symbol": symbol,
            "prefix": _plain_scalar(getattr(explicit, "Prefix", None)),
            "unit_type": _plain_scalar(getattr(explicit, "UnitType", None)) or unit_type,
        }
        return unit, normalized
    if unit_type is not None:
        project_unit = None
        try:
            project_unit = unit_util.get_project_unit(model, unit_type)
        except Exception:
            project_unit = None
        if project_unit is not None:
            symbol = None
            try:
                symbol = unit_util.get_unit_symbol(project_unit)
            except Exception:
                symbol = None
            normalized = None
            try:
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    normalized = float(value) * float(unit_util.calculate_unit_scale(model, unit_type))
            except Exception:
                normalized = None
            unit = {
                "source": "project",
                "name": _plain_scalar(getattr(project_unit, "Name", None)),
                "symbol": symbol,
                "prefix": _plain_scalar(getattr(project_unit, "Prefix", None)),
                "unit_type": unit_type,
            }
            return unit, normalized
    return {"source": "unknown", "name": None, "symbol": None, "prefix": None, "unit_type": unit_type}, None


def _quantity_record(quantity: Any, model: Any, unit_util: Any, depth: int = 0) -> dict[str, Any]:
    if depth > MAX_DEPTH:
        raise _BoundExceeded
    ifc_class = quantity.is_a()
    record = {
        "name": _plain_scalar(getattr(quantity, "Name", None)),
        "description": _plain_scalar(getattr(quantity, "Description", None)),
        "ifc_class": ifc_class,
        "formula": _plain_scalar(getattr(quantity, "Formula", None)),
        "discrimination": _plain_scalar(getattr(quantity, "Discrimination", None)) if ifc_class == "IfcPhysicalComplexQuantity" else None,
        "value": None,
        "value_type": None,
        "unit": None,
        "normalized_value": None,
        "components": [],
    }
    attribute, measure = _QUANTITY_MEASURES.get(ifc_class, (None, None))
    if attribute is not None:
        value = _plain_scalar(getattr(quantity, attribute, None))
        record["value"] = value
        record["value_type"] = measure
        unit, normalized = _resolve_quantity_unit(quantity, value, measure, model, unit_util)
        record["unit"] = unit
        record["normalized_value"] = normalized
    elif ifc_class == "IfcPhysicalComplexQuantity":
        components = getattr(quantity, "HasQuantities", None) or ()
        if len(components) > MAX_ARRAY_ITEMS:
            raise _BoundExceeded
        record["components"] = [_quantity_record(component, model, unit_util, depth + 1) for component in components]
    return record


def _quantity_set_record(definition: Any, source: str, relation_global_id: Any, shadowed: bool, model: Any, unit_util: Any) -> dict[str, Any]:
    quantities = getattr(definition, "Quantities", None) or ()
    if len(quantities) > MAX_ARRAY_ITEMS:
        raise _BoundExceeded
    return {
        "global_id": _plain_scalar(getattr(definition, "GlobalId", None)),
        "name": _plain_scalar(getattr(definition, "Name", None)),
        "description": _plain_scalar(getattr(definition, "Description", None)),
        "method_of_measurement": _plain_scalar(getattr(definition, "MethodOfMeasurement", None)),
        "source": source,
        "relation_global_id": relation_global_id,
        "shadowed_by_occurrence": shadowed,
        "quantities": [_quantity_record(quantity, model, unit_util) for quantity in quantities],
    }


def _quantity_sets(item: Any, model: Any, element_util: Any, unit_util: Any) -> list[dict[str, Any]]:
    occurrence_sets = []
    for relation in getattr(item, "IsDefinedBy", None) or ():
        if not relation.is_a("IfcRelDefinesByProperties"):
            continue
        definition = getattr(relation, "RelatingPropertyDefinition", None)
        if definition is not None and definition.is_a("IfcElementQuantity"):
            occurrence_sets.append((definition, _plain_scalar(getattr(relation, "GlobalId", None))))
    type_sets = []
    type_object = element_util.get_type(item)
    if type_object is not None:
        for definition in getattr(type_object, "HasPropertySets", None) or ():
            if definition.is_a("IfcElementQuantity"):
                type_sets.append(definition)
    occurrence_names = {_plain_scalar(getattr(definition, "Name", None)) for definition, _ in occurrence_sets}
    records = [
        _quantity_set_record(definition, "occurrence", relation_global_id, False, model, unit_util)
        for definition, relation_global_id in occurrence_sets
    ]
    records.extend(
        _quantity_set_record(definition, "type", None, _plain_scalar(getattr(definition, "Name", None)) in occurrence_names, model, unit_util)
        for definition in type_sets
    )
    records.sort(key=lambda record: (
        record["source"] != "occurrence",
        record["name"] if isinstance(record["name"], str) else "",
        record["global_id"] if isinstance(record["global_id"], str) else "",
    ))
    return records


def _material_name(material: Any) -> Any:
    if material is None:
        return None
    return _plain_scalar(getattr(material, "Name", None))


def _layer_record(layer: Any) -> dict[str, Any]:
    return {
        "material_name": _material_name(getattr(layer, "Material", None)),
        "thickness": _plain_scalar(getattr(layer, "LayerThickness", None)),
        "is_ventilated": _plain_scalar(getattr(layer, "IsVentilated", None)),
        "priority": _plain_scalar(getattr(layer, "Priority", None)),
        "category": _plain_scalar(getattr(layer, "Category", None)),
    }


def _profile_record(profile: Any) -> dict[str, Any]:
    return {
        "name": _plain_scalar(getattr(profile, "Name", None)),
        "material_name": _material_name(getattr(profile, "Material", None)),
        "category": _plain_scalar(getattr(profile, "Category", None)),
        "priority": _plain_scalar(getattr(profile, "Priority", None)),
    }


def _constituent_record(constituent: Any) -> dict[str, Any]:
    return {
        "name": _plain_scalar(getattr(constituent, "Name", None)),
        "description": _plain_scalar(getattr(constituent, "Description", None)),
        "category": _plain_scalar(getattr(constituent, "Category", None)),
        "fraction": _plain_scalar(getattr(constituent, "Fraction", None)),
        "material_name": _material_name(getattr(constituent, "Material", None)),
    }


def _material_record(material: Any, source: str, relation_global_id: Any) -> dict[str, Any]:
    ifc_class = material.is_a()
    kind = _MATERIAL_KINDS.get(ifc_class)
    if kind is None:
        raise _UnsupportedValue
    target = material
    usage_direction = None
    usage_offset = None
    if ifc_class == "IfcMaterialLayerSetUsage":
        usage_direction = _plain_scalar(getattr(material, "LayerSetDirection", None))
        usage_offset = _plain_scalar(getattr(material, "OffsetFromReferenceLine", None))
        target = getattr(material, "ForLayerSet", None)
    elif ifc_class == "IfcMaterialProfileSetUsage":
        target = getattr(material, "ForProfileSet", None)
    if target is None:
        raise _UnsupportedValue
    target_class = target.is_a()
    record = {
        "source": source,
        "relation_global_id": relation_global_id,
        "kind": kind,
        "name": None,
        "description": None,
        "category": None,
        "materials": [],
        "layers": [],
        "profiles": [],
        "constituents": [],
        "usage_direction": usage_direction,
        "usage_offset": usage_offset,
    }
    if target_class == "IfcMaterial":
        record["name"] = _plain_scalar(getattr(target, "Name", None))
        record["description"] = _plain_scalar(getattr(target, "Description", None))
        record["category"] = _plain_scalar(getattr(target, "Category", None))
    elif target_class == "IfcMaterialList":
        members = getattr(target, "Materials", None) or ()
        if len(members) > MAX_ARRAY_ITEMS:
            raise _BoundExceeded
        record["materials"] = [_material_name(member) for member in members]
    elif target_class == "IfcMaterialLayerSet":
        record["name"] = _plain_scalar(getattr(target, "LayerSetName", None))
        record["description"] = _plain_scalar(getattr(target, "Description", None))
        layers = getattr(target, "MaterialLayers", None) or ()
        if len(layers) > MAX_ARRAY_ITEMS:
            raise _BoundExceeded
        record["layers"] = [_layer_record(layer) for layer in layers]
    elif target_class == "IfcMaterialProfileSet":
        record["name"] = _plain_scalar(getattr(target, "Name", None))
        record["description"] = _plain_scalar(getattr(target, "Description", None))
        profiles = getattr(target, "MaterialProfiles", None) or ()
        if len(profiles) > MAX_ARRAY_ITEMS:
            raise _BoundExceeded
        record["profiles"] = [_profile_record(profile) for profile in profiles]
    elif target_class == "IfcMaterialConstituentSet":
        record["name"] = _plain_scalar(getattr(target, "Name", None))
        record["description"] = _plain_scalar(getattr(target, "Description", None))
        constituents = getattr(target, "MaterialConstituents", None) or ()
        if len(constituents) > MAX_ARRAY_ITEMS:
            raise _BoundExceeded
        record["constituents"] = [_constituent_record(constituent) for constituent in constituents]
    else:
        raise _UnsupportedValue
    return record


def _material_associations(item: Any, element_util: Any) -> list[dict[str, Any]]:
    associations = []
    for relation in getattr(item, "HasAssociations", None) or ():
        if relation.is_a("IfcRelAssociatesMaterial"):
            material = getattr(relation, "RelatingMaterial", None)
            if material is not None:
                associations.append(_material_record(material, "occurrence", _plain_scalar(getattr(relation, "GlobalId", None))))
    type_object = element_util.get_type(item)
    if type_object is not None:
        for relation in getattr(type_object, "HasAssociations", None) or ():
            if relation.is_a("IfcRelAssociatesMaterial"):
                material = getattr(relation, "RelatingMaterial", None)
                if material is not None:
                    associations.append(_material_record(material, "type", _plain_scalar(getattr(relation, "GlobalId", None))))
    associations.sort(key=lambda record: (
        record["source"] != "occurrence",
        record["relation_global_id"] if isinstance(record["relation_global_id"], str) else "",
    ))
    return associations


def extract_semantic(path: str | Path, projection: ProjectionV2 = "rich", output_dir: str | Path | None = None) -> ReaderExtractionResultV2:
    """Extract lossless semantic quantities and material associations under reader-extraction.v2.

    Without ``output_dir`` the complete result is returned inline under a strict byte cap; with
    ``output_dir`` the result is published as an immutable directory artifact and the inline
    response stays bounded.
    """

    def builder(model: Any, element_util: Any, item: Any, counter: _Counter) -> Any:
        metadata = _entity_metadata(item, counter)
        unit_util = _unit_util()
        properties: dict[str, Any] = {}
        quantity_sets: list[Any] = []
        material_associations: list[Any] = []
        if projection in {"rich", "properties"}:
            raw_properties = element_util.get_psets(item, psets_only=True)
            if not isinstance(raw_properties, dict):
                raise _UnsupportedValue
            properties = _normalize(raw_properties, counter)
        if projection in {"rich", "quantities"}:
            quantity_sets = _normalize({"sets": _quantity_sets(item, model, element_util, unit_util)}, counter)["sets"]
        if projection in {"rich", "materials"}:
            material_associations = _normalize({"associations": _material_associations(item, element_util)}, counter)["associations"]
        if len(properties) + len(quantity_sets) + len(material_associations) > MAX_SETS_PER_ENTITY:
            raise _BoundExceeded
        if projection == "rich":
            return ReaderEntityV2(
                **metadata,
                properties=properties,
                quantity_sets=tuple(ReaderQuantitySet(**record) for record in quantity_sets),
                material_associations=tuple(ReaderMaterialAssociation(**record) for record in material_associations),
            )
        if projection == "properties":
            return ReaderEntityProperties(**metadata, properties=properties)
        if projection == "quantities":
            return ReaderEntityQuantitiesV2(**metadata, quantity_sets=tuple(ReaderQuantitySet(**record) for record in quantity_sets))
        if projection == "materials":
            return ReaderEntityMaterialsV2(**metadata, material_associations=tuple(ReaderMaterialAssociation(**record) for record in material_associations))
        return ReaderEntityMetadata(**metadata)

    return _extract_pipeline(
        path,
        builder,
        ReaderExtractionResultV2,
        CONTRACT_VERSION_V2,
        MAX_TOTAL_NODES_V2,
        projection=projection,
        output_dir=output_dir,
        inline_byte_cap=None if output_dir is not None else MAX_INLINE_RESULT_BYTES,
    )
