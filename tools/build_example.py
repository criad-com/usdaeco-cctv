#!/usr/bin/env python3
"""Rebuild the deterministic lobby example (a 12 x 8 x 3.5 m lobby, three doors, four cameras)."""
import math
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from usdaeco_cctv import register_plugins  # noqa: E402

NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "urn:usdaeco:id:v1")
STAMP = "cctv example 0.1.0"
LENGTH, WIDTH, HEIGHT, WALL = 12.0, 8.0, 3.5, 0.2


def build(output):
    register_plugins()
    from pxr import Gf, Usd, UsdGeom, Vt
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    UsdGeom.SetStageUpAxis(stage, "Z")
    stage.SetMetadata("fallbackPrimTypes", {
        "AecoSite": Vt.TokenArray(["Xform"]), "AecoFacility": Vt.TokenArray(["Xform"]),
        "AecoLevel": Vt.TokenArray(["Xform"]), "AecoSpace": Vt.TokenArray(["Xform"]),
        "AecoSystem": Vt.TokenArray(["Scope"])})
    root = stage.DefinePrim("/CctvLobby", "Xform")
    stage.SetDefaultPrim(root)

    def identity(prim):
        prim.GetAttribute("aeco:id").Set(str(uuid.uuid5(NAMESPACE, str(prim.GetPath()))))

    def classify(prim, code):
        prim.ApplyAPI("AecoClassificationAPI", "ifc")
        prim.GetAttribute("aeco:class:ifc:code").Set(code)

    def spatial(path, kind, code):
        prim = stage.DefinePrim(path, kind)
        identity(prim)
        classify(prim, code)
        return prim

    site = spatial("/CctvLobby/Site", "AecoSite", "IfcSite")
    building = spatial("/CctvLobby/Site/Building", "AecoFacility", "IfcBuilding")
    level = spatial("/CctvLobby/Site/Building/L0", "AecoLevel", "IfcBuildingStorey")
    level.GetAttribute("aeco:elevation").Set(0.0)
    lobby = spatial("/CctvLobby/Site/Building/L0/Lobby", "AecoSpace", "IfcSpace")

    def box(parent, name, size, centre):
        sx, sy, sz = (s / 2 for s in size)
        cx, cy, cz = centre
        points = [(cx - sx, cy - sy, cz - sz), (cx + sx, cy - sy, cz - sz), (cx + sx, cy + sy, cz - sz),
                  (cx - sx, cy + sy, cz - sz), (cx - sx, cy - sy, cz + sz), (cx + sx, cy - sy, cz + sz),
                  (cx + sx, cy + sy, cz + sz), (cx - sx, cy + sy, cz + sz)]
        faces = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
        mesh = UsdGeom.Mesh.Define(stage, parent.GetPath().AppendChild(name))
        mesh.CreatePointsAttr(Vt.Vec3fArray([Gf.Vec3f(*p) for p in points]))
        mesh.CreateFaceVertexCountsAttr(Vt.IntArray([4] * 6))
        mesh.CreateFaceVertexIndicesAttr(Vt.IntArray([i for f in faces for i in f]))
        mesh.CreateSubdivisionSchemeAttr("none")
        mesh.CreateExtentAttr(Vt.Vec3fArray([Gf.Vec3f(cx - sx, cy - sy, cz - sz), Gf.Vec3f(cx + sx, cy + sy, cz + sz)]))
        return mesh

    def element(name, code, size, centre, phase):
        prim = stage.DefinePrim(lobby.GetPath().AppendChild(name), "Xform")
        prim.ApplyAPI("AecoElementAPI")
        identity(prim)
        prim.GetAttribute("aeco:phase").Set(phase)
        classify(prim, code)
        body = box(prim, "Body", size, centre).GetPrim()
        body.ApplyAPI("AecoDerivedGeometryAPI")
        for key, value in (("source", prim.GetAttribute("aeco:id").Get()), ("role", "body"),
                           ("approx", "tessellated"), ("stamp", STAMP)):
            body.GetAttribute("aeco:derived:" + key).Set(value)
        return prim

    h, t = HEIGHT, WALL
    walls = [("WallS_a", (2.5, t, h), (1.25, -t / 2, h / 2)), ("WallS_b", (8.5, t, h), (7.75, -t / 2, h / 2)),
             ("WallN_a", (5.5, t, h), (2.75, WIDTH + t / 2, h / 2)), ("WallN_b", (5.5, t, h), (9.25, WIDTH + t / 2, h / 2)),
             ("WallW", (t, WIDTH, h), (-t / 2, WIDTH / 2, h / 2)), ("WallE_a", (t, 3.5, h), (LENGTH + t / 2, 1.75, h / 2)),
             ("WallE_b", (t, 3.5, h), (LENGTH + t / 2, 6.25, h / 2))]
    for name, size, centre in walls:
        element(name, "IfcWall", size, centre, "existing")
    doors = {"Door_1": element("Door_1", "IfcDoor", (1.0, 0.05, 2.1), (6.0, WIDTH - 0.025, 1.05), "existing"),
             "Door_2": element("Door_2", "IfcDoor", (1.0, 0.05, 2.1), (3.0, 0.025, 1.05), "existing"),
             "Door_3": element("Door_3", "IfcDoor", (0.05, 1.0, 2.1), (LENGTH - 0.025, 4.0, 1.05), "proposed")}
    element("Desk", "IfcFurniture", (3.0, 1.0, 1.1), (6.0, 2.0, 0.55), "proposed")
    element("Column", "IfcColumn", (0.4, 0.4, h), (8.0, 5.0, h / 2), "existing")
    element("CableTray_1", "IfcCableCarrierSegment", (11.0, 0.3, 0.1), (6.0, 6.5, 3.25), "proposed")

    stage.CreateClassPrim("/_TypeCatalog")

    def catalog(name, model, optics, housing=()):
        kind = stage.CreateClassPrim("/_TypeCatalog/" + name)
        kind.ApplyAPI("AecoTypeAPI")
        kind.ApplyAPI("AecoCctvCameraTypeAPI")
        classify(kind, "IfcAudioVisualApplianceType.CAMERA")
        kind.GetAttribute("aeco:type:model").Set(model)
        for key, value in housing:
            kind.GetAttribute("aeco:cctvType:" + key).Set(value)
        head = stage.DefinePrim(kind.GetPath().AppendChild("Sensor_0"), "Camera")
        head.ApplyAPI("AecoCctvSensorAPI")
        for key, value in optics.items():
            head.GetAttribute("aeco:cctvSensor:" + key).Set(Gf.Vec2i(*value) if key == "pixels" else value)
        return kind

    dome = catalog("Dome_P3277", "P3277-class fixed dome, 5 MP varifocal",
                   {"focalRange": (3.0, 8.5), "hfovRange": (104.0, 34.0), "vfovRange": (76.0, 26.0),
                    "pixels": (2592, 1944)}, [("irRange", 30.0)])
    ptz = catalog("Ptz_Q6088", "Q6088-class PTZ dome, 4K, 34x zoom",
                  {"focalRange": (6.64, 225.5), "hfovRange": (60.8, 2.0), "vfovRange": (36.5, 1.1),
                   "pixels": (3840, 2160), "motorised": True, "panRange": (-180.0, 180.0),
                   "tiltRange": (0.0, 90.0)}, [("outdoor", True)])

    cameras = {}

    def camera(name, kind, position, mount, scenario, pose, offset):
        prim = stage.DefinePrim(lobby.GetPath().AppendChild(name), "Xform")
        prim.ApplyAPI("AecoElementAPI")
        identity(prim)
        prim.GetAttribute("aeco:phase").Set("proposed")
        classify(prim, "IfcAudioVisualAppliance.CAMERA")
        prim.GetInherits().AddInherit(kind.GetPath())
        prim.ApplyAPI("AecoCctvCameraAPI")
        prim.GetAttribute("aeco:cctv:mount").Set(mount)
        prim.GetAttribute("aeco:cctv:scenario").Set(scenario)
        UsdGeom.Xformable(prim).AddTranslateOp().Set(Gf.Vec3d(*position))
        sensor = stage.OverridePrim(prim.GetPath().AppendChild("Sensor_0"))
        sensor.GetAttribute("aeco:cctvSensor:offset").Set(Gf.Vec3d(*offset))
        for key, value in pose.items():
            sensor.GetAttribute("aeco:cctvSensor:" + key).Set(value)
        cameras[name] = prim
        return sensor

    # Three ceiling domes, each 2.5 m from its door (the <= 3 m door rule), looking down at the leaf.
    for name, position, pan in (("Cam_1", (6.0, 5.5, 3.3), 90.0), ("Cam_2", (3.0, 2.5, 3.3), -90.0),
                                ("Cam_3", (9.5, 4.0, 3.3), 0.0)):
        camera(name, dome, position, "ceiling", "door",
               {"pan": pan, "tilt": 40.0, "roll": 0.0, "focalLength": 3.0, "range": 14.0}, (0, 0, -0.08))
    # One corner PTZ with three presets and a 16 s tour.
    pivot = (0.3, 7.7, 3.0 - 0.15)
    sensor = camera("Cam_4", ptz, (0.3, 7.7, 3.0), "corner", "lobby",
                    {"pan": -45.0, "tilt": 20.0, "roll": 0.0, "focalLength": 7.0, "range": 20.0}, (0, 0, -0.15))

    def aim(target, focal, dwell, home=False):
        x, y = target
        dx, dy = x - pivot[0], y - pivot[1]
        distance = math.hypot(dx, dy)
        return {"pan": round(math.degrees(math.atan2(dy, dx)), 3),
                "tilt": round(math.degrees(math.atan2(pivot[2] - 1.05, distance)), 3),
                "focalLength": focal, "dwell": dwell, "home": home}
    presets = {"Home": {"pan": -45.0, "tilt": 20.0, "focalLength": 7.0, "dwell": 4.0, "home": True},
               "Door_1": aim((6.0, WIDTH), 12.0, 6.0), "Door_3": aim((LENGTH, 4.0), 16.0, 6.0)}
    for name, values in presets.items():
        sensor.ApplyAPI("AecoCctvPresetAPI", name)
        for key, value in values.items():
            sensor.GetAttribute("aeco:cctvPreset:" + name + ":" + key).Set(value)
    sensor.GetAttribute("aeco:cctvSensor:tour").Set(list(presets))

    system = stage.DefinePrim("/CctvLobby/Cctv", "AecoSystem")
    identity(system)
    classify(system, "IfcDistributionSystem.SECURITY")
    system.ApplyAPI("AecoCctvSystemAPI")
    system.GetAttribute("aeco:cctvSystem:retentionDays").Set(90)
    system.GetAttribute("aeco:cctvSystem:recorderCapacity").Set(45)
    Usd.CollectionAPI(system, "members").GetIncludesRel().SetTargets([p.GetPath() for p in cameras.values()])
    system.GetRelationship("aeco:serves").SetTargets([lobby.GetPath()])

    stage.DefinePrim("/CctvLobby/Analyses", "Scope")
    study = stage.DefinePrim("/CctvLobby/Analyses/DoorCoverage", "Scope")
    study.ApplyAPI("AecoCctvStudyAPI")
    for key, value in (("phases", ["proposed", "existing"]), ("includeUnphased", True), ("requiredDensity", 125.0),
                       ("levelSystem", "dori2015"), ("densityModel", "plane"), ("ptzPolicy", "presetsNotSole"),
                       ("maxTargetDistance", 3.0)):
        study.GetAttribute("aeco:cctvStudy:" + key).Set(value)
    Usd.CollectionAPI(study, "targets").GetIncludesRel().SetTargets([p.GetPath() for p in doors.values()])
    Usd.CollectionAPI(study, "exclusions").GetIncludesRel().SetTargets([])
    stage.GetRootLayer().Export(str(output))
    return stage


if __name__ == "__main__":
    build(Path(__file__).resolve().parents[1] / "examples/lobby.usda")
