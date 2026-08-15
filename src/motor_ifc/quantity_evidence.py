"""The question this engine cannot answer, put in a form a caller's model can.

The built-in tables select quantities by name, conservatively, and everything they do not
recognise is dropped. On real files that is most of the numbers: Schependomlaan declares
153 815 quantities and 128 310 of them sit under 536 names no table claims. Most is rightly
discarded — `Home Offset`, `Elevation to Project Zero` — but `Net Surface Area on the
Outside Face` appears 1 419 times and is paint area. Separating those two lists cannot be
tabulated in advance: they are exporter dialects, in several languages, different per
project.

So this contract does not decide. It reports what was dropped and what would be gained by
claiming it, and lets the caller's model rule on it — the same division `ids-validation.v1`
already draws, where the caller owns the requirements and the engine owns the checking.
Nothing here calls a model, opens a socket, or adds a dependency.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .element_index import (
    _CANONICAL_NAMES,
    _DIMENSION_BY_CLASS,
    _DIMENSION_ORDER,
    _SPACE_CANONICAL_NAMES,
    _SPACE_DIMENSION_ORDER,
    _SPACE_VENDOR_NAMES,
    _VENDOR_NAMES,
    _classification,
    _decomposition,
    _flatten,
    _material,
    _measurability,
    _select,
    BUILDING_ELEMENT_TYPES,
    SPATIAL_TYPES,
)
from .models import ElementGroupEvidence, QuantityEvidenceResult, ReaderUnit, VocabularyEntry
from .reader_extraction import (
    _BoundExceeded,
    _Counter,
    _extract_pipeline,
    _normalize,
    _plain_scalar,
    _QUANTITY_MEASURES,
    _resolve_quantity_unit,
    _unit_util,
    MAX_INLINE_RESULT_BYTES,
    MAX_SETS_PER_ENTITY,
    MAX_TOTAL_NODES_V2,
)

CONTRACT_VERSION = "quantity-evidence.v1"

#: Names reported inline, ordered by how many elements they would reach. The tail is
#: declared as a count, never silently dropped: a reader that mistakes a truncated list for
#: the population rules on the wrong half. 200 keeps the worst measured model (536 distinct
#: names) near 25k tokens, which is a payload a caller can actually afford to send.
MAX_VOCABULARY = 200
MAX_GROUPS = 200
MAX_GLOBAL_ID_SAMPLES = 10
MAX_OBJECT_TYPE_SAMPLES = 5
MAX_CLASS_SAMPLES = 5
MAX_SET_SAMPLES = 5


def _measure_by_dimension() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for table in (_CANONICAL_NAMES, _VENDOR_NAMES, _SPACE_CANONICAL_NAMES, _SPACE_VENDOR_NAMES):
        for dimension, (measure, _names) in table.items():
            mapping[dimension] = measure
    return mapping


_MEASURE_BY_DIMENSION = _measure_by_dimension()


def _candidates(item: Any) -> list[tuple[str, str, str, Any]]:
    """Every quantity the element declares, as ``(set name, measure, name, quantity)``.

    Unfiltered by the selection tables on purpose: what they reject is exactly what a caller
    is being asked about.
    """
    found: list[tuple[str, str, str, Any]] = []
    relations = getattr(item, "IsDefinedBy", None) or ()
    if len(relations) > MAX_SETS_PER_ENTITY:
        raise _BoundExceeded
    for relation in relations:
        if not relation.is_a("IfcRelDefinesByProperties"):
            continue
        quantity_set = getattr(relation, "RelatingPropertyDefinition", None)
        if quantity_set is None or not quantity_set.is_a("IfcElementQuantity"):
            continue
        set_name = _plain_scalar(getattr(quantity_set, "Name", None)) or ""
        for quantity in _flatten(getattr(quantity_set, "Quantities", None)):
            measure = _DIMENSION_BY_CLASS.get(quantity.is_a())
            if measure is None:
                continue
            attribute, _measure_type = _QUANTITY_MEASURES[quantity.is_a()]
            if getattr(quantity, attribute, None) is None:
                continue
            name = _plain_scalar(getattr(quantity, "Name", None))
            if isinstance(name, str) and name:
                found.append((set_name, measure, name, quantity))
    return found


def collect(path: str | Path) -> QuantityEvidenceResult:
    """Report the quantity names and element groups a caller has to rule on."""
    vocabulary: dict[tuple[str, str], dict[str, Any]] = {}
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}

    def builder(model: Any, element_util: Any, item: Any, counter: _Counter) -> None:
        unit_util = _unit_util()
        spatial = item.is_a("IfcSpace")
        source, _set_name, selected, _unresolved = _select(
            item,
            model,
            unit_util,
            _SPACE_CANONICAL_NAMES if spatial else _CANONICAL_NAMES,
            _SPACE_VENDOR_NAMES if spatial else _VENDOR_NAMES,
            _SPACE_DIMENSION_ORDER if spatial else _DIMENSION_ORDER,
        )
        taken = {
            _MEASURE_BY_DIMENSION.get(quantity.dimension, quantity.dimension): quantity.source_quantity_name
            for quantity in selected
        }
        global_id = _plain_scalar(getattr(item, "GlobalId", None))
        ifc_class = item.is_a()

        candidates: list[str] = []
        for set_name, measure, name, quantity in _candidates(item):
            if taken.get(measure) == name:
                continue
            candidates.append(name)
            key = (measure, name)
            entry = vocabulary.get(key)
            if entry is None:
                attribute, measure_type = _QUANTITY_MEASURES[quantity.is_a()]
                value = float(getattr(quantity, attribute))
                unit, normalized = _resolve_quantity_unit(quantity, value, measure_type, model, unit_util)
                entry = vocabulary[key] = {
                    "occurrences": 0,
                    "elements": set(),
                    "sets": Counter(),
                    "classes": Counter(),
                    "competitors": Counter(),
                    "value": value,
                    "unit": unit,
                    "normalized": normalized,
                }
            entry["occurrences"] += 1
            entry["elements"].add(global_id)
            entry["sets"][set_name] += 1
            entry["classes"][ifc_class] += 1
            competitor = taken.get(measure)
            if competitor:
                entry["competitors"][competitor] += 1

        measurability = _measurability(item, _decomposition(item)[0])
        # Only what a ruling could change. A container is measured through its parts and a
        # countable element through its own existence, so neither is undecided — listing
        # them would bury the thirteen proxies that are.
        undecided = measurability == "ambiguous" or (source == "fallback" and measurability in ("structural", "spatial"))
        if not undecided:
            return None
        material = _material(item) or ""
        group_key = (ifc_class, measurability, material)
        group = groups.get(group_key)
        if group is None:
            type_object = element_util.get_type(item)
            group = groups[group_key] = {
                "elements": 0,
                "object_types": [],
                "type_name": _plain_scalar(getattr(type_object, "Name", None)) if type_object is not None else None,
                "property_sets": _normalize(element_util.get_psets(item, psets_only=True) or {}, counter),
                "candidates": set(),
                "global_ids": [],
                "classification": _classification(item),
            }
        group["elements"] += 1
        group["candidates"].update(candidates)
        if len(group["global_ids"]) < MAX_GLOBAL_ID_SAMPLES:
            group["global_ids"].append(global_id)
        object_type = _plain_scalar(getattr(item, "ObjectType", None))
        if object_type and object_type not in group["object_types"] and len(group["object_types"]) < MAX_OBJECT_TYPE_SAMPLES:
            group["object_types"].append(object_type)
        return None

    def finalize() -> dict[str, Any]:
        ranked = sorted(
            vocabulary.items(),
            key=lambda item: (-len(item[1]["elements"]), -item[1]["occurrences"], item[0][0], item[0][1]),
        )
        names = tuple(
            VocabularyEntry(
                measure=measure,
                name=name,
                occurrences=data["occurrences"],
                elements_affected=len(data["elements"]),
                quantity_sets=tuple(name for name, _ in data["sets"].most_common(MAX_SET_SAMPLES)),
                on_classes=tuple(name for name, _ in data["classes"].most_common(MAX_CLASS_SAMPLES)),
                sample_value=data["value"],
                sample_unit=ReaderUnit(**data["unit"]) if data["unit"] else None,
                sample_normalized=data["normalized"],
                competes_with=data["competitors"].most_common(1)[0][0] if data["competitors"] else None,
            )
            for (measure, name), data in ranked[:MAX_VOCABULARY]
        )

        undecided = sum(data["elements"] for data in groups.values()) or 1
        ordered = sorted(
            groups.items(),
            key=lambda item: (
                not (item[1]["candidates"] or item[0][1] == "ambiguous"),
                -item[1]["elements"],
                item[0][0],
                item[0][2],
            ),
        )
        running = 0
        rows = []
        for (ifc_class, measurability, material), data in ordered[:MAX_GROUPS]:
            running += data["elements"]
            rows.append(
                ElementGroupEvidence(
                    ifc_class=ifc_class,
                    measurability=measurability,
                    material=material or None,
                    elements=data["elements"],
                    worth_deciding=bool(data["candidates"]) or measurability == "ambiguous",
                    object_type_samples=tuple(data["object_types"]),
                    type_name=data["type_name"],
                    property_sets=data["property_sets"],
                    candidates=tuple(sorted(data["candidates"])),
                    global_id_samples=tuple(data["global_ids"]),
                    cumulative_percent=round(running / undecided * 100, 1),
                )
            )
        return {
            "vocabulary": names,
            "truncated_names": max(len(ranked) - MAX_VOCABULARY, 0),
            "element_groups": tuple(rows),
            "truncated_groups": max(len(ordered) - MAX_GROUPS, 0),
            "diagnostics": [],
        }

    def prepare(model: Any) -> tuple[list[Any], dict[str, Any]]:
        instances: dict[int, Any] = {}
        for group in BUILDING_ELEMENT_TYPES + SPATIAL_TYPES:
            for alias in group:
                try:
                    items = model.by_type(alias)
                except RuntimeError:
                    continue
                for item in items:
                    global_id = getattr(item, "GlobalId", None)
                    if isinstance(global_id, str) and global_id:
                        instances[item.id()] = item
                break
        ordered = sorted(instances.values(), key=lambda item: (item.GlobalId, item.is_a(), item.id()))
        seen: set[str] = set()
        scoped = []
        for item in ordered:
            if item.GlobalId in seen:
                continue
            seen.add(item.GlobalId)
            scoped.append(item)
        return scoped, {}

    return _extract_pipeline(
        path,
        builder,
        QuantityEvidenceResult,
        CONTRACT_VERSION,
        MAX_TOTAL_NODES_V2,
        inline_byte_cap=MAX_INLINE_RESULT_BYTES,
        prepare=prepare,
        finalize=finalize,
    )
