"""B04 — synthetic quantity/material oracle models (IFC4, IFC2X3, IFC4X3).

Every value below is a fixed oracle input; the expected extraction JSON is built
independently from these constants (never from the reader) and pinned in
``corpus/expected/``. Seed 1 is consumed by ``base_project``; named seeds below
keep relation and set GlobalIds reproducible and known to the expected builder.
"""
from __future__ import annotations

from pathlib import Path

import ifcopenshell

from .corpus_common import base_project, deterministic_guid, owner_history, write_normalized

SCHEMAS = ("IFC4", "IFC2X3", "IFC4X3")
WALL_GLOBAL_ID = deterministic_guid(900)
BARE_GLOBAL_ID = deterministic_guid(901)
EXPLICIT_LENGTH_MM = 4250.0
PROJECT_AREA = 12.5
COMPONENT_A = 2.0
COMPONENT_B = 2.25
TYPE_LENGTH = 4.0
EXTRA_LENGTH = 9.0
LAYER_THICKNESS = 0.2
INNER_LAYER_THICKNESS = 0.1
CONSTITUENT_FRACTION = 0.8
SEEDS = {
    "rel_occurrence_base": 2,
    "rel_occurrence_extra": 3,
    "rel_defines_type": 4,
    "rel_material_wall": 5,
    "rel_material_type": 6,
    "rel_material_bare": 7,
    "qset_occurrence_base": 8,
    "qset_extra": 9,
    "qset_type_base": 10,
    "wall_type": 11,
}


def _seed(name: str) -> str:
    return deterministic_guid(SEEDS[name])


def build(schema: str) -> object:
    model = ifcopenshell.file(schema=schema)
    history = owner_history(model, ifcopenshell) if schema == "IFC2X3" else None
    base_project(model, ifcopenshell, schema=schema, owner_history_entity=history)
    rooted: dict = {}
    if history is not None:
        rooted = {"OwnerHistory": history}

    wall = model.create_entity("IfcWall", GlobalId=WALL_GLOBAL_ID, Name="Oracle wall", **rooted)
    bare = model.create_entity("IfcBuildingElementProxy", GlobalId=BARE_GLOBAL_ID, Name="Bare object", **rooted)

    milli_unit = model.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Prefix="MILLI", Name="METRE")
    area_kwargs: dict = {"Name": "Area", "AreaValue": PROJECT_AREA}
    if schema != "IFC2X3":
        area_kwargs["Formula"] = "W*H"
    occurrence_quantities = [
        model.create_entity("IfcQuantityLength", Name="Length", LengthValue=EXPLICIT_LENGTH_MM, Unit=milli_unit),
        model.create_entity("IfcQuantityArea", **area_kwargs),
    ]
    if schema != "IFC2X3":
        occurrence_quantities.append(
            model.create_entity(
                "IfcPhysicalComplexQuantity",
                Name="Complex",
                Discrimination="layer",
                HasQuantities=[
                    model.create_entity("IfcQuantityLength", Name="PartA", LengthValue=COMPONENT_A),
                    model.create_entity("IfcQuantityLength", Name="PartB", LengthValue=COMPONENT_B),
                ],
            )
        )
    occurrence_qset = model.create_entity(
        "IfcElementQuantity",
        GlobalId=_seed("qset_occurrence_base"),
        Name="BaseQuantities",
        MethodOfMeasurement="ORACLE-MM",
        Quantities=occurrence_quantities,
        **rooted,
    )
    model.create_entity(
        "IfcRelDefinesByProperties",
        GlobalId=_seed("rel_occurrence_base"),
        RelatedObjects=[wall],
        RelatingPropertyDefinition=occurrence_qset,
        **rooted,
    )
    extra_qset = model.create_entity(
        "IfcElementQuantity",
        GlobalId=_seed("qset_extra"),
        Name="ExtraQuantities",
        Quantities=[model.create_entity("IfcQuantityLength", Name="Length", LengthValue=EXTRA_LENGTH)],
        **rooted,
    )
    model.create_entity(
        "IfcRelDefinesByProperties",
        GlobalId=_seed("rel_occurrence_extra"),
        RelatedObjects=[wall],
        RelatingPropertyDefinition=extra_qset,
        **rooted,
    )
    type_qset = model.create_entity(
        "IfcElementQuantity",
        GlobalId=_seed("qset_type_base"),
        Name="BaseQuantities",
        Quantities=[model.create_entity("IfcQuantityLength", Name="TypeLength", LengthValue=TYPE_LENGTH)],
        **rooted,
    )
    wall_type = model.create_entity(
        "IfcWallType",
        GlobalId=_seed("wall_type"),
        Name="Oracle wall type",
        PredefinedType="NOTDEFINED",
        HasPropertySets=[type_qset],
        **rooted,
    )
    model.create_entity(
        "IfcRelDefinesByType", GlobalId=_seed("rel_defines_type"), RelatedObjects=[wall], RelatingType=wall_type, **rooted
    )

    modern = schema != "IFC2X3"
    concrete = model.create_entity("IfcMaterial", Name="Concrete", **({"Category": "structural"} if modern else {}))
    model.create_entity(
        "IfcRelAssociatesMaterial", GlobalId=_seed("rel_material_wall"), RelatedObjects=[wall], RelatingMaterial=concrete, **rooted
    )
    steel = model.create_entity("IfcMaterial", Name="Steel")
    shell_kwargs: dict = {"Material": steel, "LayerThickness": LAYER_THICKNESS, "IsVentilated": False}
    if modern:
        shell_kwargs.update(Name="Shell", Category="finish", Priority=1)
    layers = [
        model.create_entity("IfcMaterialLayer", **shell_kwargs),
        model.create_entity("IfcMaterialLayer", Material=concrete, LayerThickness=INNER_LAYER_THICKNESS, IsVentilated="UNKNOWN"),
    ]
    layer_set = model.create_entity("IfcMaterialLayerSet", MaterialLayers=layers, LayerSetName="OracleLayers")
    if modern:
        constituent = model.create_entity(
            "IfcMaterialConstituent", Name="Cement", Material=concrete, Fraction=CONSTITUENT_FRACTION, Category="binder"
        )
        constituent_set = model.create_entity("IfcMaterialConstituentSet", Name="Mix", MaterialConstituents=[constituent])
        bare_material: object = constituent_set
    else:
        bare_material = model.create_entity("IfcMaterial", Name="Cement")
    model.create_entity(
        "IfcRelAssociatesMaterial", GlobalId=_seed("rel_material_bare"), RelatedObjects=[bare], RelatingMaterial=bare_material, **rooted
    )
    if schema == "IFC2X3":
        model.create_entity(
            "IfcRelAssociatesMaterial", GlobalId=_seed("rel_material_type"), RelatedObjects=[wall_type], RelatingMaterial=layer_set, **rooted
        )
    else:
        usage = model.create_entity(
            "IfcMaterialLayerSetUsage",
            ForLayerSet=layer_set,
            LayerSetDirection="AXIS3",
            DirectionSense="POSITIVE",
            OffsetFromReferenceLine=0.1,
        )
        model.create_entity(
            "IfcRelAssociatesMaterial", GlobalId=_seed("rel_material_type"), RelatedObjects=[wall_type], RelatingMaterial=usage, **rooted
        )
    return model


def generate(models_dir: Path) -> dict[str, Path]:
    outputs = {}
    for schema in SCHEMAS:
        target = models_dir / f"b04-oracle-{schema.lower()}.ifc"
        write_normalized(build(schema), target)
        outputs[schema] = target
    return outputs


if __name__ == "__main__":
    import sys

    generate(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "models")
