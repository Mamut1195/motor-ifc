"""`quality-score.v1`: the score judges, it never refuses.

The two properties this suite exists to defend (ADR 0011):

1. A model with 100% base quantities and missing materials PASSES, however many points
   those missing materials cost it. A score floor would refuse it.
2. A model that cannot be measured is REFUSED even with a high score, by an
   error-severity code and never by the number.

Most of this file needs no IFC runtime: the contract consumes materialized facts.
"""
import json
import os
import pathlib

import pytest

import motor_ifc.element_index as index_module
from motor_ifc import derive_quality_verdict, index_ifc_elements, score_ifc_quality
from motor_ifc.models import ElementIndexResult, QualityFacts
from motor_ifc.quality_score import (
    MODEL_NOT_MEASURABLE,
    QUALITY_SCORE_THRESHOLD,
    REFUSING_VERDICTS,
    UNMEASURED_REFUSAL_SHARE,
)
from motor_ifc.rpc import handle_line

ROOT = pathlib.Path(__file__).parents[1]
MODELS = ROOT / "corpus" / "models"
SCHEPENDOMLAAN = ROOT / "benchmarks" / "results" / "2026-08-05" / "cache" / "real-schependomlaan-49m-repaired.ifc"


# --- The score is not the refusal instrument ---------------------------------------


def test_the_only_refusing_verdicts_are_unreachable_from_the_score():
    assert set(REFUSING_VERDICTS) == {"blocked", "not_audited"}
    # `blocked` comes from error codes and `not_audited` from never having run. The
    # threshold decides `ok` vs `degraded`, and neither of those refuses.
    assert "degraded" not in REFUSING_VERDICTS
    assert "ok" not in REFUSING_VERDICTS


def test_a_zero_score_without_error_codes_still_generates():
    """The hardest form of property 1: every element flawed, nothing unmeasurable."""
    result = derive_quality_verdict(
        QualityFacts(total_elements=40, elements_with_issues=40, warning_codes=("NO_MATERIAL", "NO_STOREY"), total_issues=80)
    )
    assert result.score == 0.0
    assert result.verdict == "degraded"
    assert result.refuses_generation is False


def test_a_high_score_with_an_unmeasurable_model_is_refused():
    """Property 2: the code refuses, not the number."""
    result = derive_quality_verdict(
        QualityFacts(
            total_elements=100,
            elements_with_issues=1,
            error_codes=(MODEL_NOT_MEASURABLE,),
            model_level_error_messages=("Nothing can be measured.",),
            total_issues=1,
        )
    )
    assert result.score == 99.0
    assert result.score > QUALITY_SCORE_THRESHOLD
    assert result.verdict == "blocked"
    assert result.refuses_generation is True
    assert result.error_messages == ("Nothing can be measured.",)


def test_the_threshold_separates_ok_from_degraded_and_nothing_else():
    below = derive_quality_verdict(QualityFacts(total_elements=1000, elements_with_issues=301))
    at = derive_quality_verdict(QualityFacts(total_elements=1000, elements_with_issues=300))
    assert (below.score, below.verdict, below.refuses_generation) == (69.9, "degraded", False)
    assert (at.score, at.verdict, at.refuses_generation) == (70.0, "ok", False)
    assert at.threshold == QUALITY_SCORE_THRESHOLD == 70.0


def test_score_and_threshold_are_separate_fields_from_the_refusal():
    result = derive_quality_verdict(QualityFacts(total_elements=10, elements_with_issues=10))
    fields = set(result.model_dump(mode="json"))
    assert {"score", "threshold", "refuses_generation", "verdict"} <= fields
    assert result.refuses_generation is (result.verdict in REFUSING_VERDICTS)


# --- Verdict states ----------------------------------------------------------------


def test_a_synthetic_model_is_not_applicable_rather_than_unaudited():
    result = derive_quality_verdict(QualityFacts(is_synthetic=True))
    # A distinct fifth state: a synthetic model never had an audit step to skip, so
    # calling it NOT_AUDITED would report a failure that did not happen.
    assert (result.verdict, result.score, result.refuses_generation) == ("not_applicable", None, False)


def test_an_unaudited_model_refuses_without_a_score():
    result = derive_quality_verdict(QualityFacts(audited=False))
    assert (result.verdict, result.score, result.refuses_generation) == ("not_audited", None, True)


def test_an_empty_model_does_not_divide_its_way_to_a_perfect_score():
    # `total or 1` alone would score an empty model 100.0. What stops it is the
    # error-severity code the index-driven path adds, not the arithmetic.
    result = derive_quality_verdict(QualityFacts(total_elements=0, error_codes=(MODEL_NOT_MEASURABLE,)))
    assert result.score == 100.0
    assert result.verdict == "blocked"
    assert result.refuses_generation is True


def test_facts_reject_unknown_fields():
    with pytest.raises(Exception):
        QualityFacts.model_validate({"total_elements": 1, "unexpected": True})


# --- One pass over an index --------------------------------------------------------


@pytest.mark.ifcopenshell
def test_scoring_an_index_and_re_deriving_from_its_scalars_agree():
    index = index_ifc_elements(MODELS / "b01-pcert-ifc4.ifc")
    scored = score_ifc_quality(index)
    assert scored.success is True

    replayed = derive_quality_verdict(
        QualityFacts(
            total_elements=sum(1 for entity in index.entities if entity.measurability != "spatial"),
            elements_with_issues=len(
                {issue.global_id for issue in scored.issues if issue.global_id and issue.severity in ("error", "warning")}
            ),
            error_codes=scored.error_codes,
            model_level_error_messages=scored.error_messages,
            warning_codes=scored.warning_codes,
            total_issues=scored.total_issues,
        )
    )
    # Same numbers, same verdict: the one-pass path and the persisted-rows path cannot
    # disagree about the same model.
    assert (replayed.verdict, replayed.score) == (scored.verdict, scored.score)


@pytest.mark.ifcopenshell
def test_a_failed_index_is_not_scored_as_a_perfect_model():
    result = score_ifc_quality(ElementIndexResult(success=False))
    assert result.success is False
    assert result.verdict is None
    assert result.score is None
    assert result.diagnostics[0].code == 3100


@pytest.mark.ifcopenshell
def test_unmeasurable_share_is_a_model_level_error_not_a_severity_flip():
    index = index_ifc_elements(MODELS / "b02-community-ifc2x3.ifc")
    scored = score_ifc_quality(index)
    unmeasured = [issue for issue in scored.issues if issue.code == "NO_QUANTITIES"]
    model_level = [issue for issue in scored.issues if issue.code == MODEL_NOT_MEASURABLE]
    assert len(unmeasured) / index.entity_count >= UNMEASURED_REFUSAL_SHARE
    # The per-element rows stay warnings; the model-wide statement is its own error row
    # with no global_id, so the score is untouched by the escalation.
    assert {issue.severity for issue in unmeasured} == {"warning"}
    assert [issue.severity for issue in model_level] == ["error"]
    assert model_level[0].global_id is None
    assert scored.refuses_generation is True


# --- Calibration, and what the widened scope did to it -----------------------------
#
# The 2026-07-29 table was measured over the reference application's 20-class scope. This
# engine indexes the buildingSMART-aligned scope instead — every entity with an official
# `Qto_` template — so two of the three reproduced rows now score a different number over a
# different population. That is the scope being stated, not a regression: a score is only
# comparable within a fixed scope, and ADR 0011 records both values with the reason.
#
# What did NOT change is the only thing the threshold does: every verdict is identical.


@pytest.mark.ifcopenshell
@pytest.mark.parametrize(
    "model,score,verdict",
    [
        ("b01-pcert-ifc4", 35.7, "degraded"),
        ("b02-community-ifc2x3", 0.0, "blocked"),
    ],
)
def test_scores_are_pinned_under_the_buildingsmart_scope(model, score, verdict):
    scored = score_ifc_quality(index_ifc_elements(MODELS / f"{model}.ifc"))
    assert (scored.score, scored.verdict) == (score, verdict)


@pytest.mark.ifcopenshell
def test_calibration_pcert_stays_degraded_and_still_generates():
    """Was 38.5 over 13 rows; 35.7 over 14, because the scope found a chimney.

    The verdict is what the threshold decides, and it is unchanged: below 70, degraded,
    and degraded generates.
    """
    index = index_ifc_elements(MODELS / "b01-pcert-ifc4.ifc")
    scored = score_ifc_quality(index)
    assert scored.score == 35.7
    assert scored.score < QUALITY_SCORE_THRESHOLD
    assert scored.verdict == "degraded"
    assert scored.refuses_generation is False
    # The extra row is the chimney the 20-class scope never looked at.
    assert "IfcChimney" in index.element_types


@pytest.mark.ifcopenshell
def test_calibration_duplex_scores_zero_because_nothing_is_measurable():
    index = index_ifc_elements(MODELS / "b02-community-ifc2x3.ifc")
    scored = score_ifc_quality(index)
    # Not one element carries a quantity the exporter declared. The 38 doors and windows
    # report `existence`, which is a count this engine derived, not a measurement.
    elements = [entity for entity in index.entities if entity.measurability != "spatial"]
    assert all(entity.quantity_source in ("fallback", "existence") for entity in elements)
    assert all(entity.quantity_source != "existence" for entity in elements if entity.measurability != "countable")
    assert scored.score == 0.0
    assert MODEL_NOT_MEASURABLE in scored.error_codes
    assert scored.refuses_generation is True


@pytest.mark.ifcopenshell
def test_calibration_schependomlaan_passes_and_is_fully_covered(tmp_path):
    """The row the whole ADR is about.

    Published as 83.4 over 3 331 rows; 77.1 over 3 621, because the scope brought in 277
    `IfcBuildingElementPart` layers and 13 other rows the narrower one ignored. Both sit
    above the threshold, both say OK, and the property the ADR defends is untouched: a
    model whose elements are 100% measurable and whose materials are missing PASSES. A
    score floor anywhere above either number would refuse it.
    """
    if not SCHEPENDOMLAAN.is_file():
        pytest.skip("large real fixture not present (benchmarks/results is not distributed)")
    index = _published_index(SCHEPENDOMLAAN, tmp_path)
    scored = score_ifc_quality(index)

    assert index.entity_count == 3721  # 3 621 elements + 100 rooms
    assert scored.score == 77.1
    assert scored.score > QUALITY_SCORE_THRESHOLD
    assert scored.verdict == "ok"
    assert scored.refuses_generation is False
    assert scored.error_codes == ()
    assert set(scored.warning_codes) >= {"NO_MATERIAL", "NO_STOREY"}
    # Every billable element measures, directly or through the whole it belongs to.
    assert scored.coverage.coverage_percent == 100.0


# --- Coverage: the number to act on ------------------------------------------------
#
# `score` mixes every element-level defect together and stays as calibrated. Coverage
# asks only whether an element could be measured, over a denominator that holds only
# what could have been. The gap between the two columns is the whole point of the change.


def _published_index(model_path, tmp_path):
    target = tmp_path / "idx"
    published = index_ifc_elements(model_path, "index", target)
    assert published.success is True, published.diagnostics
    return ElementIndexResult.model_validate_json((target / "extraction.json").read_text(encoding="utf-8"))


@pytest.mark.ifcopenshell
def test_pcert_coverage_holds_only_work_and_names_the_one_real_gap():
    """`Group#18`/`Group#19` leave the denominator; the chimney stays in it.

    The old raw share read 54% of rows measured and told nobody which of them mattered.
    Here the two grouping nodes are excluded, the roof is covered by its slabs, the three
    proxies are reported beside the headline, and what remains is one chimney that genuinely
    cannot be measured.
    """
    index = index_ifc_elements(MODELS / "b01-pcert-ifc4.ifc")
    coverage = score_ifc_quality(index).coverage

    assert (coverage.billable_covered, coverage.billable_total) == (8, 9)
    assert coverage.coverage_percent == 88.9
    # Seven walls and slabs measure; the eighth structural row is the chimney, which
    # carries nothing. The roof container is covered by the two slabs it decomposes into.
    assert (coverage.structural.covered, coverage.structural.total) == (7, 8)
    assert (coverage.container.covered, coverage.container.total) == (1, 1)
    assert coverage.excluded_non_geometric == 2
    # Reported beside the headline, never inside it.
    assert (coverage.ambiguous.covered, coverage.ambiguous.total) == (0, 3)
    assert (coverage.spatial.covered, coverage.spatial.total) == (0, 2)
    assert [(group.ifc_class, group.measurability, group.count) for group in coverage.uncovered_by_class] == [
        ("IfcBuildingElementProxy", "ambiguous", 3),
        ("IfcSpace", "spatial", 2),
        ("IfcChimney", "structural", 1),
    ]


@pytest.mark.ifcopenshell
def test_duplex_coverage_is_low_and_says_exactly_what_is_missing():
    index = index_ifc_elements(MODELS / "b02-community-ifc2x3.ifc")
    coverage = score_ifc_quality(index).coverage

    assert coverage.coverage_percent == 24.2
    assert (coverage.structural.covered, coverage.structural.total) == (0, 116)
    # Its 21 rooms carry a floor area even though not one element does.
    assert (coverage.spatial.covered, coverage.spatial.total) == (21, 21)
    # Its 38 doors and windows are billable by count even with nothing declared.
    assert (coverage.countable.covered, coverage.countable.total) == (38, 38)
    biggest = coverage.uncovered_by_class[0]
    assert (biggest.ifc_class, biggest.measurability, biggest.count) == ("IfcWallStandardCase", "structural", 56)


@pytest.mark.ifcopenshell
def test_gymzaal_curtain_walls_are_covered_by_the_members_they_decompose_into(tmp_path):
    index = _published_index(MODELS / "b03-community-ifc4.ifc", tmp_path)
    coverage = score_ifc_quality(index).coverage

    assert coverage.coverage_percent == 99.8
    assert (coverage.container.covered, coverage.container.total) == (28, 28)
    assert (coverage.countable.covered, coverage.countable.total) == (112, 112)
    # One roof genuinely carries nothing and is the only structural gap in the model.
    assert (coverage.structural.covered, coverage.structural.total) == (337, 338)
    assert ("IfcRoof", "structural", 1) in [
        (group.ifc_class, group.measurability, group.count) for group in coverage.uncovered_by_class
    ]


@pytest.mark.ifcopenshell
@pytest.mark.parametrize("fixture", ["real-cand-11m-repaired.ifc", "real-schependomlaan-49m-repaired.ifc"])
def test_real_project_models_are_fully_covered(fixture, tmp_path):
    path = SCHEPENDOMLAAN.parent / fixture
    if not path.is_file():
        pytest.skip("large real fixture not present (benchmarks/results is not distributed)")
    coverage = score_ifc_quality(_published_index(path, tmp_path)).coverage
    assert coverage.coverage_percent == 100.0
    assert coverage.structural.percent == 100.0
    assert coverage.uncovered_by_class == ()
    # Every room measures too, in its own bucket.
    assert coverage.spatial.percent == 100.0


@pytest.mark.ifcopenshell
def test_the_new_denominator_refuses_exactly_the_same_models_as_the_old_one():
    """The constant did not move; only the denominator got sharper.

    Duplex was the only model the raw-share rule refused, and it is the only one the
    coverage rule refuses. Anything else would be a silently changed gate.
    """
    refused = {}
    for name in ("b01-pcert-ifc4", "b02-community-ifc2x3", "b03-community-ifc4"):
        index = index_ifc_elements(MODELS / f"{name}.ifc")
        if index.publication != "none" or not index.success:
            import tempfile

            with tempfile.TemporaryDirectory() as staging:
                index = _published_index(MODELS / f"{name}.ifc", pathlib.Path(staging))
        scored = score_ifc_quality(index)
        refused[name] = MODEL_NOT_MEASURABLE in scored.error_codes
    assert refused == {
        "b01-pcert-ifc4": False,
        "b02-community-ifc2x3": True,
        "b03-community-ifc4": False,
    }
    assert UNMEASURED_REFUSAL_SHARE == 0.5


@pytest.mark.ifcopenshell
def test_the_widened_scope_moved_two_scores_and_no_verdict():
    """What the threshold decides is what has to survive a scope change.

    Widening the index to every entity buildingSMART gives quantities to changed the
    population two of these models are scored over. Every verdict is the same, so the
    threshold still classifies the same models the same way.
    """
    verdicts = {}
    for name in ("b01-pcert-ifc4", "b02-community-ifc2x3", "b03-community-ifc4"):
        index = index_ifc_elements(MODELS / f"{name}.ifc")
        if index.publication != "none" or not index.success:
            import tempfile

            with tempfile.TemporaryDirectory() as staging:
                index = _published_index(MODELS / f"{name}.ifc", pathlib.Path(staging))
        verdicts[name] = score_ifc_quality(index).verdict
    assert verdicts == {
        "b01-pcert-ifc4": "degraded",
        "b02-community-ifc2x3": "blocked",
        "b03-community-ifc4": "degraded",
    }


# --- Rooms: their own family, their own dimensions ---------------------------------


@pytest.mark.ifcopenshell
def test_a_room_reports_the_six_dimensions_it_actually_measures(tmp_path):
    """`Qto_SpaceBaseQuantities` defines six areas, not one.

    Schependomlaan carries the whole vocabulary on all 100 of its rooms, which is what a
    finishes takeoff reads: floor for flooring, wall for paint, ceiling for ceilings, and
    the perimeter the specification names for skirting boards.
    """
    if not SCHEPENDOMLAAN.is_file():
        pytest.skip("large real fixture not present (benchmarks/results is not distributed)")
    index = _published_index(SCHEPENDOMLAAN, tmp_path)
    rooms = [entity for entity in index.entities if entity.measurability == "spatial"]
    assert len(rooms) == 100
    assert {dimension for room in rooms for dimension in (quantity.dimension for quantity in room.quantities)} == {
        "floor_area",
        "wall_area",
        "ceiling_area",
        "perimeter",
        "height",
        "volume",
    }


@pytest.mark.ifcopenshell
def test_boma_areas_are_never_taken_as_floor_area(tmp_path):
    """BOMA measures rentable area, not work.

    Its rules add and deduct floor area by leasing criteria — cores, shared circulation,
    pro-rated commons — that correspond to no metre anyone builds. Schependomlaan carries
    `SpaceNetFloorAreaBOMA` and `SpaceUsableFloorAreaBOMA` beside the real ones, and the
    engine must reach past them every time.
    """
    if not SCHEPENDOMLAAN.is_file():
        pytest.skip("large real fixture not present (benchmarks/results is not distributed)")
    index = _published_index(SCHEPENDOMLAAN, tmp_path)
    rooms = [entity for entity in index.entities if entity.measurability == "spatial"]
    taken = {quantity.source_quantity_name for room in rooms for quantity in room.quantities}
    assert taken & set(index_module._SPACE_REJECTED_NAMES) == set()
    assert "NetFloorArea" in taken


@pytest.mark.ifcopenshell
def test_a_vendor_room_area_is_read_where_the_schema_defines_no_template():
    """IFC2X3 defines no `Qto_` template at all, so the dialect is all there is.

    Duplex carries its 21 room areas only as `GSA BIM Area` — a floor area from the GSA
    BIM Guide, and the difference between 21 measured rooms and 21 blanks.
    """
    index = index_ifc_elements(MODELS / "b02-community-ifc2x3.ifc")
    rooms = [entity for entity in index.entities if entity.measurability == "spatial"]
    assert len(rooms) == 21
    assert {room.quantity_source for room in rooms} == {"vendor_quantity"}
    assert {quantity.source_quantity_name for room in rooms for quantity in room.quantities} == {"GSA BIM Area"}
    assert {quantity.dimension for room in rooms for quantity in room.quantities} == {"floor_area"}


@pytest.mark.ifcopenshell
def test_room_and_element_dimensions_never_mix():
    """A wall's side area and a room's floor area are both areas and bill differently."""
    index = index_ifc_elements(MODELS / "b03-community-ifc4.ifc", "index", None)
    if not index.success:
        import tempfile

        with tempfile.TemporaryDirectory() as staging:
            index = _published_index(MODELS / "b03-community-ifc4.ifc", pathlib.Path(staging))
    room_only = {"floor_area", "wall_area", "ceiling_area", "perimeter", "height"}
    for entity in index.entities:
        dimensions = {quantity.dimension for quantity in entity.quantities}
        if entity.measurability == "spatial":
            assert dimensions & {"area", "length", "count", "weight"} == set()
        else:
            assert dimensions & room_only == set()


@pytest.mark.ifcopenshell
def test_rooms_stay_out_of_the_scored_population_and_the_headline():
    index = index_ifc_elements(MODELS / "b01-pcert-ifc4.ifc")
    scored = score_ifc_quality(index)
    rooms = {entity.global_id for entity in index.entities if entity.measurability == "spatial"}
    assert len(rooms) == 2
    # No element-level issue accuses a room of having no material.
    assert {issue.global_id for issue in scored.issues} & rooms == set()
    # And the headline denominator counts elements only.
    assert scored.coverage.billable_total == 9
    assert scored.coverage.spatial.total == 2


@pytest.mark.ifcopenshell
def test_an_empty_bucket_reports_null_rather_than_a_hundred_percent():
    index = index_ifc_elements(MODELS / "b01-pcert-ifc4.ifc")
    coverage = score_ifc_quality(index).coverage
    # PCERT has no doors or windows. A percentage of nothing is neither 100 nor 0.
    assert coverage.countable.total == 0
    assert coverage.countable.percent is None


def test_derive_verdict_alone_carries_no_coverage():
    # Coverage needs elements; the scalar path never saw them and must not invent a number.
    assert derive_quality_verdict(QualityFacts(total_elements=10, elements_with_issues=1)).coverage is None


# --- Transport ---------------------------------------------------------------------


def test_sidecar_derives_a_verdict_from_facts_without_a_job_root():
    response = json.loads(
        handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "quality.score.v1",
                    "params": {"facts": {"total_elements": 10, "elements_with_issues": 10}},
                }
            )
        )
    )
    result = response["result"]
    assert result["contract_version"] == "quality-score.v1"
    assert (result["verdict"], result["score"], result["refuses_generation"]) == ("degraded", 0.0, False)
    assert result["publication"] == "none"


def test_sidecar_rejects_unknown_fact_fields():
    response = json.loads(
        handle_line(
            json.dumps(
                {"jsonrpc": "2.0", "id": 2, "method": "quality.score.v1", "params": {"facts": {"nope": 1}}}
            )
        )
    )
    assert response["error"]["code"] == -32602
