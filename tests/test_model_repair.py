import hashlib
import json
from pathlib import Path

import pytest

import motor_ifc.model_repair as repair_module
from motor_ifc import audit_ifc, extract_ifc_semantic, repair_ifc
from motor_ifc.rpc import handle_line


def _ifc_runtime():
    return pytest.importorskip("ifcopenshell")


def _base_model(model):
    ifc = _ifc_runtime()
    guid = ifc.guid.new
    point = model.create_entity("IfcCartesianPoint", Coordinates=[0.0, 0.0, 0.0])
    axis = model.create_entity("IfcAxis2Placement3D", Location=point)
    context = model.create_entity(
        "IfcGeometricRepresentationContext", ContextType="Model", CoordinateSpaceDimension=3, WorldCoordinateSystem=axis
    )
    assignment = model.create_entity(
        "IfcUnitAssignment", Units=[model.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Name="METRE")]
    )
    model.create_entity(
        "IfcProject", GlobalId=guid(), Name="Project", RepresentationContexts=[context], UnitsInContext=assignment
    )
    return model.create_entity("IfcWall", GlobalId="1AAAAAAAAAAAAAAAAAAAAA", Name="Wall")


def make_valid_ifc(path: Path) -> None:
    ifc = _ifc_runtime()
    model = ifc.file(schema="IFC4")
    _base_model(model)
    model.write(str(path))


def make_droppable_defect_ifc(path: Path) -> str:
    ifc = _ifc_runtime()
    model = ifc.file(schema="IFC4")
    wall = _base_model(model)
    relation_global_id = ifc.guid.new()
    model.create_entity(
        "IfcRelVoidsElement", GlobalId=relation_global_id, RelatingBuildingElement=wall
    )  # RelatedOpeningElement (mandatory) missing
    model.write(str(path))
    return relation_global_id


def make_manual_defect_ifc(path: Path) -> None:
    ifc = _ifc_runtime()
    model = ifc.file(schema="IFC4")
    _base_model(model)
    model.create_entity("IfcWall", Name="NoGlobalId")  # IfcRoot.GlobalId is mandatory
    model.write(str(path))


@pytest.fixture
def valid_ifc(tmp_path):
    path = tmp_path / "valid.ifc"
    make_valid_ifc(path)
    return path


@pytest.fixture
def droppable_ifc(tmp_path):
    path = tmp_path / "droppable.ifc"
    make_droppable_defect_ifc(path)
    return path


@pytest.fixture
def manual_ifc(tmp_path):
    path = tmp_path / "manual.ifc"
    make_manual_defect_ifc(path)
    return path


def dump(result):
    return result.model_dump(mode="json")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.ifcopenshell
def test_audit_reports_typed_droppable_defect_deterministically(droppable_ifc):
    first = dump(audit_ifc(droppable_ifc))
    second = dump(audit_ifc(droppable_ifc))
    assert first == second
    assert first["contract_version"] == "model-audit.v1"
    assert first["success"] is True
    assert first["valid"] is False
    assert first["source_schema"] == "IFC4"
    assert first["source_sha256"] == sha256(droppable_ifc)
    assert first["defect_count"] == 1
    assert first["repairable_count"] == 1
    assert first["manual_count"] == 0
    assert first["repairable"] is True
    (defect,) = first["defects"]
    assert defect["ifc_class"] == "IfcRelVoidsElement"
    assert defect["attribute"] == "IfcRelVoidsElement.RelatedOpeningElement"
    assert defect["rule"] == "missing-mandatory-attribute"
    assert defect["repair_strategy"] == "drop-instance"
    assert isinstance(defect["step_id"], int)
    assert defect["global_id"]
    assert first["entity_counts"]["IfcObject"] == 1
    assert first["truncated"] is False


@pytest.mark.ifcopenshell
def test_audit_valid_model_reports_clean(valid_ifc):
    payload = dump(audit_ifc(valid_ifc))
    assert payload["success"] is True
    assert payload["valid"] is True
    assert payload["defect_count"] == 0
    assert payload["repairable"] is True
    assert payload["defects"] == []


@pytest.mark.ifcopenshell
def test_repair_publishes_valid_immutable_artifact(droppable_ifc, tmp_path):
    before = (sha256(droppable_ifc), droppable_ifc.stat().st_mtime_ns)
    output_dir = tmp_path / "repaired"
    payload = dump(repair_ifc(droppable_ifc, output_dir))
    assert payload["contract_version"] == "model-repair.v1"
    assert payload["success"] is True
    assert payload["repaired"] is True
    assert payload["defects_fixed"] == 1
    assert payload["publication"] == "immutable-directory"
    assert payload["source_sha256"] == sha256(droppable_ifc)
    assert (sha256(droppable_ifc), droppable_ifc.stat().st_mtime_ns) == before
    repaired_path = Path(payload["artifacts"]["repaired.ifc"])
    manifest_path = Path(payload["artifacts"]["repair-manifest.json"])
    assert repaired_path.parent == output_dir
    assert payload["repaired_sha256"] == sha256(repaired_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema"] == "motor-ifc.repair-manifest.v1"
    assert manifest["source_sha256"] == payload["source_sha256"]
    assert manifest["repaired_sha256"] == payload["repaired_sha256"]
    assert len(manifest["fixes"]) == 1
    # The repaired artifact validates clean and flows through the strict reader.
    audit = dump(audit_ifc(repaired_path))
    assert audit["valid"] is True
    extraction = dump(extract_ifc_semantic(repaired_path))
    assert extraction["success"] is True
    assert extraction["entity_count"] == 1
    # No staging residue.
    assert sorted(item.name for item in tmp_path.iterdir()) == ["droppable.ifc", "repaired"]


@pytest.mark.ifcopenshell
def test_repair_is_deterministic_across_runs(droppable_ifc, tmp_path):
    first = dump(repair_ifc(droppable_ifc, tmp_path / "one"))
    second = dump(repair_ifc(droppable_ifc, tmp_path / "two"))
    assert first["repaired_sha256"] == second["repaired_sha256"]
    assert first["fixes"] == second["fixes"]


@pytest.mark.ifcopenshell
def test_repair_valid_model_publishes_nothing(valid_ifc, tmp_path):
    output_dir = tmp_path / "repaired"
    payload = dump(repair_ifc(valid_ifc, output_dir))
    assert payload["success"] is True
    assert payload["repaired"] is False
    assert payload["defects_fixed"] == 0
    assert payload["artifacts"] == {}
    assert payload["publication"] == "none"
    assert not output_dir.exists()


@pytest.mark.ifcopenshell
def test_repair_manual_defect_fails_atomically_without_publication(manual_ifc, tmp_path):
    output_dir = tmp_path / "repaired"
    payload = dump(repair_ifc(manual_ifc, output_dir))
    assert payload["success"] is False
    assert payload["repaired"] is False
    assert payload["diagnostics"][0]["code"] == 2902
    assert payload["diagnostics"][0]["stage"] == "repair-apply"
    assert len(payload["remaining_defects"]) == 1
    assert payload["remaining_defects"][0]["repair_strategy"] == "manual"
    assert not output_dir.exists()
    assert sorted(item.name for item in tmp_path.iterdir()) == ["manual.ifc"]


@pytest.mark.ifcopenshell
def test_audit_defect_budget_fails_atomically(droppable_ifc, monkeypatch):
    monkeypatch.setattr(repair_module, "MAX_AUDIT_DEFECTS", 0)
    payload = dump(audit_ifc(droppable_ifc))
    assert payload["success"] is False
    assert payload["defects"] == []
    assert payload["diagnostics"][0]["code"] == 2003
    assert payload["diagnostics"][0]["stage"] == "repair-validate"


def test_audit_input_boundary_is_typed(tmp_path, monkeypatch):
    missing = dump(audit_ifc(tmp_path / "missing.ifc"))
    assert missing["success"] is False
    assert missing["diagnostics"][0]["code"] == 2400
    small = tmp_path / "small.ifc"
    small.write_bytes(b"IFC")
    monkeypatch.setattr(repair_module, "MAX_IFC_BYTES", 2)
    oversized = dump(audit_ifc(small))
    assert oversized["diagnostics"][0]["code"] == 2003


def test_runtime_unavailable_is_typed(tmp_path, monkeypatch):
    path = tmp_path / "model.ifc"
    path.write_bytes(b"IFC")
    monkeypatch.setattr(
        repair_module, "runtime", lambda: (_ for _ in ()).throw(repair_module.ReaderRuntimeUnavailable())
    )
    assert dump(audit_ifc(path))["diagnostics"][0]["code"] == 2200
    assert dump(repair_ifc(path, tmp_path / "out"))["diagnostics"][0]["code"] == 2200
    assert not (tmp_path / "out").exists()


@pytest.mark.ifcopenshell
def test_rpc_audit_and_repair_match_public_api(droppable_ifc, tmp_path):
    job_root = str(droppable_ifc.parent)
    audit_request = {"jsonrpc": "2.0", "id": 1, "method": "model.audit.v1", "params": {"ifc_path": droppable_ifc.name}}
    audit_payload = json.loads(handle_line(json.dumps(audit_request), job_root))["result"]
    assert audit_payload == dump(audit_ifc(droppable_ifc))
    repair_request = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "model.repair.v1",
        "params": {"ifc_path": droppable_ifc.name, "output_dir": "rpc-repaired"},
    }
    repair_payload = json.loads(handle_line(json.dumps(repair_request), job_root))["result"]
    assert repair_payload["success"] is True
    assert repair_payload["repaired"] is True
    assert Path(repair_payload["artifacts"]["repaired.ifc"]).parent == droppable_ifc.parent / "rpc-repaired"


@pytest.mark.ifcopenshell
def test_rpc_audit_and_repair_reject_invalid_params_and_paths(droppable_ifc):
    root = str(droppable_ifc.parent)
    cases = [
        ("model.audit.v1", {"ifc_path": droppable_ifc.name, "extra": True}, root),
        ("model.audit.v1", {"ifc_path": "../droppable.ifc"}, root),
        ("model.audit.v1", {"ifc_path": droppable_ifc.name}, None),
        ("model.repair.v1", {"ifc_path": droppable_ifc.name}, root),
        ("model.repair.v1", {"ifc_path": droppable_ifc.name, "output_dir": "../escape"}, root),
        ("model.repair.v1", {"ifc_path": droppable_ifc.name, "output_dir": "out"}, None),
    ]
    for method, params, job_root in cases:
        request = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        response = json.loads(handle_line(json.dumps(request), job_root))
        assert response["error"]["code"] == -32602


def test_capabilities_advertise_audit_and_repair():
    from motor_ifc import capabilities

    advertised = capabilities()
    assert advertised.model_audit_contract_versions == ("model-audit.v1",)
    assert advertised.model_repair_contract_versions == ("model-repair.v1",)
    assert "model-audit.v1" in advertised.contract_versions
    assert "model-repair.v1" in advertised.contract_versions


@pytest.mark.ifcopenshell
def test_repair_snapshots_are_contained_and_cleaned_under_job_root(droppable_ifc, tmp_path, monkeypatch):
    job_root = tmp_path / "jobroot"
    job_root.mkdir()
    monkeypatch.setenv("MOTOR_IFC_JOB_ROOT", str(job_root))
    payload = dump(repair_ifc(droppable_ifc, tmp_path / "repaired"))
    assert payload["success"] is True
    temp = job_root / ".tmp.motor-ifc"
    assert not temp.exists() or not any(temp.iterdir())
