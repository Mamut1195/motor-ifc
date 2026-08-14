"""Write corpus/MANIFEST.json: the pinned registry of the immutable corpus."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import ifcopenshell

MANIFEST_SCHEMA = "motor-ifc.corpus-manifest.v1"
METRIC_TYPES = ("IfcObject", "IfcElementQuantity", "IfcPropertySet", "IfcRelAssociatesMaterial", "IfcMaterial")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _metrics(path: Path) -> dict[str, int]:
    model = ifcopenshell.open(str(path))
    return {name: len(model.by_type(name)) for name in METRIC_TYPES}


def build(corpus_root: Path) -> dict:
    models_dir = corpus_root / "models"
    expected_dir = corpus_root / "expected"
    entries = []

    pcert = models_dir / "b01-pcert-ifc4.ifc"
    entries.append({
        "id": "B01-pcert-ifc4",
        "kind": "official",
        "status": "pinned",
        "file": "models/b01-pcert-ifc4.ifc",
        "source": "buildingSMART Sample-Test-Files PCERT baseline (local copy from elaboracion-de-presupuestos fixtures)",
        "license": "buildingSMART sample file",
        "ifc_schema": "IFC4",
        "sha256": _sha256(pcert),
        "bytes": pcert.stat().st_size,
        "metrics": _metrics(pcert),
    })

    for schema in ("IFC4", "IFC2X3", "IFC4X3"):
        model = models_dir / f"b04-oracle-{schema.lower()}.ifc"
        entries.append({
            "id": f"B04-oracle-{schema.lower()}",
            "kind": "oracle",
            "status": "pinned",
            "file": f"models/b04-oracle-{schema.lower()}.ifc",
            "generator": "corpus.generators.b04_oracle",
            "expected": [
                f"expected/b04-oracle-{schema.lower()}-quantities.json",
                f"expected/b04-oracle-{schema.lower()}-materials.json",
            ],
            "source": "generated (deterministic, header-normalized)",
            "license": "MAMUT internal",
            "ifc_schema": schema,
            "sha256": _sha256(model),
            "bytes": model.stat().st_size,
            "metrics": _metrics(model),
        })

    for identifier, filename, description in (
        ("B05-semantic-dense", "b05-semantic-dense.ifc", "2000 walls x 40 qtos x 8 props, minimal geometry"),
        ("B06-relation-dense", "b06-relation-dense.ifc", "500 walls x 20 IfcRelSpaceBoundary fan-out"),
        ("B07-geometry-dense", "b07-geometry-dense.ifc", "100 walls with extrusions + 64-triangle face sets"),
    ):
        path = models_dir / filename
        generator = f"corpus.generators.{identifier.split('-')[0].lower()}_" + identifier.split("-", 1)[1].replace("-", "_")
        entries.append({
            "id": identifier,
            "kind": "stress",
            "status": "pinned",
            "file": f"models/{filename}",
            "generator": generator,
            "source": f"generated (deterministic, header-normalized): {description}",
            "license": "MAMUT internal",
            "ifc_schema": "IFC4",
            "sha256": _sha256(path),
            "bytes": path.stat().st_size,
            "metrics": _metrics(path),
        })

    for identifier, filename, schema, source_path in (
        (
            "B02-community-ifc2x3",
            "b02-community-ifc2x3.ifc",
            "IFC2X3",
            "buildingsmart-community/Community-Sample-Test-Files: IFC 2.3.0.1 (IFC 2x3)/Duplex Apartment/Duplex_A_20110907.ifc",
        ),
        (
            "B03-community-ifc4",
            "b03-community-ifc4.ifc",
            "IFC4",
            "buildingsmart-community/Community-Sample-Test-Files: IFC 4.0.2.1 (IFC 4)/Example project location/example project location.ifc",
        ),
    ):
        path = models_dir / filename
        entries.append({
            "id": identifier,
            "kind": "community",
            "status": "pinned",
            "file": f"models/{filename}",
            "source": source_path,
            "license": "community sample — verify upstream terms before external redistribution",
            "ifc_schema": schema,
            "sha256": _sha256(path),
            "bytes": path.stat().st_size,
            "metrics": _metrics(path),
        })

    entries.extend([
        {
            "id": "real-cand-11m",
            "kind": "real",
            "status": "pointer",
            "source": "elaboracion-de-presupuestos apps/ifc/tests/fixtures/CAND_aleman_11M.ifc",
            "ifc_schema": "IFC4",
            "sha256": "cfb2124497b25d9a72101075e84be0feb44ff669cb1bd3251be11efebeea945c",
            "bytes": 10934237,
            "note": "dirty in source (8 defective IfcRelSpaceBoundary); extract after model.repair.v1",
        },
        {
            "id": "real-schependomlaan-49m",
            "kind": "real",
            "status": "pointer",
            "source": "elaboracion-de-presupuestos apps/ifc/tests/fixtures/IFC_Schependomlaan.ifc",
            "ifc_schema": "IFC2X3",
            "sha256": "2c3565ca1904f2aa61adab92024cf3755b2c5b21a498144d3094d7cb58cebec7",
            "bytes": 49286967,
            "note": "dirty in source (1 defective IfcRelConnectsPathElements); extract after model.repair.v1",
        },
        {
            "id": "B08-adversarial",
            "kind": "adversarial",
            "status": "generated-at-test-time",
            "generator": "corpus.generators.b08_adversarial",
            "source": "boundary and hostile-input builders (not pinned: each builder targets one documented bound)",
        },
    ])

    expected_hashes = {
        str(path.relative_to(corpus_root)).replace("\\", "/"): _sha256(path)
        for path in sorted(expected_dir.glob("*.json"))
    }
    return {
        "schema": MANIFEST_SCHEMA,
        "models": entries,
        "expected_sha256": expected_hashes,
    }


def write(corpus_root: Path) -> Path:
    manifest_path = corpus_root / "MANIFEST.json"
    manifest_path.write_text(json.dumps(build(corpus_root), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest_path


if __name__ == "__main__":
    import sys

    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
    target = write(root)
    print(f"wrote {target}")
