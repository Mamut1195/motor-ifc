"""Generate checked-in JSON Schemas from canonical Pydantic contracts."""
import json
from pathlib import Path
from pydantic import TypeAdapter
from .contracts import ArchitectureEnvelope, AuthoringEnvelope, ArchitecturePayload, StructurePayload, MepPayload
from .models import ElementIndexResult, ModelAuditResult, ModelRepairResult, QualityScoreResult, QuantityDecisions, QuantityEvidenceResult, ReaderExtractionResultV2

def write_schemas(root:Path)->None:
    root.mkdir(parents=True,exist_ok=True)
    models={"authoring-envelope-v1":TypeAdapter(AuthoringEnvelope),"architecture-envelope-v1":TypeAdapter(ArchitectureEnvelope),"architecture-payload-v1":TypeAdapter(ArchitecturePayload),"structure-payload-v1":TypeAdapter(StructurePayload),"mep-payload-v1":TypeAdapter(MepPayload),"reader-extraction-v2":TypeAdapter(ReaderExtractionResultV2),"model-audit-v1":TypeAdapter(ModelAuditResult),"model-repair-v1":TypeAdapter(ModelRepairResult),"element-index-v1":TypeAdapter(ElementIndexResult),"quality-score-v1":TypeAdapter(QualityScoreResult),"quantity-evidence-v1":TypeAdapter(QuantityEvidenceResult),"quantity-decisions-v1":TypeAdapter(QuantityDecisions)}
    for name,adapter in models.items(): (root/f"{name}.schema.json").write_text(json.dumps(adapter.json_schema(),sort_keys=True,indent=2)+"\n",encoding="utf-8")
    rpc={"$schema":"https://json-schema.org/draft/2020-12/schema","title":"JSON-RPC engine.capabilities.v1 request","type":"object","additionalProperties":False,"required":["jsonrpc","id","method"],"properties":{"jsonrpc":{"const":"2.0"},"id":{"type":["string","integer"]},"method":{"const":"engine.capabilities.v1"},"params":{"type":"object","maxProperties":0}}}
    (root/"rpc-capabilities-request-v1.schema.json").write_text(json.dumps(rpc,sort_keys=True,indent=2)+"\n",encoding="utf-8")
def main(): write_schemas(Path("schemas"))
if __name__=="__main__":main()
