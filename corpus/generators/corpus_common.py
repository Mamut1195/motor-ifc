"""Deterministic corpus generators for the motor-ifc benchmark corpus.

Every generator builds an IFC model from fixed inputs only: no clocks (the STEP
FILE_NAME timestamp is normalized), no random sources (every GlobalId derives
from an explicit integer seed through ``deterministic_guid``). Re-running a
generator reproduces identical bytes; generated models are pinned in
``corpus/MANIFEST.json`` by SHA-256 and a freshness test re-generates and compares.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import ifcopenshell

FIXED_TIMESTAMP = "1970-01-01T00:00:00"
_FILE_NAME_PATTERN = re.compile(rb"(FILE_NAME\('[^']*',')[^']*(')")


def deterministic_guid(seed: int) -> str:
    return ifcopenshell.guid.compress(f"{seed:032x}")


class GuidSequence:
    """Deterministic GlobalId source: the n-th call always yields the same GUID."""

    def __init__(self, start: int = 1) -> None:
        self.next_seed = start

    def __call__(self) -> str:
        seed = self.next_seed
        self.next_seed += 1
        return deterministic_guid(seed)


def normalize_step_header(data: bytes) -> bytes:
    """Replace the FILE_NAME timestamp with a fixed value so bytes are reproducible."""
    return _FILE_NAME_PATTERN.sub(rb"\g<1>" + FIXED_TIMESTAMP.encode("ascii") + rb"\g<2>", data, count=1)


def write_normalized(model: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    model.write(str(path))
    path.write_bytes(normalize_step_header(path.read_bytes()))


def base_project(model: Any, ifc: Any, *, schema: str, units: tuple[str, ...] = ("LENGTHUNIT", "AREAUNIT", "VOLUMEUNIT"), owner_history_entity: Any = None) -> Any:
    guid = GuidSequence(start=1)
    point = model.create_entity("IfcCartesianPoint", Coordinates=[0.0, 0.0, 0.0])
    axis = model.create_entity("IfcAxis2Placement3D", Location=point)
    context = model.create_entity(
        "IfcGeometricRepresentationContext", ContextType="Model", CoordinateSpaceDimension=3, WorldCoordinateSystem=axis
    )
    assignment = model.create_entity(
        "IfcUnitAssignment", Units=[model.create_entity("IfcSIUnit", UnitType=unit_type, Name=name) for unit_type, name in zip(units, ("METRE", "SQUARE_METRE", "CUBIC_METRE"))]
    )
    project_kwargs: dict[str, Any] = {"GlobalId": guid(), "Name": "Corpus project", "RepresentationContexts": [context], "UnitsInContext": assignment}
    if schema == "IFC2X3":
        project_kwargs["OwnerHistory"] = owner_history_entity or owner_history(model, ifc)
    return model.create_entity("IfcProject", **project_kwargs)


def owner_history(model: Any, ifc: Any) -> Any:
    person = model.create_entity("IfcPerson")
    organization = model.create_entity("IfcOrganization", Name="corpus")
    person_and_organization = model.create_entity("IfcPersonAndOrganization", ThePerson=person, TheOrganization=organization)
    application = model.create_entity(
        "IfcApplication", ApplicationDeveloper=organization, Version="1.0", ApplicationFullName="corpus", ApplicationIdentifier="corpus"
    )
    return model.create_entity(
        "IfcOwnerHistory", OwningUser=person_and_organization, OwningApplication=application, ChangeAction="ADDED", CreationDate=0
    )
