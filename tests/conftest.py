import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/"src"))
import pytest

@pytest.fixture
def architecture_snapshot():
    return {"contract_version":"authoring.v1","profile":"building.architecture@1","ifc_schema":"IFC4","model_id":"11111111-1111-1111-1111-111111111111","revision":1,"discipline":"architecture","federation_id":"22222222-2222-2222-2222-222222222222","authority":{"producer":"test-authority","ruleset_version":"1.0.0","source_hash":"sha256:"+"a"*64,"approved":True},"units":"SI","coordinate_reference":{"name":"local-engineering"},"payload":{"payload_version":"architecture.v1","storey_source_id":"level-1","storey_name":"Level 1","elements":[{"kind":"wall","source_id":"wall-1","name":"North wall","length":5.0,"thickness":0.2,"height":3.0}]}}

@pytest.fixture
def structure_snapshot(architecture_snapshot):
    snapshot=architecture_snapshot | {"profile":"building.structure@1","discipline":"structure"}
    snapshot["payload"]={"payload_version":"structure.v1","members":[
        {"kind":"member","source_id":"beam-1","name":"Beam 1","member_type":"beam"},
        {"kind":"member","source_id":"column-1","name":"Column 1","member_type":"column"},
        {"kind":"member","source_id":"plate-1","name":"Plate 1","member_type":"plate"},
        {"kind":"member","source_id":"foundation-1","name":"Foundation 1","member_type":"foundation"},
    ]}
    return snapshot

@pytest.fixture
def mep_snapshot(architecture_snapshot):
    snapshot=architecture_snapshot | {"profile":"building.mep@1","discipline":"mep"}
    snapshot["payload"]={"payload_version":"mep.v1","components":[
        {"kind":"component","source_id":"hvac-1","name":"HVAC component","system":"hvac"},
        {"kind":"component","source_id":"plumbing-1","name":"Plumbing component","system":"plumbing"},
        {"kind":"component","source_id":"electrical-1","name":"Electrical component","system":"electrical"},
    ]}
    return snapshot
