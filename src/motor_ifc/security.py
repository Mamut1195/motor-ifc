"""Filesystem publication boundary."""
from __future__ import annotations
import os, stat, unicodedata
from pathlib import Path

class UnsafePathError(ValueError): pass

_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}

def _unsafe_rpc_component(part: str) -> bool:
    stem=part.split(".",1)[0].rstrip(" .").upper()
    return ":" in part or any(unicodedata.category(char)=="Cc" for char in part) or stem in _WINDOWS_RESERVED

def _rpc_relative(relative: str, label: str) -> Path:
    if not isinstance(relative,str) or not relative or ":" in relative or any(unicodedata.category(char)=="Cc" for char in relative):
        raise UnsafePathError(f"RPC {label} must be a safe non-empty relative path")
    candidate=Path(relative)
    if candidate.is_absolute() or any(part in ("..","") or _unsafe_rpc_component(part) for part in candidate.parts):
        raise UnsafePathError(f"RPC {label} must be a safe non-empty relative path")
    return candidate

def _is_reparse(path: Path) -> bool:
    info=os.lstat(path)
    return path.is_symlink() or bool(getattr(info,"st_file_attributes",0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)

def secure_new_output(output_dir: str|Path) -> Path:
    raw=Path(output_dir)
    if any(part==".." for part in raw.parts): raise UnsafePathError("parent traversal is forbidden")
    if os.path.lexists(raw): raise UnsafePathError("output_dir must not already exist")
    parent=raw.absolute().parent
    if not parent.is_dir(): raise UnsafePathError("output parent must be an existing directory")
    cursor=parent
    while True:
        if _is_reparse(cursor): raise UnsafePathError("symlink/reparse ancestors are forbidden")
        if cursor.parent==cursor: break
        cursor=cursor.parent
    return parent.resolve(strict=True)/raw.name

def rpc_output(job_root: str|Path, relative: str) -> Path:
    candidate=_rpc_relative(relative,"output_dir")
    configured=Path(job_root)
    if not configured.is_dir() or _is_reparse(configured): raise UnsafePathError("invalid job root")
    root=configured.resolve(strict=True)
    return secure_new_output(root/candidate)

def secure_existing_input(path: str|Path, max_bytes: int) -> Path:
    raw=Path(path)
    if any(part==".." for part in raw.parts): raise UnsafePathError("parent traversal is forbidden")
    absolute=raw.absolute()
    if not absolute.is_file(): raise UnsafePathError("input must be an existing regular file")
    cursor=absolute
    while True:
        if _is_reparse(cursor): raise UnsafePathError("symlink/reparse inputs are forbidden")
        if cursor.parent==cursor: break
        cursor=cursor.parent
    resolved=absolute.resolve(strict=True)
    info=os.stat(resolved)
    if not stat.S_ISREG(info.st_mode): raise UnsafePathError("input must be a regular file")
    if info.st_size>max_bytes: raise UnsafePathError("input exceeds the byte limit")
    return resolved

def rpc_input(job_root: str|Path, relative: str, max_bytes: int) -> Path:
    candidate=_rpc_relative(relative,"input")
    configured=Path(job_root)
    if not configured.is_dir() or _is_reparse(configured): raise UnsafePathError("invalid job root")
    root=configured.resolve(strict=True)
    resolved=secure_existing_input(root/candidate,max_bytes)
    try: resolved.relative_to(root)
    except ValueError as exc: raise UnsafePathError("RPC input escapes the job root") from exc
    return resolved

class JobRootUnavailable(RuntimeError):
    """A job root was configured and cannot be used.

    Distinct from no job root at all. Configuring one is a request for
    containment; if it cannot be honoured, falling back means passing
    dir=None to tempfile, which is the system %TEMP% — and the private
    snapshot of the whole model, up to MAX_IFC_BYTES, lands outside the
    sandbox the caller asked for, with nothing said about it. A promise that
    cannot be kept is an explicit error, never a quiet degradation.
    """


def temp_root() -> Path|None:
    """The contained temp directory, or None when no job root is configured.

    None means the caller never asked for containment and the system default
    applies. It never means "containment was requested and quietly dropped":
    that raises JobRootUnavailable.
    """
    configured=os.environ.get("MOTOR_IFC_JOB_ROOT")
    if not configured: return None
    try:
        root=Path(configured)
        if not root.is_dir() or _is_reparse(root):
            raise JobRootUnavailable("MOTOR_IFC_JOB_ROOT is not a usable directory")
        target=root.resolve(strict=True)/".tmp.motor-ifc"
        target.mkdir(exist_ok=True)
        if not target.is_dir() or _is_reparse(target):
            raise JobRootUnavailable("the job root temp directory is not usable")
        return target
    except OSError as exc:
        raise JobRootUnavailable("the job root could not be prepared") from exc
