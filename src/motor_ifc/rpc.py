"""Bounded newline-delimited JSON-RPC 2.0 sidecar."""
from __future__ import annotations
import json, math, os, sys
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field
from ._version import VERSION
from .api import build_federation, capabilities, compile_snapshot, convert_ifc_to_glb, extract_ifc, extract_ifc_semantic, inspect_ifc, audit_ifc, repair_ifc, validate_ids, validate_ifc, validate_snapshot
from .ids_validation import MAX_IDS_BYTES, MAX_IFC_BYTES
from .models import CompileContext, ValidationPolicy
from .security import UnsafePathError, rpc_input, rpc_output
from .viewer_conversion import MAX_FILENAME_LENGTH
from .reader_extraction import MAX_IFC_BYTES as MAX_READER_IFC_BYTES, extract as extract_reader
MAX_RPC_LINE_BYTES=1_000_000

class _IdsValidationParams(BaseModel):
    model_config=ConfigDict(extra="forbid")
    ifc_path: str=Field(min_length=1,max_length=500)
    ids_path: str=Field(min_length=1,max_length=500)

class _ViewerConversionParams(BaseModel):
    model_config=ConfigDict(extra="forbid")
    ifc_path: str=Field(min_length=1,max_length=500)
    result_dir: str=Field(min_length=1,max_length=500)
    glb_filename: str=Field(min_length=1,max_length=MAX_FILENAME_LENGTH)

class _ReaderExtractionParams(BaseModel):
    model_config=ConfigDict(extra="forbid")
    ifc_path: str=Field(min_length=1,max_length=500)

class _ReaderExtractionV2Params(BaseModel):
    model_config=ConfigDict(extra="forbid")
    ifc_path: str=Field(min_length=1,max_length=500)
    projection: Literal["rich","metadata","properties","quantities","materials"]="rich"
    output_dir: str|None=Field(default=None,min_length=1,max_length=500)

class _ModelAuditParams(BaseModel):
    model_config=ConfigDict(extra="forbid")
    ifc_path: str=Field(min_length=1,max_length=500)

class _ContainedPathParams(BaseModel):
    model_config=ConfigDict(extra="forbid")
    path: str=Field(min_length=1,max_length=500)

class _ModelRepairParams(BaseModel):
    model_config=ConfigDict(extra="forbid")
    ifc_path: str=Field(min_length=1,max_length=500)
    output_dir: str=Field(min_length=1,max_length=500)

class RpcFault(Exception):
    def __init__(self,code:int,message:str,data:Any=None): super().__init__(message); self.code=code; self.message=message; self.data=data
class LegacyAdapterV1:
    # Legacy names, current containment. These take the same path through
    # _contained_input as their *.v1 equivalents: an older method name is
    # not a reason to skip the sandbox.
    def get_version(self,params,job_root): return {"version":VERSION}
    def inspect_ifc(self,params,job_root): return inspect_ifc(_contained_input(params,job_root)).model_dump(mode="json")
    def extract_spatial_tree(self,params,job_root):
        result=inspect_ifc(_contained_input(params,job_root)); return {"success":result.success,"spatial_tree":result.spatial_tree,"diagnostics":[d.model_dump(mode="json") for d in result.diagnostics]}
    def validate_basic(self,params,job_root): return validate_ifc(_contained_input(params,job_root)).model_dump(mode="json")
    def extract(self,method,params,job_root):
        path=_reader_path(params,job_root)
        projection={"extract_metadata":"metadata","extract_properties":"properties","extract_quantities":"quantities"}[method]
        return extract_reader(path,projection).model_dump(mode="json")
    def unsupported(self,method): raise RpcFault(-32001,f"Legacy method {method} is not implemented",{"diagnostic_code":2001})
_LEGACY=LegacyAdapterV1()
def _required(params,key):
    if not isinstance(params,dict) or key not in params: raise RpcFault(-32602,"Invalid params")
    return params[key]
def _unsupported(method): raise RpcFault(-32001,f"Method {method} is not implemented",{"diagnostic_code":2001})

def _contained_input(params,job_root):
    if not job_root: raise RpcFault(-32602,"A configured job root is required")
    request=_ContainedPathParams.model_validate(params)
    try: return rpc_input(job_root,request.path,MAX_READER_IFC_BYTES)
    except (UnsafePathError,OSError,TypeError): raise RpcFault(-32602,"Invalid or oversized input path")

def _reader_path(params,job_root):
    if not job_root: raise RpcFault(-32602,"A configured job root is required")
    request=_ReaderExtractionParams.model_validate(params)
    try: return rpc_input(job_root,request.ifc_path,MAX_READER_IFC_BYTES)
    except (UnsafePathError,OSError,TypeError): raise RpcFault(-32602,"Invalid or oversized reader input path")

def _reader_path_v2(params,job_root):
    if not job_root: raise RpcFault(-32602,"A configured job root is required")
    request=_ReaderExtractionV2Params.model_validate(params)
    try:
        path=rpc_input(job_root,request.ifc_path,MAX_READER_IFC_BYTES)
        output_dir=rpc_output(job_root,request.output_dir) if request.output_dir is not None else None
        return path,request.projection,output_dir
    except (UnsafePathError,OSError,TypeError): raise RpcFault(-32602,"Invalid or oversized reader input path")

def dispatch(method:str,params:Any,job_root:str|None=None)->Any:
    if not isinstance(params,(dict,list)): raise RpcFault(-32602,"Invalid params")
    if method=="engine.capabilities.v1": return capabilities().model_dump(mode="json")
    if method=="authoring.validate.v1": return validate_snapshot(_required(params,"snapshot"),ValidationPolicy.model_validate(params.get("policy",{}))).model_dump(mode="json")
    if method=="authoring.compile.v1":
        if not job_root: raise RpcFault(-32602,"A configured job root is required")
        try: output=rpc_output(job_root,_required(params,"output_dir"))
        except (UnsafePathError,OSError,TypeError): raise RpcFault(-32602,"Invalid output_dir")
        return compile_snapshot(_required(params,"snapshot"),output,CompileContext.model_validate(params.get("context",{}))).model_dump(mode="json")
    if method=="model.inspect.v1": return inspect_ifc(_contained_input(params,job_root)).model_dump(mode="json")
    if method=="reader.extract.v1": return extract_ifc(_reader_path(params,job_root)).model_dump(mode="json")
    if method=="reader.extract.v2":
        path,projection,output_dir=_reader_path_v2(params,job_root)
        return extract_ifc_semantic(path,projection,output_dir).model_dump(mode="json")
    if method=="model.audit.v1":
        if not job_root: raise RpcFault(-32602,"A configured job root is required")
        request=_ModelAuditParams.model_validate(params)
        try: ifc_path=rpc_input(job_root,request.ifc_path,MAX_READER_IFC_BYTES)
        except (UnsafePathError,OSError,TypeError): raise RpcFault(-32602,"Invalid or oversized audit input path")
        return audit_ifc(ifc_path).model_dump(mode="json")
    if method=="model.repair.v1":
        if not job_root: raise RpcFault(-32602,"A configured job root is required")
        request=_ModelRepairParams.model_validate(params)
        try:
            ifc_path=rpc_input(job_root,request.ifc_path,MAX_READER_IFC_BYTES)
            output_dir=rpc_output(job_root,request.output_dir)
        except (UnsafePathError,OSError,TypeError): raise RpcFault(-32602,"Invalid or oversized repair path")
        return repair_ifc(ifc_path,output_dir).model_dump(mode="json")
    if method=="model.validate.v1": return validate_ifc(_contained_input(params,job_root),ValidationPolicy.model_validate(params.get("policy",{}))).model_dump(mode="json")
    if method=="ids.validate.v1":
        if not job_root: raise RpcFault(-32602,"A configured job root is required")
        request=_IdsValidationParams.model_validate(params)
        try:
            ifc_path=rpc_input(job_root,request.ifc_path,MAX_IFC_BYTES)
            ids_path=rpc_input(job_root,request.ids_path,MAX_IDS_BYTES)
        except (UnsafePathError,OSError,TypeError): raise RpcFault(-32602,"Invalid or oversized IDS validation input path")
        return validate_ids(ifc_path,ids_path).model_dump(mode="json")
    if method in {"federation.build_manifest.v1","federation.validate.v1"}: return build_federation(_required(params,"manifests"),params.get("request")).model_dump(mode="json")
    if method=="viewer.convert.v1":
        if not job_root: raise RpcFault(-32602,"A configured job root is required")
        request=_ViewerConversionParams.model_validate(params)
        try:
            ifc_path=rpc_input(job_root,request.ifc_path,MAX_IFC_BYTES)
            result_dir=rpc_output(job_root,request.result_dir)
        except (UnsafePathError,OSError,TypeError): raise RpcFault(-32602,"Invalid or oversized viewer conversion path")
        return convert_ifc_to_glb(ifc_path,result_dir,request.glb_filename).model_dump(mode="json")
    if method in {"job.cancel.v1","cancel_job"}: return None
    if method in {"get_version","inspect_ifc","extract_spatial_tree","validate_basic"}: return getattr(_LEGACY,method)(params,job_root)
    if method in {"extract_metadata","extract_properties","extract_quantities"}: return _LEGACY.extract(method,params,job_root)
    if method=="convert_ifc": return _LEGACY.unsupported(method)
    raise RpcFault(-32601,"Method not found")

def _response(request_id,result=None,fault:RpcFault|None=None):
    body={"jsonrpc":"2.0","id":request_id}
    if fault:
        body["error"]={"code":fault.code,"message":fault.message}
        if fault.data is not None: body["error"]["data"]=fault.data
    else: body["result"]=result
    return json.dumps(body,separators=(",",":"),default=str)

def valid_request_id(value:Any,*,allow_none:bool=True)->bool:
    if value is None: return allow_none
    return not isinstance(value,bool) and isinstance(value,(str,int,float)) and (not isinstance(value,float) or math.isfinite(value))

def _reject_json_constant(value:str)->None:
    raise ValueError(f"invalid JSON constant: {value}")

def strict_json_loads(value:str|bytes)->Any:
    """Decode standards-compliant JSON, rejecting NaN and infinities at any depth."""
    return json.loads(value,parse_constant=_reject_json_constant)

def parse_line(line:str)->tuple[dict[str,Any]|None,str|None]:
    """Parse and validate one JSON-RPC line without dispatching it."""
    if len(line.encode("utf-8",errors="replace"))>MAX_RPC_LINE_BYTES: return None,_response(None,fault=RpcFault(-32600,"Request too large"))
    try: request=strict_json_loads(line)
    except (json.JSONDecodeError,RecursionError,MemoryError,ValueError): return None,_response(None,fault=RpcFault(-32700,"Parse error"))
    if not isinstance(request,dict) or request.get("jsonrpc")!="2.0" or not isinstance(request.get("method"),str):
        return None,_response(request.get("id") if isinstance(request,dict) else None,fault=RpcFault(-32600,"Invalid Request"))
    if "id" in request and not valid_request_id(request.get("id")):
        return None,_response(None,fault=RpcFault(-32600,"Invalid Request"))
    if "params" in request and not isinstance(request["params"],(dict,list)):
        return None,_response(request.get("id"),fault=RpcFault(-32600,"Invalid Request"))
    return request,None

def handle_line(line:str,job_root:str|None=None)->str|None:
    request,error_response=parse_line(line)
    if error_response is not None: return error_response
    assert request is not None
    request_id=request.get("id")
    notification="id" not in request
    try: result=dispatch(request["method"],request.get("params",{}),job_root or os.environ.get("MOTOR_IFC_JOB_ROOT"))
    except RpcFault as fault: return None if notification else _response(request_id,fault=fault)
    except (TypeError,ValueError,RecursionError,MemoryError,OverflowError): return None if notification else _response(request_id,fault=RpcFault(-32602,"Invalid params"))
    return None if notification else _response(request_id,result=result)

def main()->None:
    """Protocol loop; actual in-flight process termination belongs to the supervisor."""
    job_root=os.environ.get("MOTOR_IFC_JOB_ROOT")
    for line in sys.stdin:
        response=handle_line(line,job_root)
        if response is not None: sys.stdout.write(response+"\n"); sys.stdout.flush()
if __name__=="__main__": main()
