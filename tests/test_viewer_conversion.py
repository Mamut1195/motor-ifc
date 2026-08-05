import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from motor_ifc import compile_snapshot, convert_ifc_to_glb
from motor_ifc import gltf_adapter, viewer_conversion


def _source(tmp_path: Path) -> Path:
    path = tmp_path / "source.ifc"
    path.write_text("IFC", encoding="utf-8")
    return path


def _glb(*chunks: tuple[bytes, bytes]) -> bytes:
    body = b"".join(len(payload).to_bytes(4, "little") + kind + payload for kind, payload in chunks)
    return b"glTF" + (2).to_bytes(4, "little") + (12 + len(body)).to_bytes(4, "little") + body


def _write_glb(path: Path, payload: bytes = b"{}  ") -> None:
    content = _glb((b"JSON", payload))
    path.write_bytes(content)


def _runtime(path: Path, payload: bytes = b"{}  ") -> str:
    _write_glb(path, payload)
    return "0.8.5"


def test_conversion_publishes_exact_immutable_contract(tmp_path, monkeypatch):
    source = _source(tmp_path)
    before = (source.read_bytes(), source.stat().st_mtime_ns)
    monkeypatch.setattr(gltf_adapter, "serialize", lambda source, target: _runtime(target, b'{"asset":{}}'))

    result = convert_ifc_to_glb(source, tmp_path / "viewer-result", "building.glb")

    assert result.success and result.contract_version == "viewer-conversion.v1"
    assert result.publication == "immutable-directory"
    assert set(result.artifacts) == {"building.glb", "manifest.json", "diagnostics.json"}
    assert (source.read_bytes(), source.stat().st_mtime_ns) == before
    manifest = json.loads((tmp_path / "viewer-result" / "manifest.json").read_text(encoding="utf-8"))
    glb = tmp_path / "viewer-result" / "building.glb"
    assert manifest == {
        "schema": "motor-ifc.viewer-conversion-manifest.v1",
        "contract_version": "viewer-conversion.v1",
        "source": {"filename": "source.ifc", "sha256": hashlib.sha256(source.read_bytes()).hexdigest()},
        "artifact": {"filename": "building.glb", "media_type": "model/gltf-binary", "bytes": glb.stat().st_size, "sha256": hashlib.sha256(glb.read_bytes()).hexdigest()},
        "versions": {"engine": "0.1.0", "ifcopenshell": "0.8.5"},
    }
    assert json.loads((tmp_path / "viewer-result" / "diagnostics.json").read_text()) == {"diagnostics": []}
    assert not (tmp_path / "viewer-result" / "source-map.json").exists()


@pytest.mark.parametrize("name", ["../x.glb", "a/b.glb", "a\\b.glb", "C:x.glb", "CON.glb", "CON.foo.glb", "COM1.extra.glb", "x.glb.", "x.glb ", "x.gltf", ".glb/", "x?.glb", "x.glb\x00", "x\x7f.glb", "x\x85.glb"])
def test_conversion_rejects_unsafe_glb_filename(tmp_path, name):
    result = convert_ifc_to_glb(_source(tmp_path), tmp_path / "result", name)
    assert not result.success and result.diagnostics[0].code == 2000
    assert set(tmp_path.iterdir()) == {tmp_path / "source.ifc"}


def test_conversion_rejects_existing_destination_without_mutation(tmp_path):
    source = _source(tmp_path)
    target = tmp_path / "result"
    target.mkdir()
    sentinel = target / "sentinel"
    sentinel.write_text("keep", encoding="utf-8")
    result = convert_ifc_to_glb(source, target, "model.glb")
    assert not result.success and result.diagnostics[0].code == 2400
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_conversion_rejects_path_and_filename_bounds(tmp_path, monkeypatch):
    source = _source(tmp_path)
    monkeypatch.setattr(viewer_conversion, "MAX_PATH_LENGTH", 1)
    result = convert_ifc_to_glb(source, tmp_path / "result", "model.glb")
    assert not result.success and result.diagnostics[0].code == 2003
    result = convert_ifc_to_glb(source, tmp_path / "result", "x" * 121 + ".glb")
    assert not result.success and result.diagnostics[0].code == 2000


def test_conversion_returns_typed_staging_failure(tmp_path, monkeypatch):
    source = _source(tmp_path)
    monkeypatch.setattr(viewer_conversion.tempfile, "mkdtemp", lambda **kwargs: (_ for _ in ()).throw(OSError("private filesystem detail")))
    result = convert_ifc_to_glb(source, tmp_path / "result", "model.glb")
    assert not result.success and result.diagnostics[0].code == 2401
    assert "private filesystem detail" not in result.model_dump_json()


def test_conversion_runtime_failure_is_typed_and_cleans_stage(tmp_path, monkeypatch):
    source = _source(tmp_path)
    def unavailable(source, target):
        raise gltf_adapter.ViewerRuntimeUnavailable("private runtime detail")
    monkeypatch.setattr(gltf_adapter, "serialize", unavailable)
    result = convert_ifc_to_glb(source, tmp_path / "result", "model.glb")
    assert not result.success and result.diagnostics[0].code == 2200
    assert result.publication == "none"
    assert "private runtime detail" not in result.model_dump_json()
    assert set(tmp_path.iterdir()) == {source}


@pytest.mark.parametrize("writer", [lambda path: path.write_bytes(b"not-glb"), lambda path: _write_glb(path, b" " * 20)])
def test_conversion_rejects_invalid_or_oversized_serializer_output(tmp_path, monkeypatch, writer):
    source = _source(tmp_path)
    monkeypatch.setattr(viewer_conversion, "MAX_GLB_BYTES", 16)
    monkeypatch.setattr(gltf_adapter, "serialize", lambda source, target: (writer(target), "0.8.5")[1])
    result = convert_ifc_to_glb(source, tmp_path / "result", "model.glb")
    assert not result.success and result.diagnostics[0].code == 2700
    assert set(tmp_path.iterdir()) == {source}


@pytest.mark.parametrize("content", [
    b"glTF" + (2).to_bytes(4, "little") + (12).to_bytes(4, "little"),
    _glb((b"BIN\x00", b"data")),
    _glb((b"JSON", b"[]  ")),
    _glb((b"JSON", b"bad ")),
    _glb((b"JSON", b"{} ")),
    _glb((b"JSON", b"{}  "), (b"JSON", b"{}  ")),
    _glb((b"JSON", b"{}  "), (b"BIN\x00", b"data"), (b"BIN\x00", b"data")),
    _glb((b"JSON", b"{}  "), (b"EXT0", b"data"), (b"BIN\x00", b"data")),
    _glb((b"JSON", b"{}  "))[:-1],
    _glb((b"JSON", b"{}  ")) + b"tail",
], ids=["header-only", "bin-first", "json-array", "invalid-json", "unaligned-json", "duplicate-json", "duplicate-bin", "late-bin", "truncated", "trailing-data"])
def test_conversion_rejects_malformed_glb_structure(tmp_path, monkeypatch, content):
    source = _source(tmp_path)
    monkeypatch.setattr(gltf_adapter, "serialize", lambda source, target: (target.write_bytes(content), "0.8.5")[1])
    result = convert_ifc_to_glb(source, tmp_path / "result", "model.glb")
    assert not result.success and result.diagnostics[0].code == 2700
    assert set(tmp_path.iterdir()) == {source}


def test_conversion_rechecks_bound_after_secure_path_validation(tmp_path, monkeypatch):
    source = _source(tmp_path)
    original = viewer_conversion.secure_existing_input
    monkeypatch.setattr(viewer_conversion, "MAX_IFC_BYTES", 3)
    def grow_after_validation(path, max_bytes):
        resolved = original(path, max_bytes)
        resolved.write_bytes(b"0123456789")
        return resolved
    monkeypatch.setattr(viewer_conversion, "secure_existing_input", grow_after_validation)
    result = convert_ifc_to_glb(source, tmp_path / "result", "model.glb")
    assert not result.success and result.diagnostics[0].code == 2003
    assert set(tmp_path.iterdir()) == {source}


def test_conversion_uses_bounded_snapshot_and_detects_source_replacement(tmp_path, monkeypatch):
    source = _source(tmp_path)
    observed = {}
    replaced = False
    original_stat = Path.stat
    def changed_stat(path, *args, **kwargs):
        info = original_stat(path, *args, **kwargs)
        if replaced and path == source:
            return SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino + 1, st_size=info.st_size, st_mtime_ns=info.st_mtime_ns)
        return info
    def replace(source_snapshot, target):
        nonlocal replaced
        observed["input"] = source_snapshot.read_bytes()
        replaced = True
        _write_glb(target)
        return "0.8.5"
    monkeypatch.setattr(Path, "stat", changed_stat)
    monkeypatch.setattr(gltf_adapter, "serialize", replace)
    result = convert_ifc_to_glb(source, tmp_path / "result", "model.glb")
    assert observed["input"] == b"IFC"
    assert not result.success and result.diagnostics[0].code == 2700
    assert set(tmp_path.iterdir()) == {source}


def test_conversion_detects_source_change_and_publishes_nothing(tmp_path, monkeypatch):
    source = _source(tmp_path)
    monkeypatch.setattr(viewer_conversion, "_open_source_stream", lambda path: path.open("rb"))
    def mutate(source_snapshot, target):
        _write_glb(target)
        source.write_text("changed", encoding="utf-8")
        return "0.8.5"
    monkeypatch.setattr(gltf_adapter, "serialize", mutate)
    result = convert_ifc_to_glb(source, tmp_path / "result", "model.glb")
    assert not result.success and result.diagnostics[0].code == 2700
    assert not (tmp_path / "result").exists()


def test_conversion_detects_manifest_time_source_change_and_cleans_stage(tmp_path, monkeypatch):
    source = _source(tmp_path)
    target = tmp_path / "result"
    original_write_text = Path.write_text
    monkeypatch.setattr(viewer_conversion, "_open_source_stream", lambda path: path.open("rb"))
    monkeypatch.setattr(gltf_adapter, "serialize", lambda source, target: _runtime(target))

    def mutate_during_manifest(path, *args, **kwargs):
        written = original_write_text(path, *args, **kwargs)
        if path.name == "manifest.json":
            source.write_text("changed", encoding="utf-8")
        return written

    monkeypatch.setattr(Path, "write_text", mutate_during_manifest)
    result = convert_ifc_to_glb(source, target, "model.glb")

    assert not result.success and result.diagnostics[0].code == 2700
    assert not target.exists()
    assert set(tmp_path.iterdir()) == {source}


@pytest.mark.skipif(os.name != "nt", reason="Windows source sharing transaction")
def test_conversion_blocks_source_write_at_atomic_publication_boundary(tmp_path, monkeypatch):
    source = _source(tmp_path)
    target = tmp_path / "result"
    before = (source.read_bytes(), source.stat().st_mtime_ns)
    blocked = False
    original_replace = os.replace
    monkeypatch.setattr(gltf_adapter, "serialize", lambda source, target: _runtime(target))

    def attempt_mutation_before_rename(stage, destination):
        nonlocal blocked
        with pytest.raises(OSError):
            source.write_text("changed", encoding="utf-8")
        blocked = True
        original_replace(stage, destination)

    monkeypatch.setattr(viewer_conversion.os, "replace", attempt_mutation_before_rename)
    result = convert_ifc_to_glb(source, target, "model.glb")

    assert result.success and blocked
    assert (source.read_bytes(), source.stat().st_mtime_ns) == before
    assert set(path.name for path in target.iterdir()) == {"model.glb", "manifest.json", "diagnostics.json"}
    assert not list(tmp_path.glob(".result.stage-*"))


def test_adapter_rejects_wrong_runtime_version(tmp_path, monkeypatch):
    source = tmp_path / "source.ifc"
    source.write_text("ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n", encoding="ascii")
    monkeypatch.setattr(gltf_adapter.importlib, "import_module", lambda name: SimpleNamespace(version="0.8.4") if name == "ifcopenshell" else SimpleNamespace())
    with pytest.raises(gltf_adapter.ViewerRuntimeUnavailable):
        gltf_adapter.serialize(source, tmp_path / "out.glb")


@pytest.mark.ifcopenshell
def test_malformed_ifc_returns_typed_failure_without_publication(tmp_path):
    pytest.importorskip("ifcopenshell")
    result = convert_ifc_to_glb(_source(tmp_path), tmp_path / "result", "model.glb")
    assert not result.success and result.diagnostics[0].code == 2700
    assert result.publication == "none" and not (tmp_path / "result").exists()


@pytest.mark.ifcopenshell
def test_real_conversion_has_glb_2_content(architecture_snapshot, tmp_path):
    pytest.importorskip("ifcopenshell")
    compiled = compile_snapshot(architecture_snapshot, tmp_path / "compile-result")
    assert compiled.success
    compile_before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in (tmp_path / "compile-result").iterdir()}
    result = convert_ifc_to_glb(compiled.artifacts["architecture.ifc"], tmp_path / "viewer-result", "building.glb")
    assert result.success
    content = (tmp_path / "viewer-result" / "building.glb").read_bytes()
    assert content[:4] == b"glTF" and int.from_bytes(content[4:8], "little") == 2
    assert int.from_bytes(content[8:12], "little") == len(content) and len(content) > 100
    compile_after = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in (tmp_path / "compile-result").iterdir()}
    assert compile_after == compile_before


def test_viewer_capability_is_explicit():
    from motor_ifc import capabilities
    result = capabilities()
    assert result.viewer_conversion_contract_versions == ("viewer-conversion.v1",)
    assert result.viewer_formats == ("glb-2.0",)
