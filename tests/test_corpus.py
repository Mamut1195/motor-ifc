"""Corpus immutability and independent correctness oracle (Ola 3).

The corpus registry is ``corpus/MANIFEST.json``; pinned models must match their
SHA-256 byte-for-byte, generators must reproduce those bytes, and the B04 oracle
extraction must equal the independently built expected documents 100%.
"""
import hashlib
import importlib
import json
import pathlib

import pytest

import motor_ifc.reader_extraction as reader
from motor_ifc import extract_ifc_semantic

ROOT = pathlib.Path(__file__).parents[1]
CORPUS = ROOT / "corpus"
MODELS = CORPUS / "models"
EXPECTED = CORPUS / "expected"

PINNED_IDS = {
    "B01-pcert-ifc4",
    "B02-community-ifc2x3",
    "B03-community-ifc4",
    "B04-oracle-ifc4",
    "B04-oracle-ifc2x3",
    "B04-oracle-ifc4x3",
    "B05-semantic-dense",
    "B06-relation-dense",
    "B07-geometry-dense",
}


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_manifest() -> dict:
    return json.loads((CORPUS / "MANIFEST.json").read_text(encoding="utf-8"))


def test_manifest_pins_every_corpus_model():
    document = load_manifest()
    assert document["schema"] == "motor-ifc.corpus-manifest.v1"
    pinned = {entry["id"]: entry for entry in document["models"] if entry.get("status") == "pinned"}
    assert set(pinned) == PINNED_IDS
    for entry in pinned.values():
        path = CORPUS / entry["file"]
        assert path.is_file()
        assert sha256(path) == entry["sha256"]
        assert path.stat().st_size == entry["bytes"]
        assert entry["ifc_schema"]
    for relative, digest in document["expected_sha256"].items():
        assert sha256(CORPUS / relative) == digest


@pytest.mark.ifcopenshell
@pytest.mark.parametrize("module", ["b04_oracle", "b05_semantic_dense", "b06_relation_dense", "b07_geometry_dense"])
def test_generators_are_byte_reproducible(module, tmp_path):
    generator = importlib.import_module(f"corpus.generators.{module}")
    generator.generate(tmp_path)
    produced = sorted(tmp_path.glob("*.ifc"))
    assert produced
    for model in produced:
        pinned = MODELS / model.name
        assert pinned.is_file()
        assert model.read_bytes() == pinned.read_bytes()


@pytest.mark.ifcopenshell
@pytest.mark.parametrize("schema", ["IFC4", "IFC2X3", "IFC4X3"])
@pytest.mark.parametrize("projection", ["quantities", "materials"])
def test_oracle_extraction_matches_independent_expected(schema, projection):
    model = MODELS / f"b04-oracle-{schema.lower()}.ifc"
    expected = json.loads((EXPECTED / f"b04-oracle-{schema.lower()}-{projection}.json").read_text(encoding="utf-8"))
    assert extract_ifc_semantic(model, projection).model_dump(mode="json") == expected


@pytest.mark.ifcopenshell
def test_oracle_expected_documents_are_rebuildable():
    from corpus.generators import b04_expected
    from corpus.generators.b04_oracle import SCHEMAS

    for schema in SCHEMAS:
        model = MODELS / f"b04-oracle-{schema.lower()}.ifc"
        builders = (("quantities", b04_expected.expected_quantities), ("materials", b04_expected.expected_materials))
        for projection, builder in builders:
            checked_in = json.loads((EXPECTED / f"b04-oracle-{schema.lower()}-{projection}.json").read_text(encoding="utf-8"))
            assert builder(schema, model) == checked_in


@pytest.mark.ifcopenshell
def test_official_and_community_models_extract():
    pcert = extract_ifc_semantic(MODELS / "b01-pcert-ifc4.ifc", "quantities")
    assert pcert.success is True and pcert.entity_count > 0
    duplex = extract_ifc_semantic(MODELS / "b02-community-ifc2x3.ifc", "quantities")
    assert duplex.success is True and duplex.entity_count == 296
    # B03 crosses the inline byte cap: typed atomic failure with a publication hint.
    inline = extract_ifc_semantic(MODELS / "b03-community-ifc4.ifc", "quantities")
    assert inline.success is False
    assert inline.entities == ()
    assert inline.diagnostics[0].code == 2003
    assert "output_dir" in inline.diagnostics[0].suggested_action


@pytest.mark.ifcopenshell
def test_community_ifc4_publishes_when_inline_exceeds_cap(tmp_path):
    result = extract_ifc_semantic(MODELS / "b03-community-ifc4.ifc", "quantities", tmp_path / "published")
    assert result.success is True
    assert result.publication == "immutable-directory"
    assert result.entity_count == 609
    assert (tmp_path / "published" / "extraction.json").is_file()


@pytest.mark.ifcopenshell
def test_semantic_dense_extracts_within_budget_via_publication(tmp_path):
    result = extract_ifc_semantic(MODELS / "b05-semantic-dense.ifc", "quantities", tmp_path / "b05")
    assert result.success is True
    assert result.entity_count == 2000
    assert result.publication == "immutable-directory"


@pytest.mark.ifcopenshell
def test_relation_dense_parses_and_extracts_metadata():
    result = extract_ifc_semantic(MODELS / "b06-relation-dense.ifc", "metadata")
    assert result.success is True
    assert result.entity_count == 501


@pytest.mark.ifcopenshell
def test_geometry_dense_extracts_metadata():
    result = extract_ifc_semantic(MODELS / "b07-geometry-dense.ifc", "metadata")
    assert result.success is True
    assert result.entity_count == 100


@pytest.mark.ifcopenshell
def test_adversarial_entity_budget_n_and_n_plus_one(tmp_path, monkeypatch):
    from corpus.generators import b08_adversarial as b08

    path = b08.minimal_walls(tmp_path / "walls.ifc", 3)
    monkeypatch.setattr(reader, "MAX_ENTITIES", 2)
    result = extract_ifc_semantic(path, "metadata")
    assert result.success is False and result.entities == () and result.diagnostics[0].code == 2003
    monkeypatch.setattr(reader, "MAX_ENTITIES", 3)
    assert extract_ifc_semantic(path, "metadata").success is True


@pytest.mark.ifcopenshell
def test_adversarial_array_budget_fails_atomically(tmp_path, monkeypatch):
    from corpus.generators import b08_adversarial as b08

    path = b08.wide_quantity_set(tmp_path / "wide.ifc", quantities=6)
    monkeypatch.setattr(reader, "MAX_ARRAY_ITEMS", 5)
    result = extract_ifc_semantic(path, "quantities")
    assert result.success is False and result.entities == () and result.diagnostics[0].code == 2003


@pytest.mark.ifcopenshell
def test_adversarial_depth_bomb_fails_atomically(tmp_path):
    from corpus.generators import b08_adversarial as b08

    path = b08.deep_property_nesting(tmp_path / "deep.ifc", depth=20)
    result = extract_ifc_semantic(path, "properties")
    assert result.success is False and result.entities == () and result.diagnostics[0].code == 2003


@pytest.mark.ifcopenshell
def test_adversarial_garbage_and_truncated_fail_typed(tmp_path):
    from corpus.generators import b08_adversarial as b08

    garbage = b08.garbage(tmp_path / "garbage.ifc")
    truncated = b08.truncated(tmp_path / "truncated.ifc", MODELS / "b04-oracle-ifc4.ifc")
    for path in (garbage, truncated):
        result = extract_ifc_semantic(path, "metadata")
        assert result.success is False and result.diagnostics[0].code == 2800
