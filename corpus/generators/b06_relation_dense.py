"""B06 — relation-dense stress model: space-boundary fan-out with minimal semantics.

Closed counts: ``WALLS`` IfcWall occurrences, one IfcSpace, and
``BOUNDARIES_PER_WALL`` IfcRelSpaceBoundary relations per wall.
"""
from __future__ import annotations

from pathlib import Path

import ifcopenshell

from .corpus_common import GuidSequence, base_project, write_normalized

WALLS = 500
BOUNDARIES_PER_WALL = 20


def build() -> object:
    model = ifcopenshell.file(schema="IFC4")
    guid = GuidSequence(start=2)
    base_project(model, ifcopenshell, schema="IFC4")
    space = model.create_entity("IfcSpace", GlobalId=guid(), Name="Stress space", CompositionType="ELEMENT")
    for index in range(WALLS):
        wall = model.create_entity("IfcWall", GlobalId=ifcopenshell.guid.compress(f"{index + 100_000_000:032x}"), Name=f"Wall {index}")
        for slot in range(BOUNDARIES_PER_WALL):
            model.create_entity(
                "IfcRelSpaceBoundary",
                GlobalId=guid(),
                Name=f"Boundary {index}-{slot}",
                RelatingSpace=space,
                RelatedBuildingElement=wall,
                InternalOrExternalBoundary="INTERNAL",
                PhysicalOrVirtualBoundary="PHYSICAL",
            )
    return model


def generate(models_dir: Path) -> dict[str, Path]:
    target = models_dir / "b06-relation-dense.ifc"
    write_normalized(build(), target)
    return {"IFC4": target}


if __name__ == "__main__":
    import sys

    generate(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "models")
