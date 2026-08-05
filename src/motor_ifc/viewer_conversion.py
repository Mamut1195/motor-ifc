"""Bounded IFC-to-GLB conversion and immutable publication."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import unicodedata
from pathlib import Path
from typing import BinaryIO

from ._version import VERSION
from .diagnostics import DiagnosticCode, error
from .gltf_adapter import ViewerRuntimeUnavailable
from . import gltf_adapter
from .models import ViewerConversionResult
from .security import UnsafePathError, secure_existing_input, secure_new_output

CONTRACT_VERSION = "viewer-conversion.v1"
MANIFEST_SCHEMA = "motor-ifc.viewer-conversion-manifest.v1"
MAX_IFC_BYTES = 100_000_000
MAX_GLB_BYTES = 500_000_000
MAX_GLB_JSON_BYTES = 16_000_000
MAX_FILENAME_LENGTH = 120
MAX_PATH_LENGTH = 500
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _failure(code: DiagnosticCode, stage: str, message: str, action: str) -> ViewerConversionResult:
    return ViewerConversionResult(success=False, diagnostics=(error(code, stage, message, action),))


def _valid_filename(name: object) -> bool:
    if not isinstance(name, str) or not name or len(name) > MAX_FILENAME_LENGTH:
        return False
    try:
        encoded_length = len(name.encode("utf-8"))
    except UnicodeEncodeError:
        return False
    if encoded_length > 255 or name in {".", ".."} or name[-1] in {" ", "."}:
        return False
    if _UNSAFE_FILENAME.search(name) or any(unicodedata.category(char) == "Cc" for char in name) or Path(name).name != name or Path(name).suffix.lower() != ".glb":
        return False
    return name.split(".", 1)[0].rstrip(" .").upper() not in _WINDOWS_RESERVED


def _read_exact(stream: BinaryIO, size: int, digest) -> bytes:
    data = stream.read(size)
    digest.update(data)
    if len(data) != size:
        raise ValueError("GLB chunk is truncated")
    return data


def _valid_glb(path: Path) -> tuple[int, str]:
    size = path.stat().st_size
    if size > MAX_GLB_BYTES or size < 20:
        raise ValueError("generated GLB exceeds the byte limit")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        header = _read_exact(stream, 12, digest)
        if header[:4] != b"glTF" or int.from_bytes(header[4:8], "little") != 2:
            raise ValueError("serializer did not produce GLB 2.0")
        if int.from_bytes(header[8:12], "little") != size:
            raise ValueError("GLB length header is inconsistent")

        offset = 12
        chunk_index = 0
        seen_bin = False
        while offset < size:
            chunk_header = _read_exact(stream, 8, digest)
            chunk_length = int.from_bytes(chunk_header[:4], "little")
            chunk_type = chunk_header[4:8]
            offset += 8
            if chunk_length % 4 or chunk_length > size - offset:
                raise ValueError("GLB chunk is unaligned or out of bounds")
            if chunk_index == 0:
                if chunk_type != b"JSON" or chunk_length > MAX_GLB_JSON_BYTES:
                    raise ValueError("GLB must start with a bounded JSON chunk")
                payload = _read_exact(stream, chunk_length, digest)
                try:
                    document = json.loads(payload.decode("utf-8"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                    raise ValueError("GLB JSON chunk is invalid") from exc
                if not isinstance(document, dict):
                    raise ValueError("GLB JSON chunk must contain an object")
            else:
                if chunk_type == b"JSON":
                    raise ValueError("GLB contains a duplicate JSON chunk")
                if chunk_type == b"BIN\x00":
                    if chunk_index != 1 or seen_bin:
                        raise ValueError("GLB BIN chunk ordering is invalid")
                    seen_bin = True
                remaining = chunk_length
                while remaining:
                    block = stream.read(min(1024 * 1024, remaining))
                    if not block:
                        raise ValueError("GLB chunk is truncated")
                    digest.update(block)
                    remaining -= len(block)
            offset += chunk_length
            chunk_index += 1
        if chunk_index == 0 or offset != size:
            raise ValueError("GLB chunk structure is incomplete")
    return size, digest.hexdigest()


def _file_identity(info: os.stat_result) -> tuple[int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _open_source_stream(path: Path) -> BinaryIO:
    if os.name != "nt":
        return path.open("rb")

    import ctypes
    import msvcrt
    from ctypes import wintypes

    generic_read = 0x80000000
    file_share_read = 0x00000001
    open_existing = 3
    sequential_scan = 0x08000000
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
    handle = create_file(os.fspath(path), generic_read, file_share_read, None, open_existing, sequential_scan, None)
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
        info = os.fstat(stream.fileno())
        current = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_IFC_BYTES:
            raise UnsafePathError("input exceeds the byte limit")
        if _file_identity(info) != _file_identity(current):
            raise UnsafePathError("input identity changed during secure open")
        return stream, info
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
        raise RuntimeError("source IFC changed during baseline capture")
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


def convert(ifc_path: str | Path, result_dir: str | Path, glb_filename: str) -> ViewerConversionResult:
    """Publish one separate immutable GLB conversion result directory."""
    if not _valid_filename(glb_filename):
        return _failure(DiagnosticCode.INVALID_CONTRACT, "viewer-input", "GLB filename is invalid.", "Provide one platform-safe basename ending in .glb with at most 120 characters.")
    source_stream: BinaryIO | None = None
    try:
        if len(os.fspath(ifc_path)) > MAX_PATH_LENGTH or len(os.fspath(result_dir)) > MAX_PATH_LENGTH:
            raise UnsafePathError("path exceeds the character limit")
        source = secure_existing_input(ifc_path, MAX_IFC_BYTES)
        source_stream, source_before = _open_source(source)
        target = secure_new_output(result_dir)
    except (UnsafePathError, OSError, TypeError, ValueError) as exc:
        if source_stream is not None:
            source_stream.close()
        code = DiagnosticCode.LIMIT_EXCEEDED if "limit" in str(exc) else DiagnosticCode.UNSAFE_PATH
        return _failure(code, "viewer-input", "Conversion paths are outside the supported file boundary.", "Provide a bounded regular IFC and a new non-symlink result directory path.")

    stage: Path | None = None
    try:
        stage = Path(tempfile.mkdtemp(prefix=f".{target.name}.stage-", dir=target.parent))
        snapshot = stage / ".source.ifc"
        source_hash = _snapshot_source(source_stream, source_before, snapshot)
        artifact = stage / glb_filename
        runtime_version = gltf_adapter.serialize(snapshot, artifact)
        size, artifact_hash = _valid_glb(artifact)
        if not _source_unchanged(source, source_stream, source_before, source_hash):
            raise RuntimeError("source IFC content changed during conversion")
        snapshot.unlink()
        versions = {"engine": VERSION, "ifcopenshell": runtime_version}
        manifest = {
            "schema": MANIFEST_SCHEMA,
            "contract_version": CONTRACT_VERSION,
            "source": {"filename": source.name, "sha256": source_hash},
            "artifact": {"filename": glb_filename, "media_type": "model/gltf-binary", "bytes": size, "sha256": artifact_hash},
            "versions": versions,
        }
        (stage / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        (stage / "diagnostics.json").write_text(json.dumps({"diagnostics": []}, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        if os.path.lexists(target):
            raise UnsafePathError("result directory already exists")
        if not _source_unchanged(source, source_stream, source_before, source_hash):
            raise RuntimeError("source IFC content changed before publication")
        os.replace(stage, target)
        names = (glb_filename, "manifest.json", "diagnostics.json")
        return ViewerConversionResult(success=True, artifacts={name: target / name for name in names}, source_ifc_sha256=source_hash, artifact_sha256=artifact_hash, versions=versions, publication="immutable-directory")
    except ViewerRuntimeUnavailable:
        return _failure(DiagnosticCode.IFC_RUNTIME_UNAVAILABLE, "viewer-runtime", "IFC viewer conversion runtime is unavailable or unsupported.", "Install motor-ifc[ifc] with IfcOpenShell 0.8.5.")
    except (UnsafePathError, OSError):
        return _failure(DiagnosticCode.PUBLICATION_FAILED, "viewer-publication", "GLB conversion result could not be published.", "Use a new result directory on a writable local filesystem.")
    except Exception:
        return _failure(DiagnosticCode.VIEWER_CONVERSION_FAILED, "viewer-convert", "IFC-to-GLB conversion failed.", "Provide a readable, valid IFC with supported geometry and retry.")
    finally:
        source_stream.close()
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)
