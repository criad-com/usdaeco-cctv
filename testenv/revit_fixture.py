"""Neutral IFC4X3 reproduction of exported camera symbols and FOV pictures."""
import uuid

import ifcopenshell
from ifcopenshell.api import run
import numpy as np

NS = uuid.uuid5(uuid.NAMESPACE_URL, "urn:usdaeco:cctv:revit-fixture")


def build_revit_fixture(path, *, relation="IfcRelAggregates", distance=.77,
                        optics_change=False, metadata_change=False, anonymous=False):
    model = ifcopenshell.file(schema="IFC4X3")

    def create(cls, key, name=None, **kw):
        obj = run("root.create_entity", model, ifc_class=cls, name=name or key, **kw)
        obj.GlobalId = ifcopenshell.guid.compress(uuid.uuid5(NS, key).hex)
        return obj

    def pset(obj, name, properties):
        ps = run("pset.add_pset", model, product=obj, name=name)
        run("pset.edit_pset", model, pset=ps, properties=properties)

    project = create("IfcProject", "Camera export fixture")
    run("unit.assign_unit", model, length={"is_metric": True, "raw": "METERS"})
    facility = create("IfcBuilding", "Facility")
    level = create("IfcBuildingStorey", "Ground")
    other_level = create("IfcBuildingStorey", "Upper")
    run("aggregate.assign_object", model, relating_object=project, products=[facility])
    run("aggregate.assign_object", model, relating_object=facility, products=[level, other_level])
    for i, obj in enumerate((facility, level, other_level)):
        if obj.is_a("IfcBuildingStorey"):
            obj.Elevation = 6. if i == 2 else 0.
        matrix = np.eye(4)
        matrix[2, 3] = 6. if i == 2 else 0.
        run("geometry.edit_object_placement", model, product=obj, matrix=matrix)

    def place(obj, position):
        run("spatial.assign_container", model, relating_structure=level, products=[obj])
        matrix = np.eye(4)
        matrix[:3, 3] = position
        run("geometry.edit_object_placement", model, product=obj, matrix=matrix)

    cameras = []
    for i in range(2):
        camera = create("IfcAudioVisualAppliance", "Camera %d" % i, predefined_type="CAMERA")
        typ = create("IfcAudioVisualApplianceType", "Symbol type %d" % i,
                     name="Surveillance Camera:Dome", predefined_type="CAMERA")
        run("type.assign_type", model, related_objects=[camera], relating_type=typ)
        pset(typ, "Optics", {"FOV Focal Length Minimum": 3., "FOV Focal Length Maximum": 8.5,
             "FOV Horizontal Maximum": 100. if optics_change and i else 104.,
             "FOV Horizontal Minimum": 34., "FOV Vertical Maximum": 76.,
             "FOV Vertical Minimum": 26., "FOV Horizontal Resolution": 2592,
             "FOV Vertical Resolution": 1944, "PTZ": False,
             "Vendor Note": "Distinct" if metadata_change and i else "Preserve type evidence"})
        pset(camera, "Graphics", {"FOV Pan": float(15 + i * 165), "FOV Tilt": 30.,
             "FOV Desired Focal Length": float(4 + i * 2), "FOV Distance to Object": 12000.,
             "2D Symbol": "AXIS 2D Symbol : Dome"})
        place(camera, (4. * i, 0., 3.))
        cameras.append(camera)
    helper = create("IfcBuildingElementProxy", "Helper",
                    name="Generic graphics" if anonymous else "FOV-AXIS_v80:Default:101")
    place(helper, (0., 0., 3. - distance))
    pset(helper, "Data", {"Focal Length Minimum": 3., "Focal Length Maximum": 8.5})
    pset(helper, "Dimensions", {"FOV Horizontal Max": 104., "FOV Horizontal Min": 34.,
         "FOV Vertical Max": 76., "FOV Vertical Min": 26., "RG_Length_Id": 6000.})
    pset(helper, "Graphics", {"Horizontal Res": 2592, "Vertical Res": 1944, "Focal Length": 4.})
    symbol = create("IfcBuildingElementProxy", "Symbol", name="AXIS 2D Symbol:Dome:102")
    place(symbol, (0., 0., 3.))
    if relation:
        # Both inverse forms appear in real exporters. Keep a second hop so
        # resolving only direct camera children is insufficient.
        run("spatial.unassign_container", model, products=[helper, symbol])
        model.create_entity(relation, GlobalId=ifcopenshell.guid.compress(uuid.uuid5(NS, "owner").hex),
                            RelatingObject=cameras[0], RelatedObjects=[helper])
        model.create_entity("IfcRelNests", GlobalId=ifcopenshell.guid.compress(uuid.uuid5(NS, "nested").hex),
                            RelatingObject=helper, RelatedObjects=[symbol])
    model.write(str(path))
    return model
