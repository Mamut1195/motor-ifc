from copy import deepcopy
import hashlib, json
from pathlib import Path
import pytest
from motor_ifc import compile_snapshot, inspect_ifc, validate_ifc
from motor_ifc import api, compiler

def test_mep_unknown_system_is_rejected_without_publication(mep_snapshot,tmp_path):
    snapshot=deepcopy(mep_snapshot); snapshot["payload"]["components"][0]["system"]="fire-protection"
    result=compile_snapshot(snapshot,tmp_path/"job")
    assert not result.success and result.diagnostics[0].code==2000
    assert list(tmp_path.iterdir())==[]

def test_optional_dependency_failure_is_explicit(architecture_snapshot,tmp_path,monkeypatch):
    def unavailable(): raise compiler.IfcRuntimeUnavailable("missing")
    monkeypatch.setattr(compiler,"runtime",unavailable)
    result=compile_snapshot(architecture_snapshot,tmp_path/"job")
    assert not result.success and result.diagnostics[0].code==2200
    assert list(tmp_path.iterdir())==[]

def test_invalid_snapshot_publishes_nothing(architecture_snapshot,tmp_path):
    snapshot=deepcopy(architecture_snapshot); snapshot["payload"]["elements"][0]["length"]=-1
    result=compile_snapshot(snapshot,tmp_path/"job")
    assert not result.success and list(tmp_path.iterdir())==[]

def test_existing_output_directory_is_never_overwritten(architecture_snapshot,tmp_path):
    target=tmp_path/"job"; target.mkdir(); sentinel=target/"architecture.ifc"; sentinel.write_text("old",encoding="utf-8")
    result=compile_snapshot(architecture_snapshot,target)
    assert not result.success and result.diagnostics[0].code==2400
    assert sentinel.read_text(encoding="utf-8")=="old"


@pytest.mark.ifcopenshell
def test_compile_reopen_and_atomic_artifacts(architecture_snapshot,tmp_path):
    pytest.importorskip("ifcopenshell")
    result=compile_snapshot(architecture_snapshot,tmp_path/"job")
    assert result.success
    assert set(result.artifacts)=={"architecture.ifc","manifest.json","diagnostics.json","source-map.json"}
    assert set(result.source_map)=={"level-1","wall-1"}
    inspected=inspect_ifc(tmp_path/"job"/"architecture.ifc")
    assert inspected.success and inspected.ifc_schema=="IFC4"
    assert inspected.entity_counts["IfcBuildingStorey"]==1 and inspected.entity_counts["IfcWall"]==1
    assert validate_ifc(tmp_path/"job"/"architecture.ifc").valid
    manifest=json.loads((tmp_path/"job"/"manifest.json").read_text())
    assert manifest["sha256"]==hashlib.sha256((tmp_path/"job"/"architecture.ifc").read_bytes()).hexdigest()
    source_map=json.loads((tmp_path/"job"/"source-map.json").read_text())
    assert source_map["entries"]==result.source_map


@pytest.mark.ifcopenshell
def test_compile_namespaces_global_ids_by_model_and_authority(architecture_snapshot,tmp_path):
    pytest.importorskip("ifcopenshell")
    other_model=deepcopy(architecture_snapshot); other_model["model_id"]="33333333-3333-3333-3333-333333333333"
    other_authority=deepcopy(architecture_snapshot); other_authority["authority"]["producer"]="other-authority"
    later_revision=deepcopy(architecture_snapshot); later_revision["revision"]=2
    other_ruleset=deepcopy(architecture_snapshot); other_ruleset["authority"]["ruleset_version"]="2.0.0"
    other_source=deepcopy(architecture_snapshot); other_source["authority"]["source_hash"]="sha256:"+"b"*64
    base=compile_snapshot(architecture_snapshot,tmp_path/"base")
    model_scoped=compile_snapshot(other_model,tmp_path/"other-model")
    authority_scoped=compile_snapshot(other_authority,tmp_path/"other-authority")
    stable_revision=compile_snapshot(later_revision,tmp_path/"later-revision")
    stable_ruleset=compile_snapshot(other_ruleset,tmp_path/"other-ruleset")
    stable_source=compile_snapshot(other_source,tmp_path/"other-source")
    assert all(result.success for result in (base,model_scoped,authority_scoped,stable_revision,stable_ruleset,stable_source))
    assert base.source_map["wall-1"]!=model_scoped.source_map["wall-1"]
    assert base.source_map["wall-1"]!=authority_scoped.source_map["wall-1"]
    assert base.source_map["wall-1"]==stable_revision.source_map["wall-1"]
    assert base.source_map["wall-1"]==stable_ruleset.source_map["wall-1"]
    assert base.source_map["wall-1"]==stable_source.source_map["wall-1"]


@pytest.mark.ifcopenshell
def test_compile_structure_type_only_members(structure_snapshot,tmp_path):
    ifc=pytest.importorskip("ifcopenshell")
    result=compile_snapshot(structure_snapshot,tmp_path/"job")
    assert result.success
    assert set(result.artifacts)=={"structure.ifc","manifest.json","diagnostics.json","source-map.json"}
    assert set(result.source_map)=={"beam-1","column-1","plate-1","foundation-1"}
    model=ifc.open(str(tmp_path/"job"/"structure.ifc"))
    assert model.schema=="IFC4"
    assert {name:len(model.by_type(name)) for name in ("IfcBeam","IfcColumn","IfcPlate","IfcFooting")} == {"IfcBeam":1,"IfcColumn":1,"IfcPlate":1,"IfcFooting":1}
    products=[*model.by_type("IfcBeam"),*model.by_type("IfcColumn"),*model.by_type("IfcPlate"),*model.by_type("IfcFooting")]
    assert all(product.ObjectPlacement is None and product.Representation is None for product in products)
    assert validate_ifc(tmp_path/"job"/"structure.ifc").valid
    manifest=json.loads((tmp_path/"job"/"manifest.json").read_text())
    assert manifest["discipline"]=="structure" and manifest["artifact"]=="structure.ifc"
    assert manifest["sha256"]==hashlib.sha256((tmp_path/"job"/"structure.ifc").read_bytes()).hexdigest()
    source_map=json.loads((tmp_path/"job"/"source-map.json").read_text())
    assert source_map["entries"]==result.source_map


@pytest.mark.ifcopenshell
def test_compile_mep_type_only_components_without_inference(mep_snapshot,tmp_path):
    ifc=pytest.importorskip("ifcopenshell")
    result=compile_snapshot(mep_snapshot,tmp_path/"job")
    assert result.success
    assert set(result.artifacts)=={"mep.ifc","manifest.json","diagnostics.json","source-map.json"}
    assert set(result.source_map)=={"hvac-1","plumbing-1","electrical-1"}
    model=ifc.open(str(tmp_path/"job"/"mep.ifc"))
    assert model.schema=="IFC4"
    products=model.by_type("IfcDistributionElement")
    assert len(products)==3 and all(product.is_a()=="IfcDistributionElement" for product in products)
    assert {product.GlobalId:product.Name for product in products}=={
        result.source_map["hvac-1"]:"HVAC component",
        result.source_map["plumbing-1"]:"Plumbing component",
        result.source_map["electrical-1"]:"Electrical component",
    }
    assert all(product.ObjectPlacement is None and product.Representation is None for product in products)
    assert not model.by_type("IfcDistributionSystem") and not model.by_type("IfcDistributionPort")
    assert validate_ifc(tmp_path/"job"/"mep.ifc").valid
    manifest=json.loads((tmp_path/"job"/"manifest.json").read_text())
    assert manifest["discipline"]=="mep" and manifest["artifact"]=="mep.ifc"
    assert manifest["sha256"]==hashlib.sha256((tmp_path/"job"/"mep.ifc").read_bytes()).hexdigest()
    source_map=json.loads((tmp_path/"job"/"source-map.json").read_text())
    assert source_map["entries"]==result.source_map


@pytest.mark.ifcopenshell
def test_validate_ifc_rejects_schema_invalid_model(tmp_path):
    ifc=pytest.importorskip("ifcopenshell")
    path=tmp_path/"invalid.ifc"; model=ifc.file(schema="IFC4"); model.create_entity("IfcProject"); model.write(str(path))
    assert not validate_ifc(path).valid
