"""`quantity-evidence.v1` and `quantity-decisions.v1`: the engine asks, the caller rules.

The engine calls no model. What it does is put the question a table cannot answer into a
form a caller's model can, and then apply the answer as authority over *names* — never over
numbers.
"""
import json
import pathlib

import pytest

import motor_ifc.quantity_evidence as evidence_module
from motor_ifc import collect_quantity_evidence, index_ifc_elements
from motor_ifc.models import ElementIndexResult, QuantityDecisions
from motor_ifc.rpc import handle_line
from motor_ifc.schemas import write_schemas

ROOT = pathlib.Path(__file__).parents[1]
MODELS = ROOT / "corpus" / "models"
SCHEPENDOMLAAN = ROOT / "benchmarks" / "results" / "2026-08-05" / "cache" / "real-schependomlaan-49m-repaired.ifc"


def _published_index(model_path, tmp_path, decisions=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    target = tmp_path / "idx"
    published = index_ifc_elements(model_path, "index", target, decisions)
    assert published.success is True, published.diagnostics
    return published, ElementIndexResult.model_validate_json((target / "extraction.json").read_text(encoding="utf-8"))


# --- A decision can never carry a number -------------------------------------------


def test_no_field_of_a_decision_is_numeric(tmp_path):
    """The structural guarantee, checked on the schema a model would actually fill in.

    A caller's model rules on what a name *means*; the value always comes from the file. If
    the contract had one numeric property, an invented quantity would have somewhere to
    land and would then be indistinguishable from a measurement.
    """
    write_schemas(tmp_path)
    document = json.loads((tmp_path / "quantity-decisions-v1.schema.json").read_text(encoding="utf-8"))

    numeric = []

    def walk(node, trail):
        if isinstance(node, dict):
            declared = node.get("type")
            types = declared if isinstance(declared, list) else [declared]
            if {"number", "integer"} & set(filter(None, types)):
                numeric.append(trail)
            for key, value in node.items():
                walk(value, f"{trail}/{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{trail}/{index}")

    walk(document, "")
    assert numeric == []


def test_a_ruling_may_only_name_and_may_refuse():
    decisions = QuantityDecisions.model_validate(
        {
            "quantity_names": [
                {"measure": "area", "name": "Net Surface Area on the Outside Face", "dimension": "surface_area"},
                {"measure": "length", "name": "Home Offset", "dimension": None},
            ]
        }
    )
    # `dimension: null` is as load-bearing as a mapping: it records that the name was judged
    # rather than overlooked.
    assert decisions.quantity_names[1].dimension is None
    with pytest.raises(Exception):
        QuantityDecisions.model_validate({"quantity_names": [{"measure": "area", "name": "X", "value": 12.0}]})


# --- The question ------------------------------------------------------------------


@pytest.mark.ifcopenshell
def test_evidence_reports_what_the_tables_dropped_and_what_it_would_reach():
    result = collect_quantity_evidence(MODELS / "b03-community-ifc4.ifc")
    assert result.success is True
    assert result.publication == "none"
    assert result.entity_count == 539

    names = {entry.name: entry for entry in result.vocabulary}
    # Nothing measures area on those members and beams, so this name is the only route to
    # that dimension rather than a rival for it.
    assert names["CrossSectionArea"].competes_with is None
    assert names["CrossSectionArea"].elements_affected == 208
    assert "IfcMember" in names["CrossSectionArea"].on_classes
    # `Width` is a rival: a length is already being taken for these elements.
    assert names["Width"].competes_with == "Length"


@pytest.mark.ifcopenshell
def test_competes_with_names_the_quantity_actually_selected():
    result = collect_quantity_evidence(MODELS / "b01-pcert-ifc4.ifc")
    index = index_ifc_elements(MODELS / "b01-pcert-ifc4.ifc")
    selected = {
        quantity.source_quantity_name for entity in index.entities for quantity in entity.quantities
    }
    for entry in result.vocabulary:
        if entry.competes_with is not None:
            # A competitor is a name this engine really took, not a plausible-looking string.
            assert entry.competes_with in selected


@pytest.mark.ifcopenshell
def test_elements_affected_is_a_union_of_global_ids_not_a_sum():
    result = collect_quantity_evidence(MODELS / "b03-community-ifc4.ifc")
    for entry in result.vocabulary:
        # A name can appear more than once on the same element; counting occurrences as
        # reach is how overlapping rules overstated coverage by up to 864% downstream.
        assert entry.elements_affected <= entry.occurrences
    widths = next(entry for entry in result.vocabulary if entry.name == "Width")
    assert widths.elements_affected < widths.occurrences


@pytest.mark.ifcopenshell
def test_only_groups_a_ruling_could_change_are_reported():
    """Containers and countable elements are already measured; listing them buries the rest."""
    result = collect_quantity_evidence(MODELS / "b03-community-ifc4.ifc")
    reported = {(group.ifc_class, group.measurability) for group in result.element_groups}
    assert ("IfcBuildingElementProxy", "ambiguous") in reported
    assert ("IfcRoof", "structural") in reported
    assert not any(measurability in ("container", "countable") for _class, measurability in reported)

    proxies = next(group for group in result.element_groups if group.ifc_class == "IfcBuildingElementProxy")
    assert proxies.elements == 13
    assert proxies.worth_deciding is True
    # The signal a model reads to say "aluminium roof trim, billed per linear metre".
    assert any("daktrim aluminium" in sample for sample in proxies.object_type_samples)
    assert proxies.property_sets["Pset_BuildingElementProxyCommon"]["Reference"] == "41_ROF_daktrim aluminium"
    assert proxies.is_sample is True
    assert len(proxies.global_id_samples) <= evidence_module.MAX_GLOBAL_ID_SAMPLES


@pytest.mark.ifcopenshell
def test_groups_are_ordered_by_what_is_worth_deciding_and_carry_a_stop_signal():
    result = collect_quantity_evidence(MODELS / "b02-community-ifc2x3.ifc")
    worth = [group.worth_deciding for group in result.element_groups]
    assert worth == sorted(worth, reverse=True)
    percents = [group.cumulative_percent for group in result.element_groups]
    assert percents == sorted(percents)
    assert percents[-1] == pytest.approx(100.0, abs=0.2)


@pytest.mark.ifcopenshell
def test_a_truncated_vocabulary_declares_its_tail(monkeypatch):
    monkeypatch.setattr(evidence_module, "MAX_VOCABULARY", 3)
    result = collect_quantity_evidence(MODELS / "b03-community-ifc4.ifc")
    assert len(result.vocabulary) == 3
    # Never a silent cut: a reader that mistakes the list for the population rules on the
    # wrong half.
    assert result.truncated_names == 9


@pytest.mark.ifcopenshell
def test_a_model_the_tables_fully_understand_asks_nothing():
    """Nothing dropped means nothing to ask. Duplex's rooms are read through the GSA
    dialect the space table already accepts, and its elements declare no quantities at all.
    """
    result = collect_quantity_evidence(MODELS / "b02-community-ifc2x3.ifc")
    assert result.success is True
    assert result.vocabulary == ()
    assert result.truncated_names == 0


@pytest.mark.ifcopenshell
def test_evidence_is_reproducible():
    first = collect_quantity_evidence(MODELS / "b03-community-ifc4.ifc")
    second = collect_quantity_evidence(MODELS / "b03-community-ifc4.ifc")
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


# --- The answer, applied -----------------------------------------------------------


@pytest.mark.ifcopenshell
def test_a_ruling_recovers_a_dimension_and_says_who_decided_it(tmp_path):
    """The circuit that makes the whole contract worth having.

    `Net Surface Area on the Outside Face` is paint area on 1 419 elements and no table will
    ever claim it — it is one exporter's Dutch-English dialect. A ruling claims it, the
    number still comes from the file, and every record says a caller chose it.
    """
    if not SCHEPENDOMLAAN.is_file():
        pytest.skip("large real fixture not present (benchmarks/results is not distributed)")
    decisions = {
        "quantity_names": [
            {"measure": "area", "name": "Net Surface Area on the Outside Face", "dimension": "surface_area"}
        ],
        "decided_by": "model-via-app",
    }
    _published, index = _published_index(SCHEPENDOMLAAN, tmp_path, decisions)
    recovered = [
        entity for entity in index.entities if any(quantity.dimension == "surface_area" for quantity in entity.quantities)
    ]
    assert len(recovered) == 1419
    for entity in recovered:
        surface = next(quantity for quantity in entity.quantities if quantity.dimension == "surface_area")
        assert surface.decided_by == "model-via-app"
        # A ruled quantity is still the file's number, zeros included — the engine claims
        # the name, never the value.
        assert surface.value >= 0
    assert any(
        quantity.value > 0
        for entity in recovered
        for quantity in entity.quantities
        if quantity.dimension == "surface_area"
    )


@pytest.mark.ifcopenshell
def test_a_ruling_never_overrides_what_the_standard_already_settled(tmp_path):
    if not SCHEPENDOMLAAN.is_file():
        pytest.skip("large real fixture not present (benchmarks/results is not distributed)")
    plain = _published_index(SCHEPENDOMLAAN, tmp_path / "plain")[1]
    ruled = _published_index(
        SCHEPENDOMLAAN,
        tmp_path / "ruled",
        {"quantity_names": [{"measure": "area", "name": "Area of the Wall", "dimension": "area"}]},
    )[1]
    before = {entity.global_id: entity for entity in plain.entities}
    for entity in ruled.entities:
        original = before[entity.global_id]
        area = next((q for q in entity.quantities if q.dimension == "area"), None)
        was = next((q for q in original.quantities if q.dimension == "area"), None)
        if was is not None:
            # The caller's authority is over names the engine declined, never over the ones
            # buildingSMART already settled.
            assert area is not None and area.source_quantity_name == was.source_quantity_name
            assert area.decided_by is None


@pytest.mark.ifcopenshell
def test_a_ruling_that_matches_nothing_is_reported_not_swallowed():
    result = index_ifc_elements(
        MODELS / "b01-pcert-ifc4.ifc",
        "index",
        None,
        {"quantity_names": [{"measure": "area", "name": "No Such Quantity Anywhere", "dimension": "area"}]},
    )
    assert result.success is True
    # Silence here is a ruling the operator believes applied and is not.
    assert [(item.code, item.severity) for item in result.diagnostics] == [(3202, "warning")]


@pytest.mark.ifcopenshell
def test_ruling_a_proxy_billable_moves_it_out_of_the_ambiguous_bucket():
    plain = index_ifc_elements(MODELS / "b01-pcert-ifc4.ifc")
    ruled = index_ifc_elements(
        MODELS / "b01-pcert-ifc4.ifc",
        "index",
        None,
        {"element_groups": [{"ifc_class": "IfcBuildingElementProxy", "object_type_contains": "subgrade", "billable": True}]},
    )
    before = {entity.global_id: entity.measurability for entity in plain.entities}
    changed = [
        entity for entity in ruled.entities if before[entity.global_id] != entity.measurability
    ]
    assert [entity.name for entity in changed] == ["sand bedding"]
    assert changed[0].measurability == "structural"


@pytest.mark.ifcopenshell
def test_without_a_decisions_document_nothing_changes():
    plain = index_ifc_elements(MODELS / "b03-community-ifc4.ifc", "index", None)
    empty = index_ifc_elements(MODELS / "b03-community-ifc4.ifc", "index", None, {})
    assert plain.model_dump(mode="json") == empty.model_dump(mode="json")


# --- Transport ---------------------------------------------------------------------


@pytest.mark.ifcopenshell
def test_sidecar_serves_the_question_and_takes_the_answer(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTOR_IFC_JOB_ROOT", str(tmp_path))
    (tmp_path / "model.ifc").write_bytes((MODELS / "b03-community-ifc4.ifc").read_bytes())
    (tmp_path / "decisions.json").write_text(
        json.dumps({"quantity_names": [{"measure": "area", "name": "CrossSectionArea", "dimension": "surface_area"}]}),
        encoding="utf-8",
    )

    question = json.loads(
        handle_line(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "quantity.evidence.v1", "params": {"ifc_path": "model.ifc"}}))
    )
    assert question["result"]["contract_version"] == "quantity-evidence.v1"
    assert question["result"]["publication"] == "none"
    assert any(entry["name"] == "CrossSectionArea" for entry in question["result"]["vocabulary"])

    answered = json.loads(
        handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "element.index.v1",
                    "params": {"ifc_path": "model.ifc", "output_dir": "idx", "decisions_path": "decisions.json"},
                }
            )
        )
    )
    assert answered["result"]["publication"] == "immutable-directory"

    escape = json.loads(
        handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "element.index.v1",
                    "params": {"ifc_path": "model.ifc", "decisions_path": "../decisions.json"},
                }
            )
        )
    )
    assert escape["error"]["code"] == -32602


@pytest.mark.ifcopenshell
def test_sidecar_rejects_a_malformed_decisions_document(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTOR_IFC_JOB_ROOT", str(tmp_path))
    (tmp_path / "model.ifc").write_bytes((MODELS / "b01-pcert-ifc4.ifc").read_bytes())
    (tmp_path / "bad.json").write_text(json.dumps({"quantity_names": [{"measure": "area", "name": "X", "value": 9}]}), encoding="utf-8")
    response = json.loads(
        handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "element.index.v1",
                    "params": {"ifc_path": "model.ifc", "decisions_path": "bad.json"},
                }
            )
        )
    )
    assert response["error"]["code"] == -32602
    assert response["error"]["data"]["diagnostic_code"] == 3201
