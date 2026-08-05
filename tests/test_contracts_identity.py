from copy import deepcopy
import json
import pytest
from pydantic import TypeAdapter, ValidationError
from motor_ifc import capabilities, validate_snapshot
from motor_ifc.contracts import ArchitectureEnvelope, AuthoringEnvelope, MepEnvelope, StructureEnvelope
from motor_ifc.identity import global_id, semantic_fingerprint
from motor_ifc.models import ValidationPolicy

def test_contract_parses_architecture_and_forbids_extra(architecture_snapshot):
    parsed=TypeAdapter(AuthoringEnvelope).validate_python(architecture_snapshot)
    assert isinstance(parsed,ArchitectureEnvelope)
    invalid=deepcopy(architecture_snapshot); invalid["surprise"]=True
    with pytest.raises(ValidationError): TypeAdapter(AuthoringEnvelope).validate_python(invalid)

def test_payload_contracts_are_distinct(architecture_snapshot):
    structure=deepcopy(architecture_snapshot); structure.update(profile="building.structure@1",discipline="structure"); structure["payload"]={"payload_version":"structure.v1","members":[]}
    mep=deepcopy(architecture_snapshot); mep.update(profile="building.mep@1",discipline="mep"); mep["payload"]={"payload_version":"mep.v1","components":[]}
    assert isinstance(TypeAdapter(AuthoringEnvelope).validate_python(structure),StructureEnvelope)
    assert isinstance(TypeAdapter(AuthoringEnvelope).validate_python(mep),MepEnvelope)

def test_profile_discipline_mismatch_is_invalid(architecture_snapshot):
    invalid=deepcopy(architecture_snapshot); invalid["discipline"]="structure"
    result=validate_snapshot(invalid)
    assert not result.valid and result.diagnostics[0].code==2000

def test_duplicate_source_reference_is_invalid(architecture_snapshot):
    invalid=deepcopy(architecture_snapshot); invalid["payload"]["elements"][0]["source_id"]="level-1"
    assert not validate_snapshot(invalid).valid

def test_policy_rejects_unapproved_and_limits(architecture_snapshot):
    invalid=deepcopy(architecture_snapshot); invalid["authority"]["approved"]=False
    assert validate_snapshot(invalid).diagnostics[0].code==2002
    result=validate_snapshot(architecture_snapshot,ValidationPolicy(max_elements=0))
    assert result.diagnostics[0].code==2003

def test_deterministic_global_id_vector():
    assert global_id("architecture","wall-1","wall")=="02aP2pavvGwgiC798dmGZ6"
    assert global_id("architecture","wall-1","wall")==global_id("architecture","wall-1","wall")
    assert global_id("architecture","wall-1","opening")!=global_id("architecture","wall-1","wall")

def test_semantic_fingerprint_normalizes_mapping_order(architecture_snapshot):
    reordered=dict(reversed(list(architecture_snapshot.items())))
    assert semantic_fingerprint(architecture_snapshot)==semantic_fingerprint(reordered)

def test_capabilities_are_honest():
    result=capabilities()
    assert result.compilation_profiles==("building.architecture@1","building.structure@1","building.mep@1")
    assert set(result.compilation_profiles) <= set(result.validation_profiles)


def test_non_finite_geometry_and_duplicate_domain_ids_are_rejected(architecture_snapshot):
    invalid=deepcopy(architecture_snapshot); invalid["payload"]["elements"][0]["length"]=float("nan")
    assert not validate_snapshot(invalid).valid
    structure=deepcopy(architecture_snapshot); structure.update(profile="building.structure@1",discipline="structure"); member={"kind":"member","source_id":"m1","name":"M","member_type":"beam"}; structure["payload"]={"payload_version":"structure.v1","members":[member,member]}
    assert not validate_snapshot(structure).valid
    mep=deepcopy(architecture_snapshot); mep.update(profile="building.mep@1",discipline="mep"); component={"kind":"component","source_id":"c1","name":"C","system":"hvac"}; mep["payload"]={"payload_version":"mep.v1","components":[component,component]}
    assert not validate_snapshot(mep).valid


def test_unsupported_warning_policy_is_rejected(architecture_snapshot):
    result=validate_snapshot(architecture_snapshot,ValidationPolicy(warnings_as_errors=True))
    assert not result.valid and result.diagnostics[0].code==2000
