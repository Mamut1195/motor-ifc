"""B04 independent expected builder.

Computes the expected ``reader-extraction.v2`` documents for the oracle models
from the generator constants plus the official ``ifcopenshell.util.unit``
utility — never from motor_ifc code. Used to pin ``corpus/expected/*.json`` and
to verify 100% quantity/material correctness in the corpus tests.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import ifcopenshell
import ifcopenshell.util.unit as unit_util

from . import b04_oracle as oracle
from .corpus_common import deterministic_guid


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _unit(source: str, name: str | None, prefix: str | None, unit_type: str | None, symbol: str | None) -> dict:
    return {"source": source, "name": name, "symbol": symbol, "prefix": prefix, "unit_type": unit_type}


def _project_entity() -> dict:
    return {
        "global_id": deterministic_guid(1),
        "ifc_class": "IfcProject",
        "name": "Corpus project",
        "description": None,
        "object_type": None,
        "tag": None,
    }


def expected_quantities(schema: str, model_path: Path) -> dict:
    modern = schema != "IFC2X3"
    length_symbol = unit_util.get_unit_symbol(ifcopenshell.file(schema=schema).create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Name="METRE"))
    area_symbol = unit_util.get_unit_symbol(ifcopenshell.file(schema=schema).create_entity("IfcSIUnit", UnitType="AREAUNIT", Name="SQUARE_METRE"))
    explicit_unit = _unit("quantity", "METRE", "MILLI", "LENGTHUNIT", "mm")
    project_length = _unit("project", "METRE", None, "LENGTHUNIT", length_symbol)
    project_area = _unit("project", "SQUARE_METRE", None, "AREAUNIT", area_symbol)

    def simple(name, ifc_class, attribute_type, value, unit, normalized, formula=None, discrimination=None):
        return {
            "name": name,
            "description": None,
            "ifc_class": ifc_class,
            "formula": formula,
            "discrimination": discrimination,
            "value": value,
            "value_type": attribute_type,
            "unit": unit,
            "normalized_value": normalized,
            "components": [],
        }

    occurrence_quantities = [
        simple("Length", "IfcQuantityLength", "IfcLengthMeasure", oracle.EXPLICIT_LENGTH_MM, explicit_unit, oracle.EXPLICIT_LENGTH_MM / 1000.0),
        simple("Area", "IfcQuantityArea", "IfcAreaMeasure", oracle.PROJECT_AREA, project_area, oracle.PROJECT_AREA, formula="W*H" if modern else None),
    ]
    if modern:
        occurrence_quantities.append(
            {
                "name": "Complex",
                "description": None,
                "ifc_class": "IfcPhysicalComplexQuantity",
                "formula": None,
                "discrimination": "layer",
                "value": None,
                "value_type": None,
                "unit": None,
                "normalized_value": None,
                "components": [
                    simple("PartA", "IfcQuantityLength", "IfcLengthMeasure", oracle.COMPONENT_A, project_length, oracle.COMPONENT_A),
                    simple("PartB", "IfcQuantityLength", "IfcLengthMeasure", oracle.COMPONENT_B, project_length, oracle.COMPONENT_B),
                ],
            }
        )
    wall = {
        "global_id": oracle.WALL_GLOBAL_ID,
        "ifc_class": "IfcWall",
        "name": "Oracle wall",
        "description": None,
        "object_type": None,
        "tag": None,
        "quantity_sets": [
            {
                "global_id": deterministic_guid(oracle.SEEDS["qset_occurrence_base"]),
                "name": "BaseQuantities",
                "description": None,
                "method_of_measurement": "ORACLE-MM",
                "source": "occurrence",
                "relation_global_id": deterministic_guid(oracle.SEEDS["rel_occurrence_base"]),
                "shadowed_by_occurrence": False,
                "quantities": occurrence_quantities,
            },
            {
                "global_id": deterministic_guid(oracle.SEEDS["qset_extra"]),
                "name": "ExtraQuantities",
                "description": None,
                "method_of_measurement": None,
                "source": "occurrence",
                "relation_global_id": deterministic_guid(oracle.SEEDS["rel_occurrence_extra"]),
                "shadowed_by_occurrence": False,
                "quantities": [simple("Length", "IfcQuantityLength", "IfcLengthMeasure", oracle.EXTRA_LENGTH, project_length, oracle.EXTRA_LENGTH)],
            },
            {
                "global_id": deterministic_guid(oracle.SEEDS["qset_type_base"]),
                "name": "BaseQuantities",
                "description": None,
                "method_of_measurement": None,
                "source": "type",
                "relation_global_id": None,
                "shadowed_by_occurrence": True,
                "quantities": [simple("TypeLength", "IfcQuantityLength", "IfcLengthMeasure", oracle.TYPE_LENGTH, project_length, oracle.TYPE_LENGTH)],
            },
        ],
    }
    bare = {
        "global_id": oracle.BARE_GLOBAL_ID,
        "ifc_class": "IfcBuildingElementProxy",
        "name": "Bare object",
        "description": None,
        "object_type": None,
        "tag": None,
        "quantity_sets": [],
    }
    entities = [wall, bare]
    if schema == "IFC2X3":
        entities.insert(0, _project_entity() | {"quantity_sets": []})
    return _envelope(schema, model_path, entities)


def expected_materials(schema: str, model_path: Path) -> dict:
    modern = schema != "IFC2X3"
    wall_layers = [
        {
            "material_name": "Steel",
            "thickness": oracle.LAYER_THICKNESS,
            "is_ventilated": False,
            "priority": 1 if modern else None,
            "category": "finish" if modern else None,
        },
        {"material_name": "Concrete", "thickness": oracle.INNER_LAYER_THICKNESS, "is_ventilated": "UNKNOWN", "priority": None, "category": None},
    ]
    wall_associations = [
        {
            "source": "occurrence",
            "relation_global_id": deterministic_guid(oracle.SEEDS["rel_material_wall"]),
            "kind": "material",
            "name": "Concrete",
            "description": None,
            "category": "structural" if modern else None,
            "materials": [],
            "layers": [],
            "profiles": [],
            "constituents": [],
            "usage_direction": None,
            "usage_offset": None,
        },
        {
            "source": "type",
            "relation_global_id": deterministic_guid(oracle.SEEDS["rel_material_type"]),
            "kind": "layer_set_usage" if modern else "layer_set",
            "name": "OracleLayers",
            "description": None,
            "category": None,
            "materials": [],
            "layers": wall_layers,
            "profiles": [],
            "constituents": [],
            "usage_direction": "AXIS3" if modern else None,
            "usage_offset": 0.1 if modern else None,
        },
    ]
    bare_association = (
        {
            "source": "occurrence",
            "relation_global_id": deterministic_guid(oracle.SEEDS["rel_material_bare"]),
            "kind": "constituent_set",
            "name": "Mix",
            "description": None,
            "category": None,
            "materials": [],
            "layers": [],
            "profiles": [],
            "constituents": [
                {"name": "Cement", "description": None, "category": "binder", "fraction": oracle.CONSTITUENT_FRACTION, "material_name": "Concrete"}
            ],
            "usage_direction": None,
            "usage_offset": None,
        }
        if modern
        else {
            "source": "occurrence",
            "relation_global_id": deterministic_guid(oracle.SEEDS["rel_material_bare"]),
            "kind": "material",
            "name": "Cement",
            "description": None,
            "category": None,
            "materials": [],
            "layers": [],
            "profiles": [],
            "constituents": [],
            "usage_direction": None,
            "usage_offset": None,
        }
    )
    wall = {
        "global_id": oracle.WALL_GLOBAL_ID,
        "ifc_class": "IfcWall",
        "name": "Oracle wall",
        "description": None,
        "object_type": None,
        "tag": None,
        "material_associations": wall_associations,
    }
    bare = {
        "global_id": oracle.BARE_GLOBAL_ID,
        "ifc_class": "IfcBuildingElementProxy",
        "name": "Bare object",
        "description": None,
        "object_type": None,
        "tag": None,
        "material_associations": [bare_association],
    }
    entities = [wall, bare]
    if schema == "IFC2X3":
        entities.insert(0, _project_entity() | {"material_associations": []})
    return _envelope(schema, model_path, entities)


def _envelope(schema: str, model_path: Path, entities: list[dict]) -> dict:
    return {
        "contract_version": "reader-extraction.v2",
        "success": True,
        "source_schema": schema,
        "entity_count": len(entities),
        "entities": entities,
        "diagnostics": [],
        "truncated": False,
        "publication": "none",
        "artifact_filenames": [],
        "source_sha256": _sha256(model_path),
        "extraction_sha256": None,
    }


def write_expected(models_dir: Path, expected_dir: Path) -> dict[str, Path]:
    outputs = {}
    for schema in oracle.SCHEMAS:
        model_path = models_dir / f"b04-oracle-{schema.lower()}.ifc"
        for projection, builder in (("quantities", expected_quantities), ("materials", expected_materials)):
            target = expected_dir / f"b04-oracle-{schema.lower()}-{projection}.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(builder(schema, model_path), indent=2, sort_keys=True) + "\n", encoding="utf-8")
            outputs[f"{schema}/{projection}"] = target
    return outputs


if __name__ == "__main__":
    import sys

    base = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
    write_expected(base / "models", base / "expected")
