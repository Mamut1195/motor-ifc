import hashlib
import json
import os
import stat
from pathlib import Path

import pytest

import motor_ifc.reader_extraction as reader
from motor_ifc import extract_ifc
from motor_ifc.rpc import handle_line


def _ifc_runtime():
    return pytest.importorskip("ifcopenshell")


def make_rich_ifc(path: Path) -> None:
    ifc = _ifc_runtime()
    model = ifc.file(schema="IFC4")
    guid = ifc.guid.new
    type_nested = model.create_entity(
        "IfcComplexProperty",
        Name="Nested",
        UsageName="OrderedData",
        HasProperties=[
            model.create_entity("IfcPropertySingleValue", Name="Beta", NominalValue=model.create_entity("IfcInteger", 2)),
            model.create_entity("IfcPropertySingleValue", Name="Alpha", NominalValue=model.create_entity("IfcLabel", "type")),
        ],
    )
    type_pset = model.create_entity("IfcPropertySet", GlobalId=guid(), Name="TypeSet", HasProperties=[type_nested])
    wall_type = model.create_entity("IfcWallType", GlobalId=guid(), Name="Wall type", HasPropertySets=[type_pset], PredefinedType="NOTDEFINED")
    wall = model.create_entity(
        "IfcWall",
        GlobalId="1AAAAAAAAAAAAAAAAAAAAA",
        Name="Wall",
        Description="Reader wall",
        ObjectType="Partition",
        Tag="W-1",
    )
    model.create_entity("IfcRelDefinesByType", GlobalId=guid(), RelatedObjects=[wall], RelatingType=wall_type)
    occurrence_pset = model.create_entity(
        "IfcPropertySet",
        GlobalId=guid(),
        Name="OccurrenceSet",
        HasProperties=[model.create_entity("IfcPropertySingleValue", Name="Enabled", NominalValue=model.create_entity("IfcBoolean", True))],
    )
    model.create_entity("IfcRelDefinesByProperties", GlobalId=guid(), RelatedObjects=[wall], RelatingPropertyDefinition=occurrence_pset)
    quantities = model.create_entity(
        "IfcElementQuantity",
        GlobalId=guid(),
        Name="BaseQuantities",
        Quantities=[model.create_entity("IfcQuantityLength", Name="Length", LengthValue=4.25)],
    )
    model.create_entity("IfcRelDefinesByProperties", GlobalId=guid(), RelatedObjects=[wall], RelatingPropertyDefinition=quantities)
    model.create_entity("IfcSite", GlobalId="0AAAAAAAAAAAAAAAAAAAAA", Name="Site")
    model.create_entity("IfcPropertySingleValue", Name="Anonymous", NominalValue=model.create_entity("IfcLabel", "excluded"))
    model.write(str(path))


def make_schema_invalid_ifc(path: Path) -> None:
    path.write_text(
        """ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('ViewDefinition [CoordinationView]'),'2;1');
FILE_NAME('invalid.ifc','2026-08-03T00:00:00',(),(),'','','');
FILE_SCHEMA(('IFC4'));
ENDSEC;
DATA;
#1=IFCWALL($,$,'Missing GlobalId',$,$,$,$,$,$);
ENDSEC;
END-ISO-10303-21;
""",
        encoding="ascii",
    )


@pytest.fixture
def rich_ifc(tmp_path):
    path = tmp_path / "rich.ifc"
    make_rich_ifc(path)
    return path


def dump(result):
    return result.model_dump(mode="json")


def nested_keys(value):
    if isinstance(value, dict):
        return set(value).union(*(nested_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(nested_keys(item) for item in value))
    return set()


@pytest.mark.ifcopenshell
def test_extract_ifc_returns_deterministic_scoped_rich_dto(rich_ifc):
    first = dump(extract_ifc(rich_ifc))
    second = dump(extract_ifc(rich_ifc))
    assert first == second
    assert first | {"entities": []} == {
        "contract_version": "reader-extraction.v1",
        "success": True,
        "source_schema": "IFC4",
        "entity_count": 2,
        "entities": [],
        "diagnostics": [],
        "truncated": False,
        "publication": "none",
        "artifact_filenames": [],
    }
    assert [(item["global_id"], item["ifc_class"]) for item in first["entities"]] == [
        ("0AAAAAAAAAAAAAAAAAAAAA", "IfcSite"),
        ("1AAAAAAAAAAAAAAAAAAAAA", "IfcWall"),
    ]
    wall = first["entities"][1]
    assert wall["name"] == "Wall"
    assert wall["description"] == "Reader wall"
    assert wall["object_type"] == "Partition"
    assert wall["tag"] == "W-1"
    assert wall["properties"]["OccurrenceSet"]["Enabled"] is True
    assert wall["properties"]["TypeSet"]["Nested"]["properties"] == {"Alpha": "type", "Beta": 2}
    assert wall["quantities"]["BaseQuantities"]["Length"] == 4.25
    assert "id" not in nested_keys(first["entities"])
    assert "geometry" not in json.dumps(first).lower()
    assert "unit" not in json.dumps(first).lower()


@pytest.mark.ifcopenshell
def test_extract_ifc_is_read_only(rich_ifc):
    before = (hashlib.sha256(rich_ifc.read_bytes()).hexdigest(), rich_ifc.stat().st_mtime_ns)
    result = extract_ifc(rich_ifc)
    after = (hashlib.sha256(rich_ifc.read_bytes()).hexdigest(), rich_ifc.stat().st_mtime_ns)
    assert result.success is True
    assert after == before
    assert list(rich_ifc.parent.iterdir()) == [rich_ifc]


class FakeEntity:
    GlobalId = "gid"
    Name = None
    Description = None
    ObjectType = None
    Tag = None

    def is_a(self):
        return "IfcWall"


class FakeModel:
    schema = "IFC4"

    def __init__(self, entities=None):
        self.entities = entities if entities is not None else [FakeEntity()]

    def by_type(self, name):
        assert name == "IfcObject"
        return self.entities


class FakeIfc:
    def __init__(self, model):
        self.model = model

    def open(self, path):
        return self.model


class FakeElementUtil:
    def __init__(self, properties=None, quantities=None):
        self.properties = properties if properties is not None else {}
        self.quantities = quantities if quantities is not None else {}

    def get_psets(self, entity, *, psets_only=False, qtos_only=False):
        return self.properties if psets_only else self.quantities


def fake_runtime(monkeypatch, tmp_path, *, entities=None, properties=None, quantities=None):
    path = tmp_path / "model.ifc"
    path.write_text("IFC", encoding="ascii")
    monkeypatch.setattr(reader, "runtime", lambda: (FakeIfc(FakeModel(entities)), FakeElementUtil(properties, quantities)))
    monkeypatch.setattr(reader, "_validate_schema", lambda model: None)
    return path


@pytest.mark.parametrize(
    "constant,value",
    [
        ("MAX_STRING_LENGTH", {"P": {"Value": "xx"}}),
        ("MAX_ARRAY_ITEMS", {"P": {"Value": [1, 2]}}),
        ("MAX_DEPTH", {"P": {"Value": {"A": {"B": 1}}}}),
        ("MAX_NODES_PER_ENTITY", {"P": {"A": 1, "B": 2}}),
        ("MAX_TOTAL_NODES", {"P": {"A": 1, "B": 2}}),
    ],
)
def test_normalization_bounds_fail_atomically(monkeypatch, tmp_path, constant, value):
    path = fake_runtime(monkeypatch, tmp_path, properties=value)
    monkeypatch.setattr(reader, constant, 1)
    result = extract_ifc(path)
    assert result.success is False
    assert result.entities == ()
    assert result.entity_count == 0
    assert result.truncated is False
    assert result.diagnostics[0].code == 2003


def test_entity_and_set_bounds_fail_atomically(monkeypatch, tmp_path):
    path = fake_runtime(monkeypatch, tmp_path, entities=[FakeEntity(), FakeEntity()])
    monkeypatch.setattr(reader, "MAX_ENTITIES", 1)
    assert extract_ifc(path).diagnostics[0].code == 2003


def test_input_byte_bound_fails_before_runtime(monkeypatch, tmp_path):
    path = tmp_path / "model.ifc"
    path.write_bytes(b"IFC")
    monkeypatch.setattr(reader, "MAX_IFC_BYTES", 2)
    monkeypatch.setattr(reader, "runtime", lambda: (_ for _ in ()).throw(AssertionError("runtime reached")))
    result = extract_ifc(path)
    assert result.success is False
    assert result.entities == ()
    assert result.diagnostics[0].code == 2003
    path = fake_runtime(monkeypatch, tmp_path, properties={"A": {}, "B": {}})
    monkeypatch.setattr(reader, "MAX_ENTITIES", 10_000)
    monkeypatch.setattr(reader, "MAX_SETS_PER_ENTITY", 1)
    assert extract_ifc(path).diagnostics[0].code == 2003


def test_growth_after_path_check_fails_before_ifcopenshell_open(monkeypatch, tmp_path):
    path = tmp_path / "model.ifc"
    path.write_bytes(b"IFC")
    real_secure = reader.secure_existing_input

    def grow_after_check(value, maximum):
        checked = real_secure(value, maximum)
        checked.write_bytes(b"IFC!")
        return checked

    monkeypatch.setattr(reader, "MAX_IFC_BYTES", 3)
    monkeypatch.setattr(reader, "secure_existing_input", grow_after_check)
    monkeypatch.setattr(reader, "runtime", lambda: (_ for _ in ()).throw(AssertionError("runtime reached")))
    result = extract_ifc(path)
    assert result.success is False
    assert result.diagnostics[0].code == 2003


def test_snapshot_is_private_read_only_and_cleaned(monkeypatch, tmp_path):
    path = tmp_path / "model.ifc"
    path.write_bytes(b"IFC")
    opened = []

    class InspectingIfc(FakeIfc):
        def open(self, snapshot_path):
            snapshot = Path(snapshot_path)
            opened.append(snapshot)
            assert snapshot != path
            assert snapshot.read_bytes() == b"IFC"
            assert not os.access(snapshot, os.W_OK) or os.name == "nt"
            assert not stat.S_IMODE(snapshot.stat().st_mode) & stat.S_IWUSR
            return self.model

    before = (path.read_bytes(), path.stat().st_mtime_ns)
    monkeypatch.setattr(reader, "runtime", lambda: (InspectingIfc(FakeModel()), FakeElementUtil()))
    monkeypatch.setattr(reader, "_validate_schema", lambda model: None)
    result = extract_ifc(path)
    assert result.success is True
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert opened and not opened[0].parent.exists()
    assert list(tmp_path.iterdir()) == [path]


def test_original_change_after_snapshot_fails_atomically_and_cleans_snapshot(monkeypatch, tmp_path):
    path = fake_runtime(monkeypatch, tmp_path)
    snapshot_parents = []
    real_snapshot = reader._snapshot_source

    def remember_snapshot(stream, baseline, target):
        snapshot_parents.append(target.parent)
        return real_snapshot(stream, baseline, target)

    monkeypatch.setattr(reader, "_snapshot_source", remember_snapshot)
    monkeypatch.setattr(reader, "_source_unchanged", lambda *args: False)
    result = extract_ifc(path)
    assert result.success is False
    assert result.entities == ()
    assert result.truncated is False
    assert result.diagnostics[0].code == 2800
    assert snapshot_parents and not snapshot_parents[0].exists()


@pytest.mark.ifcopenshell
def test_schema_invalid_real_ifc_fails_atomically_without_leakage(tmp_path):
    path = tmp_path / "schema-invalid.ifc"
    make_schema_invalid_ifc(path)
    payload = dump(extract_ifc(path))
    assert payload["success"] is False
    assert payload["entities"] == []
    assert payload["entity_count"] == 0
    assert payload["truncated"] is False
    assert payload["diagnostics"] == [{
        "severity": "error",
        "code": 2800,
        "stage": "reader-validate",
        "message": "IFC failed schema validation.",
        "suggested_action": "Repair the IFC with a schema-conforming tool before extraction.",
        "json_pointer": None,
        "source_id": None,
        "global_id": None,
        "ifc_class": None,
    }]
    assert str(path) not in json.dumps(payload)
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("length", [8, 9])
def test_dictionary_key_length_bound_is_exact_without_stringification(monkeypatch, tmp_path, length):
    key = "K" * length
    path = fake_runtime(monkeypatch, tmp_path, properties={key: {}})
    monkeypatch.setattr(reader, "MAX_STRING_LENGTH", 8)
    payload = dump(extract_ifc(path))
    assert payload["success"] is (length == 8)
    assert "object at" not in json.dumps(payload)


@pytest.mark.parametrize(
    "value",
    [
        {f"K{index}": {} for index in range(2_000)},
        {"P": [[] for _ in range(1_000)]},
    ],
)
def test_empty_container_amplification_consumes_node_budget(monkeypatch, tmp_path, value):
    path = fake_runtime(monkeypatch, tmp_path, properties=value)
    monkeypatch.setattr(reader, "MAX_SETS_PER_ENTITY", 3_000)
    monkeypatch.setattr(reader, "MAX_NODES_PER_ENTITY", 500)
    result = extract_ifc(path)
    assert result.success is False
    assert result.entities == ()
    assert result.truncated is False
    assert result.diagnostics[0].code == 2003


@pytest.mark.parametrize("budget,success", [(9, True), (8, False)])
def test_node_budget_n_and_n_plus_one_are_exact(monkeypatch, tmp_path, budget, success):
    path = fake_runtime(monkeypatch, tmp_path, properties={"P": {}})
    monkeypatch.setattr(reader, "MAX_NODES_PER_ENTITY", budget)
    result = extract_ifc(path)
    assert result.success is success
    assert result.truncated is False
    if not success:
        assert result.entities == ()
        assert result.diagnostics[0].code == 2003


@pytest.mark.parametrize("budget,success", [(9, True), (8, False)])
def test_total_node_budget_n_and_n_plus_one_are_exact(monkeypatch, tmp_path, budget, success):
    path = fake_runtime(monkeypatch, tmp_path, properties={"P": {}})
    monkeypatch.setattr(reader, "MAX_TOTAL_NODES", budget)
    result = extract_ifc(path)
    assert result.success is success
    assert result.truncated is False
    if not success:
        assert result.entities == ()
        assert result.diagnostics[0].code == 2003


@pytest.mark.parametrize("value", [object(), b"binary", float("inf")])
def test_unsupported_values_fail_without_repr_leak(monkeypatch, tmp_path, value):
    path = fake_runtime(monkeypatch, tmp_path, properties={"P": {"Value": value}})
    payload = dump(extract_ifc(path))
    assert payload["success"] is False
    assert payload["entities"] == []
    assert payload["diagnostics"][0]["code"] == 2801
    assert "object at" not in json.dumps(payload)


def test_cyclic_values_fail_atomically(monkeypatch, tmp_path):
    cyclic = []
    cyclic.append(cyclic)
    path = fake_runtime(monkeypatch, tmp_path, properties={"P": {"Value": cyclic}})
    assert extract_ifc(path).diagnostics[0].code == 2801


def test_runtime_unavailable_and_malformed_ifc_are_typed(monkeypatch, tmp_path):
    path = tmp_path / "model.ifc"
    path.write_text("not IFC", encoding="ascii")
    monkeypatch.setattr(reader, "runtime", lambda: (_ for _ in ()).throw(reader.ReaderRuntimeUnavailable()))
    assert extract_ifc(path).diagnostics[0].code == 2200
    monkeypatch.undo()
    assert extract_ifc(path).diagnostics[0].code == 2800


def test_wrong_ifcopenshell_version_is_typed_runtime_failure(monkeypatch):
    real_import = reader.importlib.import_module

    class WrongVersion:
        version = "0.8.4"

    monkeypatch.setattr(reader.importlib, "import_module", lambda name: WrongVersion() if name == "ifcopenshell" else real_import(name))
    with pytest.raises(reader.ReaderRuntimeUnavailable):
        reader.runtime()


def test_unexpected_runtime_initialization_failure_closes_source(monkeypatch, tmp_path):
    path = tmp_path / "model.ifc"
    path.write_bytes(b"IFC")
    stream, baseline = reader._open_source(path)
    monkeypatch.setattr(reader, "_open_source", lambda safe_path: (stream, baseline))
    monkeypatch.setattr(reader, "runtime", lambda: (_ for _ in ()).throw(RuntimeError("private detail")))
    payload = dump(extract_ifc(path))
    assert payload["success"] is False
    assert payload["diagnostics"][0]["code"] == 2800
    assert payload["diagnostics"][0]["stage"] == "reader-runtime"
    assert "private detail" not in json.dumps(payload)
    assert stream.closed


@pytest.mark.ifcopenshell
@pytest.mark.parametrize(
    "method,section",
    [("extract_metadata", None), ("extract_properties", "properties"), ("extract_quantities", "quantities")],
)
def test_rpc_and_legacy_projections_share_exact_path_contract(rich_ifc, method, section):
    params = {"ifc_path": rich_ifc.name}
    request = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    payload = json.loads(handle_line(json.dumps(request), str(rich_ifc.parent)))["result"]
    assert payload["contract_version"] == "reader-extraction.v1"
    assert payload["success"] is True
    for entity in payload["entities"]:
        assert {"global_id", "ifc_class", "name", "description", "object_type", "tag"} <= entity.keys()
        assert (section is not None and section in entity) or (section is None and "properties" not in entity and "quantities" not in entity)


@pytest.mark.ifcopenshell
def test_reader_rpc_rejects_extra_escape_absolute_and_missing_root(rich_ifc):
    base = {"jsonrpc": "2.0", "id": 1, "method": "reader.extract.v1"}
    for params, root in [
        ({"ifc_path": rich_ifc.name, "extra": True}, rich_ifc.parent),
        ({"ifc_path": "../rich.ifc"}, rich_ifc.parent),
        ({"ifc_path": str(rich_ifc)}, rich_ifc.parent),
        ({"ifc_path": rich_ifc.name}, None),
    ]:
        response = json.loads(handle_line(json.dumps(base | {"params": params}), str(root) if root else None))
        assert response["error"]["code"] == -32602


@pytest.mark.ifcopenshell
def test_reader_rpc_rich_result_matches_public_api(rich_ifc):
    request = {"jsonrpc": "2.0", "id": 1, "method": "reader.extract.v1", "params": {"ifc_path": rich_ifc.name}}
    rpc_result = json.loads(handle_line(json.dumps(request), str(rich_ifc.parent)))["result"]
    assert rpc_result == dump(extract_ifc(rich_ifc))
