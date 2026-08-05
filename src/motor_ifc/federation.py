"""Federation invariant validation and deterministic manifest construction."""
from __future__ import annotations
import hashlib,json
from typing import Any, Literal
from pydantic import BaseModel,ConfigDict,Field,TypeAdapter,ValidationError
from .contracts import CoordinateReference
from .diagnostics import DiagnosticCode,error
from .models import FederationResult
class DisciplineManifest(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    federation_id:str=Field(min_length=1,max_length=200,pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    model_id:str=Field(min_length=1,max_length=200,pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    discipline:Literal["architecture","structure","mep"]
    revision:int=Field(ge=1,le=2_147_483_647)
    artifact:str=Field(min_length=5,max_length=200,pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*[.]ifc$")
    sha256:str=Field(pattern=r"^[0-9a-f]{64}$")
    coordinate_reference:CoordinateReference
_MANIFESTS=TypeAdapter(list[DisciplineManifest])
def build(manifests:list[dict[str,Any]],request:dict[str,Any]|None=None)->FederationResult:
    if request not in (None,{}): return FederationResult(success=False,diagnostics=(error(DiagnosticCode.FEDERATION_MISMATCH,"federation","Non-default federation request options are unsupported.","Remove request options or use a future advertised capability."),))
    try: items=_MANIFESTS.validate_python(manifests)
    except ValidationError: return FederationResult(success=False,diagnostics=(error(DiagnosticCode.FEDERATION_MISMATCH,"federation","Invalid discipline manifest.","Fix the manifest contract.",json_pointer="/manifests"),))
    if not items: return FederationResult(success=False,diagnostics=(error(DiagnosticCode.FEDERATION_MISMATCH,"federation","At least one discipline manifest is required.","Provide validated discipline manifests."),))
    federation_ids={item.federation_id for item in items}; coordinates={json.dumps(item.coordinate_reference.model_dump(mode="json"),sort_keys=True) for item in items}; disciplines=[item.discipline for item in items]
    if len(federation_ids)!=1 or len(coordinates)!=1 or len(disciplines)!=len(set(disciplines)): return FederationResult(success=False,diagnostics=(error(DiagnosticCode.FEDERATION_MISMATCH,"federation","Federation ID, coordinate reference, and unique discipline invariants must match.","Align producer manifests before federation."),))
    ordered=sorted((item.model_dump(mode="json") for item in items),key=lambda item:item["discipline"]); projection={"schema":"building.federation@1","federation_id":next(iter(federation_ids)),"models":ordered}; projection["semantic_fingerprint"]="sha256:"+hashlib.sha256(json.dumps(projection,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    return FederationResult(success=True,manifest=projection)
