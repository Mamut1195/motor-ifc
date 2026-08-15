"""Bounded element index: one measured number per dimension, per element.

`reader-extraction.v2` reports every quantity set losslessly and leaves collision
detection to its consumer (ADR 0006). This contract is that consumer. It selects one
value per dimension by name, in a declared order of preference, from the most
authoritative set an element carries, and reports which set and which quantity name
won. It reads units through `reader_extraction._resolve_quantity_unit`, the same
`ifcopenshell.util.unit` path v2 uses; it never re-derives a scale factor of its own.

It is a projection over the shared reader pipeline, not a second reader: secure open,
private snapshot, schema validation, entity ordering, node budgets, atomic failure and
bounded publication are all inherited unchanged.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from .diagnostics import DiagnosticCode, warning
from .models import ElementIndexRecord, ElementIndexResult, ElementIndexStorey, ElementQuantity, QuantityDecisions
from .reader_extraction import (
    _BoundExceeded,
    _Counter,
    _entity_metadata,
    _extract_pipeline,
    _normalize,
    _plain_scalar,
    _QUANTITY_MEASURES,
    _resolve_quantity_unit,
    _unit_util,
    MAX_ARRAY_ITEMS,
    MAX_INLINE_RESULT_BYTES,
    MAX_SETS_PER_ENTITY,
    MAX_TOTAL_NODES_V2,
)

CONTRACT_VERSION = "element-index.v1"

#: Widest material or classification summary an element may carry. The reference
#: implementation read this width out of a database column at import time; here it is
#: a declared limit that owes nothing to any consumer's storage.
MAX_MATERIAL_LENGTH = 300
MAX_CLASSIFICATION_LENGTH = 300
MAX_STOREYS = 1_000
MAX_DISTINCT_TYPES = 1_000
#: Unit-scale warnings carried inline. The count is never truncated; the individual
#: warnings are, so one pathological model cannot inflate the response without bound.
MAX_UNIT_SCALE_WARNINGS = 100

Projection = Literal["index", "rich"]

# What gets indexed, and what does not.
#
# buildingSMART states which entities carry quantities by defining a `Qto_*BaseQuantities`
# template for them. IfcOpenShell ships those official templates
# (`ifcopenshell/util/schema/Pset_IFC4_ADD2.ifc`), and IFC4 ADD2 defines 93 of them. The
# scope below is derived from that set, not invented: three branch supertypes reach every
# billable one of them, because `by_type` already returns every subtype.
#
# Deliberately outside the scope, each for a stated reason:
#
#   IfcFeatureElement (IfcOpeningElement, IfcProjectionElement) — modifiers of a host
#     element. buildingSMART is explicit that an opening "should not be linked directly to
#     the spatial structure ... ContainedInStructure shall be NIL": its quantity exists to
#     deduct from the wall, never to be billed on its own.
#   IfcSite / IfcBuilding / IfcBuildingStorey — accumulators whose area already contains
#     everything inside them. Billing them double-counts the entire model.
#   Construction resources (labour, material, equipment) — cost and schedule side, not
#     model geometry.
#
# `IfcStair` and `IfcRamp` have no `Qto_` template of their own while `IfcStairFlight` and
# `IfcRampFlight` do: buildingSMART confirms by omission that a stair is an aggregate of
# flights, which the container rule already resolves.
#
# Each entry is a group of schema aliases; the first name the loaded schema declares wins.
# `IfcBuildingElement` was renamed `IfcBuiltElement` in IFC4X3, so both are named and only
# a group where *every* alias is missing counts as skipped.
BUILDING_ELEMENT_TYPES = (
    ("IfcBuiltElement", "IfcBuildingElement"),
    ("IfcDistributionElement",),
    ("IfcElementComponent",),
)

#: Spatial entities indexed as their own family. A space measures room surfaces, never
#: element surfaces, so its quantities never share a dimension with a wall's.
SPATIAL_TYPES = (("IfcSpace",),)

_CANONICAL_SET = "base_quantity"
_VENDOR_SET = "vendor_quantity"

_DIMENSION_BY_CLASS = {
    "IfcQuantityVolume": "volume",
    "IfcQuantityArea": "area",
    "IfcQuantityLength": "length",
    "IfcQuantityCount": "count",
    "IfcQuantityWeight": "weight",
}

# Quantity names accepted per dimension, most preferred first.
#
# Net before gross: net is deducted for openings, which is the conservative basis for
# costing and the one that matches the solid when opening subtraction is applied.
# Revit remaps Length/Width/Height to Nominal* below IFC4, so those aliases are
# accepted too. Both spellings of "FootPrint" appear in official buildingSMART
# artefacts; exporters emit the lowercase one.
#
# Each entry is ``dimension: (physical measure, accepted names)``. The measure is what the
# `IfcQuantity*` class reports; the dimension is what the number is billed as. For elements
# the two coincide, for spaces they do not.
_CANONICAL_NAMES = {
    "volume": ("volume", ("NetVolume", "GrossVolume", "Volume")),
    "area": (
        "area",
        (
            "NetSideArea",
            "NetArea",
            "NetFootprintArea",
            "NetFootPrintArea",
            "GrossSideArea",
            "GrossArea",
            "GrossFootprintArea",
            "GrossFootPrintArea",
            "Area",
        ),
    ),
    "length": ("length", ("Length", "NominalLength", "Perimeter")),
    "weight": ("weight", ("NetWeight", "GrossWeight", "Weight")),
    "count": ("count", ("Count",)),
}

# Vendor quantity names known to mean the same thing. Anything not listed is not a
# measurement of the element but an auxiliary metric, and several of those are
# legitimately zero.
_VENDOR_NAMES = {
    "volume": ("volume", ("Netto-Volumen", "Net Volume", "Brutto-Volumen", "Gross Volume")),
    "area": ("area", ("Netto-Fläche", "Net Area", "Brutto-Fläche", "Gross Area")),
    "length": ("length", ("Länge", "Length")),
    "weight": ("weight", ("Gewicht", "Weight")),
    "count": ("count", ("Anzahl", "Count")),
}

#: Dimension order in every emitted record. Fixed, so two runs cannot differ.
_DIMENSION_ORDER = ("area", "volume", "length", "count", "weight")

# Space dimensions, from Qto_SpaceBaseQuantities.
#
# A space does not have "an area": the official template defines six, and each one bills
# against a different item — the specification defines NetPerimeter as "net perimeter at
# the floor level ... used for skirting boards". Folding them into an element's `area`
# would price wall paint against floor tiles.
_SPACE_CANONICAL_NAMES = {
    "floor_area": ("area", ("NetFloorArea", "GrossFloorArea")),
    "wall_area": ("area", ("NetWallArea", "GrossWallArea")),
    "ceiling_area": ("area", ("NetCeilingArea", "GrossCeilingArea")),
    "perimeter": ("length", ("NetPerimeter", "GrossPerimeter")),
    "height": ("length", ("Height", "FinishCeilingHeight", "FinishFloorHeight")),
    "volume": ("volume", ("NetVolume", "GrossVolume")),
}

# Vendor dialects for spaces.
#
# IFC2X3 defines no Qto_ template at all, so in that schema a vendor name is all there is:
# Duplex_A carries its 21 room areas only as "GSA BIM Area", a floor area from the GSA BIM
# Guide.
#
# BOMA is deliberately absent, and this is why rather than an oversight: BOMA measures
# RENTABLE area, not work to be executed. Its rules add and deduct floor area by leasing
# criteria — cores, shared circulation, pro-rated common areas — that correspond to no
# metre anyone builds. Taking `SpaceNetFloorAreaBOMA` or `SpaceUsableFloorAreaBOMA` as
# `floor_area` would bill surface nobody executes. Both names are listed here so a reader
# sees they were considered and rejected, never selected.
_SPACE_VENDOR_NAMES = {
    "floor_area": ("area", ("GSA BIM Area",)),
}
_SPACE_REJECTED_NAMES = ("SpaceNetFloorAreaBOMA", "SpaceUsableFloorAreaBOMA")

_SPACE_DIMENSION_ORDER = ("floor_area", "wall_area", "ceiling_area", "perimeter", "height", "volume")

# Classes billed per unit, matched through inheritance so `IfcSanitaryTerminal` arrives as
# an `IfcFlowTerminal` without being named. Their existence is the quantity: a door with no
# quantity set is still one door, and reporting it as unmeasured work is what makes a
# complete model look incomplete.
_COUNTABLE_CLASSES = ("IfcDoor", "IfcWindow", "IfcFlowTerminal", "IfcFlowFitting")


def _has_representation(item: Any) -> bool:
    shape = getattr(item, "Representation", None)
    return bool(getattr(shape, "Representations", None)) if shape is not None else False


def _decomposition(item: Any) -> tuple[int, str | None]:
    """``(parts, container global id)`` through `IfcRelAggregates`, both directions."""
    parts = 0
    for relation in getattr(item, "IsDecomposedBy", None) or ():
        if relation.is_a("IfcRelAggregates"):
            parts += len(getattr(relation, "RelatedObjects", None) or ())
    whole = None
    for relation in getattr(item, "Decomposes", None) or ():
        if relation.is_a("IfcRelAggregates"):
            whole = _plain_scalar(getattr(getattr(relation, "RelatingObject", None), "GlobalId", None))
    return parts, whole


def _measurability(item: Any, decomposes_into: int) -> str:
    """What it would mean for this element to be measurable.

    Structure decides before class, and the element's name decides nothing at all. A name
    rule would have to guess that `origin` and `geo-reference` are survey markers while
    `sand bedding` is real work — in one language, from one exporter. Decomposition and
    geometry are properties of the model itself.
    """
    if item.is_a("IfcSpace"):
        # A room measures room surfaces. It is billable — floors, paint, ceilings — but
        # never against the same dimension as the wall that bounds it.
        return "spatial"
    if decomposes_into:
        # Its parts carry its quantities. Counting it as unmeasured reports double-count
        # avoidance as a defect.
        return "container"
    if item.is_a("IfcBuildingElementProxy"):
        if not _has_representation(item):
            # A proxy is the class with no declared semantics; without geometry too there
            # is nothing to measure and nothing to name it by. That is a grouping node.
            return "non_geometric"
        # Genuinely undecidable: a coordinate marker in one file, roof trim in the next,
        # and carrying real quantities in a third. Reported, never guessed.
        return "ambiguous"
    if any(item.is_a(name) for name in _COUNTABLE_CLASSES):
        return "countable"
    # Any other class keeps its class meaning even without geometry. PCERT carries an
    # `IfcChimney` with a material and a storey but no body and no quantities: that is a
    # chimney nobody can measure, which is information, not noise.
    return "structural"


def _is_canonical_set(name: Any) -> bool:
    text = (name or "").strip().lower() if isinstance(name, str) else ""
    return text == "basequantities" or (text.startswith("qto_") and text.endswith("basequantities"))


def _flatten(quantities: Any, depth: int = 0) -> list[Any]:
    """Simple quantities, descending into `IfcPhysicalComplexQuantity`.

    Some exporters nest an entire quantity set under complex quantities; a flat walk
    over `Quantities` sees nothing at all in those files.
    """
    if depth > 8:
        raise _BoundExceeded
    flat: list[Any] = []
    items = quantities or ()
    if len(items) > MAX_ARRAY_ITEMS:
        raise _BoundExceeded
    for quantity in items:
        if quantity.is_a("IfcPhysicalComplexQuantity"):
            flat.extend(_flatten(getattr(quantity, "HasQuantities", None), depth + 1))
        else:
            flat.append(quantity)
    return flat


def _collect(item: Any) -> tuple[dict[str, dict[str, dict[str, Any]]], dict[str, str]]:
    """``{set_kind: {dimension: {quantity_name: quantity}}}`` plus the winning set names.

    Keeps the quantity entity, not just its number: the unit resolver needs the entity
    to read the `Unit` attribute before falling back to the project unit.
    """
    found: dict[str, dict[str, dict[str, Any]]] = {_CANONICAL_SET: {}, _VENDOR_SET: {}}
    set_names: dict[str, str] = {}
    relations = getattr(item, "IsDefinedBy", None) or ()
    if len(relations) > MAX_SETS_PER_ENTITY:
        raise _BoundExceeded
    for relation in relations:
        if not relation.is_a("IfcRelDefinesByProperties"):
            continue
        quantity_set = getattr(relation, "RelatingPropertyDefinition", None)
        if quantity_set is None or not quantity_set.is_a("IfcElementQuantity"):
            continue
        kind = _CANONICAL_SET if _is_canonical_set(getattr(quantity_set, "Name", None)) else _VENDOR_SET
        set_names.setdefault(kind, _plain_scalar(getattr(quantity_set, "Name", None)) or "")
        for quantity in _flatten(getattr(quantity_set, "Quantities", None)):
            dimension = _DIMENSION_BY_CLASS.get(quantity.is_a())
            if dimension is None:
                continue
            attribute, _measure = _QUANTITY_MEASURES[quantity.is_a()]
            if getattr(quantity, attribute, None) is None:
                continue
            name = _plain_scalar(getattr(quantity, "Name", None))
            if isinstance(name, str):
                found[kind].setdefault(dimension, {}).setdefault(name, quantity)
    return found, set_names


def _measured(
    quantity: Any, dimension: str, measure_kind: str, rank: int, model: Any, unit_util: Any
) -> tuple[ElementQuantity, bool]:
    """One selected quantity, with its declared unit and its SI value when derivable.

    Returns ``(record, resolved)``. ``count`` is dimensionless: it has no unit to
    normalize and is reported as already normalized, because scaling a count would turn
    one element into a fraction of one.
    """
    ifc_class = quantity.is_a()
    attribute, measure = _QUANTITY_MEASURES[ifc_class]
    value = float(getattr(quantity, attribute))
    if measure_kind == "count":
        return ElementQuantity(
            dimension=dimension,
            value=value,
            normalized_value=value,
            unit=None,
            source_quantity_name=_plain_scalar(getattr(quantity, "Name", None)),
            selection_rank=rank,
        ), True
    unit, normalized = _resolve_quantity_unit(quantity, value, measure, model, unit_util)
    return ElementQuantity(
        dimension=dimension,
        value=value,
        normalized_value=normalized,
        unit=unit,
        source_quantity_name=_plain_scalar(getattr(quantity, "Name", None)),
        selection_rank=rank,
    ), normalized is not None


def _decided(
    item: Any,
    model: Any,
    unit_util: Any,
    decisions: Any,
) -> tuple[tuple[ElementQuantity, ...], tuple[str, ...], set[tuple[str, str]]]:
    """Quantities the caller's rulings select, and the rulings that matched.

    A ruling only ever names something the file already declares; the number still comes
    from the file. Every record it produces carries `decided_by`, so a quantity a model
    chose can never be mistaken for one a standard named.
    """
    rulings = [
        ruling
        for ruling in decisions.quantity_names
        if ruling.dimension is not None and (not ruling.applies_to or any(item.is_a(name) for name in ruling.applies_to))
    ]
    if not rulings:
        return (), (), set()
    available: dict[tuple[str, str], Any] = {}
    for relation in getattr(item, "IsDefinedBy", None) or ():
        if not relation.is_a("IfcRelDefinesByProperties"):
            continue
        quantity_set = getattr(relation, "RelatingPropertyDefinition", None)
        if quantity_set is None or not quantity_set.is_a("IfcElementQuantity"):
            continue
        for quantity in _flatten(getattr(quantity_set, "Quantities", None)):
            measure = _DIMENSION_BY_CLASS.get(quantity.is_a())
            if measure is None:
                continue
            attribute, _measure_type = _QUANTITY_MEASURES[quantity.is_a()]
            if getattr(quantity, attribute, None) is None:
                continue
            name = _plain_scalar(getattr(quantity, "Name", None))
            if isinstance(name, str) and name:
                available.setdefault((measure, name), quantity)
    chosen: list[ElementQuantity] = []
    unresolved: list[str] = []
    matched: set[tuple[str, str]] = set()
    seen: set[str] = set()
    for rank, ruling in enumerate(rulings):
        quantity = available.get((ruling.measure, ruling.name))
        if quantity is None or ruling.dimension in seen:
            continue
        matched.add((ruling.measure, ruling.name))
        seen.add(ruling.dimension)
        record, resolved = _measured(quantity, ruling.dimension, ruling.measure, rank, model, unit_util)
        chosen.append(record.model_copy(update={"decided_by": decisions.decided_by or "caller"}))
        if not resolved:
            unresolved.append(ruling.dimension)
    return tuple(chosen), tuple(unresolved), matched


def _select(
    item: Any,
    model: Any,
    unit_util: Any,
    canonical: dict[str, tuple[str, tuple[str, ...]]],
    vendor: dict[str, tuple[str, tuple[str, ...]]],
    order: tuple[str, ...],
) -> tuple[str, str | None, tuple[ElementQuantity, ...], tuple[str, ...]]:
    """Resolve one value per dimension from the best available set.

    A canonical `BaseQuantities` set wins over a vendor set entirely: mixing them would
    pair a net area from one exporter's vocabulary with a gross volume from another's.
    When neither set yields anything the element is reported as ``fallback`` with no
    quantities at all — never as a measured zero.

    The dimension tables are a parameter because elements and spaces measure different
    things out of the same `IfcQuantityArea`: a wall's side area and a room's floor area
    are both areas, and nothing but the name tells them apart.
    """
    found, set_names = _collect(item)
    for kind, allowed in ((_CANONICAL_SET, canonical), (_VENDOR_SET, vendor)):
        by_measure = found.get(kind) or {}
        chosen: list[ElementQuantity] = []
        unresolved: list[str] = []
        for dimension in order:
            entry = allowed.get(dimension)
            if entry is None:
                continue
            measure_kind, names = entry
            available = by_measure.get(measure_kind) or {}
            for rank, name in enumerate(names):
                if name in available:
                    record, resolved = _measured(available[name], dimension, measure_kind, rank, model, unit_util)
                    chosen.append(record)
                    if not resolved:
                        unresolved.append(dimension)
                    break
        if chosen:
            return kind, set_names.get(kind), tuple(chosen), tuple(unresolved)
    return "fallback", None, (), ()


def _summarize_materials(names: Any) -> str:
    """Join material names into one bounded, repeat-free summary.

    Repeats go first: a summary of which materials an element is made of gains nothing
    from listing one twice, and a repeated layer name is what actually exhausts the
    budget on real multi-layer walls. Whole names are then taken while they fit —
    cutting one in half would invent a material that is not in the model.
    """
    summary = ""
    seen: set[str] = set()
    for name in names:
        if not isinstance(name, str) or not name or name in seen:
            continue
        seen.add(name)
        candidate = f"{summary}, {name}" if summary else name
        if len(candidate) > MAX_MATERIAL_LENGTH:
            return summary or name[:MAX_MATERIAL_LENGTH]
        summary = candidate
    return summary


def _layer_names(layers: Any) -> list[Any]:
    return [
        _plain_scalar(getattr(getattr(layer, "Material", None), "Name", None))
        for layer in (layers or ())
    ]


def _material(item: Any) -> str:
    for relation in getattr(item, "HasAssociations", None) or ():
        if not relation.is_a("IfcRelAssociatesMaterial"):
            continue
        material = getattr(relation, "RelatingMaterial", None)
        if material is None:
            continue
        if material.is_a("IfcMaterial"):
            return _summarize_materials([_plain_scalar(getattr(material, "Name", None))])
        if material.is_a("IfcMaterialLayerSetUsage"):
            layer_set = getattr(material, "ForLayerSet", None)
            return _summarize_materials(_layer_names(getattr(layer_set, "MaterialLayers", None)))
        if material.is_a("IfcMaterialLayerSet"):
            return _summarize_materials(_layer_names(getattr(material, "MaterialLayers", None)))
        if material.is_a("IfcMaterialList"):
            return _summarize_materials(
                _plain_scalar(getattr(member, "Name", None)) for member in getattr(material, "Materials", None) or ()
            )
        name = _plain_scalar(getattr(material, "Name", None))
        if name:
            return _summarize_materials([name])
    return ""


def _classification(item: Any) -> str:
    """The element's classification reference — `Identification`, else `ItemReference`.

    IFC2X3 spells the attribute `ItemReference`; IFC4 renamed it `Identification`.
    """
    for relation in getattr(item, "HasAssociations", None) or ():
        if not relation.is_a("IfcRelAssociatesClassification"):
            continue
        reference = getattr(relation, "RelatingClassification", None)
        for attribute in ("Identification", "ItemReference"):
            value = _plain_scalar(getattr(reference, attribute, None))
            if isinstance(value, str) and value:
                return value[:MAX_CLASSIFICATION_LENGTH]
    return ""


def _properties(item: Any) -> dict[str, Any]:
    """`{"<set name>.<property name>": value}` for single-value properties."""
    properties: dict[str, Any] = {}
    for relation in getattr(item, "IsDefinedBy", None) or ():
        if not relation.is_a("IfcRelDefinesByProperties"):
            continue
        property_set = getattr(relation, "RelatingPropertyDefinition", None)
        if property_set is None or not property_set.is_a("IfcPropertySet"):
            continue
        set_name = _plain_scalar(getattr(property_set, "Name", None)) or "Unknown"
        for item_property in getattr(property_set, "HasProperties", None) or ():
            if not item_property.is_a("IfcPropertySingleValue"):
                continue
            nominal = getattr(item_property, "NominalValue", None)
            if nominal is None:
                continue
            properties[f"{set_name}.{_plain_scalar(getattr(item_property, 'Name', None))}"] = _plain_scalar(nominal)
    return properties


def _storey_map(model: Any) -> dict[str, tuple[str | None, str | None]]:
    mapping: dict[str, tuple[str | None, str | None]] = {}
    for relation in model.by_type("IfcRelContainedInSpatialStructure"):
        structure = getattr(relation, "RelatingStructure", None)
        if structure is None or not structure.is_a("IfcBuildingStorey"):
            continue
        storey = (_plain_scalar(getattr(structure, "GlobalId", None)), _plain_scalar(getattr(structure, "Name", None)))
        for element in getattr(relation, "RelatedElements", None) or ():
            global_id = getattr(element, "GlobalId", None)
            if isinstance(global_id, str) and global_id:
                mapping.setdefault(global_id, storey)
    return mapping


def _storeys(model: Any) -> tuple[ElementIndexStorey, ...]:
    records = [
        ElementIndexStorey(
            global_id=storey.GlobalId,
            name=_plain_scalar(getattr(storey, "Name", None)),
            elevation=_plain_scalar(getattr(storey, "Elevation", None)),
        )
        for storey in model.by_type("IfcBuildingStorey")
        if isinstance(getattr(storey, "GlobalId", None), str) and storey.GlobalId
    ]
    if len(records) > MAX_STOREYS:
        raise _BoundExceeded
    return tuple(sorted(records, key=lambda record: record.global_id))


def index(
    path: str | Path,
    projection: Projection = "index",
    output_dir: str | Path | None = None,
    decisions: QuantityDecisions | None = None,
) -> ElementIndexResult:
    """Index a model's building elements with their resolved quantities.

    Without ``output_dir`` the whole result is returned inline under a strict byte cap;
    with it the result is published as one immutable directory artifact.

    ``decisions`` is caller-supplied authority over naming: it can claim a quantity name the
    built-in tables reject, or rule an element class billable. It can never supply a number,
    and everything it selects is marked. Without it the behaviour is exactly the frozen one.
    """
    unresolved: list[tuple[str, str]] = []
    unresolved_count = 0
    storey_by_element: dict[str, tuple[str | None, str | None]] = {}
    matched_rulings: set[tuple[str, str]] = set()

    def prepare(model: Any) -> tuple[list[Any], dict[str, Any]]:
        nonlocal storey_by_element
        storey_by_element = _storey_map(model)
        instances: dict[int, Any] = {}
        skipped: list[str] = []
        for group in BUILDING_ELEMENT_TYPES + SPATIAL_TYPES:
            resolved = False
            for type_name in group:
                try:
                    items = model.by_type(type_name)
                except RuntimeError:
                    # An alias the loaded schema does not declare. Only a group where every
                    # alias is missing is a real gap: a class we meant to bill must not
                    # disappear without a trace, but a renamed one is not missing.
                    continue
                resolved = True
                for item in items:
                    global_id = getattr(item, "GlobalId", None)
                    if isinstance(global_id, str) and global_id:
                        instances[item.id()] = item
                break
            if not resolved:
                skipped.append(group[0])
        ordered = sorted(instances.values(), key=lambda item: (item.GlobalId, item.is_a(), item.id()))
        seen: set[str] = set()
        scoped = []
        duplicates = 0
        for item in ordered:
            if item.GlobalId in seen:
                duplicates += 1
                continue
            seen.add(item.GlobalId)
            scoped.append(item)
        element_types = sorted({item.is_a() for item in scoped})
        if len(element_types) > MAX_DISTINCT_TYPES:
            raise _BoundExceeded
        projects = model.by_type("IfcProject")
        return scoped, {
            "project_name": _plain_scalar(getattr(projects[0], "Name", None)) if projects else None,
            "storeys": _storeys(model),
            "element_types": tuple(element_types),
            "skipped_types": tuple(skipped),
            "duplicate_global_id_count": duplicates,
        }

    def builder(model: Any, element_util: Any, item: Any, counter: _Counter) -> ElementIndexRecord:
        nonlocal unresolved_count
        metadata = _entity_metadata(item, counter)
        unit_util = _unit_util()
        spatial = item.is_a("IfcSpace")
        source, set_name, quantities, unresolved_dimensions = _select(
            item,
            model,
            unit_util,
            _SPACE_CANONICAL_NAMES if spatial else _CANONICAL_NAMES,
            _SPACE_VENDOR_NAMES if spatial else _VENDOR_NAMES,
            _SPACE_DIMENSION_ORDER if spatial else _DIMENSION_ORDER,
        )
        for dimension in unresolved_dimensions:
            unresolved_count += 1
            if len(unresolved) < MAX_UNIT_SCALE_WARNINGS:
                unresolved.append((metadata["global_id"], dimension))
        storey_global_id, storey_name = storey_by_element.get(metadata["global_id"], (None, None))
        decomposes_into, part_of = _decomposition(item)
        measurability = _measurability(item, decomposes_into)
        if decisions is not None:
            decided, decided_unresolved, matched = _decided(item, model, unit_util, decisions)
            matched_rulings.update(matched)
            if decided:
                # A ruling adds dimensions the tables never claimed and overrides none they
                # did: the caller's authority is over names the engine declined, not over
                # the ones buildingSMART already settled.
                taken = {quantity.dimension for quantity in quantities}
                added = tuple(quantity for quantity in decided if quantity.dimension not in taken)
                if added:
                    quantities = tuple(quantities) + added
                    if source == "fallback":
                        source = "caller_decision"
                    unresolved_dimensions = tuple(unresolved_dimensions) + decided_unresolved
            if any(
                ruling.billable
                and item.is_a(ruling.ifc_class)
                and (
                    ruling.object_type_contains is None
                    or ruling.object_type_contains in (_plain_scalar(getattr(item, "ObjectType", None)) or "")
                )
                for ruling in decisions.element_groups
            ):
                measurability = "structural" if measurability == "ambiguous" else measurability
        if source == "fallback" and measurability == "countable":
            # Its existence is the quantity. Marked `existence` rather than folded into
            # `base_quantity`: this number was counted, not measured, and a derived number
            # must not travel indistinguishable from one the exporter wrote.
            source = "existence"
            quantities = (ElementQuantity(dimension="count", value=1.0, normalized_value=1.0),)
        properties = _normalize(_properties(item), counter) if projection == "rich" else {}
        for _ in range(7 + 2 * len(quantities)):
            counter.node()
        return ElementIndexRecord(
            **metadata,
            storey_global_id=storey_global_id,
            storey_name=storey_name,
            material=_material(item) or None,
            classification=_classification(item) or None,
            measurability=measurability,
            part_of_global_id=part_of,
            decomposes_into=decomposes_into,
            quantity_source=source,
            quantity_set_name=set_name,
            quantities=quantities,
            properties=properties,
        )

    def finalize() -> dict[str, Any]:
        unmatched = [
            (ruling.measure, ruling.name)
            for ruling in (decisions.quantity_names if decisions is not None else ())
            if ruling.dimension is not None and (ruling.measure, ruling.name) not in matched_rulings
        ]
        return {
            "unresolved_unit_scale_count": unresolved_count,
            "diagnostics": [
                warning(
                    DiagnosticCode.UNMATCHED_QUANTITY_DECISION,
                    "element-index",
                    f"No {measure} quantity named {name!r} exists in this model.",
                    "Drop the ruling or re-collect quantity evidence for this model.",
                )
                for measure, name in unmatched
            ] + [
                warning(
                    DiagnosticCode.UNRESOLVED_UNIT_SCALE,
                    "element-index",
                    f"The selected {dimension} quantity has no derivable SI value.",
                    "Declare the project unit for this measure, or read value with its unit instead of normalized_value.",
                    global_id=global_id,
                )
                for global_id, dimension in unresolved
            ],
        }

    return _extract_pipeline(
        path,
        builder,
        ElementIndexResult,
        CONTRACT_VERSION,
        MAX_TOTAL_NODES_V2,
        projection=projection,
        output_dir=output_dir,
        inline_byte_cap=None if output_dir is not None else MAX_INLINE_RESULT_BYTES,
        prepare=prepare,
        finalize=finalize,
    )
