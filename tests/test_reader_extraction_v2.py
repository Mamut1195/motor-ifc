import hashlib
import json
from pathlib import Path

import pytest

import motor_ifc.reader_extraction as reader
from motor_ifc import extract_ifc, extract_ifc_semantic
from motor_ifc.models import ReaderExtractionResultV2
from motor_ifc.rpc import handle_line


def _ifc_runtime():
    return pytest.importorskip("ifcopenshell")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_semantic_ifc(path: Path) -> None:
    ifc = _ifc_runtime()
    model = ifc.file(schema="IFC4")
    guid = ifc.guid.new
    point = model.create_entity("IfcCartesianPoint", Coordinates=[0.0, 0.0, 0.0])
    axis = model.create_entity("IfcAxis2Placement3D", Location=point)
    context = model.create_entity(
        "IfcGeometricRepresentationContext", ContextType="Model", CoordinateSpaceDimension=3, WorldCoordinateSystem=axis
    )
    assignment = model.create_entity(
        "IfcUnitAssignment",
        Units=[
            model.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Name="METRE"),
            model.create_entity("IfcSIUnit", UnitType="AREAUNIT", Name="SQUARE_METRE"),
        ],
    )
    model.create_entity(
        "IfcProject", GlobalId=guid(), Name="Project", RepresentationContexts=[context], UnitsInContext=assignment
    )
    wall = model.create_entity("IfcWall", GlobalId="1AAAAAAAAAAAAAAAAAAAAA", Name="Wall")
    explicit_unit = model.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Prefix="MILLI", Name="METRE")
    occurrence_qset = model.create_entity(
        "IfcElementQuantity",
        GlobalId=guid(),
        Name="BaseQuantities",
        MethodOfMeasurement="MM-1",
        Quantities=[
            model.create_entity("IfcQuantityLength", Name="Length", LengthValue=4250.0, Unit=explicit_unit),
            model.create_entity("IfcQuantityArea", Name="Area", AreaValue=12.5, Formula="W*H"),
            model.create_entity(
                "IfcPhysicalComplexQuantity",
                Name="Complex",
                Discrimination="layer",
                HasQuantities=[
                    model.create_entity("IfcQuantityLength", Name="PartA", LengthValue=2.0),
                    model.create_entity("IfcQuantityLength", Name="PartB", LengthValue=2.25),
                ],
            ),
        ],
    )
    model.create_entity("IfcRelDefinesByProperties", GlobalId=guid(), RelatedObjects=[wall], RelatingPropertyDefinition=occurrence_qset)
    extra_qset = model.create_entity(
        "IfcElementQuantity",
        GlobalId=guid(),
        Name="ExtraQuantities",
        Quantities=[model.create_entity("IfcQuantityLength", Name="Length", LengthValue=9.0)],
    )
    model.create_entity("IfcRelDefinesByProperties", GlobalId=guid(), RelatedObjects=[wall], RelatingPropertyDefinition=extra_qset)
    type_qset = model.create_entity(
        "IfcElementQuantity",
        GlobalId=guid(),
        Name="BaseQuantities",
        Quantities=[model.create_entity("IfcQuantityLength", Name="TypeLength", LengthValue=4.0)],
    )
    wall_type = model.create_entity(
        "IfcWallType", GlobalId=guid(), Name="Wall type", PredefinedType="NOTDEFINED", HasPropertySets=[type_qset]
    )
    model.create_entity("IfcRelDefinesByType", GlobalId=guid(), RelatedObjects=[wall], RelatingType=wall_type)
    concrete = model.create_entity("IfcMaterial", Name="Concrete", Category="structural")
    model.create_entity("IfcRelAssociatesMaterial", GlobalId=guid(), RelatedObjects=[wall], RelatingMaterial=concrete)
    steel = model.create_entity("IfcMaterial", Name="Steel")
    layer = model.create_entity(
        "IfcMaterialLayer", Material=steel, LayerThickness=0.2, IsVentilated=False, Name="Shell", Category="finish", Priority=1
    )
    unknown_layer = model.create_entity("IfcMaterialLayer", Material=concrete, LayerThickness=0.1, IsVentilated="UNKNOWN")
    layer_set = model.create_entity("IfcMaterialLayerSet", MaterialLayers=[layer, unknown_layer], LayerSetName="WallLayers")
    usage = model.create_entity(
        "IfcMaterialLayerSetUsage",
        ForLayerSet=layer_set,
        LayerSetDirection="AXIS3",
        DirectionSense="POSITIVE",
        OffsetFromReferenceLine=0.1,
    )
    model.create_entity("IfcRelAssociatesMaterial", GlobalId=guid(), RelatedObjects=[wall_type], RelatingMaterial=usage)
    constituent = model.create_entity(
        "IfcMaterialConstituent", Name="Cement", Material=concrete, Fraction=0.8, Category="binder"
    )
    constituent_set = model.create_entity("IfcMaterialConstituentSet", Name="Mix", MaterialConstituents=[constituent])
    model.create_entity("IfcRelAssociatesMaterial", GlobalId=guid(), RelatedObjects=[wall], RelatingMaterial=constituent_set)
    model.write(str(path))


def make_semantic_ifc2x3(path: Path) -> None:
    ifc = _ifc_runtime()
    model = ifc.file(schema="IFC2X3")
    guid = ifc.guid.new
    point = model.create_entity("IfcCartesianPoint", Coordinates=[0.0, 0.0, 0.0])
    axis = model.create_entity("IfcAxis2Placement3D", Location=point)
    context = model.create_entity(
        "IfcGeometricRepresentationContext", ContextType="Model", CoordinateSpaceDimension=3, WorldCoordinateSystem=axis
    )
    assignment = model.create_entity(
        "IfcUnitAssignment", Units=[model.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Name="METRE")]
    )
    person = model.create_entity("IfcPerson")
    organization = model.create_entity("IfcOrganization", Name="fixture-org")
    person_and_organization = model.create_entity("IfcPersonAndOrganization", ThePerson=person, TheOrganization=organization)
    application = model.create_entity(
        "IfcApplication", ApplicationDeveloper=organization, Version="1.0", ApplicationFullName="fixture", ApplicationIdentifier="fixture"
    )
    owner_history = model.create_entity(
        "IfcOwnerHistory", OwningUser=person_and_organization, OwningApplication=application, ChangeAction="ADDED", CreationDate=0
    )
    model.create_entity(
        "IfcProject",
        GlobalId=guid(),
        OwnerHistory=owner_history,
        Name="Project",
        RepresentationContexts=[context],
        UnitsInContext=assignment,
    )
    wall = model.create_entity("IfcWall", GlobalId="1AAAAAAAAAAAAAAAAAAAAA", OwnerHistory=owner_history, Name="Wall")
    qset = model.create_entity(
        "IfcElementQuantity",
        GlobalId=guid(),
        OwnerHistory=owner_history,
        Name="BaseQuantities",
        Quantities=[model.create_entity("IfcQuantityCount", Name="Count", CountValue=3.0)],
    )
    model.create_entity(
        "IfcRelDefinesByProperties",
        GlobalId=guid(),
        OwnerHistory=owner_history,
        RelatedObjects=[wall],
        RelatingPropertyDefinition=qset,
    )
    model.write(str(path))


@pytest.fixture
def semantic_ifc(tmp_path):
    path = tmp_path / "semantic.ifc"
    make_semantic_ifc(path)
    return path


def dump(result):
    return result.model_dump(mode="json")


def wall_entity(payload):
    return next(entity for entity in payload["entities"] if entity["global_id"] == "1AAAAAAAAAAAAAAAAAAAAA")


@pytest.mark.ifcopenshell
def test_semantic_quantities_are_lossless_with_units_and_provenance(semantic_ifc):
    payload = dump(extract_ifc_semantic(semantic_ifc, "quantities"))
    assert payload | {"entities": []} == {
        "contract_version": "reader-extraction.v2",
        "success": True,
        "source_schema": "IFC4",
        "entity_count": 1,
        "entities": [],
        "diagnostics": [],
        "truncated": False,
        "publication": "none",
        "artifact_filenames": [],
        "source_sha256": hashlib.sha256(semantic_ifc.read_bytes()).hexdigest(),
        "extraction_sha256": None,
    }
    sets = wall_entity(payload)["quantity_sets"]
    assert [(item["source"], item["name"]) for item in sets] == [
        ("occurrence", "BaseQuantities"),
        ("occurrence", "ExtraQuantities"),
        ("type", "BaseQuantities"),
    ]
    occurrence, extra, type_set = sets
    assert occurrence["method_of_measurement"] == "MM-1"
    assert occurrence["relation_global_id"] is not None
    assert occurrence["shadowed_by_occurrence"] is False
    assert type_set["relation_global_id"] is None
    assert type_set["shadowed_by_occurrence"] is True
    # Duplicate quantity names across sets are preserved, never collapsed.
    assert occurrence["quantities"][0]["name"] == "Length"
    assert extra["quantities"][0]["name"] == "Length"
    explicit = occurrence["quantities"][0]
    assert explicit["ifc_class"] == "IfcQuantityLength"
    assert explicit["value"] == 4250.0
    assert explicit["value_type"] == "IfcLengthMeasure"
    assert explicit["unit"] == {"source": "quantity", "name": "METRE", "symbol": "mm", "prefix": "MILLI", "unit_type": "LENGTHUNIT"}
    assert explicit["normalized_value"] == 4.25
    project = occurrence["quantities"][1]
    assert project["formula"] == "W*H"
    assert project["unit"]["source"] == "project"
    assert project["unit"]["name"] == "SQUARE_METRE"
    assert project["unit"]["unit_type"] == "AREAUNIT"
    assert project["normalized_value"] == 12.5
    complex_quantity = occurrence["quantities"][2]
    assert complex_quantity["ifc_class"] == "IfcPhysicalComplexQuantity"
    assert complex_quantity["discrimination"] == "layer"
    assert complex_quantity["value"] is None
    assert [(item["name"], item["value"]) for item in complex_quantity["components"]] == [("PartA", 2.0), ("PartB", 2.25)]
    assert all(item["unit"]["source"] == "project" for item in complex_quantity["components"])


@pytest.mark.ifcopenshell
def test_semantic_materials_cover_occurrence_type_and_composition(semantic_ifc):
    payload = dump(extract_ifc_semantic(semantic_ifc, "materials"))
    associations = wall_entity(payload)["material_associations"]
    by_kind = {(item["source"], item["kind"]): item for item in associations}
    assert set(by_kind) == {
        ("occurrence", "material"),
        ("occurrence", "constituent_set"),
        ("type", "layer_set_usage"),
    }
    material = by_kind[("occurrence", "material")]
    assert material["name"] == "Concrete"
    assert material["category"] == "structural"
    assert material["relation_global_id"] is not None
    constituent_set = by_kind[("occurrence", "constituent_set")]
    assert constituent_set["name"] == "Mix"
    assert constituent_set["constituents"] == [
        {"name": "Cement", "description": None, "category": "binder", "fraction": 0.8, "material_name": "Concrete"}
    ]
    usage = by_kind[("type", "layer_set_usage")]
    assert usage["name"] == "WallLayers"
    assert usage["usage_direction"] == "AXIS3"
    assert usage["usage_offset"] == 0.1
    assert usage["layers"] == [
        {"material_name": "Steel", "thickness": 0.2, "is_ventilated": False, "priority": 1, "category": "finish"},
        {"material_name": "Concrete", "thickness": 0.1, "is_ventilated": "UNKNOWN", "priority": None, "category": None},
    ]


@pytest.mark.ifcopenshell
def test_semantic_extraction_is_deterministic_and_read_only(semantic_ifc):
    first = dump(extract_ifc_semantic(semantic_ifc))
    second = dump(extract_ifc_semantic(semantic_ifc))
    assert first == second
    before = (hashlib.sha256(semantic_ifc.read_bytes()).hexdigest(), semantic_ifc.stat().st_mtime_ns)
    result = extract_ifc_semantic(semantic_ifc)
    after = (hashlib.sha256(semantic_ifc.read_bytes()).hexdigest(), semantic_ifc.stat().st_mtime_ns)
    assert result.success is True
    assert after == before
    assert list(semantic_ifc.parent.iterdir()) == [semantic_ifc]


@pytest.mark.ifcopenshell
def test_semantic_rich_keeps_v1_properties_and_adds_semantic_sections(semantic_ifc):
    payload = dump(extract_ifc_semantic(semantic_ifc, "rich"))
    wall = wall_entity(payload)
    assert wall["properties"] == {}
    assert len(wall["quantity_sets"]) == 3
    assert len(wall["material_associations"]) == 3


@pytest.mark.ifcopenshell
@pytest.mark.parametrize(
    "projection,present,absent",
    [
        ("metadata", [], ["properties", "quantity_sets", "material_associations"]),
        ("properties", ["properties"], ["quantity_sets", "material_associations"]),
        ("quantities", ["quantity_sets"], ["properties", "material_associations"]),
        ("materials", ["material_associations"], ["properties", "quantity_sets"]),
    ],
)
def test_semantic_projections_select_sections(semantic_ifc, projection, present, absent):
    payload = dump(extract_ifc_semantic(semantic_ifc, projection))
    wall = wall_entity(payload)
    for key in present:
        assert key in wall
    for key in absent:
        assert key not in wall


@pytest.mark.ifcopenshell
def test_semantic_ifc2x3_count_quantity_without_formula(tmp_path):
    path = tmp_path / "semantic-2x3.ifc"
    make_semantic_ifc2x3(path)
    payload = dump(extract_ifc_semantic(path, "quantities"))
    assert payload["success"] is True
    assert payload["source_schema"] == "IFC2X3"
    (qset,) = wall_entity(payload)["quantity_sets"]
    (count,) = qset["quantities"]
    assert count["ifc_class"] == "IfcQuantityCount"
    assert count["value"] == 3
    assert count["formula"] is None
    assert count["unit"]["source"] == "unknown"
    assert count["normalized_value"] is None


@pytest.mark.ifcopenshell
def test_semantic_node_budget_fails_atomically(semantic_ifc, monkeypatch):
    monkeypatch.setattr(reader, "MAX_NODES_PER_ENTITY", 10)
    result = extract_ifc_semantic(semantic_ifc)
    assert result.success is False
    assert result.entities == ()
    assert result.entity_count == 0
    assert result.truncated is False
    assert result.diagnostics[0].code == 2003


@pytest.mark.ifcopenshell
def test_semantic_set_budget_fails_atomically(semantic_ifc, monkeypatch):
    monkeypatch.setattr(reader, "MAX_SETS_PER_ENTITY", 1)
    result = extract_ifc_semantic(semantic_ifc)
    assert result.success is False
    assert result.entities == ()
    assert result.diagnostics[0].code == 2003


class FakeQuantity:
    Name = "Length"
    Description = None
    Formula = None
    Unit = None

    def __init__(self, value):
        self.LengthValue = value

    def is_a(self, name=None):
        return "IfcQuantityLength" if name is None else name == "IfcQuantityLength"


class FakeQuantitySet:
    GlobalId = "qset"
    Name = "Q"
    Description = None
    MethodOfMeasurement = None

    def __init__(self, quantities):
        self.Quantities = quantities

    def is_a(self, name=None):
        return "IfcElementQuantity" if name is None else name == "IfcElementQuantity"


class FakeRelation:
    GlobalId = "rel"

    def __init__(self, definition):
        self.RelatingPropertyDefinition = definition

    def is_a(self, name=None):
        return "IfcRelDefinesByProperties" if name is None else name == "IfcRelDefinesByProperties"


class FakeSemanticEntity:
    GlobalId = "gid"
    Name = None
    Description = None
    ObjectType = None
    Tag = None
    HasAssociations = ()

    def __init__(self, defined_by=()):
        self.IsDefinedBy = defined_by

    def is_a(self):
        return "IfcWall"


class FakeModel:
    schema = "IFC4"

    def __init__(self, entities):
        self.entities = entities

    def by_type(self, name):
        assert name == "IfcObject"
        return self.entities


class FakeIfc:
    def __init__(self, model):
        self.model = model

    def open(self, path):
        return self.model


class FakeElementUtil:
    def get_psets(self, entity, *, psets_only=False, qtos_only=False):
        return {}

    def get_type(self, entity):
        return None


class FakeUnitUtil:
    si_type_names = {}

    def get_measure_unit_type(self, measure):
        raise ValueError("unknown measure")

    def get_project_unit(self, model, unit_type):
        return None


def fake_semantic_runtime(monkeypatch, tmp_path, *, entities):
    path = tmp_path / "model.ifc"
    path.write_text("IFC", encoding="ascii")
    monkeypatch.setattr(reader, "runtime", lambda: (FakeIfc(FakeModel(entities)), FakeElementUtil()))
    monkeypatch.setattr(reader, "_validate_schema", lambda model: None)
    monkeypatch.setattr(reader, "_unit_util", lambda: FakeUnitUtil())
    return path


@pytest.mark.parametrize("value", [object(), b"binary", float("inf")])
def test_semantic_unsupported_values_fail_atomically_without_repr_leak(monkeypatch, tmp_path, value):
    entity = FakeSemanticEntity(defined_by=[FakeRelation(FakeQuantitySet([FakeQuantity(value)]))])
    path = fake_semantic_runtime(monkeypatch, tmp_path, entities=[entity])
    payload = dump(extract_ifc_semantic(path, "quantities"))
    assert payload["success"] is False
    assert payload["entities"] == []
    assert payload["diagnostics"][0]["code"] == 2801
    assert "object at" not in json.dumps(payload)


def test_semantic_unknown_unit_is_explicit_not_invented(monkeypatch, tmp_path):
    entity = FakeSemanticEntity(defined_by=[FakeRelation(FakeQuantitySet([FakeQuantity(2.5)]))])
    path = fake_semantic_runtime(monkeypatch, tmp_path, entities=[entity])
    payload = dump(extract_ifc_semantic(path, "quantities"))
    assert payload["success"] is True
    (quantity,) = payload["entities"][0]["quantity_sets"][0]["quantities"]
    assert quantity["value"] == 2.5
    assert quantity["unit"] == {"source": "unknown", "name": None, "symbol": None, "prefix": None, "unit_type": None}
    assert quantity["normalized_value"] is None


def test_v1_contract_remains_without_unit_semantics(semantic_ifc):
    _ifc_runtime()
    payload = dump(extract_ifc(semantic_ifc))
    assert payload["contract_version"] == "reader-extraction.v1"
    assert payload["success"] is True
    assert "unit" not in json.dumps(payload).lower()


@pytest.mark.ifcopenshell
def test_reader_v2_rpc_matches_public_api_and_projection(semantic_ifc):
    request = {"jsonrpc": "2.0", "id": 1, "method": "reader.extract.v2", "params": {"ifc_path": semantic_ifc.name}}
    rpc_result = json.loads(handle_line(json.dumps(request), str(semantic_ifc.parent)))["result"]
    assert rpc_result == dump(extract_ifc_semantic(semantic_ifc))
    quantities_request = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "reader.extract.v2",
        "params": {"ifc_path": semantic_ifc.name, "projection": "quantities"},
    }
    quantities_result = json.loads(handle_line(json.dumps(quantities_request), str(semantic_ifc.parent)))["result"]
    assert quantities_result == dump(extract_ifc_semantic(semantic_ifc, "quantities"))


@pytest.mark.ifcopenshell
def test_reader_v2_rpc_rejects_invalid_params_and_unsafe_paths(semantic_ifc):
    base = {"jsonrpc": "2.0", "id": 1, "method": "reader.extract.v2"}
    for params, root in [
        ({"ifc_path": semantic_ifc.name, "projection": "bogus"}, semantic_ifc.parent),
        ({"ifc_path": semantic_ifc.name, "extra": True}, semantic_ifc.parent),
        ({"ifc_path": "../semantic.ifc"}, semantic_ifc.parent),
        ({"ifc_path": str(semantic_ifc)}, semantic_ifc.parent),
        ({"ifc_path": semantic_ifc.name}, None),
    ]:
        response = json.loads(handle_line(json.dumps(base | {"params": params}), str(root) if root else None))
        assert response["error"]["code"] == -32602


def test_capabilities_advertise_reader_v1_and_v2():
    from motor_ifc import capabilities

    advertised = capabilities()
    assert advertised.reader_extraction_contract_versions == ("reader-extraction.v1", "reader-extraction.v2")
    assert "reader-extraction.v2" in advertised.contract_versions


@pytest.mark.ifcopenshell
def test_semantic_publication_publishes_immutable_artifact(semantic_ifc, tmp_path):
    before = (sha256(semantic_ifc), semantic_ifc.stat().st_mtime_ns)
    output_dir = tmp_path / "published"
    payload = dump(extract_ifc_semantic(semantic_ifc, "rich", output_dir))
    assert payload["contract_version"] == "reader-extraction.v2"
    assert payload["success"] is True
    assert payload["entity_count"] == 1
    assert payload["entities"] == []
    assert payload["publication"] == "immutable-directory"
    assert payload["artifact_filenames"] == ["extraction.json", "extraction-manifest.json"]
    assert payload["source_sha256"] == sha256(semantic_ifc)
    assert (sha256(semantic_ifc), semantic_ifc.stat().st_mtime_ns) == before
    extraction_path = output_dir / "extraction.json"
    manifest_path = output_dir / "extraction-manifest.json"
    assert extraction_path.is_file() and manifest_path.is_file()
    assert payload["extraction_sha256"] == sha256(extraction_path)
    document = json.loads(extraction_path.read_text(encoding="utf-8"))
    # The artifact is the canonical inline document and validates against the frozen DTO.
    assert ReaderExtractionResultV2.model_validate(document).model_dump(mode="json") == document
    assert document == dump(extract_ifc_semantic(semantic_ifc, "rich"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema"] == "motor-ifc.reader-publication-manifest.v1"
    assert manifest["contract"] == "reader-extraction.v2"
    assert manifest["projection"] == "rich"
    assert manifest["source_sha256"] == payload["source_sha256"]
    assert manifest["extraction_sha256"] == payload["extraction_sha256"]
    assert manifest["entity_count"] == 1
    assert manifest["artifacts"] == ["extraction.json", "extraction-manifest.json"]
    assert manifest["versions"]["contract"] == "reader-extraction.v2"
    assert manifest["versions"]["ifcopenshell"] == "0.8.5"
    # No staging residue.
    assert sorted(item.name for item in tmp_path.iterdir()) == ["published", "semantic.ifc"]


@pytest.mark.ifcopenshell
def test_semantic_publication_is_deterministic_across_runs(semantic_ifc, tmp_path):
    first = dump(extract_ifc_semantic(semantic_ifc, "quantities", tmp_path / "one"))
    second = dump(extract_ifc_semantic(semantic_ifc, "quantities", tmp_path / "two"))
    assert first["extraction_sha256"] == second["extraction_sha256"]
    assert (tmp_path / "one" / "extraction.json").read_bytes() == (tmp_path / "two" / "extraction.json").read_bytes()


@pytest.mark.ifcopenshell
def test_semantic_publication_budget_failure_publishes_nothing(semantic_ifc, tmp_path, monkeypatch):
    monkeypatch.setattr(reader, "MAX_NODES_PER_ENTITY", 10)
    output_dir = tmp_path / "published"
    payload = dump(extract_ifc_semantic(semantic_ifc, "rich", output_dir))
    assert payload["success"] is False
    assert payload["publication"] == "none"
    assert payload["diagnostics"][0]["code"] == 2003
    assert not output_dir.exists()
    assert sorted(item.name for item in tmp_path.iterdir()) == ["semantic.ifc"]


@pytest.mark.parametrize("budget,success", [(6, True), (5, False)])
def test_semantic_total_node_budget_n_and_n_plus_one_are_exact(monkeypatch, tmp_path, budget, success):
    path = fake_semantic_runtime(monkeypatch, tmp_path, entities=[FakeSemanticEntity()])
    monkeypatch.setattr(reader, "MAX_TOTAL_NODES_V2", budget)
    result = extract_ifc_semantic(path, "metadata")
    assert result.success is success
    assert result.truncated is False
    if not success:
        assert result.entities == ()
        assert result.diagnostics[0].code == 2003


@pytest.mark.ifcopenshell
def test_semantic_inline_byte_cap_fails_with_publication_hint(semantic_ifc, monkeypatch):
    monkeypatch.setattr(reader, "MAX_INLINE_RESULT_BYTES", 100)
    payload = dump(extract_ifc_semantic(semantic_ifc, "rich"))
    assert payload["success"] is False
    assert payload["entities"] == []
    assert payload["truncated"] is False
    assert payload["diagnostics"][0]["code"] == 2003
    assert payload["diagnostics"][0]["stage"] == "reader-extract"
    assert "output_dir" in payload["diagnostics"][0]["suggested_action"]


@pytest.mark.ifcopenshell
def test_v1_inline_is_not_subject_to_v2_byte_cap(semantic_ifc, monkeypatch):
    monkeypatch.setattr(reader, "MAX_INLINE_RESULT_BYTES", 1)
    payload = dump(extract_ifc(semantic_ifc))
    assert payload["contract_version"] == "reader-extraction.v1"
    assert payload["success"] is True


@pytest.mark.ifcopenshell
def test_semantic_publication_rejects_unsafe_output_without_publishing(semantic_ifc, tmp_path):
    before = (sha256(semantic_ifc), semantic_ifc.stat().st_mtime_ns)
    existing = tmp_path / "taken"
    existing.mkdir()
    payload = dump(extract_ifc_semantic(semantic_ifc, "rich", existing))
    assert payload["success"] is False
    assert payload["diagnostics"][0]["code"] == 2400
    assert payload["diagnostics"][0]["stage"] == "reader-output"
    assert list(existing.iterdir()) == []
    traversal = dump(extract_ifc_semantic(semantic_ifc, "rich", tmp_path / ".." / "escape"))
    assert traversal["success"] is False
    assert traversal["diagnostics"][0]["code"] == 2400
    assert not (tmp_path.parent / "escape").exists()
    assert (sha256(semantic_ifc), semantic_ifc.stat().st_mtime_ns) == before


@pytest.mark.ifcopenshell
def test_reader_v2_rpc_publication_matches_public_api(semantic_ifc, tmp_path):
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "reader.extract.v2",
        "params": {"ifc_path": semantic_ifc.name, "projection": "quantities", "output_dir": "published"},
    }
    response = json.loads(handle_line(json.dumps(request), str(semantic_ifc.parent)))
    api_payload = dump(extract_ifc_semantic(semantic_ifc, "quantities", tmp_path / "api"))
    assert response["result"]["success"] is True
    assert response["result"]["publication"] == "immutable-directory"
    assert response["result"]["extraction_sha256"] == api_payload["extraction_sha256"]
    assert (tmp_path / "published" / "extraction.json").read_bytes() == (tmp_path / "api" / "extraction.json").read_bytes()


@pytest.mark.ifcopenshell
def test_reader_v2_rpc_rejects_invalid_publication_params(semantic_ifc):
    base = {"jsonrpc": "2.0", "id": 1, "method": "reader.extract.v2"}
    for params in [
        {"ifc_path": semantic_ifc.name, "output_dir": "../escape"},
        {"ifc_path": semantic_ifc.name, "output_dir": ""},
        {"ifc_path": semantic_ifc.name, "output_dir": "published", "extra": True},
    ]:
        response = json.loads(handle_line(json.dumps(base | {"params": params}), str(semantic_ifc.parent)))
        assert response["error"]["code"] == -32602


@pytest.mark.ifcopenshell
def test_reader_snapshots_are_contained_and_cleaned_under_job_root(semantic_ifc, tmp_path, monkeypatch):
    job_root = tmp_path / "jobroot"
    job_root.mkdir()
    monkeypatch.setenv("MOTOR_IFC_JOB_ROOT", str(job_root))
    payload = dump(extract_ifc_semantic(semantic_ifc, "metadata"))
    assert payload["success"] is True
    temp = job_root / ".tmp.motor-ifc"
    assert not temp.exists() or not any(temp.iterdir())
