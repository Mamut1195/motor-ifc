import json
from pathlib import Path

def test_checked_in_schemas_are_valid_json():
    paths=list(Path("schemas").glob("*.schema.json"))
    assert {p.name for p in paths}>={"authoring-envelope-v1.schema.json","architecture-payload-v1.schema.json","structure-payload-v1.schema.json","mep-payload-v1.schema.json","rpc-capabilities-request-v1.schema.json"}
    for path in paths:
        value=json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(value,dict)


def test_checked_in_schemas_are_fresh(tmp_path):
    from motor_ifc.schemas import write_schemas
    write_schemas(tmp_path)
    checked=Path("schemas")
    assert {p.name:p.read_text(encoding="utf-8") for p in checked.glob("*.schema.json")} == {p.name:p.read_text(encoding="utf-8") for p in tmp_path.glob("*.schema.json")}
