"""IFC4 discipline vertical compilers."""
from __future__ import annotations
import importlib, os, shutil, tempfile
from pathlib import Path
from typing import Any
from ._version import VERSION
from .contracts import ArchitectureEnvelope, MepEnvelope, StructureEnvelope
from .identity import global_id

class IfcRuntimeUnavailable(RuntimeError): pass

def runtime():
    try: return importlib.import_module("ifcopenshell")
    except ImportError as exc: raise IfcRuntimeUnavailable("IfcOpenShell is not installed") from exc

def _axis(file, location=(0.0, 0.0, 0.0)):
    point = file.create_entity("IfcCartesianPoint", Coordinates=location)
    z = file.create_entity("IfcDirection", DirectionRatios=(0.0, 0.0, 1.0))
    x = file.create_entity("IfcDirection", DirectionRatios=(1.0, 0.0, 0.0))
    return file.create_entity("IfcAxis2Placement3D", Location=point, Axis=z, RefDirection=x)

def _placement(file, relative_to=None, location=(0.0, 0.0, 0.0)):
    return file.create_entity("IfcLocalPlacement", PlacementRelTo=relative_to, RelativePlacement=_axis(file, location))

def _owner(file):
    person = file.create_entity("IfcPerson", Identification="motor-ifc")
    org = file.create_entity("IfcOrganization", Identification="MAMUT", Name="MAMUT")
    pao = file.create_entity("IfcPersonAndOrganization", ThePerson=person, TheOrganization=org)
    app = file.create_entity("IfcApplication", ApplicationDeveloper=org, Version=VERSION, ApplicationFullName="motor-ifc", ApplicationIdentifier="motor-ifc")
    return file.create_entity("IfcOwnerHistory", OwningUser=pao, OwningApplication=app, ChangeAction="ADDED", CreationDate=0)

def _context(file):
    return file.create_entity("IfcGeometricRepresentationContext", ContextIdentifier="Body", ContextType="Model", CoordinateSpaceDimension=3, Precision=1e-5, WorldCoordinateSystem=_axis(file))

def _units(file):
    length = file.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Name="METRE")
    area = file.create_entity("IfcSIUnit", UnitType="AREAUNIT", Name="SQUARE_METRE")
    volume = file.create_entity("IfcSIUnit", UnitType="VOLUMEUNIT", Name="CUBIC_METRE")
    return file.create_entity("IfcUnitAssignment", Units=(length, area, volume))

def _rel_aggregate(file, owner, parent, child, snapshot, suffix):
    gid=global_id(snapshot.authority.producer,snapshot.model_id,"architecture",suffix,"aggregate")
    file.create_entity("IfcRelAggregates", GlobalId=gid, OwnerHistory=owner, RelatingObject=parent, RelatedObjects=(child,))

def _wall_shape(file, context, wall):
    points = tuple(file.create_entity("IfcCartesianPoint", Coordinates=p) for p in ((0.0,0.0),(wall.length,0.0),(wall.length,wall.thickness),(0.0,wall.thickness)))
    poly = file.create_entity("IfcPolyline", Points=(*points, points[0]))
    profile = file.create_entity("IfcArbitraryClosedProfileDef", ProfileType="AREA", OuterCurve=poly)
    solid = file.create_entity("IfcExtrudedAreaSolid", SweptArea=profile, Position=_axis(file), ExtrudedDirection=file.create_entity("IfcDirection", DirectionRatios=(0.0,0.0,1.0)), Depth=wall.height)
    rep = file.create_entity("IfcShapeRepresentation", ContextOfItems=context, RepresentationIdentifier="Body", RepresentationType="SweptSolid", Items=(solid,))
    return file.create_entity("IfcProductDefinitionShape", Representations=(rep,))

def compile_architecture(snapshot: ArchitectureEnvelope, target: Path) -> dict[str, str]:
    ifc = runtime(); file = ifc.file(schema="IFC4")
    owner = _owner(file); context = _context(file); units = _units(file)
    source_map: dict[str, str] = {}
    identity=(snapshot.authority.producer,snapshot.model_id,"architecture")
    project_gid = global_id(*identity, str(snapshot.model_id), "project")
    project = file.create_entity("IfcProject", GlobalId=project_gid, OwnerHistory=owner, Name=f"Model {snapshot.model_id}", RepresentationContexts=(context,), UnitsInContext=units)
    site = file.create_entity("IfcSite", GlobalId=global_id(*identity, str(snapshot.model_id), "site"), OwnerHistory=owner, Name="Site", CompositionType="ELEMENT", ObjectPlacement=_placement(file))
    building = file.create_entity("IfcBuilding", GlobalId=global_id(*identity, str(snapshot.model_id), "building"), OwnerHistory=owner, Name="Building", CompositionType="ELEMENT", ObjectPlacement=_placement(file, site.ObjectPlacement))
    storey_gid = global_id(*identity, snapshot.payload.storey_source_id, "storey")
    storey = file.create_entity("IfcBuildingStorey", GlobalId=storey_gid, OwnerHistory=owner, Name=snapshot.payload.storey_name, CompositionType="ELEMENT", Elevation=snapshot.payload.elevation, ObjectPlacement=_placement(file, building.ObjectPlacement, (0.0,0.0,snapshot.payload.elevation)))
    source_map[snapshot.payload.storey_source_id] = storey_gid
    _rel_aggregate(file, owner, project, site, snapshot, "project-site"); _rel_aggregate(file, owner, site, building, snapshot, "site-building"); _rel_aggregate(file, owner, building, storey, snapshot, "building-storey")
    walls=[]
    for wall in snapshot.payload.elements:
        gid=global_id(*identity, wall.source_id, "wall")
        product=file.create_entity("IfcWall", GlobalId=gid, OwnerHistory=owner, Name=wall.name, ObjectPlacement=_placement(file, storey.ObjectPlacement, (wall.x,wall.y,wall.z)), Representation=_wall_shape(file, context, wall))
        walls.append(product); source_map[wall.source_id]=gid
    if walls:
        file.create_entity("IfcRelContainedInSpatialStructure", GlobalId=global_id(*identity, snapshot.payload.storey_source_id, "containment"), OwnerHistory=owner, RelatedElements=tuple(walls), RelatingStructure=storey)
    file.write(str(target))
    reopened=ifc.open(str(target))
    if len(reopened.by_type("IfcProject")) != 1 or len(reopened.by_type("IfcBuildingStorey")) != 1:
        raise RuntimeError("reopened IFC failed hierarchy invariant")
    validate = importlib.import_module("ifcopenshell.validate")
    logger = validate.json_logger()
    validate.validate(reopened, logger)
    if any(item.get("level") == "error" for item in logger.statements):
        raise RuntimeError("generated IFC failed schema validation")
    return source_map

def compile_structure(snapshot: StructureEnvelope, target: Path) -> dict[str, str]:
    ifc = runtime(); file = ifc.file(schema="IFC4")
    owner = _owner(file); context = _context(file); units = _units(file)
    identity=(snapshot.authority.producer,snapshot.model_id,"structure")
    file.create_entity("IfcProject", GlobalId=global_id(*identity, str(snapshot.model_id), "project"), OwnerHistory=owner, Name=f"Model {snapshot.model_id}", RepresentationContexts=(context,), UnitsInContext=units)
    classes = {"beam":"IfcBeam", "column":"IfcColumn", "plate":"IfcPlate", "foundation":"IfcFooting"}
    source_map: dict[str, str] = {}
    for member in snapshot.payload.members:
        gid = global_id(*identity, member.source_id, member.member_type)
        file.create_entity(classes[member.member_type], GlobalId=gid, OwnerHistory=owner, Name=member.name)
        source_map[member.source_id] = gid
    file.write(str(target))
    reopened = ifc.open(str(target))
    if len(reopened.by_type("IfcProject")) != 1:
        raise RuntimeError("reopened IFC failed project invariant")
    validate = importlib.import_module("ifcopenshell.validate")
    logger = validate.json_logger()
    validate.validate(reopened, logger)
    if any(item.get("level") == "error" for item in logger.statements):
        raise RuntimeError("generated IFC failed schema validation")
    return source_map

def compile_mep(snapshot: MepEnvelope, target: Path) -> dict[str, str]:
    ifc = runtime(); file = ifc.file(schema="IFC4")
    owner = _owner(file); context = _context(file); units = _units(file)
    identity=(snapshot.authority.producer,snapshot.model_id,"mep")
    file.create_entity("IfcProject", GlobalId=global_id(*identity, str(snapshot.model_id), "project"), OwnerHistory=owner, Name=f"Model {snapshot.model_id}", RepresentationContexts=(context,), UnitsInContext=units)
    source_map: dict[str, str] = {}
    for component in snapshot.payload.components:
        gid = global_id(*identity, component.source_id, f"component-{component.system}")
        file.create_entity("IfcDistributionElement", GlobalId=gid, OwnerHistory=owner, Name=component.name)
        source_map[component.source_id] = gid
    file.write(str(target))
    reopened = ifc.open(str(target))
    if len(reopened.by_type("IfcProject")) != 1:
        raise RuntimeError("reopened IFC failed project invariant")
    validate = importlib.import_module("ifcopenshell.validate")
    logger = validate.json_logger()
    validate.validate(reopened, logger)
    if any(item.get("level") == "error" for item in logger.statements):
        raise RuntimeError("generated IFC failed schema validation")
    return source_map
