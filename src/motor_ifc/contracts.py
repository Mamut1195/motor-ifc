"""Versioned authoring snapshot contracts."""
from __future__ import annotations
from typing import Annotated, Literal, Union
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator

class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

class Authority(StrictModel):
    producer: str = Field(min_length=1, max_length=100)
    ruleset_version: str = Field(min_length=1, max_length=50)
    source_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    approved: bool

class CoordinateReference(StrictModel):
    name: str = Field(default="local-engineering", min_length=1, max_length=200)
    crs: str | None = Field(default=None, max_length=100)
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation_degrees: float = 0.0

class Wall(StrictModel):
    kind: Literal["wall"] = "wall"
    source_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    length: float = Field(gt=0, le=10_000)
    thickness: float = Field(gt=0, le=10)
    height: float = Field(gt=0, le=1_000)

class ArchitecturePayload(StrictModel):
    payload_version: Literal["architecture.v1"] = "architecture.v1"
    storey_source_id: str = Field(min_length=1, max_length=200)
    storey_name: str = Field(min_length=1, max_length=200)
    elevation: float = Field(default=0.0, ge=-10_000, le=100_000)
    elements: list[Wall] = Field(default_factory=list, max_length=10_000)

    @model_validator(mode="after")
    def unique_ids(self) -> "ArchitecturePayload":
        ids = [self.storey_source_id, *(element.source_id for element in self.elements)]
        if len(ids) != len(set(ids)):
            raise ValueError("source_id values must be unique within the payload")
        return self

class StructuralMember(StrictModel):
    kind: Literal["member"] = "member"
    source_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    member_type: Literal["beam", "column", "plate", "foundation"]

class StructurePayload(StrictModel):
    payload_version: Literal["structure.v1"] = "structure.v1"
    members: list[StructuralMember] = Field(default_factory=list, max_length=10_000)

    @model_validator(mode="after")
    def unique_ids(self) -> "StructurePayload":
        if len({item.source_id for item in self.members}) != len(self.members):
            raise ValueError("source_id values must be unique within the payload")
        return self

class MepComponent(StrictModel):
    kind: Literal["component"] = "component"
    source_id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    system: Literal["hvac", "plumbing", "electrical"]

class MepPayload(StrictModel):
    payload_version: Literal["mep.v1"] = "mep.v1"
    components: list[MepComponent] = Field(default_factory=list, max_length=10_000)

    @model_validator(mode="after")
    def unique_ids(self) -> "MepPayload":
        if len({item.source_id for item in self.components}) != len(self.components):
            raise ValueError("source_id values must be unique within the payload")
        return self

class EnvelopeBase(StrictModel):
    contract_version: Literal["authoring.v1"]
    ifc_schema: Literal["IFC4"]
    model_id: UUID
    revision: int = Field(ge=1, le=2_147_483_647)
    federation_id: UUID
    authority: Authority
    units: Literal["SI"]
    coordinate_reference: CoordinateReference

class ArchitectureEnvelope(EnvelopeBase):
    profile: Literal["building.architecture@1"]
    discipline: Literal["architecture"]
    payload: ArchitecturePayload

class StructureEnvelope(EnvelopeBase):
    profile: Literal["building.structure@1"]
    discipline: Literal["structure"]
    payload: StructurePayload

class MepEnvelope(EnvelopeBase):
    profile: Literal["building.mep@1"]
    discipline: Literal["mep"]
    payload: MepPayload

AuthoringEnvelope = Annotated[Union[ArchitectureEnvelope, StructureEnvelope, MepEnvelope], Field(discriminator="profile")]
