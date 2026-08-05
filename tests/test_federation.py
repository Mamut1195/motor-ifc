from copy import deepcopy
import hashlib
from motor_ifc import build_federation

def item(discipline="architecture",federation_id="fed-1",origin=(0.0,0.0,0.0),sha=None):
    return {"federation_id":federation_id,"model_id":f"model-{discipline}","discipline":discipline,"revision":1,"artifact":f"{discipline}.ifc","sha256":sha or hashlib.sha256(discipline.encode()).hexdigest(),"coordinate_reference":{"name":"local","origin":origin,"rotation_degrees":0.0}}

def test_federation_builds_with_independent_hashes():
    result=build_federation([item(),item("structure")])
    assert result.success
    assert [m["discipline"] for m in result.manifest["models"]]==["architecture","structure"]
    assert result.manifest["models"][0]["sha256"]!=result.manifest["models"][1]["sha256"]

def test_federation_rejects_coordinate_and_id_mismatch():
    assert not build_federation([item(),item("structure",origin=(1.0,0.0,0.0))]).success
    assert not build_federation([item(),item("structure",federation_id="fed-2")]).success

def test_federation_rejects_duplicate_discipline():
    assert not build_federation([item(),item(sha="b"*64)]).success


def test_federation_rejects_paths_and_non_default_request():
    bad=item(); bad["artifact"]="../architecture.ifc"
    assert not build_federation([bad]).success
    assert not build_federation([item()],{"merge":True}).success
