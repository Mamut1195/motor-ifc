import json
import pytest
import motor_ifc.ids_validation as ids_validation
from motor_ifc.rpc import MAX_RPC_LINE_BYTES, handle_line

def call(method,params=None,request_id=1):
    request={"jsonrpc":"2.0","id":request_id,"method":method,"params":params or {}}
    return json.loads(handle_line(json.dumps(request)))

def test_rpc_parse_and_method_errors():
    assert json.loads(handle_line("{"))["error"]["code"]==-32700
    assert call("missing.method")["error"]["code"]==-32601
    assert call("model.inspect.v1")["error"]["code"]==-32602

def test_rpc_capabilities_and_legacy_version():
    capabilities=call("engine.capabilities.v1")["result"]
    assert capabilities["engine_version"]=="0.1.0"
    assert capabilities["supervision_contract_versions"]==["process-supervision.v1"]
    assert capabilities["supervisor_default_workers"]==1
    assert capabilities["supervisor_max_workers"]==4
    assert capabilities["request_cancellation"] is True
    assert capabilities["reader_extraction_contract_versions"]==["reader-extraction.v1","reader-extraction.v2"]
    assert call("get_version")["result"]=={"version":"0.1.0"}

def test_rpc_viewer_conversion_requires_job_root():
    response=call("viewer.convert.v1",{"ifc_path":"model.ifc","result_dir":"viewer","glb_filename":"model.glb"})
    assert response["error"]["code"]==-32602

def test_cancel_notification_has_no_response():
    notification=json.dumps({"jsonrpc":"2.0","method":"cancel_job","params":{"id":"job-1"}})
    assert handle_line(notification) is None
    notification=json.dumps({"jsonrpc":"2.0","method":"job.cancel.v1","params":{"id":"job-1"}})
    assert handle_line(notification) is None

def test_regular_notification_has_no_response():
    assert handle_line(json.dumps({"jsonrpc":"2.0","method":"engine.capabilities.v1"})) is None


def test_rpc_rejects_invalid_request_shapes_and_oversize():
    assert json.loads(handle_line("[]"))["error"]["code"]==-32600
    assert json.loads(handle_line(json.dumps({"jsonrpc":"2.0"})))["error"]["code"]==-32600
    assert json.loads(handle_line(" "*(MAX_RPC_LINE_BYTES+1)))["error"]["code"]==-32600


@pytest.mark.parametrize("params",[None,True,1,"value"])
def test_rpc_rejects_scalar_params_as_invalid_request(params):
    request={"jsonrpc":"2.0","id":1,"method":"engine.capabilities.v1","params":params}
    response=json.loads(handle_line(json.dumps(request)))
    assert response["error"]["code"]==-32600


@pytest.mark.parametrize("constant",["NaN","Infinity","-Infinity"])
def test_rpc_rejects_non_finite_values_at_any_depth(constant):
    line=f'{{"jsonrpc":"2.0","id":1,"method":"engine.capabilities.v1","params":{{"nested":[{constant}]}}}}'
    response=json.loads(handle_line(line))
    assert response["error"]["code"]==-32700


@pytest.mark.parametrize("request_id",[float("nan"),float("inf"),float("-inf")],ids=["nan","infinity","negative-infinity"])
def test_rpc_rejects_non_finite_float_request_ids(request_id):
    response=call("engine.capabilities.v1",request_id=request_id)
    assert response["id"] is None
    assert response["error"]["code"]==-32700


def test_rpc_compile_path_is_anchored_to_job_root(tmp_path):
    request={"jsonrpc":"2.0","id":1,"method":"authoring.compile.v1","params":{"snapshot":{},"output_dir":str(tmp_path/"absolute")}}
    response=json.loads(handle_line(json.dumps(request),str(tmp_path)))
    assert response["error"]["code"]==-32602

def test_rpc_ids_validation_requires_exact_contained_paths(tmp_path):
    (tmp_path/"model.ifc").write_text("IFC",encoding="utf-8")
    (tmp_path/"requirements.ids").write_text("<ids xmlns='http://standards.buildingsmart.org/IDS'><info><title>T</title></info><specifications/></ids>",encoding="utf-8")
    response=call("ids.validate.v1",{"ifc_path":"model.ifc","ids_path":"requirements.ids","extra":True})
    assert response["error"]["code"]==-32602
    request={"jsonrpc":"2.0","id":1,"method":"ids.validate.v1","params":{"ifc_path":"../model.ifc","ids_path":"requirements.ids"}}
    response=json.loads(handle_line(json.dumps(request),str(tmp_path)))
    assert response["error"]["code"]==-32602

def test_rpc_ids_validation_returns_typed_runtime_unavailable(tmp_path,monkeypatch):
    (tmp_path/"model.ifc").write_text("IFC",encoding="utf-8")
    ids="""<ids xmlns="http://standards.buildingsmart.org/IDS"><info><title>T</title></info><specifications><specification name="S" ifcVersion="IFC4"><applicability/></specification></specifications></ids>"""
    (tmp_path/"requirements.ids").write_text(ids,encoding="utf-8")
    monkeypatch.setattr(ids_validation,"runtime",lambda: (_ for _ in ()).throw(ids_validation.IdsRuntimeUnavailable()))
    request={"jsonrpc":"2.0","id":1,"method":"ids.validate.v1","params":{"ifc_path":"model.ifc","ids_path":"requirements.ids"}}
    response=json.loads(handle_line(json.dumps(request),str(tmp_path)))
    assert response["result"]["success"] is False
    assert response["result"]["diagnostics"][0]["code"]==2600

def test_rpc_viewer_conversion_rejects_extra_and_escaping_paths(tmp_path):
    (tmp_path/"model.ifc").write_text("IFC",encoding="utf-8")
    request={"jsonrpc":"2.0","id":1,"method":"viewer.convert.v1","params":{"ifc_path":"model.ifc","result_dir":"viewer","glb_filename":"model.glb","extra":True}}
    assert json.loads(handle_line(json.dumps(request),str(tmp_path)))["error"]["code"]==-32602
    request["params"]={"ifc_path":"../model.ifc","result_dir":"viewer","glb_filename":"model.glb"}
    assert json.loads(handle_line(json.dumps(request),str(tmp_path)))["error"]["code"]==-32602
    request["params"]={"ifc_path":"model.ifc","result_dir":"../viewer","glb_filename":"model.glb"}
    assert json.loads(handle_line(json.dumps(request),str(tmp_path)))["error"]["code"]==-32602

@pytest.mark.parametrize("path",["C:drive.glb","name:ads","safe/name:ads","CON.extra/result","safe/\x85"])
def test_rpc_viewer_conversion_rejects_unsafe_path_components(tmp_path,path):
    (tmp_path/"model.ifc").write_text("IFC",encoding="utf-8")
    params={"ifc_path":"model.ifc","result_dir":path,"glb_filename":"model.glb"}
    assert call_with_root("viewer.convert.v1",params,tmp_path)["error"]["code"]==-32602

def call_with_root(method,params,job_root):
    request={"jsonrpc":"2.0","id":1,"method":method,"params":params}
    return json.loads(handle_line(json.dumps(request),str(job_root)))

def test_rpc_viewer_conversion_returns_typed_runtime_failure(tmp_path,monkeypatch):
    from motor_ifc import gltf_adapter
    (tmp_path/"model.ifc").write_text("IFC",encoding="utf-8")
    monkeypatch.setattr(gltf_adapter,"serialize",lambda source,target: (_ for _ in ()).throw(gltf_adapter.ViewerRuntimeUnavailable("missing")))
    request={"jsonrpc":"2.0","id":1,"method":"viewer.convert.v1","params":{"ifc_path":"model.ifc","result_dir":"viewer","glb_filename":"model.glb"}}
    response=json.loads(handle_line(json.dumps(request),str(tmp_path)))
    assert response["result"]["success"] is False
    assert response["result"]["diagnostics"][0]["code"]==2200

def test_rpc_inspect_and_validate_require_contained_paths(tmp_path,monkeypatch):
    import motor_ifc.rpc as rpc_module
    (tmp_path/"small.ifc").write_bytes(b"IFC")
    for method in ("model.inspect.v1","model.validate.v1"):
        request={"jsonrpc":"2.0","id":1,"method":method,"params":{"path":"small.ifc"}}
        assert json.loads(handle_line(json.dumps(request),None))["error"]["code"]==-32602
        escaping={"jsonrpc":"2.0","id":1,"method":method,"params":{"path":"../small.ifc"}}
        assert json.loads(handle_line(json.dumps(escaping),str(tmp_path)))["error"]["code"]==-32602
        extra={"jsonrpc":"2.0","id":1,"method":method,"params":{"path":"small.ifc","extra":True}}
        assert json.loads(handle_line(json.dumps(extra),str(tmp_path)))["error"]["code"]==-32602
    monkeypatch.setattr(rpc_module,"MAX_READER_IFC_BYTES",2)
    oversized={"jsonrpc":"2.0","id":1,"method":"model.inspect.v1","params":{"path":"small.ifc"}}
    assert json.loads(handle_line(json.dumps(oversized),str(tmp_path)))["error"]["code"]==-32602

@pytest.mark.ifcopenshell
def test_rpc_inspect_and_validate_match_public_api_under_job_root(tmp_path):
    ifc=pytest.importorskip("ifcopenshell")
    from motor_ifc import inspect_ifc, validate_ifc
    model=ifc.file(schema="IFC4")
    point=model.create_entity("IfcCartesianPoint",Coordinates=[0.0,0.0,0.0])
    axis=model.create_entity("IfcAxis2Placement3D",Location=point)
    context=model.create_entity("IfcGeometricRepresentationContext",ContextType="Model",CoordinateSpaceDimension=3,WorldCoordinateSystem=axis)
    assignment=model.create_entity("IfcUnitAssignment",Units=[model.create_entity("IfcSIUnit",UnitType="LENGTHUNIT",Name="METRE")])
    model.create_entity("IfcProject",GlobalId=ifc.guid.new(),Name="Project",RepresentationContexts=[context],UnitsInContext=assignment)
    model.create_entity("IfcWall",GlobalId="1AAAAAAAAAAAAAAAAAAAAA",Name="Wall")
    model.write(str(tmp_path/"model.ifc"))
    inspect_response=json.loads(handle_line(json.dumps({"jsonrpc":"2.0","id":1,"method":"model.inspect.v1","params":{"path":"model.ifc"}}),str(tmp_path)))
    assert inspect_response["result"]==inspect_ifc(tmp_path/"model.ifc").model_dump(mode="json")
    validate_response=json.loads(handle_line(json.dumps({"jsonrpc":"2.0","id":2,"method":"model.validate.v1","params":{"path":"model.ifc"}}),str(tmp_path)))
    assert validate_response["result"]==validate_ifc(tmp_path/"model.ifc").model_dump(mode="json")
