#!/usr/bin/env python3
"""Executable acceptance claims for the codeless usdAecoCctv library and its companion tools.

Runs the schema, computation, derivation, validator, example, profile,
vanilla-runtime and hygiene checks and ends with the family's
"N checks, M failed" line. --report writes the claims as JSON.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import traceback

import numpy as np

ROOT = Path(__file__).resolve().parent
TOOLCHAIN = Path(os.environ.get("TOOLCHAIN_DIR", ROOT.parent / "usdaeco-toolchain"))
sys.path[:0] = [str(ROOT / "tools"), str(TOOLCHAIN / "tools")]
from usdaeco_check import (Report, can_apply, link_check, plugin_requires,  # noqa: E402
                           registry_probe, term_sweep, validate_examples)
from usdaeco_check.plugins import check_requirements  # noqa: E402
from usdaeco_cctv import (APIS, camera_type_of, core_root, iter_cameras, iter_studies,  # noqa: E402
                          presets_of, register_plugins, registry, sensors_of)

REPORT = Report()
check = REPORT.check
EXAMPLE = ROOT / "examples/lobby.usda"
DERIVED = ROOT / "examples/lobby.derived.usda"
LOBBY = "/CctvLobby/Site/Building/L0/Lobby/"
DERIVED_SET = {"aeco:cctv:mountHeight", "aeco:cctvType:sensorCount"} | {
    "aeco:cctvSensor:" + n for n in ("hfov", "vfov", "effectiveWidth", "targetRange")} | {
    "aeco:cctvStudy:" + n for n in ("inputHash", "stamp", "unphasedInView", "unclassifiedInView", "exclusionsCovered")} | {
    "aeco:cctvCoverage:" + n for n in ("target", "requiredDensity", "density", "level", "fraction",
                                       "fixedCoverage", "views", "viewNotes", "blockers", "dutyFraction", "nearestViewDistance", "enclosedSamples")}
STATIC_RULES = ("cctvInvalidSampling", "cctvMissingOptics", "cctvUnsupportedTopology", "cctvKindMismatch", "cctvSensorMissing", "cctvSensorOrphan", "cctvOutOfEnvelope",
                "cctvNativeCameraAuthored", "cctvDerivedMismatch", "cctvMountFrame", "cctvSystemCapacity")


def digest(path_or_bytes):
    data = path_or_bytes if isinstance(path_or_bytes, bytes) else Path(path_or_bytes).read_bytes()
    return hashlib.sha256(data).hexdigest()


def fresh():
    """An anonymous copy of the model example."""
    layer = Sdf.Layer.CreateAnonymous("lobby.usda")
    layer.TransferContent(Sdf.Layer.FindOrOpen(str(EXAMPLE)))
    return Usd.Stage.Open(layer)


def stacked():
    """A root edit layer above anonymous copies of the derived and model layers."""
    model = Sdf.Layer.CreateAnonymous("lobby.usda")
    model.TransferContent(Sdf.Layer.FindOrOpen(str(EXAMPLE)))
    derived_copy = Sdf.Layer.CreateAnonymous("cctv.derived.usda")
    derived_copy.TransferContent(Sdf.Layer.FindOrOpen(str(DERIVED)))
    derived_copy.subLayerPaths = []
    root = Sdf.Layer.CreateAnonymous("stack.usda")
    root.subLayerPaths = [derived_copy.identifier, model.identifier]
    for field in ("upAxis", "metersPerUnit", "fallbackPrimTypes", "defaultPrim"):
        root.pseudoRoot.SetInfo(field, model.pseudoRoot.GetInfo(field))
    return Usd.Stage.Open(root), root, derived_copy, model


def findings(stage, profile=None):
    return Counter((e.GetName(), str(e.GetType()).rsplit(".", 1)[-1])
                   for e in validators.validate_stage(stage, include_builtin=False, profile=profile)
                   if e.GetType() != UsdValidation.ValidationErrorType.Info)


def subprocess_python(args, *, cwd=ROOT, keep_plugins=False):
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    if not keep_plugins:
        env.pop("PXR_PLUGINPATH_NAME", None)
        env.pop("PXR_AR_DEFAULT_SEARCH_PATH", None)
    return subprocess.run([sys.executable, *map(str, args)], cwd=cwd, env=env,
                          text=True, capture_output=True, check=True)


def schema_checks():
    plugin = Plug.Registry().GetPluginWithName("usdAecoCctv")
    REPORT.add(registry_probe(APIS))
    manifest = json.loads((ROOT / "library.json").read_text())
    check("codeless manifest declares version, kind tier and the core requirement",
          plugin.isResource and plugin.name == manifest["name"] and len(plugin.metadata["Types"]) == 9
          and plugin.metadata["aeco"] == {k: manifest[k] for k in ("version", "tier", "requires")})
    REPORT.run("sensor API applies to Camera only (S6: Camera accepted, Mesh and Xform refused)", can_apply,
               [("Camera", "AecoCctvSensorAPI", True), ("Mesh", "AecoCctvSensorAPI", False),
                ("Xform", "AecoCctvSensorAPI", False), ("Camera", "AecoCctvPresetAPI", True, "Home"),
                ("Mesh", "AecoCctvPresetAPI", False, "Home")])
    REPORT.run("study and coverage APIs apply to Scope only", can_apply,
               [("Scope", "AecoCctvStudyAPI", True), ("Xform", "AecoCctvStudyAPI", False),
                ("Scope", "AecoCctvCoverageAPI", True), ("Mesh", "AecoCctvCoverageAPI", False)])
    REPORT.run("system API applies to AecoSystem only", can_apply,
               [("AecoSystem", "AecoCctvSystemAPI", True), ("Scope", "AecoCctvSystemAPI", False),
                ("AecoZone", "AecoCctvSystemAPI", False)])
    REPORT.run("camera, target and sightline APIs apply to Imageable, not to groups", can_apply,
               [("Xform", "AecoCctvCameraAPI", True), ("AecoZone", "AecoCctvCameraAPI", False),
                ("Mesh", "AecoCctvTargetAPI", True), ("AecoSystem", "AecoCctvTargetAPI", False),
                ("Mesh", "AecoCctvSightlineAPI", True), ("AecoZone", "AecoCctvSightlineAPI", False)])
    stage = Usd.Stage.CreateInMemory()
    study = stage.DefinePrim("/Study", "Scope")
    study.ApplyAPI("AecoCctvStudyAPI")
    names = {c.GetName() for c in Usd.CollectionAPI.GetAll(study)}
    check("S6: built-in CollectionAPI:targets, :exclusions and :cameras expand on a study Scope",
          names == {"targets", "exclusions", "cameras"} and all(
              study.GetRelationship("collection:" + n + ":includes") for n in names), str(sorted(names)))
    registry_ = Usd.SchemaRegistry()
    definitions = {api: registry_.FindAppliedAPIPrimDefinition(api) for api in APIS}
    properties = {n: d for d in definitions.values() for n in d.GetPropertyNames() if n.startswith("aeco:")}
    flags = {n for n, d in properties.items() if d.GetPropertyMetadata(n, "aecoDerived") is True}
    drivers = ("aeco:cctvType:irRange", "aeco:cctvStudy:mountHeightRange")
    check("71 aeco: properties; exactly the 23 derived properties carry aecoDerived, irRange and mountHeightRange do not [E13]",
          len(properties) == 71 and flags == DERIVED_SET
          and all(properties[n].GetPropertyMetadata(n, "aecoDerived") is None for n in drivers),
          "%d properties; flagged: %s" % (len(properties), ", ".join(sorted(flags))))
    source = Sdf.Layer.FindOrOpen(str(ROOT / "usdAecoCctv/schema.usda"))
    docs = [p.documentation for api in APIS for p in source.GetPrimAtPath("/" + api).properties]
    check("every property documents units, DRIVER/DERIVED and its host mirror; no kind tokens",
          len(docs) == 71 and all(d and "Units:" in d and "Host mirror:" in d and ("DRIVER" in d or "DERIVED" in d)
                                  for d in docs)
          and all(n.startswith(("aeco:cctv:", "aeco:cctvType:", "aeco:cctvSensor:", "aeco:cctvPreset:",
                                "aeco:cctvStudy:", "aeco:cctvCoverage:", "aeco:cctvTarget:",
                                "aeco:cctvSightline:", "aeco:cctvSystem:"))
                  and not n.endswith((":kind", ":form", ":systemType")) for n in properties)
          and "usdAeco/schema.usda" in " ".join(source.subLayerPaths))
    types = plugin.metadata["Types"]
    check("registered type names are Usd + AecoCctv + class; preset API is multiple-apply",
          set(types) == {"UsdAecoCctv" + n + "API" for n in ("Camera", "CameraType", "Sensor", "Preset", "Study",
                                                              "Coverage", "Target", "Sightline", "System")}
          and types["UsdAecoCctvPresetAPI"]["schemaKind"] == "multipleApplyAPI"
          and "aeco:cctvPreset:__INSTANCE_NAME__:dwell" in definitions["AecoCctvPresetAPI"].GetPropertyNames())
    core = dict(Plug.Registry().GetPluginWithName("usdAeco").metadata["aeco"])
    mine = {k: manifest[k] for k in ("version", "tier", "requires")}
    refused = 0
    for metadata in ({"usdAecoCctv": mine}, *(
            {"usdAecoCctv": mine, "usdAeco": dict(core, version=version)}
            for version in ("0.8.4", "1.0.0"))):
        try:
            check_requirements(metadata)
        except ValueError:
            refused += 1
    check("missing core, 0.8.4 and 1.0.0 refused by the dependency contract", refused == 3 and check_requirements(
        {"usdAecoCctv": mine, "usdAeco": core}) == ["usdAeco", "usdAecoCctv"])
    check("compatible core 0.9.0 and 0.9.1 accepted", all(check_requirements(
        {"usdAecoCctv": mine, "usdAeco": dict(core, version=version)}) == ["usdAeco", "usdAecoCctv"]
        for version in ("0.9.0", "0.9.1")))


def registry_checks():
    ladders = registry("density_levels")
    dori = ladders["dori2015"]
    ok = (dori == {"detect": 25, "observe": 62.5, "recognise": 125, "identify": 250}
          and ladders["oodpcvs2025"] == {"overview": 20, "outline": 40, "discern": 80, "perceive": 125,
                                          "characterise": 250, "validate": 500, "scrutinise": 1500}
          and 180 in ladders["project"].values()
          and set(registry("scenarios")) == {"door", "corridor", "intersection", "perimeter", "gate", "lane",
                                             "dock", "lobby", "area", "rackAisle", "plant", "roof"}
          and registry("camera_classes") == ["IfcAudioVisualAppliance", "IfcAudioVisualAppliance.CAMERA", "IfcBuildingElementProxy"])
    classes = registry("sightline_transparent_classes")
    detail = "%d transparent classes" % len(classes)
    try:
        import ifcopenshell
        schema = ifcopenshell.schema_by_name("IFC4X3_ADD2")
        for name in classes:
            entity, _, predefined = name.partition(".")
            schema.declaration_by_name(entity)
            if predefined:
                enum = schema.declaration_by_name(entity + "TypeEnum")
                ok = ok and predefined in enum.enumeration_items()
        try:
            schema.declaration_by_name("IfcCurtainWallPanel")
            ok = False
        except RuntimeError:
            pass
        detail += ", verified as IFC4X3_ADD2 names with ifcopenshell " + ifcopenshell.version
    except ImportError:
        detail += " (ifcopenshell absent; names not re-verified)"
    except RuntimeError as exc:
        ok, detail = False, str(exc)
    check("registries: DORI 2015, the 2025 seven levels, a project ladder with 180, scenarios, IFC classes", ok, detail)


def computation_checks():
    from usdaeco_cctv.density import arc_range, ladder, level_name, optics, plane_range
    from usdaeco_cctv.frames import decompose, sensor_matrix
    from usdaeco_cctv.sectors import is_closed, level_shells, ptz_envelope, sector_mesh
    look = Gf.Vec3d(0, 0, -1)
    cardinal = all(Gf.IsClose(sensor_matrix(pan=pan).TransformDir(look), Gf.Vec3d(*d), 1e-12)
                   for pan, d in ((0, (1, 0, 0)), (90, (0, 1, 0)), (-90, (0, -1, 0)), (180, (-1, 0, 0))))
    down = sensor_matrix(tilt=30).TransformDir(look)[2]
    rng = np.random.default_rng(7)
    worst = 0.0
    for pan, tilt, roll in rng.uniform([-180, -89, -180], [180, 89, 180], size=(100, 3)):
        m = sensor_matrix((1, 2, 3), pan, tilt, roll)
        worst = max(worst, np.max(np.abs(np.array(decompose(m)) - [pan, tilt, roll])))
    check("frames: pan 0 = device +X, tilt positive down, decompose round-trips within 1e-9",
          cardinal and abs(down + 0.5) < 1e-12 and worst < 1e-9, "worst round-trip error %.2e deg" % worst)
    p = dict(focal_range=(3, 8.5), hfov_range=(104, 34), vfov_range=(76, 26), pixels=(2592, 1944))
    wide, tele = optics(3, **p), optics(8.5, **p)
    check("optics: the P3277-class datasheet gives 104/76 deg at 3 mm and 34/26 deg at 8.5 mm (within 0.1 deg)",
          all(abs(a - b) < 0.1 for a, b in ((wide["hfov"], 104), (wide["vfov"], 76), (tele["hfov"], 34), (tele["vfov"], 26))),
          "3 mm: %.3f/%.3f, 8.5 mm: %.3f/%.3f, width %.3f mm" % (wide["hfov"], wide["vfov"], tele["hfov"], tele["vfov"],
                                                              wide["effectiveWidth"]))
    plane, arc = plane_range(2688, 88, 250), arc_range(2688, 88, 250)
    check("density: 2688 px at 88 deg reach identify at 5.6 m (plane) and 7.0 m (arc); levels named from the ladder",
          abs(plane - 5.567) < 0.01 and abs(arc - 6.999) < 0.01 and level_name(180, ladder("dori2015")) == "recognise"
          and level_name(180, ladder("project")) == "project" and level_name(1, ladder("dori2015")) == "none",
          "plane %.3f m, arc %.3f m" % (plane, arc))
    points, counts, indices = sector_mesh(104, 76, 14.0, nu=24, nv=14)
    radii = np.linalg.norm(points[1:], axis=1)
    fish = sector_mesh(180, 180, 5.0, projection="fisheye")
    cyl = sector_mesh(270, 90, 5.0, projection="cylindrical")
    check("sectors: apex + (nu+1)(nv+1) points on the radius, closed and consistently oriented, for all projections",
          len(points) == 1 + 25 * 15 and np.allclose(radii, 14.0) and np.allclose(points[0], 0)
          and is_closed(counts, indices) and is_closed(*fish[1:]) and is_closed(*cyl[1:])
          and abs(math.degrees(2 * math.atan(np.max(np.abs(points[1:, 0]) / -points[1:, 2]))) - 104) < 1e-9,
          "%d points, %d faces" % (len(points), len(counts)))
    shells = level_shells(104, 76, 2592, 14.0, ladder("dori2015"))
    env = ptz_envelope((-180, 180), (0, 90), 20.0)
    check("level shells lie strictly inside the sector; the PTZ envelope reaches downward only",
          set(shells) == {"identify", "recognise"}
          and all(np.max(np.linalg.norm(m[0][1:], axis=1)) < 14.0 for m in shells.values())
          and is_closed(*env[1:]) and np.all(env[0][1:, 2] <= 1e-9) and np.allclose(np.linalg.norm(env[0][1:], axis=1), 20.0),
          "shells " + ", ".join("%s %.2f m" % (n, np.max(np.linalg.norm(m[0][1:], axis=1))) for n, m in shells.items()))


def validator_checks():
    validators.register()
    validators.register()
    names = {m.name for m in UsdValidation.ValidationRegistry().GetValidatorMetadataForKeyword(validators.KEYWORD)}
    check("twenty-three validators registered idempotently under UsdAecoCctvValidators (eleven static, twelve study rules)",
          len(names) == 23 and names == {"usdAecoCctvValidators:" + n[0].upper() + n[1:] + "Checker" for n in STATIC_RULES + validators.STUDY_RULES})


def example_checks():
    # The legacy claim covers these two stage roots. Input overlays and transient
    # data-centre layers are verified by their composed example harness below.
    with tempfile.TemporaryDirectory(prefix="cctv-lobby-roots-") as directory:
        for path in (EXAMPLE, DERIVED):
            shutil.copyfile(path, Path(directory) / path.name)
        result = validate_examples(directory, [], validators=[
            lambda stage: [e for e in validators.validate_stage(stage, include_core=True, include_builtin=False)
                           if e.GetType() != UsdValidation.ValidationErrorType.Info]])
    check("both example layers compose with fallbacks and pass cctv, core and built-in validators with zero warnings",
          bool(result) and result.detail.endswith("0 warnings"), result.detail)
    s = Usd.Stage.Open(str(DERIVED))
    for name, profile in (("cctv", ROOT / "conformance/profiles/cctv.json"), ("security", ROOT / "conformance/profiles/security.json")):
        issues = validators.validate_stage(s, include_core=True, profile=profile)
        expected = {"cctvStudyMissingResults"} if name == "security" else set()
        check("profile %s: unrun example has the declared missing-study grade" % name,
              {e.GetName() for e in issues if e.GetType() != UsdValidation.ValidationErrorType.Info} == expected,
              "; ".join(e.GetName() for e in issues))
    cameras = list(iter_cameras(s))
    ptz = s.GetPrimAtPath(LOBBY + "Cam_4/Sensor_0")
    presets = presets_of(ptz)
    system = s.GetPrimAtPath("/CctvLobby/Cctv")
    study = iter_studies(s)[0]
    check("example: four cameras with one inherited sensor each, two catalog types, three presets and a tour",
          len(cameras) == 4 and all(len(sensors_of(c)) == 1 for c in cameras)
          and {camera_type_of(c).GetName() for c in cameras} == {"Dome_P3277", "Ptz_Q6088"}
          and list(presets) == ["Home", "Door_1", "Door_3"] and presets["Home"]["home"] is True
          and list(ptz.GetAttribute("aeco:cctvSensor:tour").Get()) == ["Home", "Door_1", "Door_3"]
          and ptz.GetAttribute("aeco:cctvSensor:focalRange").Get() == Gf.Vec2d(6.64, 225.5)
          and system.GetAttribute("aeco:cctvSystem:recorderCapacity").Get() == 45
          and len(Usd.CollectionAPI(system, "members").GetIncludesRel().GetTargets()) == 4
          and len(Usd.CollectionAPI(study, "targets").GetIncludesRel().GetTargets()) == 3
          and study.GetAttribute("aeco:cctvStudy:ptzPolicy").Get() == "presetsNotSole")
    ids = [c.GetAttribute("aeco:id").Get() for c in cameras]
    check("example ids are uuid5 of the prim path under urn:usdaeco:id:v1", all(
        i == str(uuid.uuid5(uuid.uuid5(uuid.NAMESPACE_URL, "urn:usdaeco:id:v1"), str(c.GetPath())))
        for i, c in zip(ids, cameras)) and len(set(ids)) == 4)


def derivation_checks(directory):
    from usdaeco_cctv.derive import derive, derive_file, is_derived_layer
    from usdaeco_cctv.frames import decompose
    from usdaeco_cctv.density import plane_range
    outputs = []
    for i in (1, 2):  # a copy of the model beside each output keeps the relative sublayer path
        run = directory / ("run%d" % i)
        run.mkdir()
        shutil.copy(EXAMPLE, run / "lobby.usda")
        out = run / "lobby.derived.usda"
        stats = derive_file(run / "lobby.usda", out)
        outputs.append(out)
    same = digest(outputs[0]) == digest(outputs[1])
    check("derivation is deterministic: two runs are byte-equal and equal the checked-in derived layer",
          same and digest(outputs[0]) == digest(DERIVED) and stats["sensors"] == 4 and not stats["skipped"],
          json.dumps({k: v for k, v in stats.items() if k != "output"}, sort_keys=True))
    (directory / "cli").mkdir()
    shutil.copy(EXAMPLE, directory / "cli" / "lobby.usda")
    cli_out = directory / "cli" / "lobby.derived.usda"
    output = subprocess_python([ROOT / "tools/aeco-cctv", "derive", directory / "cli" / "lobby.usda", "-o", cli_out],
                               keep_plugins=True)
    check("aeco-cctv derive CLI reproduces the API output byte for byte and reports its counters",
          digest(cli_out) == digest(DERIVED) and json.loads(output.stdout)["sensors"] == 4)
    s = Usd.Stage.Open(str(DERIVED))
    sensor = s.GetPrimAtPath(LOBBY + "Cam_1/Sensor_0")
    cam = UsdGeom.Camera(sensor)
    ops = UsdGeom.Xformable(sensor).GetOrderedXformOps()
    width = 2 * 3 * math.tan(math.radians(52))
    check("derived camera attributes: focalLength 3, horizontalAperture = effective width, clippingRange (0.05, 14), one transform op",
          abs(cam.GetFocalLengthAttr().Get() - 3) < 1e-6 and abs(cam.GetHorizontalApertureAttr().Get() - width) < 1e-4
          and abs(cam.GetVerticalApertureAttr().Get() - 2 * 3 * math.tan(math.radians(38))) < 1e-4
          and cam.GetClippingRangeAttr().Get() == Gf.Vec2f(0.05, 14)
          and len(ops) == 1 and ops[0].GetOpType() == UsdGeom.XformOp.TypeTransform
          and abs(sensor.GetAttribute("aeco:cctvSensor:hfov").Get() - 104) < 1e-9
          and abs(sensor.GetAttribute("aeco:cctvSensor:effectiveWidth").Get() - width) < 1e-9
          and abs(sensor.GetAttribute("aeco:cctvSensor:targetRange").Get() - plane_range(2592, 104, 125)) < 1e-9,
          "targetRange %.3f m at 125 px/m" % sensor.GetAttribute("aeco:cctvSensor:targetRange").Get())
    world = UsdGeom.XformCache().GetLocalToWorldTransform(sensor)
    look = world.TransformDir(Gf.Vec3d(0, 0, -1))
    angles = decompose(ops[0].Get())
    check("derived pose: Cam_1 looks along +Y tilted 40 deg down from a pivot 3.22 m up; decompose reads (90, 40, 0) back",
          Gf.IsClose(look, Gf.Vec3d(0, math.cos(math.radians(40)), -math.sin(math.radians(40))), 1e-9)
          and Gf.IsClose(world.Transform(Gf.Vec3d(0)), Gf.Vec3d(6, 5.5, 3.22), 1e-9)
          and max(abs(a - b) for a, b in zip(angles, (90, 40, 0))) < 1e-9)
    heights = {c.GetName(): c.GetAttribute("aeco:cctv:mountHeight").Get() for c in iter_cameras(s)}
    check("mountHeight derives from the level datum; sensorCount is authored on both catalog types and inherited",
          all(abs(heights[n] - 3.22) < 1e-9 for n in ("Cam_1", "Cam_2", "Cam_3")) and abs(heights["Cam_4"] - 2.85) < 1e-9
          and all(s.GetPrimAtPath("/_TypeCatalog/" + n).GetAttribute("aeco:cctvType:sensorCount").Get() == 1
                  for n in ("Dome_P3277", "Ptz_Q6088"))
          and s.GetPrimAtPath(LOBBY + "Cam_4").GetAttribute("aeco:cctvType:sensorCount").Get() == 1, str(heights))
    guides = {}
    for c in iter_cameras(s):
        for head in sensors_of(c):
            guides[c.GetName()] = {g.GetName(): g for g in head.GetChildren() if g.IsA(UsdGeom.Mesh)}
    sector = guides["Cam_1"]["Sector"]
    binding = UsdShade.MaterialBindingAPI(sector).GetDirectBinding().GetMaterial()
    shader = UsdShade.Shader(binding.GetPrim().GetChild("PreviewSurface"))
    identify = guides["Cam_1"]["Shell_identify"]
    reach = max(Gf.Vec3f(p).GetLength() for p in identify.GetAttribute("points").Get())
    check("guides: Sector + Shell_identify + Shell_recognise under each dome, Envelope under the PTZ; purpose guide, "
          "AecoDerivedGeometryAPI (source = camera id, roles sector/coverage), UsdPreviewSurface opacity 0.3",
          all(set(guides[n]) == {"Sector", "Shell_identify", "Shell_recognise"} for n in ("Cam_1", "Cam_2", "Cam_3"))
          and set(guides["Cam_4"]) == {"Sector", "Shell_identify", "Envelope"}
          and all(g.GetAttribute("purpose").Get() == "guide" and g.HasAPI("AecoDerivedGeometryAPI")
                  and g.GetAttribute("aeco:derived:role").Get() == ("coverage" if g.GetName().startswith("Shell_") else "sector")
                  and g.GetAttribute("aeco:derived:source").Get() == g.GetParent().GetParent().GetAttribute("aeco:id").Get()
                  and g.GetAttribute("subdivisionScheme").Get() == "none"
                  for gs in guides.values() for g in gs.values())
          and shader.GetIdAttr().Get() == "UsdPreviewSurface" and abs(shader.GetInput("opacity").Get() - 0.3) < 1e-6
          and abs(reach - plane_range(2592, 104, 250)) < 1e-3,
          "identify shell reaches %.3f m" % reach)
    ptz = s.GetPrimAtPath(LOBBY + "Cam_4/Sensor_0")
    times = {n: set(ptz.GetAttribute(n).GetTimeSamples()) for n in ("xformOp:transform", "focalLength", "horizontalAperture")}
    times["Sector.points"] = set(guides["Cam_4"]["Sector"].GetAttribute("points").GetTimeSamples())
    envelope = guides["Cam_4"]["Envelope"]
    poses = [UsdGeom.XformCache(Usd.TimeCode(t)).GetLocalToWorldTransform(envelope) for t in (0, 96, 240, 300)]
    boundaries = {0.0, 96.0, 240.0, 384.0}
    check("tour: samples at the dwell boundaries 0/96/240/384 tc on the transform, focal length, aperture and sector "
          "points; layer 24 tcps, 0-384; the envelope stays in the device frame while the head moves",
          all(boundaries <= t for t in times.values()) and s.GetTimeCodesPerSecond() == 24
          and s.GetStartTimeCode() == 0 and s.GetEndTimeCode() == 384
          and all(Gf.IsClose(p, poses[0], 1e-9) for p in poses[1:])
          and Gf.IsClose(poses[0].TransformDir(Gf.Vec3d(1, 0, 0)), Gf.Vec3d(1, 0, 0), 1e-9)
          and abs(ptz.GetAttribute("focalLength").Get(96) - 12) < 1e-6 and abs(ptz.GetAttribute("focalLength").Get(240) - 16) < 1e-6,
          "sample times " + str(sorted(times["xformOp:transform"])))
    model = Sdf.Layer.FindOrOpen(str(EXAMPLE))
    stack = Sdf.Layer.CreateAnonymous("stage.usda")
    intent = directory / "intent.usda"
    intent_layer = Sdf.Layer.CreateNew(str(intent))
    stack.subLayerPaths = [intent_layer.identifier, model.identifier]
    for field in ("upAxis", "metersPerUnit", "fallbackPrimTypes", "defaultPrim"):
        stack.pseudoRoot.SetInfo(field, model.pseudoRoot.GetInfo(field))
    st = Usd.Stage.Open(stack)
    with Usd.EditContext(st, Usd.EditTarget(intent_layer)):
        head = st.GetPrimAtPath(LOBBY + "Cam_1/Sensor_0")
        head.GetAttribute("aeco:cctvSensor:pan").Set(45.0)
        head.GetAttribute("aeco:cctvSensor:hfov").Set(1.0)
    out = Sdf.Layer.CreateAnonymous("cctv.derived.usda")
    derive(st, out)
    head = st.GetPrimAtPath(LOBBY + "Cam_1/Sensor_0")
    spec = out.GetAttributeAtPath(head.GetPath().AppendProperty("aeco:cctvSensor:hfov"))
    look = UsdGeom.XformCache().GetLocalToWorldTransform(head).TransformDir(Gf.Vec3d(0, 0, -1))
    check("intent view: a driver edited in intent.usda is followed (pan 45), a derived opinion there is ignored (hfov stays 104)",
          is_derived_layer(out) and spec is not None and abs(spec.default - 104) < 1e-9
          and abs(math.degrees(math.atan2(look[1], look[0])) - 45) < 1e-9)
    session = Sdf.Layer.CreateAnonymous("session.usda")
    derived_copy = Sdf.Layer.CreateAnonymous("cctv.derived.usda")
    derived_copy.TransferContent(Sdf.Layer.FindOrOpen(str(DERIVED)))
    derived_copy.subLayerPaths = []
    session.subLayerPaths = [derived_copy.identifier, model.identifier]
    for field in ("upAxis", "metersPerUnit", "fallbackPrimTypes", "defaultPrim"):
        session.pseudoRoot.SetInfo(field, model.pseudoRoot.GetInfo(field))
    composed = Usd.Stage.Open(session)
    with_guides = bool(composed.GetPrimAtPath(LOBBY + "Cam_1/Sensor_0/Sector"))
    composed.MuteLayer(derived_copy.identifier)
    plain = Usd.Stage.Open(model)
    check("muting the derived layer restores the model bit-identically (flatten equality) [B7/E11]",
          with_guides and not composed.GetPrimAtPath(LOBBY + "Cam_1/Sensor_0/Sector")
          and composed.Flatten(False).ExportToString() == plain.Flatten(False).ExportToString())


def seeded_checks():
    def seed(label, mutate, expected, stage_factory=fresh):
        s = stage_factory()
        mutate(s)
        found = findings(s)
        check("seeded " + label, found[expected] == 1 and sum(found.values()) == 1, str(dict(found)))

    seed("wrong IFC kind -> cctvKindMismatch (warn)",
         lambda s: s.GetPrimAtPath(LOBBY + "Cam_1").GetAttribute("aeco:class:ifc:code").Set("IfcWall"),
         ("cctvKindMismatch", "Warn"))
    seed("deactivated sensor -> cctvSensorMissing (error)",
         lambda s: s.GetPrimAtPath(LOBBY + "Cam_2/Sensor_0").SetActive(False), ("cctvSensorMissing", "Error"))

    def orphan(s):
        p = s.DefinePrim(LOBBY + "Desk/Lens", "Camera")
        p.ApplyAPI("AecoCctvSensorAPI")
    seed("sensor API under a non-camera element -> cctvSensorOrphan (warn)", orphan, ("cctvSensorOrphan", "Warn"))

    def envelope(s):
        s.GetPrimAtPath(LOBBY + "Cam_3/Sensor_0").GetAttribute("aeco:cctvSensor:focalLength").Set(12.0)
    seed("12 mm on a 3-8.5 mm dome -> cctvOutOfEnvelope (error)", envelope, ("cctvOutOfEnvelope", "Error"))
    s = fresh()
    s.GetPrimAtPath(LOBBY + "Cam_4/Sensor_0").GetAttribute("aeco:cctvPreset:Door_1:tilt").Set(-5.0)
    check("seeded preset tilt -5 on a (0, 90) tilt range -> cctvOutOfEnvelope names the preset",
          findings(s) == Counter({("cctvOutOfEnvelope", "Error"): 1}) and any(
              "preset Door_1" in e.GetMessage() for e in validators.validate_stage(s, include_builtin=False)))

    def native(s):
        UsdGeom.Camera(s.GetPrimAtPath(LOBBY + "Cam_1/Sensor_0")).GetFocalLengthAttr().Set(9.0)
    seed("focalLength authored above the derived layer -> cctvNativeCameraAuthored (warn)", native,
         ("cctvNativeCameraAuthored", "Warn"), lambda: stacked()[0])

    def xform(s):
        UsdGeom.Xformable(s.GetPrimAtPath(LOBBY + "Cam_2/Sensor_0")).AddTranslateOp().Set(Gf.Vec3d(0, 0, 1))
    seed("an xformOp authored on a sensor in the model layer -> cctvNativeCameraAuthored (warn)", xform,
         ("cctvNativeCameraAuthored", "Warn"), lambda: stacked()[0])

    def mismatch(s):
        s.GetPrimAtPath(LOBBY + "Cam_1/Sensor_0").GetAttribute("aeco:cctvSensor:focalLength").Set(6.0)
    seed("driver edited after derivation -> cctvDerivedMismatch (warn)", mismatch, ("cctvDerivedMismatch", "Warn"),
         lambda: stacked()[0])
    seed("ceiling dome tilted upward -> cctvMountFrame (warn)",
         lambda s: s.GetPrimAtPath(LOBBY + "Cam_1/Sensor_0").GetAttribute("aeco:cctvSensor:tilt").Set(-10.0),
         ("cctvMountFrame", "Warn"))

    def band(s):
        s.GetPrimAtPath("/CctvLobby/Analyses/DoorCoverage").GetAttribute("aeco:cctvStudy:mountHeightRange").Set(Gf.Vec2d(2.4, 2.9))
    s = stacked()[0]
    band(s)
    check("seeded mountHeightRange (2.4, 2.9) -> cctvMountFrame for the three 3.22 m domes, not the 2.85 m PTZ",
          findings(s) == Counter({("cctvMountFrame", "Warn"): 3}), str(dict(findings(s))))
    seed("recorder capacity 3 with four members -> cctvSystemCapacity (warn)",
         lambda s: s.GetPrimAtPath("/CctvLobby/Cctv").GetAttribute("aeco:cctvSystem:recorderCapacity").Set(3),
         ("cctvSystemCapacity", "Warn"))
    s = fresh()
    s.DefinePrim("/CctvLobby/Analyses/DoorCoverage/Results", "Scope")
    check("an empty Results scope still reports cctvStudyMissingResults (info)",
          [(e.GetName(), str(e.GetType()).rsplit(".", 1)[-1]) for e in validators.validate_stage(s, include_builtin=False)]
          == [("cctvStudyMissingResults", "Info")])
    stale = UsdValidation.ValidationError("cctvStudyStale", UsdValidation.ValidationErrorType.Warn, [], "x")
    sole = UsdValidation.ValidationError("cctvPtzSoleCoverage", UsdValidation.ValidationErrorType.Warn, [], "y")
    far = UsdValidation.ValidationError("cctvTargetTooFar", UsdValidation.ValidationErrorType.Warn, [], "z")
    graded = apply_profile([stale, sole, far], ROOT / "conformance/profiles/security.json")
    default = load_profile(ROOT / "conformance/profiles/cctv.json")["severity_overrides"]
    check("security profile grades stale results, PTZ-only coverage and distant targets as errors; cctv.json lists all 23 rules",
          all(e.GetType() == UsdValidation.ValidationErrorType.Error for e in graded)
          and set(default) == set(STATIC_RULES + validators.STUDY_RULES)
          and default["cctvSensorMissing"] == "error" and default["cctvPtzSoleCoverage"] == "warn")


VANILLA = r'''
import json, sys
from pxr import Usd, UsdGeom, Plug
assert not any(p.name.startswith('usdAeco') for p in Plug.Registry().GetAllPlugins())
assert Usd.SchemaRegistry().FindAppliedAPIPrimDefinition('AecoCctvSensorAPI') is None
out = {}
for path in sys.argv[1:]:
    s = Usd.Stage.Open(path)
    assert not s.GetCompositionErrors(), path
    assert s.GetPrimAtPath('/CctvLobby/Site').GetPrimTypeInfo().GetSchemaTypeName() == 'Xform'
    assert s.GetPrimAtPath('/CctvLobby/Cctv').GetPrimTypeInfo().GetSchemaTypeName() == 'Scope'
    head = s.GetPrimAtPath('/CctvLobby/Site/Building/L0/Lobby/Cam_1/Sensor_0')
    assert head.GetTypeName() == 'Camera' and head.GetAttribute('aeco:cctvSensor:pan').Get() == 90
    xf = {str(p.GetPath()): [float(v) for row in UsdGeom.Xformable(p).ComputeLocalToWorldTransform(Usd.TimeCode.Default()) for v in row]
          for p in s.Traverse() if UsdGeom.Xformable(p)}
    out[path] = dict(transforms=xf, focalLength=head.GetAttribute('focalLength').Get(),
                     focalAuthored=head.GetAttribute('focalLength').HasAuthoredValue(),
                     sector=len(head.GetChild('Sector').GetAttribute('points').Get() or []) if head.GetChild('Sector') else 0,
                     ptz96=[float(v) for row in UsdGeom.Xformable(s.GetPrimAtPath('/CctvLobby/Site/Building/L0/Lobby/Cam_4/Sensor_0')).ComputeLocalToWorldTransform(Usd.TimeCode(96)) for v in row])
print(json.dumps(out))
'''


def vanilla_check():
    output = json.loads(subprocess_python(["-c", VANILLA, EXAMPLE, DERIVED]).stdout)
    ok = True
    for path in (EXAMPLE, DERIVED):
        s = Usd.Stage.Open(str(path))
        mine = {str(p.GetPath()): [float(v) for row in UsdGeom.Xformable(p).ComputeLocalToWorldTransform(Usd.TimeCode.Default()) for v in row]
                for p in s.Traverse() if UsdGeom.Xformable(p)}
        ptz = [float(v) for row in UsdGeom.Xformable(s.GetPrimAtPath(LOBBY + "Cam_4/Sensor_0")).ComputeLocalToWorldTransform(Usd.TimeCode(96)) for v in row]
        ok = ok and output[str(path)]["transforms"] == mine and output[str(path)]["ptz96"] == ptz
    check("vanilla probe [B7]: no plugins, both layers compose with fallbacks, every world transform (tour included) "
          "and the derived camera attributes are identical",
          ok and output[str(DERIVED)]["focalLength"] == 3 and output[str(DERIVED)]["sector"] == 376
          and output[str(DERIVED)]["focalAuthored"] and not output[str(EXAMPLE)]["focalAuthored"]
          and output[str(EXAMPLE)]["sector"] == 0,
          "%d transforms in the derived layer" % len(output[str(DERIVED)]["transforms"]))


def study_checks(directory, kernel, include_importer=True):
    """C2 acceptance: real scenario outcomes and measured kernel/layer contracts."""
    import importlib.util
    from usdaeco_cctv.study import Settings, enumerate_views, gather_obstacles, input_hash, run_study
    from usdaeco_cctv.raycast import EmbreeKernel, NumpyKernel, embree_available
    from usdaeco_cctv.bench import boxes, sensor_rays, time_kernel, parity_stats
    spec = importlib.util.spec_from_file_location("cctv_cases", ROOT / "scenarios/run.py")
    scenarios = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scenarios)
    suite = scenarios.run_suite(directory / "cases", kernel=kernel, verbose=False, include_importer=include_importer)
    cases = {r["id"]: r for r in suite["cases"]}
    for record in suite["cases"]:
        check("C2 scenario " + record["id"], record["passed"],
              "; ".join(record["failures"]) if record["failures"] else json.dumps(record.get("findings", [])))
    study = "/CctvLobby/Analyses/DoorCoverage"
    stage = fresh()
    initial = stage.Flatten().ExportToString()
    known = {p.GetPath() for p in stage.Traverse()}
    out = (directory / "analysis.usda").resolve()
    report = run_study(stage, study, out, kernel="numpy", recompute=True)
    check("C2 numpy lobby study < 1 s (six views at 96 x 54)",
          report["seconds"] < 1 and report["views"] == 6,
          "%.3f s; %d depth rays" % (report["seconds"], report["rays"]))
    layer = Sdf.Layer.FindOrOpen(str(out))
    data = layer.customLayerData
    check("C2 analysis provenance has study, tool, time and the full sha256 input hash",
          {"aeco:cctv:study", "aeco:cctv:tool", "aeco:cctv:time", "aeco:cctv:inputHash"} <= set(data)
          and data["aeco:cctv:inputHash"] == report["inputHash"] and len(report["inputHash"]) == 64)
    specs = []
    layer.Traverse(Sdf.Path.absoluteRootPath, lambda path: specs.append(layer.GetPrimAtPath(path)) if path.IsPrimPath() else None)
    check("C2 existing prims receive overs; only new result Scopes and guide Meshes receive defs (USD definition deviation)",
          all(p.specifier == Sdf.SpecifierOver if p.path in known else p.typeName in ("Scope", "Mesh")
              for p in specs if p))
    stage.GetSessionLayer().subLayerPaths.remove(str(out.resolve()))
    repeated = run_study(stage, study, out, kernel="numpy")
    check("C2 input hash and results stable; unchanged rerun reuses all six views",
          repeated["inputHash"] == report["inputHash"] == input_hash(stage, study)
          and repeated["results"] == report["results"] and len(repeated["viewsReused"]) == 6
          and not repeated["viewsComputed"])
    intent = Sdf.Layer.CreateAnonymous("hash-probe.usda")
    stage.GetSessionLayer().subLayerPaths.insert(0, intent.identifier)
    stage.SetEditTarget(intent)
    sensor = stage.GetPrimAtPath(LOBBY + "Cam_2/Sensor_0")
    attr = sensor.GetAttribute("aeco:cctvSensor:pan")
    before = attr.Get()
    attr.Set(before + 1e-8)
    check("C2 input hash changes for a small sensor driver edit", input_hash(stage, study) != report["inputHash"])
    stage.SetEditTarget(stage.GetRootLayer())
    stage.GetSessionLayer().subLayerPaths.remove(intent.identifier)
    tray = cases["V-tray"]
    check("C2 full 11 m tray rerun recomputes five candidates and reuses one (documented limit deviation)",
          tray["study"]["viewsComputed"] == 5 and tray["study"]["viewsReused"] == 1)
    local = cases["V-local-edit"]
    check("C2 local tray edit recomputes <= 2 views and reuses four", local["study"]["viewsComputed"] <= 2
          and local["study"]["viewsReused"] >= 4)
    door = tray["results"]["Door_1"]
    check("C2 PTZ-only Door_1 dutyFraction = 6/16; message carries duty and CableTray_1",
          door["dutyFraction"] == 0.375 and not door["fixedCoverage"]
          and "0.375" in tray["messages"]["cctvPtzSoleCoverage@Door_1"]
          and "CableTray_1" in tray["messages"]["cctvPtzSoleCoverage@Door_1"])
    check("C2 security.json re-grades the real PTZ finding to error",
          ["cctvPtzSoleCoverage", "error", "Door_1"] in tray["profiles"]["security"])
    stage.MuteLayer(layer.identifier)
    check("C2 muting the analysis restores the stage bit-identically (flatten equality)",
          stage.Flatten().ExportToString() == initial)
    stage.UnmuteLayer(layer.identifier)
    # Fresh CLI uses the same default-time drivers and emits the same results.
    cli = subprocess_python([ROOT / "tools/aeco-cctv", "study", EXAMPLE, study,
                             "-o", directory / "cli-study.usda", "--kernel", "numpy", "--recompute"])
    cli_report = json.loads(cli.stdout)
    check("C2 aeco-cctv study CLI agrees with the API", cli_report["inputHash"] == report["inputHash"]
          and cli_report["results"] == report["results"])
    cfg = Settings(stage.GetPrimAtPath(study))
    owners, triangles, owner_ids = gather_obstacles(stage, cfg)
    views, _skipped = enumerate_views(stage, cfg)
    origins = np.concatenate([np.broadcast_to(v.origin, (cfg.nx * cfg.ny, 3)) for v in views])
    dirs = np.concatenate([v.frustum.grid(cfg.nx, cfg.ny) for v in views])
    if embree_available():
        _, tn, on = time_kernel(NumpyKernel(), triangles, owner_ids, origins, dirs)
        _, te, oe = time_kernel(EmbreeKernel(), triangles, owner_ids, origins, dirs)
        parity = parity_stats(tn, on, te, oe)
        check("C2 lobby kernel parity: identical hit owner on every ray", parity["identicalOwners"], json.dumps(parity))
        triangles, owners = boxes(20000)
        origins, dirs = sensor_rays(457)
        measured, _t, _o = time_kernel(EmbreeKernel(), triangles, owners, origins, dirs)
        check("C2 Embree: 457 x 5184 rays against 240000 triangles, build + cast < 2 s",
              measured["build"] + measured["cast"] < 2, json.dumps(measured))
    else:
        check("C2 lobby kernel parity availability recorded", True, "embree unavailable; parity not verified")
        check("C2 full-scale Embree benchmark availability recorded", True, "embree unavailable; timing not verified")


def integrity_checks(directory):
    """Additional release contracts; scenario rows cover the fourteen named cases."""
    sys.path.insert(0, str(ROOT / "testenv"))
    import test_integrity as integrity
    import test_sampling as sampling
    from usdaeco_cctv import __version__
    manifest = json.loads((ROOT / "library.json").read_text())
    source = Sdf.Layer.FindOrOpen(str(ROOT / "usdAecoCctv/schema.usda"))
    check("release version and additive schema metadata agree", __version__ == manifest["version"] == "0.5.5"
          and source.customLayerData.get("schemaVersion") == "0.2.1")
    def assertion(function):
        function()
        return True
    REPORT.run("security profile rejects a never-run study", assertion, integrity.test_never_run_security_grade)
    REPORT.run("explicit grids use the declared spacing", assertion, sampling.test_grid_spacing)
    def fraction_probe():
        for fraction, covered in ((0., True), (.5, True), (1., False)):
            path = directory / str(fraction)
            path.mkdir()
            sampling.test_explicit_pass_fraction(path, fraction, covered)
        return True
    REPORT.run("sample fraction controls coverage independently of the primary point", fraction_probe)
    def projection_probe():
        for projection in ("fisheye", "cylindrical"):
            path = directory / projection
            path.mkdir()
            integrity.test_study_refuses_unsupported_projection(path, projection)
        return True
    REPORT.run("nonrectilinear studies refuse output with sensor attribution", projection_probe)
    def exclusion_probe():
        sampling.test_exclusion_sites_name_study_target_and_view(directory)
        return True
    REPORT.run("exclusion findings expose study target and responsible view sites", exclusion_probe)


def performance_checks(directory):
    """Correctness gates stay portable; facility budgets are measured separately."""
    sys.path.insert(0, str(ROOT / "testenv"))
    import pytest
    import test_performance as performance
    def caches():
        with pytest.MonkeyPatch.context() as patch:
            performance.test_geometry_and_scene_buffers_are_immutable_and_reused(patch)
        performance.test_bvh_reuse_and_complete_timing_accounting(directory)
        return True
    def culling():
        for kernel in performance.KERNELS:
            for fixture in ("lobby", "cross-level"):
                target = directory / (kernel + fixture)
                target.mkdir()
                performance.test_every_culled_result_and_blocker_matches_uncropped(target, kernel, fixture)
        performance.test_culling_retains_near_origin_blockers_and_large_bounds()
        return True
    def quads():
        with pytest.MonkeyPatch.context() as patch:
            performance.test_quad_fast_path_keeps_winding_holes_and_refusals(patch)
        return True
    REPORT.run("immutable geometry and BVHs reuse inputs with complete stage timings", caches)
    REPORT.run("culling preserves every lobby and cross-level result and blocker", culling)
    REPORT.run("convex quad fast path preserves winding and omitted hole faces", quads)


def importer_checks(directory):
    sys.path.insert(0, str(ROOT / "testenv"))
    from fixtures import build_baseline, write_cobie, convert
    from usdaeco_cctv.importer import import_cctv
    from usdaeco_cctv.derive import derive_file
    expected = dict(cameras=3, sensors=6, presets=2, types=3, propsBlocked=137,
                    subInstancesFolded=2, unmatched=0)
    source, base, workbook = (directory / n for n in ("fixture.ifc", "core.usda", "cobie.xlsx"))
    build_baseline(source)
    write_cobie(source, workbook)
    convert(source, base)
    inputs = [source, workbook, *directory.glob("core.*")]
    before = {p: digest(p) for p in inputs}
    kind, cobie = directory / "kind.usda", directory / "cobie.usda"
    stats = import_cctv(base, source, kind)
    check("IFC fixture: exactly 3 cameras, 6 sensors, 2 presets, 3 types, 137 blocks, 2 folded, 0 unmatched",
          stats == expected, json.dumps(stats, sort_keys=True))
    cobie_stats = import_cctv(base, workbook, cobie)
    check("COBie Component/Type/Attribute route equals IFC: counters and kind-layer bytes",
          cobie_stats == stats and digest(kind) == digest(cobie), json.dumps(cobie_stats, sort_keys=True))
    cli = directory / "cli.usda"
    result = subprocess_python([ROOT / "tools/aeco-cctv-import", base, source, "-o", cli], keep_plugins=True)
    cli_stats = json.loads(result.stdout)
    timings = cli_stats.pop("timingsSeconds")
    check("aeco-cctv-import CLI equals API: JSON counters and byte-identical layer",
          cli_stats == stats and digest(cli) == digest(kind)
          and timings["total"] >= sum(v for k, v in timings.items() if k != "total"))
    stage = Usd.Stage.Open(str(kind))
    cameras = list(iter_cameras(stage))
    fixed = next(c for c in cameras if c.GetName() == "Fixed")
    sensor = sensors_of(fixed)[0]
    check("IFC tilt sign flips (-30 to +30); length-typed pan stays 15 degrees; standard zoom 4 wins",
          [sensor.GetAttribute("aeco:cctvSensor:" + n).Get() for n in ("pan", "tilt", "focalLength")] == [15., 30., 4.])
    check("project mapping: corridor roll 90, range 18 m, density 180, scenario door, mount wall",
          [sensor.GetAttribute("aeco:cctvSensor:" + n).Get() for n in ("roll", "range", "targetDensity")] == [90., 18., 180.]
          and fixed.GetAttribute("aeco:cctv:scenario").Get() == "door"
          and fixed.GetAttribute("aeco:cctv:mount").Get() == "wall")
    cat = camera_type_of(fixed)
    layer = stage.GetRootLayer()
    check("catalog optics inherited; occurrences author only overrides, no native camera data or sensor identity",
          cat is not None and cat.HasAPI("AecoTypeAPI")
          and not layer.GetAttributeAtPath(sensor.GetPath().AppendProperty("aeco:cctvSensor:focalRange"))
          and not any(p.HasAuthoredValueOpinion() for c in cameras for s in sensors_of(c)
                      for p in (s.GetAttribute("focalLength"), s.GetAttribute("aeco:id")) if p)
          and sensor.GetAttribute("aeco:cctvSensor:focalRange").Get() == Gf.Vec2d(3, 8.5))
    check("every promoted copy blocked; unknown Vendor Note and unsupported display booleans survive",
          fixed.GetAttribute("aeco:props:Pset_CameraProject:FOV_Tilt").Get() is None
          and fixed.GetAttribute("aeco:props:Pset_CameraProject:Vendor_Note").Get() == "Keep occurrence quarantine"
          and fixed.GetAttribute("aeco:props:Pset_CameraProject:Recognize").Get() is True)
    four = next(c for c in cameras if c.GetName() == "Four_head")
    ptz = next(c for c in cameras if c.GetName() == "PTZ")
    check("four independent heads and one motorised head with two enabled presets",
          [s.GetAttribute("aeco:cctvSensor:pan").Get() for s in sensors_of(four)] == [0., 90., 180., 270.]
          and list(presets_of(sensors_of(ptz)[0])) == ["Preset_1", "Preset_2"]
          and sensors_of(ptz)[0].GetAttribute("aeco:cctvSensor:motorised").Get())
    check("FOV and symbol pictures deactivated and folded into sensor refs; unrelated proxy remains an element",
          sum(bool(s.GetAttribute("aeco:props:cctv:subInstances").Get()) for c in cameras for s in sensors_of(c)) == 2
          and len([p for p in stage.Traverse() if p.HasAPI("AecoElementAPI")]) == 4)
    mm_dir = directory / "mm"
    mm_dir.mkdir()
    build_baseline(mm_dir / "fixture.ifc", millimetres=True)
    convert(mm_dir / "fixture.ifc", mm_dir / "core.usda")
    mmstats = import_cctv(mm_dir / "core.usda", mm_dir / "fixture.ifc", mm_dir / "kind.usda")
    mmstage = Usd.Stage.Open(str(mm_dir / "kind.usda"))
    same = all(s.GetAttribute(n).Get() == mmstage.GetPrimAtPath(s.GetPath()).GetAttribute(n).Get()
               for c in cameras for s in sensors_of(c) for n in (
                   "aeco:cctvSensor:pan", "aeco:cctvSensor:range", "aeco:cctvSensor:offset", "aeco:cctvSensor:focalRange"))
    check("metre and millimetre IFC variants produce the same SI drivers and exact counters", same and mmstats == stats)
    root = Sdf.Layer.CreateAnonymous("stack.usda")
    root.subLayerPaths = [str(kind), str(base)]
    for key in ("defaultPrim", "upAxis", "metersPerUnit", "fallbackPrimTypes"):
        root.pseudoRoot.SetInfo(key, stage.GetMetadata(key))
    composed = Usd.Stage.Open(root)
    composed.MuteLayer(str(kind))
    plain = Usd.Stage.Open(str(base))
    check("mute kind layer recovers core composition bit-identically (flatten equality)",
          composed.Flatten(False).ExportToString() == plain.Flatten(False).ExportToString())
    derived_stats = derive_file(kind, directory / "derived.usda")
    check("imported fixture derives end to end: six sensors, six sectors, zero skipped",
          derived_stats["sensors"] == derived_stats["sectors"] == 6 and not derived_stats["skipped"])
    check("IFC, COBie and all core input layers remain SHA256-identical", all(digest(p) == h for p, h in before.items()))

    print("== stage: Revit helper import")
    from test_revit_importer import (test_nested_ownership_beats_nearer_camera_and_supercomponent,
                                     test_top_level_helper_with_optics_folds_beyond_legacy_radius,
                                     test_catalog_sharing_preserves_identity_pose_and_unknown_evidence)
    for name, claim, args in (
        ("nested IFC aggregates and nests outrank proximity and SuperComponent",
         test_nested_ownership_beats_nearer_camera_and_supercomponent, ("IfcRelAggregates",)),
        ("anonymous Revit graphics require matching optics and same-level proximity",
         test_top_level_helper_with_optics_folds_beyond_legacy_radius, (True,)),
        ("duplicate symbol catalogs share optics without changing occurrence identities or poses",
         test_catalog_sharing_preserves_identity_pose_and_unknown_evidence, (False, False, 1))):
        with tempfile.TemporaryDirectory(dir=directory) as probe:
            claim(Path(probe), *args)
        check(name, True)


def real_data_checks():
    from real_data_gate import run_gate
    source = Path(os.environ["AECO_REAL_DATA_ROOT"])
    with tempfile.TemporaryDirectory(prefix="aeco-cctv-real-") as temporary:
        work = Path(os.environ.get("AECO_REAL_DATA_WORK", temporary))
        summary = run_gate(source, work, ROOT / "baselines/real-data-door-study.json", kernel="auto")
    check("real data: 457 cameras, 469 sensors, 469 pictures folded, zero unmatched and skipped",
          summary["cameras"] == 457 and summary["sensors"] == 469 and summary["subInstancesFolded"] == 469
          and summary["unmatched"] == summary["derivedSkipped"] == 0, json.dumps({k: summary[k] for k in (
              "cameras", "sensors", "presets", "types", "propsBlocked", "subInstancesFolded", "unmatched")}))
    parity = summary["parity"]
    check("real data: 469 half-angles within 1e-3 rad and 2345 host-formula radii within 1 mm",
          parity["halfAngleRadians"]["count"] == 469 and parity["hostRadiusMetres"]["count"] == 2345
          and parity["halfAngleFailures"] == parity["hostRadiusFailures"] == 0, json.dumps(parity))
    study = summary["study"]
    check("real data: 176 door cameras; every target reported, level counts reconcile, inputs unchanged",
          study["cameras"] == 176 and study["targets"] > 0 and study["skipped"] == 0
          and sum(v["covered"] + v["uncovered"] for v in study["levels"]) == study["targets"]
          and summary["inputsUnchanged"] == 1,
          "%d targets; %.3f s (60 s budget); %d shared levels" % (study["targets"], study["seconds"], summary["sharedLevels"]))


def core_example_checks():
    """Run every core rule on both derived examples and prove E15 executes."""
    from usdaeco_check.validation import run
    keyword = validators.register_core_validators()
    plugin = Plug.Registry().GetPluginWithName("usdAecoValidators")
    reg = UsdValidation.ValidationRegistry()
    names = {m.name for m in reg.GetValidatorMetadataForKeyword(keyword)}
    declared = {plugin.name + ":" + n for n in plugin.metadata["Validators"] if n != "keywords"}
    required = {plugin.name + ":" + n + "Checker" for n in ("DerivedExactOnMesh", "ExactWithoutTolerance")}
    ok = bool(names) and names == declared and required <= names and plugin.isLoaded
    details = [f"Python plugin loaded; {len(names)}/{len(declared)} core validators"]
    example_out = ROOT / "examples/datacentre/out"
    for path, source in ((DERIVED, EXAMPLE), (example_out / "example.usda", example_out / "source.usda")):
        stage = Usd.Stage.Open(str(path))
        if not stage or stage.GetCompositionErrors():
            raise ValueError(path.name + " does not compose")
        base = Usd.Stage.Open(str(source))
        if not base or base.GetCompositionErrors():
            raise ValueError(source.name + " does not compose")
        baseline = [e for e in run(base, [keyword]) if e.GetType() != UsdValidation.ValidationErrorType.Info]
        issues = [e for e in run(stage, [keyword]) if e.GetType() != UsdValidation.ValidationErrorType.Info]
        guides = [p for p in stage.Traverse() if p.IsA(UsdGeom.Mesh)
                  and p.HasAPI("AecoDerivedGeometryAPI")
                  and p.GetAttribute("aeco:derived:role").Get() in ("sector", "coverage")]
        marked = bool(guides) and all(p.GetAttribute("aeco:derived:approx").Get() == "tessellated"
                                     and not p.GetAttribute("aeco:derived:tolerance").HasAuthoredValueOpinion()
                                     for p in guides)
        errors, warnings = validators.split(issues)
        fingerprint = lambda findings: Counter((e.GetName(), e.GetType(), e.GetMessage()) for e in findings)
        # Published source health warnings remain visible and must be unchanged.
        # No errors or newly introduced warnings are accepted from our outputs.
        unchanged = fingerprint(issues) == fingerprint(baseline)
        ok = ok and marked and not errors and unchanged
        details.append(f"{path.relative_to(ROOT)}: {len(guides)} tessellated guides without tolerance; "
                       f"{len(errors)} errors, {len(warnings)} warnings; "
                       + ("findings identical to source" if unchanged else "findings differ from source"))
        details.extend(e.GetName() + ": " + e.GetMessage() for e in issues)
        if not guides:
            continue
        # Only the anonymous session changes: published examples remain untouched.
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            guides[0].GetAttribute("aeco:derived:approx").Set("exact")
        try:
            seeded = {(e.GetName(), e.GetType()) for e in run(stage, [keyword])}
            caught = {("DerivedExactOnMesh", UsdValidation.ValidationErrorType.Error),
                      ("ExactWithoutTolerance", UsdValidation.ValidationErrorType.Warn)} <= seeded
            ok = ok and caught
            details.append("seeded exact Mesh rejected" if caught else "E15 did not execute")
        finally:
            stage.GetSessionLayer().Clear()
    check("core Python validators execute on both derived examples; E15 rejects seeded exact Mesh guides",
          ok, "; ".join(details))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", choices=("numpy", "embree", "auto"), default="numpy",
                        help="scenario kernel (Nix uses numpy; optional parity probes both)")
    parser.add_argument("--report", type=Path, help="optional JSON acceptance report")
    parser.add_argument("--core-plugin", default=os.environ.get(
        "CORE_PLUGIN_DIR", str(core_root() / "out/plugins/usdAeco/resources")))
    parser.add_argument("--plugin", default=os.environ.get(
        "CCTV_PLUGIN_DIR", str(ROOT / "usdAecoCctv")))
    parser.add_argument("--profile", type=Path, default=ROOT / "conformance/profiles/cctv.json",
                        help="Severity overlay for example conformance; seeded contract probes retain default grades")
    parser.add_argument("--without-importer", action="store_true", help="USD-only checks for runtimes without IFC/COBie wheels")
    args = parser.parse_args()
    os.environ["CORE_PLUGIN_DIR"] = args.core_plugin
    os.environ["CCTV_PLUGIN_DIR"] = args.plugin
    global Gf, Plug, Sdf, Usd, UsdGeom, UsdShade, UsdValidation, validators, apply_profile, load_profile, uuid
    import uuid
    try:
        if not REPORT.run("plugin requirements", plugin_requires, [args.core_plugin, args.plugin]):
            return REPORT.finish()
        register_plugins()
        sys.path.insert(0, str(core_root() / "tools"))
        from pxr import Gf, Plug, Sdf, Usd, UsdGeom, UsdShade, UsdValidation
        from usdaeco_cctv import validators
        from usdaeco_cctv.profiles import apply_profile, load_profile
        from usdaeco_check.structure import check_structure
        from usdaeco_cctv.schema_contract import unchanged
        validators.register_core_validators()
        check("Sdf property contract equals v0.4.8: nine APIs / 71 properties", bool(unchanged(ROOT)))
        print("== stage: schema")
        schema_checks()
        registry_checks()
        print("== stage: computation")
        computation_checks()
        validator_checks()
        print("== stage: example")
        example_checks()
        profiled = validators.validate_stage(Usd.Stage.Open(str(DERIVED)), include_core=True, profile=args.profile)
        check("profile example conformance (--profile)", all(e.GetName() == "cctvStudyMissingResults"
              for e in validators.split(profiled)[0]))
        print("== stage: derivation")
        with tempfile.TemporaryDirectory(prefix="aeco-cctv-check-") as directory:
            derivation_checks(Path(directory))
        print("== stage: validators")
        seeded_checks()
        print("== stage: coverage, scenarios and benchmark")
        with tempfile.TemporaryDirectory(prefix="aeco-cctv-study-check-") as directory:
            study_checks(Path(directory), args.kernel, not args.without_importer)


        with tempfile.TemporaryDirectory(prefix="aeco-cctv-integrity-") as directory:
            integrity_checks(Path(directory))
            performance_checks(Path(directory))

        import real_data_gate  # registers the previous opt-in environment alias
        if not args.without_importer:
            print("== stage: importer")
            with tempfile.TemporaryDirectory(prefix="aeco-cctv-import-") as directory:
                importer_checks(Path(directory))
            if os.environ.get("AECO_REAL_DATA_ROOT") and not os.environ.get("CI"):
                print("== stage: opt-in real-data gate")
                real_data_checks()
            else:
                print("NOT RUN real-data gate: opt-in input is absent or CI is enabled")
        else:
            print("NOT RUN real-data gate: importer checks disabled")
        print("== stage: pinned data-centre example")
        from usdaeco_check.example import check_example
        REPORT.add(check_example(ROOT / "examples/datacentre"))
        core_example_checks()
        measured = json.loads((ROOT / "examples/datacentre/out/findings.json").read_text())
        door_results = next(row for row in measured if row["name"] == "CriticalDoors")["results"]
        covered = sum(row["fixedCoverage"] for row in door_results.values())
        check("data-centre CriticalDoors measured fixed coverage", len(door_results) == 11 and covered == 11, f"{covered}/11 at 250 px/m, plane, dori2015")
        check("data-centre Privacy has zero exclusion hits", not next(row for row in measured if row["name"] == "Privacy")["exclusionsCovered"])
        footprint = next(row for row in measured if row["name"] == "CoverageFootprint")
        check("data-centre study shells cover less L00 hall plan area than nominal sectors",
              footprint["shells"] == footprint["sensors"] == footprint["sectors"] == 11
              and footprint["studies"] == {"CriticalDoors": {"sensors": 11, "views": 11, "shells": 11},
                                           "Privacy": {"sensors": 45, "views": 49, "shells": 49}}
              and 0 <= footprint["shellUnionAreaM2"] < footprint["sectorUnionAreaM2"]
              and max(footprint["shellAreaDeltaM2"], footprint["sectorAreaDeltaM2"]) < .01,
              f"shell union {footprint['shellUnionAreaM2']:.4f} m2; sector union {footprint['sectorUnionAreaM2']:.4f} m2; 0.01 m scanlines")
        from usdaeco_cctv.example_metrics import measure_render
        limits = next(row for row in measured if row["name"] == "RenderMetrics")["views"]
        render_metrics = {view: measure_render(ROOT / "examples/datacentre/out/renders" / (view + ".png"), bounds)
                          for view, bounds in limits.items()}
        check("data-centre renders are non-uniform, foreground >=20%, saturated white <=40%, within caps",
              set(render_metrics) == {"overview", "lookthrough"} and all(m["passed"] for m in render_metrics.values()),
              "; ".join(f"{view}: foreground {m['foregroundFraction']:.2%}, white {m['saturatedWhiteFraction']:.2%}, {m['width']}x{m['height']}, {m['bytes']} bytes"
                        for view, m in render_metrics.items()))
        print("== stage: structure")
        for result in check_structure(ROOT, deps=[args.core_plugin]):
            REPORT.add(result)
        print("== stage: vanilla and hygiene")
        vanilla_check()
        for path in (ROOT / "README.md", ROOT / "docs", ROOT / "examples", ROOT / "scenarios"):
            REPORT.add(link_check(path))
        swept = [p for name in ("usdAecoCctv", "usdAecoCctvValidators", "tools", "testenv", "docs", "examples",
                   "conformance", "registries", "README.md", "check.py", "build.sh", "flake.nix", "dependencies.json",
                   "scenarios", "baselines", "CHANGELOG.md")
                 for p in ((ROOT / name).rglob("*") if (ROOT / name).is_dir() else [ROOT / name])
                 if p.is_file() and not {"out", "__pycache__", ".pytest_cache"}.intersection(p.relative_to(ROOT).parts)]
        # Match S25: only the exact public org slug is exempt from private terms.
        REPORT.add(term_sweep(swept,
            [r'\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b',
             r'[/]Volumes[/]', r'[/]Users[/]', r'\b(?:[c][d][c][-_]?1|(?!(?<![\w-])[c][r][i][a][d]-com(?![\w-]))[c][r][i][a][d])\b',
             r'\b(?:forum|npg)[-_]\d+\b', r'\b[0-9a-f]{2}(?::[0-9a-f]{2}){5}\b']))
    except Exception:
        check("check execution", False, traceback.format_exc())
    results = [{"name": r.name, "passed": r.ok, "detail": r.detail} for r in REPORT.results]
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps({"passed": len(results) - REPORT.failed, "total": len(results),
                                           "checks": results}, indent=2) + "\n")
    return REPORT.finish()


if __name__ == "__main__":
    sys.exit(main())
