"""Tier A derivation (design 5.1): per sensor, no scene.

For every sensor the derivation reads the drivers (pose, zoom, design range
and the catalog optics the occurrence inherits) and writes, into a derived
layer, the stock UsdGeomCamera attributes, one xformOp:transform, the four
derived attributes, guide gprims (Sector, Shell_<level>, Envelope) marked
AecoDerivedGeometryAPI with a bound UsdPreviewSurface, the catalog type's
sensorCount and the camera element's mountHeight. A guard tour becomes time
samples on everything that depends on pan, tilt and focal length.

Drivers are read from the stage as composed (an intent layer included);
properties flagged aecoDerived are read through a view with intent.usda
muted, so a stray derived opinion in an intent layer can never feed the
derivation (the sync lesson). Muting the derived layer restores the stage.
"""
import math
import os
from pathlib import Path

import numpy as np
from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade, Vt

from . import __version__, camera_type_of, iter_cameras, iter_studies, presets_of, sensors_of
from .density import head_optics, ladder as load_ladder, range_at_density
from .frames import sensor_matrix
from .output import isolated_stage, publish, refuse_input, validate_layer
from .sectors import level_ranges, ptz_envelope, sector_mesh

STAMP = "usdAecoCctv derive " + __version__
LAYER_MARK = "aeco:cctv:layer"
TIME_CODES_PER_SECOND = 24.0
SLEW_SECONDS = 0.5      # a preset is held for its dwell, then the head slews for half a second
NEAR_CLIP = 0.05        # metres
OPACITY = 0.3
LOOKS = Sdf.Path("/AecoCctvLooks")
DEFAULT_LADDER, DEFAULT_MODEL, DEFAULT_DENSITY = "dori2015", "plane", 125.0
SECTOR_COLOUR = Gf.Vec3f(0.0, 0.85, 0.95)                          # Axis legend: cyan sector
SHELL_COLOURS = (Gf.Vec3f(0.55, 0.85, 0.8), Gf.Vec3f(0.0, 0.5, 0.45))  # teal, lighter to deeper by level
ENVELOPE_COLOUR = Gf.Vec3f(0.75, 0.75, 0.75)
CAMERA_ATTRIBUTES = ("focalLength", "horizontalAperture", "verticalAperture", "clippingRange")
PREFIX = "aeco:cctvSensor:"


def find_layer(stage, basename):
    """The layer of the stage's stack with this file name (or anonymous tag), else None."""
    for layer in stage.GetLayerStack():
        name = Path(layer.realPath).name if layer.realPath else layer.identifier.rsplit(":", 1)[-1]
        if name == basename:
            return layer
    return None


def is_derived_layer(layer):
    return layer is not None and dict(layer.customLayerData).get(LAYER_MARK) == "derived"


class Reader:
    """Drivers from the composed stage; aecoDerived properties from the intent-muted view."""

    def __init__(self, stage):
        self.stage = stage
        self.view = None
        intent = find_layer(stage, "intent.usda")
        if intent is not None:
            self.view = Usd.Stage.Open(stage.GetRootLayer())
            self.view.MuteLayer(intent.identifier)

    def get(self, prim, name, time=Usd.TimeCode.Default()):
        attr = prim.GetAttribute(name)
        if not attr:
            return None
        if self.view is not None and attr.GetMetadata("aecoDerived"):
            other = self.view.GetPrimAtPath(prim.GetPath()).GetAttribute(name)
            return other.Get(time) if other else None
        return attr.Get(time)


def settings_of(stage, reader, model=None, study_path=None):
    """Use an explicitly selected study or stable library defaults."""
    study = stage.GetPrimAtPath(study_path) if study_path else None
    if study_path and (not study or not study.HasAPI("AecoCctvStudyAPI")):
        raise ValueError("selected study must wear AecoCctvStudyAPI")

    def option(name):
        return reader.get(study, "aeco:cctvStudy:" + name) if study else None
    return dict(model=model or option("densityModel") or DEFAULT_MODEL,
                ladder=option("levelSystem") or DEFAULT_LADDER,
                density=option("requiredDensity") or DEFAULT_DENSITY,
                study=str(study.GetPath()) if study else "")


def drivers_of(sensor, reader):
    names = ("projection", "spectrum", "focalRange", "hfovRange", "vfovRange", "sensorSize", "pixels",
             "offset", "panRange", "tiltRange", "motorised", "pan", "tilt", "roll", "focalLength",
             "range", "targetDensity", "tour")
    d = {name: reader.get(sensor, PREFIX + name) for name in names}
    for name in ("focalRange", "hfovRange", "vfovRange", "sensorSize", "pixels", "offset", "panRange", "tiltRange"):
        d[name] = tuple(float(v) for v in d[name])
    d["tour"] = [str(t) for t in (d["tour"] or [])]
    return d


def sensor_state(d, settings, pan=None, tilt=None, focal=None):
    """Everything the derivation writes for one pose: optics, ranges and the local matrix."""
    pan = d["pan"] if pan is None else pan
    tilt = d["tilt"] if tilt is None else tilt
    focal = d["focalLength"] if focal is None else focal
    projection = "fisheye" if d["spectrum"] == "radar" and d["projection"] == "rectilinear" else d["projection"]
    o = head_optics(focal, d["focalRange"], d["hfovRange"], d["vfovRange"], d["pixels"], d["sensorSize"], projection)
    model = o["model"] or settings["model"]
    pixels = d["pixels"][0]
    density = d["targetDensity"] or settings["density"]
    target_range = range_at_density(pixels, o["hfov"], density, model) if pixels > 0 else 0.0
    radius = d["range"] or target_range
    return dict(o, model=model, projection=projection, pan=pan, tilt=tilt, roll=d["roll"], density=density,
                targetRange=target_range, radius=radius,
                levels=level_ranges(o["hfov"], pixels, load_ladder(settings["ladder"]), model),
                matrix=sensor_matrix(d["offset"], pan, tilt, d["roll"]))


def tour_states(d, presets, settings, tcps=TIME_CODES_PER_SECOND):
    """(time code, state) samples: each preset held for its dwell, a slew, and the loop closure."""
    samples, time = [], 0.0
    slew = SLEW_SECONDS * tcps
    for name in d["tour"]:
        if name not in presets:
            raise ValueError("tour names an unknown preset: " + name)
        preset = presets[name]
        state = sensor_state(d, settings, preset["pan"], preset["tilt"], preset["focalLength"])
        dwell = (preset["dwell"] or 5.0) * tcps
        samples.append((time, state))
        if dwell > slew:
            samples.append((time + dwell - slew, state))
        time += dwell
    if samples:
        samples.append((time, samples[0][1]))
    return samples


def _same(a, b):
    if isinstance(a, (Vt.Vec3fArray, Vt.Vec3dArray, list, tuple)) and not isinstance(a, Gf.Vec3f):
        return list(a) == list(b)
    return a == b


def _author(attr, default, samples=()):
    """Author a default and, when a tour changes the value, one sample per boundary."""
    attr.Set(default)
    if any(not _same(value, default) for _, value in samples):
        for time, value in samples:
            attr.Set(value, Usd.TimeCode(time))


def _points(array):
    return Vt.Vec3fArray.FromNumpy(np.asarray(array, dtype=np.float32))


def _extent(array):
    a = np.asarray(array, dtype=np.float32)
    return Vt.Vec3fArray([Gf.Vec3f(*map(float, a.min(axis=0))), Gf.Vec3f(*map(float, a.max(axis=0)))])


def _material(stage, name, colour):
    path = LOOKS.AppendChild(name)
    material = UsdShade.Material.Define(stage, path)
    shader = UsdShade.Shader.Define(stage, path.AppendChild("PreviewSurface"))
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(colour)
    shader.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(OPACITY)
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.9)
    material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    return material


def _guide(stage, parent, name, counts, indices, colour, source, stamp, material):
    mesh = UsdGeom.Mesh.Define(stage, parent.GetPath().AppendChild(name))
    mesh.CreateFaceVertexCountsAttr(Vt.IntArray(counts))
    mesh.CreateFaceVertexIndicesAttr(Vt.IntArray(indices))
    mesh.CreateSubdivisionSchemeAttr("none")
    mesh.CreateDoubleSidedAttr(True)
    mesh.CreatePurposeAttr("guide")
    mesh.CreateDisplayColorPrimvar("constant").Set(Vt.Vec3fArray([colour]))
    mesh.CreateDisplayOpacityPrimvar("constant").Set(Vt.FloatArray([OPACITY]))
    prim = mesh.GetPrim()
    prim.ApplyAPI("AecoDerivedGeometryAPI")
    # Sector, density shells and PTZ envelopes are sampled Meshes (core E15).
    # Sampling resolution does not establish a geometric tolerance.
    for key, value in (("source", source), ("role", "coverage" if name.startswith("Shell_") else "sector"), ("approx", "tessellated"), ("stamp", stamp)):
        prim.GetAttribute("aeco:derived:" + key).Set(value)
    UsdShade.MaterialBindingAPI.Apply(prim).Bind(material)
    return mesh


def _shell_colour(rank, count):
    t = rank / max(count - 1, 1)
    return Gf.Vec3f(*(SHELL_COLOURS[0] * (1 - t) + SHELL_COLOURS[1] * t))


class Derivation:
    def __init__(self, stage, settings, stamp=STAMP):
        self.stage, self.settings, self.stamp = stage, settings, stamp
        self.materials = {}
        self.ladder = load_ladder(settings["ladder"])
        self.rank = {name: i for i, name in enumerate(sorted(self.ladder, key=self.ladder.get))}
        self.order = {name: -self.ladder[name] for name in self.ladder}  # innermost (densest) shell first
        self.end_time = 0.0
        self.tours = 0

    def material(self, name, colour):
        if name not in self.materials:
            self.materials[name] = _material(self.stage, name, colour)
        return self.materials[name]

    def sensor(self, sensor, camera, reader):
        d = drivers_of(sensor, reader)
        default = sensor_state(d, self.settings)
        samples = tour_states(d, presets_of(sensor), self.settings) if d["motorised"] else []
        if samples:
            self.tours += 1
            self.end_time = max(self.end_time, samples[-1][0])
        states = [default] + [s for _, s in samples]
        source = camera.GetAttribute("aeco:id").Get() or ""
        cam = UsdGeom.Camera(sensor)

        def series(key):
            return [(t, key(s)) for t, s in samples]
        _author(cam.CreateFocalLengthAttr(), float(default["focalLength"]), series(lambda s: float(s["focalLength"])))
        _author(cam.CreateHorizontalApertureAttr(), float(default["effectiveWidth"]), series(lambda s: float(s["effectiveWidth"])))
        _author(cam.CreateVerticalApertureAttr(), float(default["effectiveHeight"]), series(lambda s: float(s["effectiveHeight"])))
        if all(s["radius"] > 0 for s in states):
            _author(cam.CreateClippingRangeAttr(), Gf.Vec2f(NEAR_CLIP, default["radius"]),
                    series(lambda s: Gf.Vec2f(NEAR_CLIP, s["radius"])))
        op = UsdGeom.Xformable(sensor).MakeMatrixXform()
        _author(op.GetAttr(), default["matrix"], series(lambda s: s["matrix"]))
        for name, key in (("hfov", "hfov"), ("vfov", "vfov"), ("effectiveWidth", "effectiveWidth"), ("targetRange", "targetRange")):
            _author(sensor.GetAttribute(PREFIX + name), float(default[key]), series(lambda s, k=key: float(s[k])))
        written = {"sector": False, "shells": [], "envelope": False}
        if all(s["radius"] > 0 for s in states):
            self.sector(sensor, default, samples, source)
            written["sector"] = True
            written["shells"] = self.shells(sensor, default, samples, source)
            if d["motorised"] and d["panRange"] != (0, 0) and d["tiltRange"] != (0, 0):
                self.envelope(sensor, d, default, samples, source)
                written["envelope"] = True
        return dict(written, tour=len(samples) > 0, hfov=default["hfov"], vfov=default["vfov"],
                    radius=default["radius"], targetRange=default["targetRange"])

    def guide_parent(self, sensor):
        if not self.settings["study"]:
            return sensor
        from .study import study_name
        name = "Coverage_" + study_name(self.stage.GetPrimAtPath(self.settings["study"])) + "_Design"
        return self.stage.DefinePrim(sensor.GetPath().AppendChild(name), "Scope")

    def cap(self, state, radius):
        return sector_mesh(state["hfov"], state["vfov"], radius, projection=state["projection"])

    def sector(self, sensor, default, samples, source):
        points, counts, indices = self.cap(default, default["radius"])
        mesh = _guide(self.stage, self.guide_parent(sensor), "Sector", counts, indices, SECTOR_COLOUR, source, self.stamp,
                      self.material("Sector", SECTOR_COLOUR))
        series = [(t, self.cap(s, s["radius"])[0]) for t, s in samples]
        _author(mesh.CreatePointsAttr(), _points(points), [(t, _points(p)) for t, p in series])
        _author(mesh.CreateExtentAttr(), _extent(points), [(t, _extent(p)) for t, p in series])

    def shells(self, sensor, default, samples, source):
        names = sorted({n for s in [default] + [s for _, s in samples] for n, d in s["levels"].items() if 0 < d < s["radius"]},
                       key=self.order.get)
        for name in names:
            colour = _shell_colour(self.rank[name], len(self.ladder))
            mesh = _guide(self.stage, self.guide_parent(sensor), "Shell_" + name, *self.cap(default, 1.0)[1:], colour, source,
                          self.stamp, self.material("Shell_" + name, colour))

            def radius(s):  # a level beyond the design range coincides with the sector
                return min(s["levels"].get(name, s["radius"]) or s["radius"], s["radius"])
            points = self.cap(default, radius(default))[0]
            series = [(t, self.cap(s, radius(s))[0]) for t, s in samples]
            _author(mesh.CreatePointsAttr(), _points(points), [(t, _points(p)) for t, p in series])
            _author(mesh.CreateExtentAttr(), _extent(points), [(t, _extent(p)) for t, p in series])
        return names

    def envelope(self, sensor, d, default, samples, source):
        points, counts, indices = ptz_envelope(d["panRange"], d["tiltRange"], default["radius"])
        mesh = _guide(self.stage, self.guide_parent(sensor), "Envelope", counts, indices, ENVELOPE_COLOUR, source, self.stamp,
                      self.material("Envelope", ENVELOPE_COLOUR))
        series = [(t, ptz_envelope(d["panRange"], d["tiltRange"], s["radius"])[0]) for t, s in samples]
        _author(mesh.CreatePointsAttr(), _points(points), [(t, _points(p)) for t, p in series])
        _author(mesh.CreateExtentAttr(), _extent(points), [(t, _extent(p)) for t, p in series])

        def inverse(state):  # undo the sensor's rotation: the envelope stays in the device frame
            rotation = Gf.Matrix4d(state["matrix"])
            rotation.SetTranslateOnly(Gf.Vec3d(0))
            return rotation.GetInverse()
        op = UsdGeom.Xformable(mesh).MakeMatrixXform()
        _author(op.GetAttr(), inverse(default), [(t, inverse(s)) for t, s in samples])

    def camera(self, camera, sensors, reader, cache, up):
        offset = reader.get(sensors[0], PREFIX + "offset")
        pivot = cache.GetLocalToWorldTransform(camera).Transform(Gf.Vec3d(*offset))
        datum = level_datum(self.stage, camera, pivot[up], cache, up)
        camera.GetAttribute("aeco:cctv:mountHeight").Set(float(pivot[up] - datum))
        return float(pivot[up] - datum)

    def type(self, prim):
        count = len([c for c in prim.GetAllChildren() if c.IsA(UsdGeom.Camera) and c.HasAPI("AecoCctvSensorAPI")])
        prim.GetAttribute("aeco:cctvType:sensorCount").Set(count)
        return count


def level_datum(stage, camera, height, cache, up, *, level_datums=None):
    """The datum of the level the camera sits under, else the highest level datum at or below it."""
    def datum(level):
        origin = cache.GetLocalToWorldTransform(level).Transform(Gf.Vec3d(0))[up]
        if UsdGeom.Xformable(level).GetOrderedXformOps():
            return origin
        return origin + (level.GetAttribute("aeco:elevation").Get() or 0.0)
    prim = camera.GetParent()
    while prim and not prim.IsPseudoRoot():
        if prim.GetTypeName() == "AecoLevel":
            return datum(prim)
        prim = prim.GetParent()
    below = level_datums if level_datums is not None else [datum(p) for p in stage.Traverse() if p.GetTypeName() == "AecoLevel"]
    below = [d for d in below if d <= height + 1e-9]
    return max(below) if below else 0.0


def _derive(stage, layer, model=None, stamp=STAMP, study_path=None):
    """Write Tier A for every camera on the stage into layer; return counters.

    The layer is cleared and rewritten. If it is not already part of the
    stage's layer stack it is inserted at the top of the session layer so the
    stage composes the result; muting it restores the stage bit-identically.
    """
    sublayers = list(layer.subLayerPaths)
    layer.Clear()
    layer.subLayerPaths = sublayers
    if not any(l.identifier == layer.identifier for l in stage.GetLayerStack()):
        stage.GetSessionLayer().subLayerPaths.insert(0, layer.identifier)
    reader = Reader(stage)
    settings = settings_of(stage, reader, model, study_path)
    layer.customLayerData = {LAYER_MARK: "derived", "aeco:cctv:stamp": stamp,
                             "aeco:cctv:densityModel": settings["model"], "aeco:cctv:levelSystem": settings["ladder"]}
    stats = dict(cameras=0, sensors=0, tours=0, sectors=0, shells=0, envelopes=0, types=0, skipped=[])
    up = 2 if UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.z else 1
    cache = UsdGeom.XformCache(Usd.TimeCode.Default())
    with Usd.EditContext(stage, Usd.EditTarget(layer)):
        run = Derivation(stage, settings, stamp)
        types = {}
        for camera in list(iter_cameras(stage)):
            sensors = sensors_of(camera)
            if study_path:
                from .study import Settings, camera_selected, phase_included, sensor_selected
                scope = Settings(stage.GetPrimAtPath(study_path))
                if not camera_selected(camera, scope) or not phase_included(camera, scope):
                    continue
                sensors = [s for s in sensors if sensor_selected(s, scope) and phase_included(s, scope)]
            if not sensors:
                stats["skipped"].append(str(camera.GetPath()) + ": no sensor")
                continue
            stats["cameras"] += 1
            run.camera(camera, sensors, reader, cache, up)
            kind = camera_type_of(camera)
            if kind is not None:
                types[str(kind.GetPath())] = kind
            for sensor in sensors:
                try:
                    result = run.sensor(sensor, camera, reader)
                except ValueError as exc:
                    stats["skipped"].append(str(sensor.GetPath()) + ": " + str(exc))
                    continue
                stats["sensors"] += 1
                stats["tours"] += int(result["tour"])
                stats["sectors"] += int(result["sector"])
                stats["shells"] += len(result["shells"])
                stats["envelopes"] += int(result["envelope"])
        for path in sorted(types):
            run.type(types[path])
            stats["types"] += 1
        if run.tours:
            layer.timeCodesPerSecond = TIME_CODES_PER_SECOND
            layer.framesPerSecond = TIME_CODES_PER_SECOND
            layer.startTimeCode = 0.0
            layer.endTimeCode = run.end_time
    stats["densityModel"], stats["levelSystem"] = settings["model"], settings["ladder"]
    return stats


def derive(stage, layer, model=None, stamp=STAMP, study_path=None):
    """Derive in isolation, then replace and compose a destination outside the input stack."""
    refuse_input(stage, layer)
    if not layer.empty and not is_derived_layer(layer):
        raise ValueError("output is not an owned derived layer")
    work = isolated_stage(stage)
    pending = Sdf.Layer.CreateAnonymous("cctv-derived.usda")
    stats = _derive(work, pending, model, stamp, study_path)
    if stats["skipped"]:
        raise ValueError("derivation failed: " + "; ".join(stats["skipped"]))
    validate_layer(pending)
    if work.GetCompositionErrors():
        raise ValueError("derived output does not compose")
    layer.TransferContent(pending)
    stage.GetSessionLayer().subLayerPaths.insert(0, layer.identifier)
    return stats


def derive_file(stage_path, output, model=None, bare=False, stamp=STAMP, study_path=None):
    """Derive to an owned destination outside the input stack and publish atomically."""
    stage = Usd.Stage.Open(str(stage_path))
    if not stage or stage.GetCompositionErrors():
        raise ValueError("cannot compose input stage")
    output = Path(output).resolve()
    refuse_input(stage, output)
    existing = Sdf.Layer.FindOrOpen(str(output)) if output.exists() else None
    if existing and not is_derived_layer(existing):
        raise ValueError("output is not an owned derived layer")
    layer = Sdf.Layer.CreateAnonymous("cctv-derived.usda")
    stats = derive(stage, layer, model, stamp, study_path)
    stage.GetSessionLayer().subLayerPaths.remove(layer.identifier)
    root = stage.GetRootLayer()
    if not bare and root.realPath:
        relative = os.path.relpath(Path(root.realPath).resolve(), output.parent).replace(os.sep, "/")
        layer.subLayerPaths = [relative if relative.startswith(".") else "./" + relative]
        layer.defaultPrim = root.defaultPrim
        for field in ("upAxis", "metersPerUnit", "fallbackPrimTypes"):
            if stage.HasAuthoredMetadata(field):
                layer.pseudoRoot.SetInfo(field, stage.GetMetadata(field))
    publish(layer, output)
    stats["output"] = str(output)
    return stats
