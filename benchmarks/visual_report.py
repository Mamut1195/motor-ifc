"""Standalone visual HTML report for the IFC motor (real supervisor transport).

Exercises the real engine over the available IFC models (audit -> repair ->
rich extraction -> GLB conversion, plus a full authoring.compile.v1 cycle with a
synthetic snapshot) and writes a self-contained Spanish HTML report with an
embedded three.js viewer.

Usage:
  python benchmarks/visual_report.py [--run-id visual-2026-08-11] \
      [--models id1,id2,...] [--timeout-ms 600000]

Evidence: benchmarks/results/<run-id>/visual.jsonl + hardware.json + glb/*.glb
Report:   <repo>/reporte-motor-ifc-2026-08-11.html
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import shutil
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmarks.common import (
    CORPUS,
    FIXTURES,
    SupervisorClient,
    hardware_fingerprint,
    jsonl_append,
    sha256,
    stage_input,
)

REPO = Path(__file__).resolve().parents[1]
REPORT_HTML = REPO / "reporte-motor-ifc-2026-08-11.html"

MODELS: dict[str, dict] = {
    "pcert-real": {"source": FIXTURES / "PCERT_Building-Architecture_IFC4.ifc"},
    "cand-11m": {"source": FIXTURES / "CAND_aleman_11M.ifc"},
    "schependomlaan-49m": {"source": FIXTURES / "IFC_Schependomlaan.ifc"},
    "b01-pcert-ifc4": {"source": CORPUS / "models/b01-pcert-ifc4.ifc"},
    "b02-community-ifc2x3": {"source": CORPUS / "models/b02-community-ifc2x3.ifc"},
    "b03-community-ifc4": {"source": CORPUS / "models/b03-community-ifc4.ifc"},
    "b05-semantic-dense": {"source": CORPUS / "models/b05-semantic-dense.ifc"},
}

# Small models first, schependomlaan last.
EXECUTION_ORDER = [
    "b01-pcert-ifc4",
    "b02-community-ifc2x3",
    "b03-community-ifc4",
    "b05-semantic-dense",
    "pcert-real",
    "cand-11m",
    "schependomlaan-49m",
]

# Minimal valid authoring.v1 + building.architecture@1 snapshot (1 storey, 3 walls).
COMPILE_SNAPSHOT: dict[str, Any] = {
    "contract_version": "authoring.v1",
    "profile": "building.architecture@1",
    "ifc_schema": "IFC4",
    "model_id": "11111111-1111-1111-1111-111111111111",
    "revision": 1,
    "discipline": "architecture",
    "federation_id": "22222222-2222-2222-2222-222222222222",
    "authority": {
        "producer": "visual-report",
        "ruleset_version": "1.0.0",
        "source_hash": "sha256:" + "a" * 64,
        "approved": True,
    },
    "units": "SI",
    "coordinate_reference": {"name": "local-engineering"},
    "payload": {
        "payload_version": "architecture.v1",
        "storey_source_id": "level-1",
        "storey_name": "Nivel 1",
        "elements": [
            {"kind": "wall", "source_id": "wall-1", "name": "Muro norte", "length": 5.0, "thickness": 0.2, "height": 3.0, "x": 0.0, "y": 0.0},
            {"kind": "wall", "source_id": "wall-2", "name": "Muro este", "length": 4.0, "thickness": 0.2, "height": 3.0, "x": 6.0, "y": 0.0},
            {"kind": "wall", "source_id": "wall-3", "name": "Muro sur", "length": 5.0, "thickness": 0.15, "height": 2.8, "x": 0.0, "y": 4.0},
        ],
    },
}


def rpc_call(client: SupervisorClient, model_id: str, phase: str, method: str, params: dict) -> tuple[dict | None, dict]:
    """One JSON-RPC call; never raises. Returns (result|None, evidence record)."""
    request_id = f"{model_id}-{phase}"
    record: dict[str, Any] = {"phase": phase, "model_id": model_id, "method": method}
    try:
        response = client.request(request_id, method, params)
    except Exception as exc:  # transport-level failure (timeout, closed pipe, ...)
        record.update(success=False, error=type(exc).__name__, detail=str(exc)[:300])
        return None, record
    record["client_elapsed_ms"] = response.get("_client_elapsed_ms")
    completed = client.events_for("worker_completed", request_id)
    record["worker_elapsed_ms"] = completed[0].get("elapsed_ms") if completed else None
    record["rss_peak"] = completed[0].get("rss_peak") if completed else None
    if "error" in response:
        fault = response["error"]
        record.update(
            success=False,
            error_code=fault.get("code"),
            error=fault.get("message"),
            diagnostic_code=(fault.get("data") or {}).get("diagnostic_code"),
        )
        return None, record
    result = response.get("result") or {}
    record["success"] = bool(result.get("success"))
    if not record["success"]:
        record["diagnostics"] = [d.get("code") for d in result.get("diagnostics", []) if isinstance(d, dict)]
    return result, record


def timing_fields(record: dict) -> dict:
    return {
        "client_elapsed_ms": record.get("client_elapsed_ms"),
        "worker_elapsed_ms": record.get("worker_elapsed_ms"),
        "rss_peak": record.get("rss_peak"),
    }


def sample_quantities(entities: list | None, limit: int = 15) -> list[dict]:
    """Defensive sampling of rich-projection quantity sets (getattr/.get with defaults)."""
    samples: list[dict] = []
    for entity in entities or []:
        if not isinstance(entity, dict):
            continue
        for qset in entity.get("quantity_sets") or []:
            for quantity in (qset.get("quantities") or []):
                unit = quantity.get("unit") or {}
                samples.append({
                    "entity": entity.get("name") or entity.get("global_id"),
                    "ifc_class": entity.get("ifc_class"),
                    "set": qset.get("name"),
                    "set_source": qset.get("source"),
                    "name": quantity.get("name"),
                    "value": quantity.get("value"),
                    "unit": unit.get("symbol") or unit.get("name"),
                    "normalized_value": quantity.get("normalized_value"),
                })
                if len(samples) >= limit:
                    return samples
    return samples


def sample_materials(entities: list | None, limit: int = 10) -> list[dict]:
    samples: list[dict] = []
    for entity in entities or []:
        if not isinstance(entity, dict):
            continue
        for assoc in entity.get("material_associations") or []:
            names = list(assoc.get("materials") or [])
            if not names:
                names = [layer.get("material_name") for layer in assoc.get("layers") or [] if layer.get("material_name")]
            if not names:
                names = [c.get("material_name") for c in assoc.get("constituents") or [] if c.get("material_name")]
            samples.append({
                "entity": entity.get("name") or entity.get("global_id"),
                "ifc_class": entity.get("ifc_class"),
                "kind": assoc.get("kind"),
                "source": assoc.get("source"),
                "name": assoc.get("name"),
                "materials": names,
            })
            if len(samples) >= limit:
                return samples
    return samples


def run_model(model_id: str, source: Path, run_id: str, run_dir: Path, evidence: Path, timeout_ms: int) -> dict:
    report: dict[str, Any] = {
        "model_id": model_id,
        "source": str(source),
        "used_repaired": False,
        "audit": None,
        "repair": None,
        "extract": None,
        "glb": None,
    }
    if not source.exists():
        report["error"] = "source-missing"
        jsonl_append(evidence, {"phase": "model", "model_id": model_id, "method": None, "success": False, "error": "source-missing", "source": str(source)})
        return report
    report["ifc_bytes"] = source.stat().st_size
    report["ifc_sha256"] = sha256(source)
    glb_dir = run_dir / "glb"
    glb_dir.mkdir(parents=True, exist_ok=True)
    job_root = Path(tempfile.mkdtemp(prefix=f"visual-{model_id}-"))
    client = SupervisorClient(job_root, workers=4, timeout_ms=timeout_ms)
    try:
        relative = stage_input(job_root, source)

        # 2. model.audit.v1
        result, record = rpc_call(client, model_id, "audit", "model.audit.v1", {"ifc_path": relative})
        jsonl_append(evidence, record)
        audit: dict[str, Any] = {"success": record["success"], **timing_fields(record)}
        if record.get("diagnostics"):
            audit["diagnostics"] = record["diagnostics"]
        if record.get("error"):
            audit["error"] = record["error"]
        if result:
            defects = result.get("defects") or []
            audit.update(
                source_schema=result.get("source_schema"),
                valid=result.get("valid"),
                defect_count=result.get("defect_count"),
                repairable_count=result.get("repairable_count"),
                manual_count=result.get("manual_count"),
                repairable=result.get("repairable"),
                top_classes=[[k, n] for k, n in Counter(d.get("ifc_class") for d in defects).most_common(5)],
                strategies=[[k, n] for k, n in Counter(d.get("repair_strategy") for d in defects).most_common(5)],
                sample_defects=defects[:5],
            )
        report["audit"] = audit

        # 3. model.repair.v1 (only when repairable defects were found)
        current_rel = relative
        if result and record["success"] and (result.get("repairable") or result.get("repairable_count")):
            result_r, record_r = rpc_call(client, model_id, "repair", "model.repair.v1", {"ifc_path": relative, "output_dir": "repaired"})
            jsonl_append(evidence, record_r)
            repair: dict[str, Any] = {"success": record_r["success"], **timing_fields(record_r)}
            if record_r.get("diagnostics"):
                repair["diagnostics"] = record_r["diagnostics"]
            if record_r.get("error"):
                repair["error"] = record_r["error"]
            if result_r:
                repair.update(
                    repaired=result_r.get("repaired"),
                    defects_fixed=result_r.get("defects_fixed"),
                    repaired_sha256=result_r.get("repaired_sha256"),
                    sample_fixes=(result_r.get("fixes") or [])[:5],
                )
                if record_r["success"] and result_r.get("repaired"):
                    repaired_ifc = job_root / "repaired" / "repaired.ifc"
                    if repaired_ifc.exists():
                        current_rel = stage_input(job_root, repaired_ifc)
                        report["used_repaired"] = True
            report["repair"] = repair

        # 4. reader.extract.v2 (rich, published under out/)
        result_e, record_e = rpc_call(
            client, model_id, "extract", "reader.extract.v2",
            {"ifc_path": current_rel, "projection": "rich", "output_dir": "out"},
        )
        jsonl_append(evidence, record_e)
        extract: dict[str, Any] = {"success": record_e["success"], **timing_fields(record_e)}
        if record_e.get("diagnostics"):
            extract["diagnostics"] = record_e["diagnostics"]
        if record_e.get("error"):
            extract["error"] = record_e["error"]
        payload: dict | None = None
        artifact = job_root / "out" / "extraction.json"
        if artifact.exists():
            extract["artifact_bytes"] = artifact.stat().st_size
            extract["sha256"] = sha256(artifact)
            try:
                payload = json.loads(artifact.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = None
        elif result_e:
            payload = result_e  # inline extraction (no published artifact)
        if payload:
            extract["entity_count"] = payload.get("entity_count")
            extract["source_schema"] = payload.get("source_schema")
            entities = payload.get("entities")
        else:
            entities = None
        extract["quantity_samples"] = sample_quantities(entities)
        extract["material_samples"] = sample_materials(entities)
        report["extract"] = extract

        # 5. viewer.convert.v1
        glb_name = f"{model_id}.glb"
        result_g, record_g = rpc_call(
            client, model_id, "viewer", "viewer.convert.v1",
            {"ifc_path": current_rel, "result_dir": "glb", "glb_filename": glb_name},
        )
        jsonl_append(evidence, record_g)
        glb: dict[str, Any] = {"success": record_g["success"], **timing_fields(record_g)}
        if record_g.get("diagnostics"):
            glb["diagnostics"] = record_g["diagnostics"]
        if record_g.get("error"):
            glb["error"] = record_g["error"]
        produced = job_root / "glb" / glb_name
        if record_g["success"] and produced.exists():
            dest = glb_dir / glb_name
            shutil.copy2(produced, dest)
            glb.update(
                bytes=dest.stat().st_size,
                sha256=sha256(dest),
                rel_path=f"benchmarks/results/{run_id}/glb/{glb_name}",
            )
        report["glb"] = glb
    finally:
        try:
            client.close()
        except Exception:
            pass
        shutil.rmtree(job_root, ignore_errors=True)
    jsonl_append(evidence, {
        "phase": "model-summary",
        "model_id": model_id,
        "method": None,
        "success": bool((report.get("extract") or {}).get("success")),
        "used_repaired": report["used_repaired"],
    })
    return report


def run_compile_demo(run_id: str, run_dir: Path, evidence: Path, timeout_ms: int) -> dict:
    """Full cycle: authoring.compile.v1 -> viewer.convert.v1 -> reader.extract.v2."""
    demo: dict[str, Any] = {"model_id": "compile-demo", "compile": None, "extract": None, "glb": None}
    glb_dir = run_dir / "glb"
    glb_dir.mkdir(parents=True, exist_ok=True)
    job_root = Path(tempfile.mkdtemp(prefix="visual-compile-demo-"))
    client = SupervisorClient(job_root, workers=4, timeout_ms=timeout_ms)
    try:
        # 7a. authoring.compile.v1 with the inline minimal snapshot.
        result_c, record_c = rpc_call(
            client, "compile-demo", "compile", "authoring.compile.v1",
            {"snapshot": COMPILE_SNAPSHOT, "output_dir": "compiled"},
        )
        jsonl_append(evidence, record_c)
        compile_info: dict[str, Any] = {"success": record_c["success"], **timing_fields(record_c)}
        if record_c.get("diagnostics"):
            compile_info["diagnostics"] = record_c["diagnostics"]
        if record_c.get("error"):
            compile_info["error"] = record_c["error"]
        compiled_ifc = job_root / "compiled" / "architecture.ifc"
        if result_c:
            compile_info["semantic_fingerprint"] = result_c.get("semantic_fingerprint")
            compile_info["artifacts"] = sorted((result_c.get("artifacts") or {}).keys())
        if compiled_ifc.exists():
            compile_info["ifc_bytes"] = compiled_ifc.stat().st_size
            compile_info["ifc_sha256"] = sha256(compiled_ifc)
        demo["compile"] = compile_info

        if record_c["success"] and compiled_ifc.exists():
            ifc_rel = "compiled/architecture.ifc"
            # 7b. viewer.convert.v1 over the compiled IFC.
            result_g, record_g = rpc_call(
                client, "compile-demo", "viewer", "viewer.convert.v1",
                {"ifc_path": ifc_rel, "result_dir": "glb", "glb_filename": "compile-demo.glb"},
            )
            jsonl_append(evidence, record_g)
            glb: dict[str, Any] = {"success": record_g["success"], **timing_fields(record_g)}
            produced = job_root / "glb" / "compile-demo.glb"
            if record_g["success"] and produced.exists():
                dest = glb_dir / "compile-demo.glb"
                shutil.copy2(produced, dest)
                glb.update(
                    bytes=dest.stat().st_size,
                    sha256=sha256(dest),
                    rel_path=f"benchmarks/results/{run_id}/glb/compile-demo.glb",
                )
            demo["glb"] = glb
            # 7c. reader.extract.v2 (rich, inline, no publication).
            result_e, record_e = rpc_call(
                client, "compile-demo", "extract", "reader.extract.v2",
                {"ifc_path": ifc_rel, "projection": "rich"},
            )
            jsonl_append(evidence, record_e)
            extract: dict[str, Any] = {"success": record_e["success"], **timing_fields(record_e)}
            if result_e:
                extract["entity_count"] = result_e.get("entity_count")
                extract["source_schema"] = result_e.get("source_schema")
                extract["quantity_samples"] = sample_quantities(result_e.get("entities"), limit=15)
            demo["extract"] = extract
    finally:
        try:
            client.close()
        except Exception:
            pass
        shutil.rmtree(job_root, ignore_errors=True)
    return demo


# ---------------------------------------------------------------- HTML report


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def fmt_s(ms: Any) -> str:
    return "—" if ms is None else f"{ms / 1000:.2f} s"


def fmt_mb(bytes_value: Any) -> str:
    return "—" if bytes_value is None else f"{bytes_value / 1048576:.1f} MB"


def fmt_bytes(value: Any) -> str:
    if value is None:
        return "—"
    if value >= 1048576:
        return f"{value / 1048576:.2f} MB"
    if value >= 1024:
        return f"{value / 1024:.1f} KB"
    return f"{value} B"


def badge(ok: bool | None, skip_label: str = "—") -> str:
    if ok is None:
        return f'<span class="badge badge-skip">{esc(skip_label)}</span>'
    return '<span class="badge badge-pass">PASS</span>' if ok else '<span class="badge badge-fail">FAIL</span>'


def svg_bars(entries: list[tuple[str, float | None]], title: str, unit: str) -> str:
    """Inline SVG vertical bar chart (no JS). entries: (label, value|None)."""
    values = [v for _, v in entries if v is not None]
    if not values:
        return f'<p class="muted">Sin datos para {esc(title)}.</p>'
    bar_w, gap, left = 46, 26, 50
    top, bottom, height = 34, 64, 260
    plot_h = height - top - bottom
    width = left + len(entries) * (bar_w + gap) + 20
    vmax = max(values) or 1.0
    parts = [
        f'<figure class="chart"><figcaption>{esc(title)} ({esc(unit)})</figcaption>',
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{esc(title)}">',
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" stroke="#c8cdd4"/>',
        f'<line x1="{left}" y1="{top + plot_h}" x2="{width - 10}" y2="{top + plot_h}" stroke="#c8cdd4"/>',
    ]
    for index, (label, value) in enumerate(entries):
        x = left + gap // 2 + index * (bar_w + gap)
        if value is None:
            parts.append(
                f'<text x="{x + bar_w / 2}" y="{top + plot_h - 6}" text-anchor="middle" class="bar-na">n/d</text>'
            )
        else:
            bar_h = max(2.0, plot_h * value / vmax)
            y = top + plot_h - bar_h
            parts.append(
                f'<rect x="{x}" y="{y:.1f}" width="{bar_w}" height="{bar_h:.1f}" class="bar">'
                f"<title>{esc(label)}: {value:.2f} {esc(unit)}</title></rect>"
                f'<text x="{x + bar_w / 2}" y="{y - 5:.1f}" text-anchor="middle" class="bar-val">{value:.1f}</text>'
            )
        parts.append(
            f'<text x="{x + bar_w / 2}" y="{top + plot_h + 12}" text-anchor="end" class="bar-label" '
            f'transform="rotate(-35 {x + bar_w / 2} {top + plot_h + 12})">{esc(label)}</text>'
        )
    parts.append("</svg></figure>")
    return "".join(parts)


CSS = """
:root{--ink:#1d2530;--muted:#6a7482;--line:#dfe4ea;--bg:#f5f7fa;--card:#ffffff;--accent:#2563eb;}
*{box-sizing:border-box;}
body{margin:0;font-family:"Segoe UI",system-ui,Arial,sans-serif;background:var(--bg);color:var(--ink);line-height:1.45;}
header{background:linear-gradient(135deg,#1e3a5f,#2563eb);color:#fff;padding:2rem 2.5rem;}
header h1{margin:0 0 .4rem;font-size:1.6rem;}
header p{margin:.15rem 0;font-size:.9rem;opacity:.92;}
main{max-width:1180px;margin:0 auto;padding:1.5rem 2rem 3rem;}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:1.4rem 1.6rem;margin:1.2rem 0;box-shadow:0 1px 3px rgba(20,30,50,.06);}
h2{margin:0 0 1rem;font-size:1.25rem;border-bottom:2px solid var(--accent);padding-bottom:.4rem;display:inline-block;}
h3{font-size:1.05rem;margin:1.2rem 0 .5rem;}
table{border-collapse:collapse;width:100%;font-size:.88rem;margin:.6rem 0;}
th,td{border:1px solid var(--line);padding:.45rem .6rem;text-align:left;vertical-align:top;}
th{background:#eef2f7;font-weight:600;}
tr:nth-child(even) td{background:#fafbfc;}
.badge{display:inline-block;padding:.15rem .6rem;border-radius:999px;font-size:.75rem;font-weight:700;letter-spacing:.03em;}
.badge-pass{background:#d7f5df;color:#177245;border:1px solid #8fdca8;}
.badge-fail{background:#fde0e0;color:#b3261e;border:1px solid #f1a5a0;}
.badge-skip{background:#eceff3;color:#6a7482;border:1px solid #d3d9e0;}
.muted{color:var(--muted);}
.chart{margin:.8rem 0;overflow-x:auto;}
.chart figcaption{font-weight:600;margin-bottom:.3rem;}
.chart svg{max-width:100%;height:auto;}
.bar{fill:#2563eb;}
.bar-val{font-size:10px;fill:#1d2530;}
.bar-label{font-size:10px;fill:#6a7482;}
.bar-na{font-size:10px;fill:#b3261e;}
.model-card{border:1px solid var(--line);border-radius:8px;padding:1rem 1.2rem;margin:1rem 0;background:#fdfdfe;}
.kv{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:.35rem 1.2rem;font-size:.88rem;margin:.4rem 0;}
.kv div b{color:var(--muted);font-weight:600;}
#viewer-container{width:100%;height:520px;border:1px solid var(--line);border-radius:8px;background:#f5f7fa;overflow:hidden;}
.viewer-note{background:#fff8e1;border:1px solid #f0d980;color:#7a5d00;padding:.5rem .8rem;border-radius:6px;font-size:.85rem;margin:.6rem 0;}
select{font-size:.95rem;padding:.35rem .6rem;margin:.4rem 0;}
details{margin:1rem 0;}
details summary{cursor:pointer;font-weight:600;}
pre{white-space:pre-wrap;word-break:break-all;background:#0f172a;color:#dbe4f0;padding:1rem;border-radius:8px;font-size:.78rem;max-height:520px;overflow:auto;}
code{background:#eef2f7;padding:.05rem .3rem;border-radius:4px;font-size:.85em;}
"""

VIEWER_JS = """
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const container = document.getElementById('viewer-container');
const select = document.getElementById('glb-select');
const status = document.getElementById('viewer-status');
const scene = new THREE.Scene();
scene.background = new THREE.Color(0xf5f7fa);
const camera = new THREE.PerspectiveCamera(50, 1, 0.01, 100000);
const renderer = new THREE.WebGLRenderer({ antialias: true });
container.appendChild(renderer.domElement);
function resize() {
  const w = container.clientWidth, h = container.clientHeight || 520;
  renderer.setSize(w, h);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
resize();
window.addEventListener('resize', resize);
const controls = new OrbitControls(camera, renderer.domElement);
scene.add(new THREE.HemisphereLight(0xffffff, 0x8899aa, 1.15));
const dirLight = new THREE.DirectionalLight(0xffffff, 1.4);
dirLight.position.set(6, 12, 8);
scene.add(dirLight);
const grid = new THREE.GridHelper(100, 100, 0xbbc3cc, 0xe2e6ea);
scene.add(grid);
const loader = new GLTFLoader();
let current = null;
function frameObject(object) {
  const box = new THREE.Box3().setFromObject(object);
  if (box.isEmpty()) { status.textContent = 'El GLB no contiene geometría visible.'; return; }
  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  const radius = Math.max(size.x, size.y, size.z, 1);
  controls.target.copy(center);
  camera.position.set(center.x + radius * 0.9, center.y + radius * 0.7, center.z + radius * 0.9);
  camera.near = radius / 1000;
  camera.far = radius * 100;
  camera.updateProjectionMatrix();
  grid.position.y = box.min.y;
  grid.scale.setScalar(Math.max(radius / 50, 0.05));
}
function loadModel(id) {
  if (current) { scene.remove(current); current = null; }
  const b64 = GLB_DATA[id];
  if (!b64) { status.textContent = 'Sin datos GLB para ' + id; return; }
  status.textContent = 'Cargando ' + id + '…';
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  loader.parse(bytes.buffer, '', (gltf) => {
    current = gltf.scene;
    scene.add(current);
    frameObject(current);
    status.textContent = id + ' cargado (' + (bytes.length / 1048576).toFixed(2) + ' MB). Arrastra para orbitar, rueda para zoom.';
  }, (err) => {
    console.error(err);
    status.textContent = 'Error al parsear el GLB de ' + id + ': ' + (err && err.message ? err.message : err);
  });
}
select.addEventListener('change', () => loadModel(select.value));
if (select.value) loadModel(select.value);
(function animate() {
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
})();
"""

CDN_FAIL_JS = """
window.addEventListener('error', function (event) {
  var status = document.getElementById('viewer-status');
  if (status && !status.textContent) {
    status.textContent = 'No se pudo cargar three.js desde el CDN. ¿Sin internet? ' +
      'Los datos del reporte (tablas y gráficos) funcionan offline.';
  }
}, true);
setTimeout(function () {
  var status = document.getElementById('viewer-status');
  var canvas = document.querySelector('#viewer-container canvas');
  if (status && !canvas) {
    status.textContent = 'three.js no se cargó desde el CDN (¿sin internet?). ' +
      'Las tablas y gráficos del reporte funcionan offline.';
  }
}, 5000);
"""


def render_html(run_id: str, hardware: dict, model_reports: list[dict], demo: dict, evidence: Path) -> str:
    generated_at = time.strftime("%Y-%m-%d %H:%M:%S")
    parts: list[str] = []
    parts.append("<!DOCTYPE html><html lang=\"es\"><head><meta charset=\"utf-8\">")
    parts.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    parts.append("<title>Reporte visual del motor IFC</title>")
    parts.append(f"<style>{CSS}</style></head><body>")

    # Header
    parts.append("<header><h1>Reporte visual del motor IFC</h1>")
    parts.append(f"<p>Fecha de generación: {esc(generated_at)} &middot; Run ID: <code>{esc(run_id)}</code></p>")
    parts.append(
        "<p>Hardware: "
        + esc(f"{hardware.get('machine','?')} · {hardware.get('os','?')} · CPU {hardware.get('cpu','?')} · "
              f"{hardware.get('logical_cores','?')} núcleos · Python {hardware.get('python','?')} · "
              f"ifcopenshell {hardware.get('ifcopenshell','?')}")
        + "</p>"
    )
    parts.append("<p>Generado por <code>benchmarks/visual_report.py</code> a través del supervisor JSON-RPC real de <code>motor_ifc</code>.</p>")
    parts.append("</header><main>")

    # Executive summary
    parts.append("<section><h2>Resumen ejecutivo</h2>")
    parts.append("<table><thead><tr><th>Modelo</th><th>Auditoría</th><th>Reparación</th><th>Extracción rich</th><th>GLB</th></tr></thead><tbody>")
    for report in model_reports:
        audit = report.get("audit") or {}
        repair = report.get("repair")
        extract = report.get("extract") or {}
        glb = report.get("glb") or {}
        if repair is None:
            repair_badge = badge(None, "N/A")
        else:
            repair_badge = badge(bool(repair.get("success")))
        parts.append(
            f"<tr><td><code>{esc(report['model_id'])}</code></td>"
            f"<td>{badge(audit.get('success') if audit else False)}</td>"
            f"<td>{repair_badge}</td>"
            f"<td>{badge(extract.get('success') if extract else False)}</td>"
            f"<td>{badge(glb.get('success') if glb else False)}</td></tr>"
        )
    demo_compile = demo.get("compile") or {}
    demo_extract = demo.get("extract") or {}
    demo_glb = demo.get("glb") or {}
    parts.append(
        "<tr><td><code>compile-demo</code> (sintético)</td>"
        f"<td colspan=\"2\">compile: {badge(demo_compile.get('success') if demo_compile else False)}</td>"
        f"<td>{badge(demo_extract.get('success') if demo_extract else False)}</td>"
        f"<td>{badge(demo_glb.get('success') if demo_glb else False)}</td></tr>"
    )
    parts.append("</tbody></table></section>")

    # SVG charts
    parts.append("<section><h2>Rendimiento de extracción (rich)</h2>")
    time_entries = []
    rss_entries = []
    for report in model_reports:
        extract = report.get("extract") or {}
        elapsed = extract.get("worker_elapsed_ms") or extract.get("client_elapsed_ms")
        time_entries.append((report["model_id"], elapsed / 1000.0 if elapsed else None))
        rss = extract.get("rss_peak")
        rss_entries.append((report["model_id"], rss / 1048576.0 if rss else None))
    parts.append(svg_bars(time_entries, "Tiempo de extracción por modelo", "s"))
    parts.append(svg_bars(rss_entries, "RSS pico del worker por modelo", "MB"))
    parts.append("</section>")

    # Per-model cards
    parts.append("<section><h2>Fichas por modelo</h2>")
    for report in model_reports:
        parts.append(f'<div class="model-card"><h3><code>{esc(report["model_id"])}</code></h3>')
        if report.get("error"):
            parts.append(f'<p class="muted">No se pudo procesar: {esc(report["error"])} (<code>{esc(report.get("source"))}</code>)</p></div>')
            continue
        audit = report.get("audit") or {}
        repair = report.get("repair")
        extract = report.get("extract") or {}
        glb = report.get("glb") or {}
        schema = extract.get("source_schema") or audit.get("source_schema")
        parts.append('<div class="kv">')
        parts.append(f"<div><b>Tamaño IFC:</b> {fmt_bytes(report.get('ifc_bytes'))}</div>")
        parts.append(f"<div><b>Esquema:</b> {esc(schema or '—')}</div>")
        parts.append(f"<div><b>Entidades:</b> {esc(extract.get('entity_count') if extract.get('entity_count') is not None else '—')}</div>")
        parts.append(f"<div><b>Fuente reparada:</b> {'sí' if report.get('used_repaired') else 'no'}</div>")
        parts.append(f"<div><b>Extracción:</b> {fmt_s(extract.get('worker_elapsed_ms') or extract.get('client_elapsed_ms'))}</div>")
        parts.append(f"<div><b>RSS pico:</b> {fmt_mb(extract.get('rss_peak'))}</div>")
        artifact_bytes = extract.get("artifact_bytes")
        parts.append(f"<div><b>extraction.json:</b> {fmt_bytes(artifact_bytes)}"
                     + (f" <code>{esc((extract.get('sha256') or '')[:12])}</code>" if extract.get("sha256") else "")
                     + "</div>")
        glb_sha = (glb.get("sha256") or "")[:12]
        parts.append(f"<div><b>GLB:</b> {fmt_bytes(glb.get('bytes'))}"
                     + (f" <code>{esc(glb_sha)}</code>" if glb_sha else "")
                     + "</div>")
        parts.append("</div>")

        # Audit defects
        parts.append("<h3>Auditoría</h3>")
        if audit.get("success"):
            parts.append(
                f"<p>Defectos: <b>{esc(audit.get('defect_count', 0))}</b> "
                f"(reparables: {esc(audit.get('repairable_count', 0))}, manuales: {esc(audit.get('manual_count', 0))}).</p>"
            )
            if audit.get("top_classes"):
                parts.append("<p>Clases con más defectos: " + ", ".join(f"<code>{esc(k)}</code> ({n})" for k, n in audit["top_classes"]) + ".</p>")
            if audit.get("strategies"):
                parts.append("<p>Estrategias: " + ", ".join(f"<code>{esc(k)}</code> ({n})" for k, n in audit["strategies"]) + ".</p>")
        else:
            parts.append(f'<p class="muted">Auditoría fallida: {esc(audit.get("error") or audit.get("diagnostics") or "desconocido")}</p>')

        # Repair
        if repair is not None:
            parts.append("<h3>Reparación</h3>")
            if repair.get("success"):
                parts.append(
                    f"<p>Reparado: <b>{'sí' if repair.get('repaired') else 'no'}</b>; "
                    f"defectos corregidos: {esc(repair.get('defects_fixed', 0))}.</p>"
                )
                fixes = repair.get("sample_fixes") or []
                if fixes:
                    parts.append("<ul>" + "".join(
                        f"<li><code>{esc(f.get('ifc_class'))}</code> #{esc(f.get('step_id'))}: {esc(f.get('rule'))} → {esc(f.get('repair_strategy'))}</li>"
                        for f in fixes
                    ) + "</ul>")
            else:
                parts.append(f'<p class="muted">Reparación fallida: {esc(repair.get("error") or repair.get("diagnostics") or "desconocido")}</p>')

        # Quantity samples
        quantities = extract.get("quantity_samples") or []
        parts.append(f"<h3>Muestra de cantidades ({len(quantities)})</h3>")
        if quantities:
            parts.append("<table><thead><tr><th>Entidad</th><th>Clase</th><th>Conjunto</th><th>Cantidad</th><th>Valor</th><th>Unidad</th><th>Valor SI</th></tr></thead><tbody>")
            for q in quantities:
                parts.append(
                    f"<tr><td>{esc(q.get('entity'))}</td><td><code>{esc(q.get('ifc_class'))}</code></td>"
                    f"<td>{esc(q.get('set'))} <span class=\"muted\">({esc(q.get('set_source'))})</span></td>"
                    f"<td>{esc(q.get('name'))}</td><td>{esc(q.get('value'))}</td>"
                    f"<td>{esc(q.get('unit') or '—')}</td>"
                    f"<td>{esc(q.get('normalized_value') if q.get('normalized_value') is not None else '—')}</td></tr>"
                )
            parts.append("</tbody></table>")
        else:
            parts.append('<p class="muted">Sin cantidades muestreadas.</p>')

        # Material samples
        materials = extract.get("material_samples") or []
        parts.append(f"<h3>Muestra de materiales ({len(materials)})</h3>")
        if materials:
            parts.append("<table><thead><tr><th>Entidad</th><th>Clase</th><th>Asociación</th><th>Nombre</th><th>Materiales</th></tr></thead><tbody>")
            for m in materials:
                parts.append(
                    f"<tr><td>{esc(m.get('entity'))}</td><td><code>{esc(m.get('ifc_class'))}</code></td>"
                    f"<td><code>{esc(m.get('kind'))}</code> <span class=\"muted\">({esc(m.get('source'))})</span></td>"
                    f"<td>{esc(m.get('name') or '—')}</td><td>{esc(', '.join(m.get('materials') or []) or '—')}</td></tr>"
                )
            parts.append("</tbody></table>")
        else:
            parts.append('<p class="muted">Sin materiales muestreados.</p>')
        parts.append("</div>")

    # compile-demo card
    parts.append('<div class="model-card"><h3><code>compile-demo</code> (ciclo completo authoring → IFC → GLB → extracción)</h3>')
    compile_info = demo.get("compile") or {}
    demo_extract = demo.get("extract") or {}
    demo_glb = demo.get("glb") or {}
    parts.append('<div class="kv">')
    parts.append(f"<div><b>Compilación:</b> {'PASS' if compile_info.get('success') else 'FAIL'}</div>")
    parts.append(f"<div><b>IFC generado:</b> {fmt_bytes(compile_info.get('ifc_bytes'))}"
                 + (f" <code>{esc((compile_info.get('ifc_sha256') or '')[:12])}</code>" if compile_info.get("ifc_sha256") else "") + "</div>")
    parts.append(f"<div><b>Esquema:</b> {esc(demo_extract.get('source_schema') or '—')}</div>")
    parts.append(f"<div><b>Entidades extraídas:</b> {esc(demo_extract.get('entity_count') if demo_extract.get('entity_count') is not None else '—')}</div>")
    parts.append(f"<div><b>GLB:</b> {fmt_bytes(demo_glb.get('bytes'))}</div>")
    parts.append(f"<div><b>Huella semántica:</b> <code>{esc((compile_info.get('semantic_fingerprint') or '—')[:16])}</code></div>")
    parts.append("</div>")
    parts.append("<p class=\"muted\">Snapshot <code>authoring.v1</code> / <code>building.architecture@1</code>: 1 planta, 3 muros rectangulares. "
                 "El IFC se compila con <code>authoring.compile.v1</code>, se convierte a GLB con <code>viewer.convert.v1</code> "
                 "y se re-extrae con <code>reader.extract.v2</code> (rich).</p>")
    parts.append("</div></section>")

    # 3D viewer — GLBs embebidos como base64 para que funcione al abrir con file://
    # (los navegadores bloquean fetch/XHR de archivos locales desde file://).
    glb_dir = evidence.parent / "glb"
    glb_ids = [r["model_id"] for r in model_reports if (r.get("glb") or {}).get("success")]
    if (demo.get("glb") or {}).get("success"):
        glb_ids.append("compile-demo")
    glb_data: dict[str, str] = {}
    for mid in glb_ids:
        glb_file = glb_dir / f"{mid}.glb"
        if glb_file.exists() and glb_file.stat().st_size > 0:
            glb_data[mid] = base64.b64encode(glb_file.read_bytes()).decode("ascii")
    parts.append("<section><h2>Visor 3D</h2>")
    parts.append('<div class="viewer-note">Los GLB van embebidos en este archivo (funciona con doble clic, sin servidor). '
                 'Solo el motor three.js viene de un CDN: el visor 3D requiere internet; las tablas y gráficos funcionan offline.</div>')
    if glb_data:
        parts.append('<label for="glb-select">Modelo: </label><select id="glb-select">')
        for mid in glb_data:
            parts.append(f'<option value="{esc(mid)}">{esc(mid)}</option>')
        parts.append('</select> <span id="viewer-status" class="muted"></span><div id="viewer-container"></div>')
        glb_json = json.dumps(glb_data, separators=(",", ":"))
        parts.append(f'<script>const GLB_DATA = {glb_json};</script>')
        parts.append('<script type="importmap">{"imports":{"three":"https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js","three/addons/":"https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"}}</script>')
        parts.append(f'<script type="module">{VIEWER_JS}</script>')
        parts.append(f'<script>{CDN_FAIL_JS}</script>')
    else:
        parts.append('<p class="muted">No se generó ningún GLB; el visor no está disponible.</p>')
    parts.append("</section>")

    # Raw evidence
    parts.append("<section><h2>Evidencia cruda</h2><details><summary>JSON de evidencia embebido</summary>")
    raw = json.dumps(
        {"run_id": run_id, "generated_at": generated_at, "hardware": hardware, "models": model_reports, "compile_demo": demo},
        indent=2, ensure_ascii=False, default=str,
    )
    parts.append(f"<pre>{esc(raw)}</pre></details>")
    parts.append(f'<p class="muted">Evidencia JSONL por fase: <code>{esc(str(evidence))}</code></p>')
    parts.append("</section></main></body></html>")
    return "".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description="Genera el reporte HTML visual del motor IFC.")
    parser.add_argument("--run-id", default="visual-2026-08-11")
    parser.add_argument("--models", default=None, help="IDs separados por coma (por defecto: todos).")
    parser.add_argument("--timeout-ms", type=int, default=600000)
    parser.add_argument("--html-only", action="store_true",
                        help="Regenera solo el HTML desde <run-dir>/report-data.json (sin ejecutar el pipeline).")
    args = parser.parse_args()

    run_dir = REPO / "benchmarks" / "results" / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "glb").mkdir(parents=True, exist_ok=True)
    evidence = run_dir / "visual.jsonl"

    if args.html_only:
        data = json.loads((run_dir / "report-data.json").read_text(encoding="utf-8"))
        REPORT_HTML.write_text(
            render_html(args.run_id, data["hardware"], data["models"], data["compile_demo"], evidence),
            encoding="utf-8",
        )
        print(json.dumps({"html": str(REPORT_HTML), "mode": "html-only"}), flush=True)
        return

    evidence.unlink(missing_ok=True)  # idempotent re-run of the same run-id

    if args.models:
        requested = {item.strip() for item in args.models.split(",") if item.strip()}
        unknown = requested - set(MODELS)
        if unknown:
            print(json.dumps({"error": "unknown-models", "models": sorted(unknown)}), flush=True)
            raise SystemExit(2)
        selected = [model_id for model_id in EXECUTION_ORDER if model_id in requested]
    else:
        selected = list(EXECUTION_ORDER)

    hardware = hardware_fingerprint()
    (run_dir / "hardware.json").write_text(json.dumps(hardware, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"phase": "hardware", **hardware}), flush=True)

    model_reports: list[dict] = []
    for model_id in selected:
        started = time.monotonic()
        report = run_model(model_id, MODELS[model_id]["source"], args.run_id, run_dir, evidence, args.timeout_ms)
        model_reports.append(report)
        extract = report.get("extract") or {}
        print(json.dumps({
            "done": model_id,
            "extract_success": extract.get("success"),
            "entity_count": extract.get("entity_count"),
            "glb": bool((report.get("glb") or {}).get("success")),
            "wall_s": round(time.monotonic() - started, 1),
        }), flush=True)

    demo = run_compile_demo(args.run_id, run_dir, evidence, args.timeout_ms)
    print(json.dumps({
        "done": "compile-demo",
        "compile": bool((demo.get("compile") or {}).get("success")),
        "glb": bool((demo.get("glb") or {}).get("success")),
        "entity_count": (demo.get("extract") or {}).get("entity_count"),
    }), flush=True)

    report_data = {"run_id": args.run_id, "hardware": hardware, "models": model_reports, "compile_demo": demo}
    (run_dir / "report-data.json").write_text(
        json.dumps(report_data, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8"
    )
    REPORT_HTML.write_text(render_html(args.run_id, hardware, model_reports, demo, evidence), encoding="utf-8")
    print(json.dumps({"html": str(REPORT_HTML), "run_dir": str(run_dir)}), flush=True)


if __name__ == "__main__":
    main()
