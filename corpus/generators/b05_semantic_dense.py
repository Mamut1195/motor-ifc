"""B05 — semantics-dense stress model: many pset/qto leaves, minimal geometry.

Closed counts: ``WALLS`` IfcWall occurrences, each with ``QTO_PER_WALL`` quantities
in one qto set and ``PSET_PER_WALL`` single-value properties in one pset.
"""
from __future__ import annotations

from pathlib import Path

import ifcopenshell

from .corpus_common import GuidSequence, base_project, write_normalized

WALLS = 2_000
QTO_PER_WALL = 40
PSET_PER_WALL = 8


def build() -> object:
    model = ifcopenshell.file(schema="IFC4")
    guid = GuidSequence(start=2)
    base_project(model, ifcopenshell, schema="IFC4")
    for index in range(WALLS):
        wall = model.create_entity("IfcWall", GlobalId=ifcopenshell.guid.compress(f"{index + 100_000_000:032x}"), Name=f"Wall {index}")
        quantities = [
            model.create_entity("IfcQuantityLength", Name=f"Length {slot}", LengthValue=float(index + slot))
            for slot in range(QTO_PER_WALL)
        ]
        qset = model.create_entity("IfcElementQuantity", GlobalId=guid(), Name=f"Qto {index}", Quantities=quantities)
        properties = [
            model.create_entity("IfcPropertySingleValue", Name=f"Prop {slot}", NominalValue=model.create_entity("IfcLabel", f"value-{index}-{slot}"))
            for slot in range(PSET_PER_WALL)
        ]
        pset = model.create_entity("IfcPropertySet", GlobalId=guid(), Name=f"Pset {index}", HasProperties=properties)
        model.create_entity("IfcRelDefinesByProperties", GlobalId=guid(), RelatedObjects=[wall], RelatingPropertyDefinition=qset)
        model.create_entity("IfcRelDefinesByProperties", GlobalId=guid(), RelatedObjects=[wall], RelatingPropertyDefinition=pset)
    return model


def generate(models_dir: Path) -> dict[str, Path]:
    target = models_dir / "b05-semantic-dense.ifc"
    write_normalized(build(), target)
    return {"IFC4": target}


if __name__ == "__main__":
    import sys

    generate(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "models")
