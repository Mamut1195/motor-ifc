"""Bounded, deterministic, read-only IFC object extraction."""
from __future__ import annotations

import hashlib
import importlib
import math
import os
import stat
import tempfile
from pathlib import Path
from typing import Any, BinaryIO, Literal

from .diagnostics import DiagnosticCode, error
from .models import (
    ReaderEntity,
    ReaderEntityMetadata,
    ReaderEntityProperties,
    ReaderEntityQuantities,
    ReaderExtractionResult,
)
from .security import UnsafePathError, secure_existing_input

CONTRACT_VERSION = "reader-extraction.v1"
IFCOPENSHELL_VERSION = "0.8.5"
MAX_IFC_BYTES = 100_000_000
MAX_ENTITIES = 10_000
MAX_SETS_PER_ENTITY = 1_000
MAX_NODES_PER_ENTITY = 10_000
MAX_TOTAL_NODES = 100_000
MAX_DEPTH = 16
MAX_STRING_LENGTH = 1_000
MAX_ARRAY_ITEMS = 1_000

Projection = Literal["rich", "metadata", "properties", "quantities"]


class ReaderRuntimeUnavailable(RuntimeError):
    pass


class _BoundExceeded(ValueError):
    pass


class _UnsupportedValue(ValueError):
    pass


class _SchemaInvalid(ValueError):
    pass


class _Counter:
    def __init__(self) -> None:
        self.entity = 0
        self.total = 0

    def node(self) -> None:
        self.entity += 1
        self.total += 1
        if self.entity > MAX_NODES_PER_ENTITY or self.total > MAX_TOTAL_NODES:
            raise _BoundExceeded

    def ensure(self, additional: int) -> None:
        if self.entity + additional > MAX_NODES_PER_ENTITY or self.total + additional > MAX_TOTAL_NODES:
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


def _failure(code: DiagnosticCode, stage: str, message: str, action: str) -> ReaderExtractionResult:
    return ReaderExtractionResult(
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


def extract(path: str | Path, projection: Projection = "rich") -> ReaderExtractionResult:
    """Extract stable object metadata and selected rich sections without publication."""
    source_stream: BinaryIO | None = None
    try:
        safe_path = secure_existing_input(path, MAX_IFC_BYTES)
        source_stream, source_before = _open_source(safe_path)
    except (UnsafePathError, OSError, TypeError) as exc:
        code = DiagnosticCode.LIMIT_EXCEEDED if "byte limit" in str(exc) else DiagnosticCode.UNSAFE_PATH
        return _failure(code, "reader-input", "IFC input is outside the supported file boundary.", "Provide a bounded regular IFC file without symlink or reparse components.")
    try:
        ifc, element_util = runtime()
    except ReaderRuntimeUnavailable:
        source_stream.close()
        return _failure(DiagnosticCode.IFC_RUNTIME_UNAVAILABLE, "reader-runtime", "IfcOpenShell 0.8.5 runtime is unavailable.", "Install the motor-ifc[ifc] optional dependency.")
    except Exception:
        source_stream.close()
        return _failure(DiagnosticCode.READER_EXTRACTION_FAILED, "reader-runtime", "IFC reader runtime could not be initialized.", "Install the supported motor-ifc[ifc] runtime and retry.")
    try:
        with tempfile.TemporaryDirectory(prefix="motor-ifc-reader-") as snapshot_dir:
            snapshot = Path(snapshot_dir) / "source.ifc"
            source_hash = _snapshot_source(source_stream, source_before, snapshot)
            model = ifc.open(str(snapshot))
            _validate_schema(model)
            scoped = [item for item in model.by_type("IfcObject") if isinstance(getattr(item, "GlobalId", None), str) and item.GlobalId]
            if len(scoped) > MAX_ENTITIES:
                raise _BoundExceeded
            scoped.sort(key=lambda item: (item.GlobalId, item.is_a()))
            result_entities = []
            counter = _Counter()
            for item in scoped:
                counter.entity = 0
                global_id = _metadata_value(item.GlobalId, counter)
                ifc_class = _metadata_value(item.is_a(), counter)
                metadata = {
                    "global_id": global_id,
                    "ifc_class": ifc_class,
                    "name": _metadata_value(getattr(item, "Name", None), counter),
                    "description": _metadata_value(getattr(item, "Description", None), counter),
                    "object_type": _metadata_value(getattr(item, "ObjectType", None), counter),
                    "tag": _metadata_value(getattr(item, "Tag", None), counter),
                }
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
                    result_entities.append(ReaderEntity(**metadata, properties=properties, quantities=quantities))
                elif projection == "properties":
                    result_entities.append(ReaderEntityProperties(**metadata, properties=properties))
                elif projection == "quantities":
                    result_entities.append(ReaderEntityQuantities(**metadata, quantities=quantities))
                else:
                    result_entities.append(ReaderEntityMetadata(**metadata))
            if not _source_unchanged(safe_path, source_stream, source_before, source_hash):
                raise RuntimeError("source IFC changed during extraction")
            return ReaderExtractionResult(
                success=True,
                source_schema=str(model.schema),
                entity_count=len(result_entities),
                entities=tuple(result_entities),
            )
    except _SchemaInvalid:
        return _failure(DiagnosticCode.READER_EXTRACTION_FAILED, "reader-validate", "IFC failed schema validation.", "Repair the IFC with a schema-conforming tool before extraction.")
    except _BoundExceeded:
        return _failure(DiagnosticCode.LIMIT_EXCEEDED, "reader-extract", "IFC extraction exceeds reader-extraction.v1 bounds.", "Reduce or split the IFC before extraction.")
    except _UnsupportedValue:
        return _failure(DiagnosticCode.UNSUPPORTED_READER_VALUE, "reader-normalize", "IFC extraction contains a value unsupported by reader-extraction.v1.", "Remove unsupported entity references, binary, non-finite, or cyclic property values.")
    except Exception:
        return _failure(DiagnosticCode.READER_EXTRACTION_FAILED, "reader-extract", "IFC could not be safely extracted.", "Provide a readable schema-valid IFC file supported by IfcOpenShell 0.8.5.")
    finally:
        source_stream.close()
