"""Synthetic IFC4X3 cameras and a matching COBie writer; no external data."""
import json
from pathlib import Path
import uuid

import ifcopenshell
from ifcopenshell.api import run
import ifcopenshell.util.element as element
import numpy as np

NS = uuid.uuid5(uuid.NAMESPACE_URL, "urn:usdaeco:cctv:fixture")


def build_baseline(output, millimetres=False, *, architecture=False, schema="IFC4X3"):
    """Three cameras: fixed, four-head, PTZ with two presets; two pictures.

    Length-valued IFC fields use project units. Focal lengths are numbers
    in optical mm and PanHorizontal deliberately has IFC's length type.
    Stable UUIDs permit cross-unit/route comparisons and shared level names.
    """
    model = ifcopenshell.file(schema=schema)
    scale = 1000. if millimetres else 1.

    def create(ifc_class, name, **kw):
        obj = run("root.create_entity", model, ifc_class=ifc_class, name=name, **kw)
        obj.GlobalId = ifcopenshell.guid.compress(uuid.uuid5(NS, ifc_class + ":" + name).hex)
        return obj

    project = create("IfcProject", "Camera fixture")
    run("unit.assign_unit", model, length={"is_metric": True, "raw": "MILLIMETERS" if millimetres else "METERS"})
    # Revit/IFC project angles are degrees, not radians.
    angle = run("unit.add_conversion_based_unit", model, name="degree")
    project.UnitsInContext.Units = tuple(project.UnitsInContext.Units) + (angle,)
    context = run("context.add_context", model, context_type="Model")
    body = run("context.add_context", model, context_type="Model", context_identifier="Body",
               target_view="MODEL_VIEW", parent=context)
    facility = create("IfcBuilding", "Facility")
    level = create("IfcBuildingStorey", "Level 0")
    level.Elevation = 0.
    run("aggregate.assign_object", model, relating_object=project, products=[facility])
    run("aggregate.assign_object", model, relating_object=facility, products=[level])
    for obj in (facility, level):
        run("geometry.edit_object_placement", model, product=obj, matrix=np.eye(4))

    def pset(obj, name, values):
        ps = run("pset.add_pset", model, product=obj, name=name)
        props = []
        for key, value in values.items():
            typ = "IfcBoolean" if isinstance(value, bool) else "IfcInteger" if isinstance(value, int) else "IfcReal" if isinstance(value, float) else "IfcLabel"
            if isinstance(value, tuple):
                typ, value = value
            props.append(model.create_entity("IfcPropertySingleValue", Name=key,
                NominalValue=model.create_entity(typ, value)))
        ps.HasProperties = props
        return ps

    def place(obj, x, y, z, dimensions):
        run("spatial.assign_container", model, relating_structure=level, products=[obj])
        matrix = np.eye(4)
        matrix[:3, 3] = (x, y, z)
        run("geometry.edit_object_placement", model, product=obj, matrix=matrix)
        rep = run("geometry.add_wall_representation", model, context=body,
                  length=dimensions[0], thickness=dimensions[1], height=dimensions[2])
        run("geometry.assign_representation", model, product=obj, representation=rep)

    if architecture:
        for i in range(3):
            door = create("IfcDoor", "Door %d" % i)
            place(door, i * 10 + 2.5, 0., 0., (.1, .9, 2.1))
        model.write(str(output))
        return model

    cameras = []
    for i, name in enumerate(("Fixed", "Four head", "PTZ")):
        cls = "IfcAudioVisualAppliance" if i != 1 else "IfcBuildingElementProxy"
        camera = create(cls, name, predefined_type="CAMERA" if i != 1 else "NOTDEFINED")
        typ = create(cls + "Type", name + " type", predefined_type="CAMERA" if i != 1 else "NOTDEFINED")
        run("type.assign_type", model, related_objects=[camera], relating_type=typ)
        place(camera, i * 10., 0., 2.8, (.15, .15, .15))
        pset(typ, "Pset_AudioVisualApplianceTypeCamera", {
            "IsOutdoors": i == 2, "VideoResolutionWidth": 2592, "VideoResolutionHeight": 1944})
        pset(typ, "Optical data", {
            "FOV Focal Length Minimum": 3., "FOV Focal Length Maximum": 90. if i == 2 else 8.5,
            "FOV Horizontal Maximum": ("IfcPlaneAngleMeasure", 104.),
            "FOV Horizontal Minimum": ("IfcPlaneAngleMeasure", 3. if i == 2 else 34.),
            "FOV Vertical Maximum": ("IfcPlaneAngleMeasure", 76.),
            "FOV Vertical Minimum": ("IfcPlaneAngleMeasure", 2. if i == 2 else 26.),
            "FOV Horizontal Resolution": 2592, "FOV Vertical Resolution": 1944,
            "Origin Horizontal": ("IfcLengthMeasure", .1 * scale),
            "Origin Vertical": ("IfcLengthMeasure", -.05 * scale),
            "Placement ID": (17160, 17161, 17165)[i], "PTZ": i == 2,
            "Vendor Note": "Keep type quarantine"})
        standard = {"TiltHorizontal": ("IfcPlaneAngleMeasure", -30.),
                    "PanHorizontal": ("IfcLengthMeasure", 15.), "Zoom": 4.}
        if i == 0:
            pset(camera, "Pset_AudioVisualApplianceTypeCamera", standard)
        own = {"FOV Pan" if i != 1 else "FOV Camera Rotation": 25.,
               "FOV Tilt" if i != 1 else "FOV Camera Tilt": 35.,
               "FOV Desired Focal Length": 5., "FOV Actual Focal Length": 5.,
               "FOV Distance to Object": ("IfcLengthMeasure", 18. * scale),
               "FOV Target Pixel Density": 180, "Corridor Format": i == 0,
               "Scenario": "DOOR", "Vendor Note": "Keep occurrence quarantine",
               "Detect": False, "Observe": False, "Recognize": True, "Identify": False}
        if i in (1, 2):
            for n in range(1, 5 if i == 1 else 3):
                own.update({"FOV %d Pan" % n: float((n - 1) * 90),
                            "FOV %d Tilt" % n: 30., "FOV %d Desired Focal Length" % n: 4. + n})
                if i == 2:
                    own["Preset %d" % n] = True
        pset(camera, "Pset_CameraProject", own)
        cameras.append(camera)
    for i, name in enumerate(("FOV-AXIS fixture", "AXIS 2D Symbol fixture")):
        sub = create("IfcBuildingElementProxy", name)
        place(sub, i * 10. + .1, 0., 2.75, (.01, .01, .01))
        pset(sub, "Pset_Source", {"SuperComponent": cameras[i].GlobalId, "Unmapped": "Keep"})
    pole = create("IfcBuildingElementProxy", "Pole")
    place(pole, 40., 0., 0., (.1, .1, 3.))
    model.write(str(output))
    return model


def write_cobie(model_or_path, output, *, oracle=False):
    """Write the same source facts into Component/Type/Attribute sheets."""
    from openpyxl import Workbook
    import ifcopenshell.util.unit as unit
    model = ifcopenshell.open(str(model_or_path)) if isinstance(model_or_path, (str, Path)) else model_or_path
    book = Workbook()
    book.remove(book.active)
    components, types, attrs = (book.create_sheet(n) for n in ("Component", "Type", "Attribute"))
    components.append(["Name", "TypeName", "ExtIdentifier", "ExtObject"])
    types.append(["Name", "ExtIdentifier", "ExtObject"])
    attrs.append(["Name", "SheetName", "RowName", "Value", "Unit", "Category"])
    seen = set()

    def properties(obj, sheet):
        for ps, vals in element.get_psets(obj, should_inherit=False, verbose=True).items():
            for k, data in vals.items():
                if k == "id":
                    continue
                prop = model.by_id(data["id"])
                value = data["value"]
                if isinstance(value, (dict, list)):
                    value = json.dumps(value)
                nominal = getattr(prop, "NominalValue", None)
                length = nominal and "LengthMeasure" in nominal.is_a()
                units = "m" if length and unit.calculate_unit_scale(model) == 1 else "mm"
                attrs.append([k, sheet, obj.Name, value, units, ps])
    for obj in model.by_type("IfcElement"):
        typ = element.get_type(obj)
        components.append([obj.Name, typ.Name if typ else "", obj.GlobalId, obj.is_a()])
        properties(obj, "Component")
        if oracle and typ and obj.Name in ("Fixed", "Four head", "PTZ"):
            # Independent host formula oracle for the first head/preset. The
            # ordinary fixture writer remains an exact copy of source facts.
            import math
            optics = element.get_pset(typ, "Optical data")
            state = element.get_pset(obj, "Pset_CameraProject")
            standard = element.get_pset(obj, "Pset_AudioVisualApplianceTypeCamera") or {}
            focal = standard.get("Zoom", state.get("FOV 1 Desired Focal Length", state["FOV Desired Focal Length"]))
            lo, hi = (optics["FOV Focal Length " + n] for n in ("Minimum", "Maximum"))
            w0 = 2 * lo * math.tan(math.radians(optics["FOV Horizontal Maximum"] / 2))
            w1 = 2 * hi * math.tan(math.radians(optics["FOV Horizontal Minimum"] / 2))
            half = math.atan((w0 + (w1 - w0) * (focal - lo) / (hi - lo)) / (2 * focal))
            far = state["FOV Distance to Object"] * unit.calculate_unit_scale(model)
            attrs.append(["Horizontal Angle", "Component", obj.Name, math.degrees(half), "degree", "Host observation"])
            attrs.append(["FOV Distance to Object", "Component", obj.Name, far, "m", "Host observation"])
            for band, density in (("Det", 26), ("Obs", 62), ("Rec", 125), ("Id", 250), ("UD", 180)):
                radius = min(far, optics["FOV Horizontal Resolution"] / (2 * half * density))
                attrs.append(["RG_Length_" + band, "Component", obj.Name, radius, "m", "Host observation"])
        if typ and typ.id() not in seen:
            types.append([typ.Name, typ.GlobalId, typ.is_a()])
            properties(typ, "Type")
            seen.add(typ.id())
    book.save(output)
    book.close()


def convert(source, destination, *, geometry=True):
    """Reference converter in a fresh process: geometry before importing pxr."""
    import os
    import subprocess
    import sys
    from usdaeco_cctv import core_root
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    kit = Path(os.environ.get("TOOLCHAIN_DIR", core_root().parent / "usdaeco-toolchain"))
    code = "import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); sys.path.insert(0,sys.argv.pop(1)); runpy.run_module('usdaeco_ifc.convert',run_name='__main__')"
    return subprocess.run([sys.executable, "-c", code, str(kit / "tools"), str(Path(os.environ.get("AECO_IFC_ROOT", core_root().parent / "usdaeco-ifc")) / "tools"), str(source),
                           "-o", str(destination), *([] if geometry else ["--no-geometry"])],
                          env=env, text=True, capture_output=True, check=True)
