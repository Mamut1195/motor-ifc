"""Deterministic semantic identity primitives."""
import base64, hashlib, json, uuid
from typing import Any
_NAMESPACE=uuid.UUID("2a09fb88-63ad-5d15-a8dd-66a39c4f0b36")
_STD="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
_IFC="0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$"
_TRANSLATION=str.maketrans(_STD,_IFC)
def semantic_uuid(discipline:str,source_id:str,role:str)->uuid.UUID:
    return uuid.uuid5(_NAMESPACE,f"{discipline}\x1f{source_id}\x1f{role}")
def _compress(value:uuid.UUID)->str:
    encoded=base64.b64encode(bytes.fromhex("0000"+value.hex)).decode("ascii")
    return encoded[2:].translate(_TRANSLATION)
def global_id(discipline:str,source_id:str,role:str)->str:
    return _compress(semantic_uuid(discipline,source_id,role))
def semantic_fingerprint(value:Any)->str:
    if hasattr(value,"model_dump"): value=value.model_dump(mode="json",exclude_none=True)
    canonical=json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False)
    return "sha256:"+hashlib.sha256(canonical.encode("utf-8")).hexdigest()
