"""Narrow IfcOpenShell 0.8.5 GLB serializer adapter."""
from __future__ import annotations

import importlib
from pathlib import Path


class ViewerRuntimeUnavailable(RuntimeError):
    pass


def serialize(source: Path, target: Path) -> str:
    """Serialize one IFC to GLB without invoking an external process."""
    with source.open("rb") as stream:
        header = stream.read(64).lstrip(b"\xef\xbb\xbf \t\r\n")
        stream.seek(max(0, source.stat().st_size - 64))
        trailer = stream.read().rstrip(b" \t\r\n")
    if not header.startswith(b"ISO-10303-21;") or not trailer.endswith(b"END-ISO-10303-21;"):
        raise ValueError("source is not an IFC STEP physical file")
    try:
        ifc = importlib.import_module("ifcopenshell")
        geom = importlib.import_module("ifcopenshell.geom")
        wrapper = importlib.import_module("ifcopenshell.ifcopenshell_wrapper")
    except ImportError as exc:
        raise ViewerRuntimeUnavailable("IfcOpenShell is not installed") from exc
    if getattr(ifc, "version", None) != "0.8.5":
        raise ViewerRuntimeUnavailable("IfcOpenShell runtime version is unsupported")

    model = ifc.open(str(source))
    settings = geom.settings()
    settings.set("dimensionality", wrapper.CURVES_SURFACES_AND_SOLIDS)
    settings.set("apply-default-materials", True)
    serializer_settings = geom.serializer_settings()
    serializer_settings.set("use-element-guids", True)
    serializer = geom.serializers.gltf(str(target), settings, serializer_settings)
    serializer.setFile(model)
    serializer.setUnitNameAndMagnitude("METER", 1.0)
    serializer.writeHeader()
    iterator = geom.iterator(settings, model, 1)
    if iterator.initialize():
        while True:
            serializer.write(iterator.get())
            if not iterator.next():
                break
    serializer.finalize()
    return ifc.version
