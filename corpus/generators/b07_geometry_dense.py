"""B07 — geometry-dense stress model: extrusions plus tessellation, minimal semantics.

Closed counts: ``WALLS`` IfcWall occurrences, each with one extruded-area-solid
representation and one triangulated face set of ``TRIANGLES`` triangles.
"""
from __future__ import annotations

from pathlib import Path

import ifcopenshell

from .corpus_common import base_project, write_normalized

WALLS = 100
TRIANGLES = 64


def _tessellation(model: object, index: int) -> object:
    coordinates = [[float(index), 0.0, 0.0], [float(index) + 1.0, 0.0, 0.0], [float(index), 1.0, 0.0]]
    for triangle in range(TRIANGLES):
        offset = float(triangle) / TRIANGLES
        coordinates.append([float(index) + offset, 1.0 + offset, 0.0])
    coord_index = [[1, 2, 3 + triangle] for triangle in range(TRIANGLES)]
    point_list = model.create_entity("IfcCartesianPointList3D", CoordList=coordinates)
    return model.create_entity("IfcTriangulatedFaceSet", Coordinates=point_list, CoordIndex=coord_index, Closed=False)


def build() -> object:
    model = ifcopenshell.file(schema="IFC4")
    project = base_project(model, ifcopenshell, schema="IFC4")
    context = project.RepresentationContexts[0]
    for index in range(WALLS):
        wall = model.create_entity("IfcWall", GlobalId=ifcopenshell.guid.compress(f"{index + 100_000_000:032x}"), Name=f"Wall {index}")
        profile = model.create_entity("IfcRectangleProfileDef", ProfileType="AREA", XDim=0.2, YDim=5.0)
        direction = model.create_entity("IfcDirection", DirectionRatios=[0.0, 0.0, 1.0])
        position = model.create_entity("IfcAxis2Placement3D", Location=model.create_entity("IfcCartesianPoint", Coordinates=[float(index), 0.0, 0.0]))
        extrusion = model.create_entity("IfcExtrudedAreaSolid", SweptArea=profile, Position=position, ExtrudedDirection=direction, Depth=3.0)
        face_set = _tessellation(model, index)
        shape = model.create_entity(
            "IfcShapeRepresentation",
            ContextOfItems=context,
            RepresentationIdentifier="Body",
            RepresentationType="Tessellation",
            Items=[extrusion, face_set],
        )
        product_shape = model.create_entity("IfcProductDefinitionShape", Representations=[shape])
        wall.Representation = product_shape
    return model


def generate(models_dir: Path) -> dict[str, Path]:
    target = models_dir / "b07-geometry-dense.ifc"
    write_normalized(build(), target)
    return {"IFC4": target}


if __name__ == "__main__":
    import sys

    generate(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "models")
