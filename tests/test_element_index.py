"""`element-index.v1`: canonical quantity selection, unit provenance, spatial facts.

The known-answer cases run against `corpus/models`, whose bytes are pinned by SHA-256
in `corpus/MANIFEST.json`. Expected values come from the file and from
`ifcopenshell.util.unit`, never from another `motor_ifc` code path.
"""
import json
import pathlib

import pytest

import motor_ifc.element_index as index_module
import motor_ifc.reader_extraction as reader
from motor_ifc import index_ifc_elements
from motor_ifc.models import ElementIndexResult
from motor_ifc.rpc import handle_line

ROOT = pathlib.Path(__file__).parents[1]
MODELS = ROOT / "corpus" / "models"


def _ifc_runtime():
    return pytest.importorskip("ifcopenshell")


def _by_global_id(result, global_id):
    return next(entity for entity in result.entities if entity.global_id == global_id)


def _dimension(entity, dimension):
    return next(quantity for quantity in entity.quantities if quantity.dimension == dimension)


def _published_index(model_path):
    """Index through publication, for models that exceed the inline byte cap.

    The artifact is the canonical inline document, so it validates straight back into the
    DTO — that round trip is what lets a large model be scored without re-reading it.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as staging:
        target = pathlib.Path(staging) / "idx"
        published = index_ifc_elements(model_path, "index", target)
        assert published.success is True, published.diagnostics
        return ElementIndexResult.model_validate_json((target / "extraction.json").read_text(encoding="utf-8"))


# --- Pure selection rules (no runtime) --------------------------------------------


@pytest.mark.parametrize(
    "name,canonical",
    [
        ("BaseQuantities", True),
        ("basequantities", True),
        ("Qto_WallBaseQuantities", True),
        ("  Qto_SlabBaseQuantities  ", True),
        ("ExtraQuantities", False),
        ("Qto_WallSomethingElse", False),
        ("", False),
        (None, False),
    ],
)
def test_canonical_set_names_are_recognized_in_both_conventions(name, canonical):
    assert index_module._is_canonical_set(name) is canonical


def test_material_summary_drops_repeats_before_it_runs_out_of_room():
    names = ["Brick", "Brick", "Mortar", "Brick"]
    assert index_module._summarize_materials(names) == "Brick, Mortar"


def test_material_summary_takes_whole_names_only():
    long_name = "L" * 200
    other = "O" * 200
    # The second name does not fit, and half of it would name a material the model
    # does not contain.
    assert index_module._summarize_materials([long_name, other]) == long_name


def test_material_summary_bounds_a_single_oversized_name_rather_than_claiming_none():
    summary = index_module._summarize_materials(["X" * 500])
    assert len(summary) == index_module.MAX_MATERIAL_LENGTH
    assert summary


def _official_qto_entities(ifc):
    """Every entity buildingSMART defines base quantities for, from its own templates.

    `Pset_IFC4_ADD2.ifc` ships with IfcOpenShell and is buildingSMART's statement of which
    entities carry quantities. Reading it here turns the engine's scope from an assertion
    into something checkable against the standard.
    """
    import pathlib
    import re

    templates = pathlib.Path(ifc.__file__).parent / "util" / "schema" / "Pset_IFC4_ADD2.ifc"
    document = ifc.open(str(templates))
    entities = set()
    for template in document.by_type("IfcPropertySetTemplate"):
        if not (template.Name or "").startswith("Qto_"):
            continue
        for name in re.split(r"[,/]", template.ApplicableEntity or ""):
            name = name.strip()
            if name and not name.endswith("Type"):
                entities.add(name)
    return entities


#: Entities with official base quantities that are deliberately out of scope. Each is a
#: decision recorded in ADR 0011, not an omission.
EXCLUDED_FROM_SCOPE = {
    # Modifiers of a host element: the quantity deducts from (or adds to) the wall it
    # cuts. buildingSMART: an opening's `ContainedInStructure` shall be NIL.
    "IfcOpeningElement",
    "IfcProjectionElement",
    # Accumulators: their area already contains everything inside them.
    "IfcSite",
    "IfcBuilding",
    "IfcBuildingStorey",
    # Cost and schedule side, not model geometry.
    "IfcConstructionEquipmentResource",
    "IfcConstructionMaterialResource",
    "IfcLaborResource",
}


@pytest.mark.ifcopenshell
def test_scope_covers_every_entity_buildingsmart_gives_quantities_to():
    ifc = _ifc_runtime()
    schema = ifc.ifcopenshell_wrapper.schema_by_name("IFC4")

    def expand(name):
        try:
            declaration = schema.declaration_by_name(name)
        except Exception:
            return set()
        found = set()

        def walk(node):
            found.add(node.name())
            for child in node.subtypes() or ():
                walk(child)

        walk(declaration)
        return found

    scope = set()
    for group in index_module.BUILDING_ELEMENT_TYPES + index_module.SPATIAL_TYPES:
        for alias in group:
            scope |= expand(alias)

    official = _official_qto_entities(ifc)
    assert len(official) == 93
    missing = sorted(entity for entity in official - EXCLUDED_FROM_SCOPE if entity in expand("IfcRoot") and entity not in scope)
    assert missing == []
    # And nothing excluded sneaked in through a supertype — an opening billed as an
    # element would price the void as if it were the wall.
    assert sorted(EXCLUDED_FROM_SCOPE & scope) == []


@pytest.mark.ifcopenshell
def test_scope_groups_name_no_class_twice_through_a_parent():
    ifc = _ifc_runtime()
    schema = ifc.ifcopenshell_wrapper.schema_by_name("IFC4")
    roots = [group[-1] for group in index_module.BUILDING_ELEMENT_TYPES + index_module.SPATIAL_TYPES]
    redundant = []
    for name in roots:
        parent = schema.declaration_by_name(name).supertype()
        while parent is not None:
            if parent.name() in roots:
                redundant.append(name)
                break
            parent = parent.supertype()
    # `by_type` already returns every subtype, so a parent plus its subtype would
    # yield the same entity twice and inflate every count derived from the index.
    assert redundant == []


# --- Known-answer cases on pinned corpus models -----------------------------------


@pytest.mark.ifcopenshell
def test_millimetre_project_normalizes_length_and_leaves_area_and_volume_alone():
    """The defect this contract exists to stop.

    `b01-pcert-ifc4.ifc` declares LENGTHUNIT as MILLI METRE while AREAUNIT and
    VOLUMEUNIT are plain square/cubic metres. A 6 m wall is stored as 6000, and a rule
    reading the declared number would bill 6000 linear metres for it — a x1000 number
    wearing a base-quantity provenance label that every later check passes. Areas and
    volumes are already right, so scaling them by the length factor would break what
    works.
    """
    result = index_ifc_elements(MODELS / "b01-pcert-ifc4.ifc")
    assert result.success is True
    wall = _by_global_id(result, "0OfZwWc8j9QP5uX8xPTxDH")

    length = _dimension(wall, "length")
    assert length.value == pytest.approx(6000.0)
    assert length.normalized_value == pytest.approx(6.0)
    assert (length.unit.name, length.unit.prefix) == ("METRE", "MILLI")

    for dimension in ("area", "volume"):
        quantity = _dimension(wall, dimension)
        assert quantity.normalized_value == pytest.approx(quantity.value)
        assert quantity.unit.prefix is None


@pytest.mark.ifcopenshell
def test_index_reports_schema_storeys_types_and_provenance():
    result = index_ifc_elements(MODELS / "b01-pcert-ifc4.ifc")
    assert result.success is True
    assert result.source_schema == "IFC4"
    assert result.entity_count == 16
    assert result.project_name == "ifc silly sample scene - project"
    assert [storey.name for storey in result.storeys] == ["00 groundfloor"]
    # IfcChimney and IfcSpace arrive with the buildingSMART-aligned scope; the old
    # 20-class list never looked at either.
    assert result.element_types == ("IfcBuildingElementProxy", "IfcChimney", "IfcRoof", "IfcSlab", "IfcSpace", "IfcWall")
    assert result.duplicate_global_id_count == 0
    assert result.unresolved_unit_scale_count == 0
    assert result.diagnostics == ()
    assert result.publication == "none"
    assert result.artifact_filenames == ()

    wall = _by_global_id(result, "0OfZwWc8j9QP5uX8xPTxDH")
    assert wall.quantity_source == "base_quantity"
    assert wall.quantity_set_name == "Qto_WallBaseQuantities"
    assert wall.storey_name == "00 groundfloor"
    assert wall.material == "stone_sand-lime"
    assert _dimension(wall, "area").source_quantity_name == "NetSideArea"


@pytest.mark.ifcopenshell
def test_entities_are_ordered_and_the_index_is_reproducible():
    first = index_ifc_elements(MODELS / "b01-pcert-ifc4.ifc")
    second = index_ifc_elements(MODELS / "b01-pcert-ifc4.ifc")
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    global_ids = [entity.global_id for entity in first.entities]
    assert global_ids == sorted(global_ids)


@pytest.mark.ifcopenshell
def test_ifc2x3_indexes_through_the_same_path():
    result = index_ifc_elements(MODELS / "b02-community-ifc2x3.ifc")
    assert result.success is True
    assert result.source_schema == "IFC2X3"
    assert result.entity_count == 178  # 157 elements + 21 rooms
    assert "IfcWallStandardCase" in result.element_types
    assert "IfcWall" in result.element_types
    # Every element arrives once, through its most general declared class.
    assert result.duplicate_global_id_count == 0


@pytest.mark.ifcopenshell
def test_projection_rich_adds_properties_and_index_does_not():
    lean = index_ifc_elements(MODELS / "b01-pcert-ifc4.ifc", "index")
    rich = index_ifc_elements(MODELS / "b01-pcert-ifc4.ifc", "rich")
    assert all(entity.properties == {} for entity in lean.entities)
    assert any(entity.properties for entity in rich.entities)


# --- Selection semantics on purpose-built models ----------------------------------


def _model_with_quantity_sets(path, sets, schema="IFC4", length_unit=("METRE", None)):
    ifc = _ifc_runtime()
    model = ifc.file(schema=schema)
    guid = ifc.guid.new
    point = model.create_entity("IfcCartesianPoint", Coordinates=[0.0, 0.0, 0.0])
    axis = model.create_entity("IfcAxis2Placement3D", Location=point)
    context = model.create_entity(
        "IfcGeometricRepresentationContext", ContextType="Model", CoordinateSpaceDimension=3, WorldCoordinateSystem=axis
    )
    name, prefix = length_unit
    assignment = model.create_entity(
        "IfcUnitAssignment",
        Units=[
            model.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Name=name, Prefix=prefix),
            model.create_entity("IfcSIUnit", UnitType="AREAUNIT", Name="SQUARE_METRE"),
            model.create_entity("IfcSIUnit", UnitType="VOLUMEUNIT", Name="CUBIC_METRE"),
        ],
    )
    model.create_entity(
        "IfcProject", GlobalId=guid(), Name="Selection", RepresentationContexts=[context], UnitsInContext=assignment
    )
    wall = model.create_entity("IfcWall", GlobalId="1AAAAAAAAAAAAAAAAAAAAA", Name="Wall")
    for set_name, quantities in sets:
        quantity_set = model.create_entity("IfcElementQuantity", GlobalId=guid(), Name=set_name, Quantities=quantities(model))
        model.create_entity(
            "IfcRelDefinesByProperties", GlobalId=guid(), RelatedObjects=[wall], RelatingPropertyDefinition=quantity_set
        )
    model.write(str(path))
    return path


@pytest.mark.ifcopenshell
def test_net_wins_over_gross_and_a_later_zero_cannot_overwrite_it(tmp_path):
    """An exporter can carry many volumes on one wall, several legitimately zero.

    Walking them flat and keeping whichever came last indexes a real wall as 0.0 and
    labels it a successful read. Selection is by name, in a declared order.
    """

    def quantities(model):
        return [
            model.create_entity("IfcQuantityVolume", Name="GrossVolume", VolumeValue=3.0),
            model.create_entity("IfcQuantityVolume", Name="NetVolume", VolumeValue=2.49624),
            model.create_entity("IfcQuantityVolume", Name="ColumnVolume", VolumeValue=0.0),
            model.create_entity("IfcQuantityVolume", Name="Volume", VolumeValue=0.0),
        ]

    path = _model_with_quantity_sets(tmp_path / "net.ifc", [("BaseQuantities", quantities)])
    entity = index_ifc_elements(path).entities[0]
    volume = _dimension(entity, "volume")
    assert volume.value == pytest.approx(2.49624)
    assert volume.source_quantity_name == "NetVolume"
    assert volume.selection_rank == 0


@pytest.mark.ifcopenshell
def test_a_canonical_set_wins_over_a_vendor_set_entirely(tmp_path):
    def canonical(model):
        return [model.create_entity("IfcQuantityArea", Name="NetArea", AreaValue=10.0)]

    def vendor(model):
        return [
            model.create_entity("IfcQuantityArea", Name="Netto-Fläche", AreaValue=99.0),
            model.create_entity("IfcQuantityVolume", Name="Netto-Volumen", VolumeValue=99.0),
        ]

    path = _model_with_quantity_sets(
        tmp_path / "mixed.ifc", [("VendorQuantities", vendor), ("Qto_WallBaseQuantities", canonical)]
    )
    entity = index_ifc_elements(path).entities[0]
    assert entity.quantity_source == "base_quantity"
    assert entity.quantity_set_name == "Qto_WallBaseQuantities"
    # Not a merge: the vendor volume is not grafted onto the canonical area.
    assert [quantity.dimension for quantity in entity.quantities] == ["area"]
    assert _dimension(entity, "area").value == pytest.approx(10.0)


@pytest.mark.ifcopenshell
def test_vendor_names_are_read_when_no_canonical_set_exists(tmp_path):
    def vendor(model):
        return [model.create_entity("IfcQuantityVolume", Name="Netto-Volumen", VolumeValue=7.5)]

    path = _model_with_quantity_sets(tmp_path / "vendor.ifc", [("Mengen", vendor)])
    entity = index_ifc_elements(path).entities[0]
    assert entity.quantity_source == "vendor_quantity"
    assert _dimension(entity, "volume").value == pytest.approx(7.5)


@pytest.mark.ifcopenshell
def test_quantities_nested_under_a_complex_quantity_are_found(tmp_path):
    """Some exporters nest an entire quantity set under complex quantities."""

    def nested(model):
        return [
            model.create_entity(
                "IfcPhysicalComplexQuantity",
                Name="Wrapper",
                Discrimination="layer",
                HasQuantities=[model.create_entity("IfcQuantityArea", Name="NetArea", AreaValue=5.5)],
            )
        ]

    path = _model_with_quantity_sets(tmp_path / "nested.ifc", [("BaseQuantities", nested)])
    entity = index_ifc_elements(path).entities[0]
    assert _dimension(entity, "area").value == pytest.approx(5.5)


@pytest.mark.ifcopenshell
def test_an_element_with_nothing_readable_is_fallback_not_a_measured_zero(tmp_path):
    def auxiliary(model):
        return [model.create_entity("IfcQuantityArea", Name="SomeAuxiliaryMetric", AreaValue=0.0)]

    path = _model_with_quantity_sets(tmp_path / "aux.ifc", [("BaseQuantities", auxiliary)])
    entity = index_ifc_elements(path).entities[0]
    assert entity.quantity_source == "fallback"
    assert entity.quantities == ()


@pytest.mark.ifcopenshell
def test_count_is_reported_normalized_without_a_unit(tmp_path):
    def counted(model):
        return [model.create_entity("IfcQuantityCount", Name="Count", CountValue=3.0)]

    path = _model_with_quantity_sets(tmp_path / "count.ifc", [("BaseQuantities", counted)], length_unit=("METRE", "MILLI"))
    entity = index_ifc_elements(path).entities[0]
    count = _dimension(entity, "count")
    # Dimensionless: scaling it would turn one element into 0.001 elements.
    assert (count.value, count.normalized_value, count.unit) == (3.0, 3.0, None)


@pytest.mark.ifcopenshell
def test_an_unnormalizable_unit_is_reported_not_silently_dropped(tmp_path):
    """A quantity whose unit will not resolve must say so.

    Returning `normalized_value: null` alone reads as "nothing to see"; the warning is
    what makes an order-of-magnitude gap visible before it reaches a price.
    """
    ifc = _ifc_runtime()
    model = ifc.file(schema="IFC4")
    guid = ifc.guid.new
    point = model.create_entity("IfcCartesianPoint", Coordinates=[0.0, 0.0, 0.0])
    axis = model.create_entity("IfcAxis2Placement3D", Location=point)
    context = model.create_entity(
        "IfcGeometricRepresentationContext", ContextType="Model", CoordinateSpaceDimension=3, WorldCoordinateSystem=axis
    )
    # No LENGTHUNIT declared anywhere and no Unit on the quantity: nothing to derive
    # an SI value from, and nothing may be invented.
    assignment = model.create_entity(
        "IfcUnitAssignment", Units=[model.create_entity("IfcSIUnit", UnitType="AREAUNIT", Name="SQUARE_METRE")]
    )
    model.create_entity(
        "IfcProject", GlobalId=guid(), Name="Unitless", RepresentationContexts=[context], UnitsInContext=assignment
    )
    wall = model.create_entity("IfcWall", GlobalId="1AAAAAAAAAAAAAAAAAAAAA", Name="Wall")
    quantity_set = model.create_entity(
        "IfcElementQuantity",
        GlobalId=guid(),
        Name="BaseQuantities",
        Quantities=[model.create_entity("IfcQuantityLength", Name="Length", LengthValue=4.0)],
    )
    model.create_entity(
        "IfcRelDefinesByProperties", GlobalId=guid(), RelatedObjects=[wall], RelatingPropertyDefinition=quantity_set
    )
    path = tmp_path / "unitless.ifc"
    model.write(str(path))

    result = index_ifc_elements(path)
    length = _dimension(result.entities[0], "length")
    assert length.value == pytest.approx(4.0)
    assert length.normalized_value is None
    assert result.unresolved_unit_scale_count == 1
    assert [(item.code, item.severity, item.global_id) for item in result.diagnostics] == [
        (3001, "warning", "1AAAAAAAAAAAAAAAAAAAAA")
    ]


# --- Measurability taxonomy --------------------------------------------------------


@pytest.mark.ifcopenshell
def test_pcert_taxonomy_separates_containers_grouping_nodes_and_proxies():
    """PCERT carries all three contaminants of a coverage denominator in 13 objects.

    `house - roof` has no geometry and decomposes into the two slabs that do measure —
    counting it as unmeasured work reports double-count avoidance as a defect.
    `Group#18`/`Group#19` have no representation at all. `origin` and `geo-reference` are
    survey markers that happen to be proxies, and `sand bedding` is real work that happens
    to be one too: nothing structural separates them, so all three land in `ambiguous`.
    """
    result = index_ifc_elements(MODELS / "b01-pcert-ifc4.ifc")
    by_name = {entity.name: entity for entity in result.entities}

    roof = by_name["house - roof"]
    assert roof.measurability == "container"
    assert roof.decomposes_into == 2

    assert {by_name[name].measurability for name in ("Group#18", "Group#19")} == {"non_geometric"}
    assert {by_name[name].measurability for name in ("origin", "geo-reference", "sand bedding")} == {"ambiguous"}
    assert {entity.measurability for entity in result.entities if entity.ifc_class in ("IfcWall", "IfcSlab")} == {"structural"}


@pytest.mark.ifcopenshell
def test_parts_point_back_at_the_container_they_decompose_from():
    """Without this link a caller summing both a container and its parts double-counts."""
    result = index_ifc_elements(MODELS / "b01-pcert-ifc4.ifc")
    by_name = {entity.name: entity for entity in result.entities}
    roof = by_name["house - roof"]
    slabs = [entity for entity in result.entities if entity.part_of_global_id == roof.global_id]
    assert {slab.name for slab in slabs} == {"house - roof - slab left", "house - roof - slab right"}
    assert all(slab.quantities for slab in slabs)
    assert by_name["floor"].part_of_global_id is None


@pytest.mark.ifcopenshell
def test_measurability_reads_structure_and_never_the_element_name(tmp_path):
    """`origin` is not detected by being called "origin".

    A name rule would have to know that `origin` is a survey marker while `sand bedding`
    is work — in one language, from one exporter. In the pinned file `origin` carries a
    body and is reported `ambiguous`, not filtered away; and a wall with no geometry is
    demoted regardless of its class being a structural one.
    """
    pcert = index_ifc_elements(MODELS / "b01-pcert-ifc4.ifc")
    by_name = {entity.name: entity for entity in pcert.entities}
    assert by_name["origin"].measurability == "ambiguous"

    # And the mirror case, which a real file forced: `house - chimney` has a material and
    # a storey but no body and no quantities. It keeps its class — a chimney nobody can
    # measure is a gap worth seeing, not a node to drop.
    chimney = by_name["house - chimney"]
    assert (chimney.ifc_class, chimney.measurability) == ("IfcChimney", "structural")
    assert chimney.quantities == ()

    def quantities(model):
        return [model.create_entity("IfcQuantityArea", Name="NetArea", AreaValue=1.0)]

    path = _model_with_quantity_sets(tmp_path / "bodiless.ifc", [("BaseQuantities", quantities)])
    wall = index_ifc_elements(path).entities[0]
    # A wall without geometry is still a wall: only a proxy, the class defined by having no
    # declared semantics, is demoted when it has no body either.
    assert (wall.ifc_class, wall.measurability) == ("IfcWall", "structural")


@pytest.mark.ifcopenshell
def test_a_countable_element_without_quantities_counts_itself():
    """22 sanitary terminals in gymzaal carry no quantity set. They are still 22 units."""
    result = _published_index(MODELS / "b03-community-ifc4.ifc")
    terminals = [entity for entity in result.entities if entity.ifc_class == "IfcSanitaryTerminal"]
    assert len(terminals) == 22
    assert {entity.measurability for entity in terminals} == {"countable"}
    assert {entity.quantity_source for entity in terminals} == {"existence"}
    # Derived, and it says so: never folded into base_quantity.
    for terminal in terminals:
        assert [(q.dimension, q.value, q.unit) for q in terminal.quantities] == [("count", 1.0, None)]

    curtain_walls = [entity for entity in result.entities if entity.ifc_class == "IfcCurtainWall"]
    assert len(curtain_walls) == 27
    assert {entity.measurability for entity in curtain_walls} == {"container"}
    assert all(entity.decomposes_into > 0 for entity in curtain_walls)


# --- Boundary and transport --------------------------------------------------------


@pytest.mark.ifcopenshell
def test_a_class_the_schema_does_not_declare_is_recorded_not_swallowed(tmp_path, monkeypatch):
    def quantities(model):
        return [model.create_entity("IfcQuantityArea", Name="NetArea", AreaValue=1.0)]

    path = _model_with_quantity_sets(tmp_path / "skip.ifc", [("BaseQuantities", quantities)])
    monkeypatch.setattr(
        index_module,
        "BUILDING_ELEMENT_TYPES",
        (("IfcWall",), ("IfcNotAThingInThisSchema",), ("IfcAlsoNotAThing", "IfcBuildingElement")),
    )
    monkeypatch.setattr(index_module, "SPATIAL_TYPES", ())
    result = index_ifc_elements(path)
    assert result.success is True
    # Only the group where every alias is missing counts as skipped: a class renamed
    # between schemas is not a class that vanished.
    assert result.skipped_types == ("IfcNotAThingInThisSchema",)


@pytest.mark.ifcopenshell
def test_entity_budget_fails_the_whole_index_atomically(monkeypatch):
    monkeypatch.setattr(reader, "MAX_ENTITIES", 15)
    result = index_ifc_elements(MODELS / "b01-pcert-ifc4.ifc")
    assert result.success is False
    assert result.entities == ()
    assert result.truncated is False
    assert result.diagnostics[0].code == 2003
    monkeypatch.setattr(reader, "MAX_ENTITIES", 16)
    assert index_ifc_elements(MODELS / "b01-pcert-ifc4.ifc").success is True


@pytest.mark.ifcopenshell
def test_oversized_inline_result_fails_typed_and_points_at_publication(monkeypatch):
    monkeypatch.setattr(index_module, "MAX_INLINE_RESULT_BYTES", 200)
    result = index_ifc_elements(MODELS / "b01-pcert-ifc4.ifc")
    assert result.success is False
    assert result.entities == ()
    assert result.diagnostics[0].code == 2003
    assert "output_dir" in result.diagnostics[0].suggested_action


@pytest.mark.ifcopenshell
def test_published_artifact_is_byte_identical_to_the_inline_document(tmp_path):
    inline = index_ifc_elements(MODELS / "b01-pcert-ifc4.ifc")
    published = index_ifc_elements(MODELS / "b01-pcert-ifc4.ifc", "index", tmp_path / "published")
    assert published.success is True
    assert published.publication == "immutable-directory"
    assert published.entity_count == inline.entity_count
    document = (tmp_path / "published" / "extraction.json").read_text(encoding="utf-8")
    assert json.loads(document) == json.loads(
        json.dumps({**inline.model_dump(mode="json"), "source_sha256": published.source_sha256})
    )
    # The artifact is the canonical document, so it round-trips into the same DTO and
    # can be scored without re-reading the model.
    assert ElementIndexResult.model_validate_json(document).entity_count == inline.entity_count

    manifest = json.loads((tmp_path / "published" / "extraction-manifest.json").read_text(encoding="utf-8"))
    assert manifest["contract"] == "element-index.v1"
    assert manifest["versions"]["contract"] == "element-index.v1"


@pytest.mark.ifcopenshell
def test_a_malformed_source_fails_with_a_typed_index_error(tmp_path):
    path = tmp_path / "garbage.ifc"
    path.write_bytes(b"not an ifc file at all")
    result = index_ifc_elements(path)
    assert result.success is False
    assert result.diagnostics[0].code in {2800, 3000}


@pytest.mark.ifcopenshell
def test_sidecar_method_is_contained_under_the_job_root(tmp_path, monkeypatch):
    monkeypatch.setenv("MOTOR_IFC_JOB_ROOT", str(tmp_path))
    (tmp_path / "model.ifc").write_bytes((MODELS / "b01-pcert-ifc4.ifc").read_bytes())

    response = json.loads(
        handle_line(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "element.index.v1", "params": {"ifc_path": "model.ifc"}}))
    )
    assert response["result"]["contract_version"] == "element-index.v1"
    assert response["result"]["entity_count"] == 16
    assert response["result"]["publication"] == "none"
    assert response["result"]["artifact_filenames"] == []

    escape = json.loads(
        handle_line(
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "element.index.v1", "params": {"ifc_path": "../model.ifc"}})
        )
    )
    assert escape["error"]["code"] == -32602

    extra = json.loads(
        handle_line(
            json.dumps(
                {"jsonrpc": "2.0", "id": 3, "method": "element.index.v1", "params": {"ifc_path": "model.ifc", "nope": 1}}
            )
        )
    )
    assert extra["error"]["code"] == -32602
