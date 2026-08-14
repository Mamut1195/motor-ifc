"""B08 — adversarial bundle builders for boundary and hostile-input tests.

These models are generated at test time (not pinned in the corpus): each builder
targets one documented reader bound so tests can assert N/N+1 behavior and typed
atomic failure without large checked-in binaries.
"""
from __future__ import annotations

from pathlib import Path

import ifcopenshell

from .corpus_common import base_project, write_normalized


def minimal_walls(path: Path, count: int) -> Path:
    model = ifcopenshell.file(schema="IFC4")
    base_project(model, ifcopenshell, schema="IFC4")
    for index in range(count):
        model.create_entity("IfcWall", GlobalId=ifcopenshell.guid.compress(f"{index + 200_000_000:032x}"), Name=f"Wall {index}")
    write_normalized(model, path)
    return path


def wide_quantity_set(path: Path, quantities: int) -> Path:
    model = ifcopenshell.file(schema="IFC4")
    guid = ifcopenshell.guid.new
    base_project(model, ifcopenshell, schema="IFC4")
    wall = model.create_entity("IfcWall", GlobalId=ifcopenshell.guid.compress(f"{300_000_000:032x}"), Name="Wide wall")
    qset = model.create_entity(
        "IfcElementQuantity",
        GlobalId=guid(),
        Name="Wide",
        Quantities=[
            model.create_entity("IfcQuantityLength", Name=f"Length {slot}", LengthValue=float(slot)) for slot in range(quantities)
        ],
    )
    model.create_entity("IfcRelDefinesByProperties", GlobalId=guid(), RelatedObjects=[wall], RelatingPropertyDefinition=qset)
    write_normalized(model, path)
    return path


def deep_property_nesting(path: Path, depth: int) -> Path:
    model = ifcopenshell.file(schema="IFC4")
    guid = ifcopenshell.guid.new
    base_project(model, ifcopenshell, schema="IFC4")
    wall = model.create_entity("IfcWall", GlobalId=ifcopenshell.guid.compress(f"{300_000_001:032x}"), Name="Deep wall")
    current = model.create_entity("IfcPropertySingleValue", Name="Leaf", NominalValue=model.create_entity("IfcLabel", "leaf"))
    for level in range(depth):
        current = model.create_entity(
            "IfcComplexProperty", Name=f"Level {depth - level}", UsageName="nest", HasProperties=[current]
        )
    pset = model.create_entity("IfcPropertySet", GlobalId=guid(), Name="Deep", HasProperties=[current])
    model.create_entity("IfcRelDefinesByProperties", GlobalId=guid(), RelatedObjects=[wall], RelatingPropertyDefinition=pset)
    write_normalized(model, path)
    return path


def garbage(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"NOT-IFC-CONTENT")
    return path


def truncated(path: Path, source: Path) -> Path:
    data = source.read_bytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data[: len(data) // 2])
    return path
