"""UsdValidation rules under keyword AecoCctvValidators (design section 6).

The study rules only READ the
results a study layer carries (aeco-cctv study writes them; --recompute is
the CLI's job, never a validator's); cctvStudyStale recomputes the input
hash without casting a ray. Severities are the defaults of
conformance/profiles/cctv.json; validate_stage(..., profile=...) re-grades them.
"""
import math
from collections import defaultdict
from importlib import import_module

from pxr import Usd, UsdGeom, UsdValidation

from . import ANGLE_TOLERANCE, LENGTH_TOLERANCE, iter_cameras, iter_studies, presets_of, registry, sensors_of
from .density import head_optics, level_name
from .derive import CAMERA_ATTRIBUTES, PREFIX, is_derived_layer
from .profiles import apply_profile, load_profile

KEYWORD = "UsdAecoCctvValidators"
CAMERA_ENTITY = "IfcAudioVisualAppliance"
DOWNWARD_MOUNTS = ("ceiling", "pendant", "recessed")
SEVERITIES = {"error": UsdValidation.ValidationErrorType.Error,
              "warn": UsdValidation.ValidationErrorType.Warn,
              "info": UsdValidation.ValidationErrorType.Info}


def _issue(name, prims, message, severity="warn"):
    return UsdValidation.ValidationError(name, SEVERITIES[severity], [
        UsdValidation.ValidationErrorSite(p.GetStage(), p.GetPath()) for p in prims], message)


def _kind(prim, time_range):
    if not prim.HasAPI("AecoCctvCameraAPI"):
        return []
    code = prim.GetAttribute("aeco:class:ifc:code").Get() or ""
    if code == CAMERA_ENTITY + ".CAMERA" or code.split(".")[0] == "IfcBuildingElementProxy":
        return []
    return [_issue("cctvKindMismatch", [prim], "Camera API has incompatible IFC classification: " + repr(code))]


def _sampling(prim, time_range):
    if not prim.HasAPI("AecoCctvTargetAPI"):
        return []
    from .study import sampling
    try:
        sampling(prim)
    except ValueError as exc:
        return [_issue("cctvInvalidSampling", [prim], str(exc), "error")]
    return []


def _topology(prim, time_range):
    if not prim.IsA(UsdGeom.Mesh) or UsdGeom.Imageable(prim).ComputePurpose() not in ("default", "render"):
        return []
    import numpy as np
    from .study import _triangulate, UnsupportedTopology
    try:
        _triangulate(UsdGeom.Mesh(prim), np.eye(4), Usd.TimeCode.Default())
    except UnsupportedTopology as exc:
        return [_issue(exc.rule, [prim], str(exc), "error")]
    return []


def missing_optics(sensor):
    """Minimum source evidence for a usable optical head; schema fallbacks are not evidence."""
    if sensor.GetAttribute(PREFIX + "spectrum").Get() == "radar":
        return []
    def supplied(name):
        attr = sensor.GetAttribute(PREFIX + name)
        value = attr.Get() if attr else None
        return bool(attr and attr.HasAuthoredValueOpinion() and value is not None and
                    all(math.isfinite(float(v)) and v > 0 for v in value))
    missing = []
    if not supplied("focalRange"):
        missing.append("focalRange")
    if not supplied("hfovRange") and not supplied("sensorSize"):
        missing.append("hfovRange or sensorSize")
    if not supplied("pixels"):
        missing.append("pixels")
    return missing


def _missing_optics(prim, time_range):
    if not prim.HasAPI("AecoCctvSensorAPI") or not prim.GetParent().HasAPI("AecoCctvCameraAPI"):
        return []
    missing = missing_optics(prim)
    return [_issue("cctvMissingOptics", [prim], "missing drivers: " + ", ".join(missing), "error")] if missing else []


def _sensor_missing(prim, time_range):
    if prim.HasAPI("AecoCctvCameraAPI") and not sensors_of(prim):
        return [_issue("cctvSensorMissing", [prim], "Camera element has no Camera child wearing AecoCctvSensorAPI", "error")]
    return []


def _sensor_orphan(prim, time_range):
    if not prim.HasAPI("AecoCctvSensorAPI"):
        return []
    parent = prim.GetParent()
    if parent and (parent.HasAPI("AecoCctvCameraAPI") or parent.HasAPI("AecoCctvCameraTypeAPI")):
        return []
    return [_issue("cctvSensorOrphan", [prim], "Sensor API on a prim whose parent is not a camera element or catalog type")]


def _outside(value, span):
    lo, hi = span
    return (lo, hi) != (0, 0) and not lo - ANGLE_TOLERANCE <= value <= hi + ANGLE_TOLERANCE


def _envelope(prim, time_range):
    if not prim.HasAPI("AecoCctvSensorAPI"):
        return []

    def get(name):
        return prim.GetAttribute(PREFIX + name).Get()
    pan_range, tilt_range, focal_range = tuple(get("panRange")), tuple(get("tiltRange")), tuple(get("focalRange"))
    poses = {"": (get("pan"), get("tilt"), get("focalLength"))}
    poses.update({name: (p["pan"], p["tilt"], p["focalLength"]) for name, p in presets_of(prim).items()})
    faults = []
    for name, (pan, tilt, focal) in poses.items():
        label = ("preset " + name + ": ") if name else ""
        if _outside(pan, pan_range):
            faults.append(label + "pan %g outside %s" % (pan, pan_range))
        if _outside(tilt, tilt_range):
            faults.append(label + "tilt %g outside %s" % (tilt, tilt_range))
        if focal and not focal_range[0] - LENGTH_TOLERANCE <= focal <= focal_range[1] + LENGTH_TOLERANCE:
            faults.append(label + "focal length %g mm outside %s" % (focal, focal_range))
    return [_issue("cctvOutOfEnvelope", [prim], "; ".join(faults), "error")] if faults else []


def _native(prim, time_range):
    """Stock camera attributes or xformOps authored on a sensor anywhere but a derived layer."""
    if not prim.HasAPI("AecoCctvSensorAPI"):
        return []
    offenders = []
    for prop in prim.GetProperties():
        name = prop.GetName()
        if name not in CAMERA_ATTRIBUTES and not name.startswith("xformOp"):
            continue
        specs = list(prop.GetPropertyStack(Usd.TimeCode.Default()))
        specs += [s for s in prop.GetPropertyStack(Usd.TimeCode.EarliestTime()) if s not in specs]
        layers = sorted({s.layer.identifier for s in specs if not is_derived_layer(s.layer)})
        if layers:
            offenders.append(name + " in " + ", ".join(layers))
    if offenders:
        return [_issue("cctvNativeCameraAuthored", [prim],
                       "Derived camera properties authored outside the derived layer: " + "; ".join(offenders))]
    return []


def _mismatch(prim, time_range):
    if not prim.HasAPI("AecoCctvSensorAPI"):
        return []
    derived = {n: prim.GetAttribute(PREFIX + n) for n in ("hfov", "vfov", "effectiveWidth")}
    if not any(a.HasAuthoredValue() for a in derived.values()):
        return []

    def get(name):
        return prim.GetAttribute(PREFIX + name).Get()
    try:
        projection = "fisheye" if get("spectrum") == "radar" and get("projection") == "rectilinear" else get("projection")
        o = head_optics(get("focalLength"), tuple(get("focalRange")), tuple(get("hfovRange")), tuple(get("vfovRange")),
                        tuple(get("pixels")), tuple(get("sensorSize")), projection)
    except ValueError as exc:
        return [_issue("cctvDerivedMismatch", [prim], "Drivers do not resolve to optics: " + str(exc))]
    faults = ["%s %g vs %g" % (n, a.Get(), o[n]) for n, a in derived.items()
              if a.HasAuthoredValue() and abs(a.Get() - o[n]) > ANGLE_TOLERANCE]
    return [_issue("cctvDerivedMismatch", [prim], "Derived optics disagree with the drivers: " + "; ".join(faults))] if faults else []


def _mount_frame(stage, time_range):
    issues = []
    from .study import Settings, camera_selected, phase_included
    bands = [(s, tuple(s.GetAttribute("aeco:cctvStudy:mountHeightRange").Get())) for s in iter_studies(stage)]
    bands = [(s, b) for s, b in bands if b != (0, 0)]
    for camera in iter_cameras(stage):
        mount = camera.GetAttribute("aeco:cctv:mount").Get()
        sensors = sensors_of(camera)
        if mount in DOWNWARD_MOUNTS:
            for sensor in sensors:
                if sensor.GetAttribute(PREFIX + "tilt").Get() < -ANGLE_TOLERANCE:
                    issues.append(_issue("cctvMountFrame", [camera, sensor], "%s mount with a sensor tilted upward (%g)"
                                         % (mount, sensor.GetAttribute(PREFIX + "tilt").Get())))
        height = camera.GetAttribute("aeco:cctv:mountHeight")
        if height.HasAuthoredValue():
            for study, (lo, hi) in bands:
                scope = Settings(study)
                if not camera_selected(camera, scope) or not phase_included(camera, scope):
                    continue
                if not lo - LENGTH_TOLERANCE <= height.Get() <= hi + LENGTH_TOLERANCE:
                    issues.append(_issue("cctvMountFrame", [camera, study], "mount height %.3f m outside %s of %s"
                                         % (height.Get(), (lo, hi), study.GetPath())))
    return issues


def _system_capacity(prim, time_range):
    if not prim.HasAPI("AecoCctvSystemAPI"):
        return []
    capacity = prim.GetAttribute("aeco:cctvSystem:recorderCapacity").Get() or 0
    if capacity <= 0:
        return []
    targets = Usd.CollectionAPI(prim, "members").GetIncludesRel().GetTargets()
    members = [t for t in targets if prim.GetStage().GetPrimAtPath(t).HasAPI("AecoCctvCameraAPI")]
    if len(members) > capacity:
        return [_issue("cctvSystemCapacity", [prim], "%d camera members exceed the recorder capacity of %d" % (len(members), capacity))]
    return []


# ----------------------------------------------------------------------------
# Study rules: read the results the coverage engine wrote (design section 6)
# ----------------------------------------------------------------------------

def _results_of(study):
    scope = study.GetChild("Results")
    return [r for r in scope.GetChildren() if r.HasAPI("AecoCctvCoverageAPI")] if scope else []


def _target_of(result):
    targets = result.GetRelationship("aeco:cctvCoverage:target").GetTargets()
    prim = result.GetStage().GetPrimAtPath(targets[0]) if targets else None
    return prim if prim else result


def _blockers(result):
    return [p.name for p in result.GetRelationship("aeco:cctvCoverage:blockers").GetTargets()]


def _has_hash(study):
    attr = study.GetAttribute("aeco:cctvStudy:inputHash")
    return bool(attr and attr.HasAuthoredValue() and attr.Get())


def _result_problems(study):
    from .study import Settings, phase_included
    stage = study.GetStage()
    try:
        settings = Settings(study)
    except ValueError as exc:
        return [(study, str(exc))]
    expected = {p for p in settings.targets
                if not stage.GetPrimAtPath(p) or phase_included(stage.GetPrimAtPath(p), settings)}
    by_target = defaultdict(list)
    problems = []
    for result in _results_of(study):
        links = result.GetRelationship("aeco:cctvCoverage:target").GetTargets()
        if len(links) != 1 or not stage.GetPrimAtPath(links[0]) or not stage.GetPrimAtPath(links[0]).IsActive():
            problems.append((result, "target relationship must resolve to exactly one target"))
            continue
        by_target[links[0]].append(result)
        if links[0] not in expected:
            problems.append((result, "result target is outside the active targets collection"))
        def get(name):
            return result.GetAttribute("aeco:cctvCoverage:" + name).Get()
        if any(not result.GetAttribute("aeco:cctvCoverage:" + n).HasAuthoredValueOpinion()
               for n in ("density", "fraction", "dutyFraction", "fixedCoverage", "level", "enclosedSamples")):
            problems.append((result, "required result values are unauthored"))
        enclosed = get("enclosedSamples")
        if type(enclosed) is not int or enclosed < 0:
            problems.append((result, "enclosedSamples must be a nonnegative integer"))
        elif enclosed:
            from .study import target_points
            if enclosed > len(target_points(stage, stage.GetPrimAtPath(links[0]))):
                problems.append((result, "enclosedSamples exceeds the target sample count"))
        values = {n: get(n) for n in ("requiredDensity", "density", "fraction", "dutyFraction", "nearestViewDistance")}
        if any(v is None or not math.isfinite(v) or v < 0 for v in values.values()):
            problems.append((result, "result numbers must be finite and nonnegative"))
            continue
        if values["fraction"] > 1 or values["dutyFraction"] > 1:
            problems.append((result, "fraction and dutyFraction must be in [0, 1]"))
        paths = result.GetRelationship("aeco:cctvCoverage:views").GetTargets()
        views = [stage.GetPrimAtPath(p) for p in paths]
        if any(not v or not v.HasAPI("AecoCctvSensorAPI") for v in views):
            problems.append((result, "covering views must resolve to sensors"))
            continue
        fixed = any(not v.GetAttribute(PREFIX + "motorised").Get() for v in views)
        from .study import sampling
        try:
            fractional = sampling(stage.GetPrimAtPath(links[0]))[1] > 0
        except ValueError as exc:
            problems.append((result, str(exc)))
            continue
        bad_fixed = (bool(get("fixedCoverage")) != fixed) if not fractional else (get("fixedCoverage") and not fixed)
        if bad_fixed or (get("fixedCoverage") and values["dutyFraction"] != 1) or (not views and values["dutyFraction"] != 0):
            problems.append((result, "fixedCoverage/dutyFraction disagree with covering sensors"))
        radar = bool(views) and all(v.GetAttribute(PREFIX + "spectrum").Get() == "radar" or
                                  v.GetAttribute(PREFIX + "pixels").Get()[0] <= 0 for v in views)
        level = min(settings.ladder, key=settings.ladder.get) if radar else level_name(values["density"], settings.ladder)
        if get("level") != level:
            problems.append((result, "level disagrees with density and the study ladder"))
    for path in sorted(expected):
        if len(by_target[path]) != 1:
            problems.append((stage.GetPrimAtPath(path) or study,
                             "%s needs exactly one active result; found %d" % (path, len(by_target[path]))))
    return problems


def _has_results(study):
    return _has_hash(study) and not _result_problems(study)


def _incomplete(prim, time_range):
    if not prim.HasAPI("AecoCctvStudyAPI") or not _has_hash(prim):
        return []
    return [_issue("cctvStudyIncomplete", [prim, site], reason) for site, reason in _result_problems(prim)]


def _missing_results(prim, time_range):
    if not prim.HasAPI("AecoCctvStudyAPI") or _has_hash(prim):
        return []
    return [_issue("cctvStudyMissingResults", [prim], "coverage study has never been run (aeco-cctv study %s)" % prim.GetPath(), "info")]


def _stale(prim, time_range):
    if not prim.HasAPI("AecoCctvStudyAPI") or not _has_hash(prim):
        return []
    from .study import input_hash
    try:
        current = input_hash(prim.GetStage(), prim)
    except ValueError as exc:
        return [_issue("cctvStudyStale", [prim], "coverage inputs no longer resolve: " + str(exc))]
    if current != prim.GetAttribute("aeco:cctvStudy:inputHash").Get():
        return [_issue("cctvStudyStale", [prim], "coverage results are older than their inputs; re-run aeco-cctv study %s" % prim.GetPath())]
    return []


def _mostly_enclosed(prim, time_range):
    if not prim.HasAPI("AecoCctvStudyAPI") or not _has_results(prim):
        return []
    from .study import target_points
    issues = []
    for result in _results_of(prim):
        enclosed = result.GetAttribute("aeco:cctvCoverage:enclosedSamples").Get()
        if not enclosed:
            continue
        target = _target_of(result)
        total = len(target_points(prim.GetStage(), target))
        if enclosed > total / 2:
            issues.append(_issue("cctvTargetMostlyEnclosed", [target, prim],
                "%s: %d/%d samples enclosed by opaque bodies; redefine the target region" % (
                    target.GetPath(), enclosed, total)))
    return issues


def _uncovered(prim, time_range):
    if not prim.HasAPI("AecoCctvStudyAPI") or not _has_results(prim):
        return []
    issues = []
    for result in _results_of(prim):
        if result.GetRelationship("aeco:cctvCoverage:views").GetTargets():
            continue
        target = _target_of(result)
        if prim.GetAttribute("aeco:cctvStudy:excludeEnclosedSamples").Get():
            enclosed = result.GetAttribute("aeco:cctvCoverage:enclosedSamples").Get()
            if enclosed:
                from .study import target_points
                if enclosed == len(target_points(prim.GetStage(), target)):
                    continue  # No standable samples; the region warning applies.
        required = result.GetAttribute("aeco:cctvCoverage:requiredDensity").Get()
        density = result.GetAttribute("aeco:cctvCoverage:density").Get()
        level = result.GetAttribute("aeco:cctvCoverage:level").Get()
        blockers = _blockers(result)
        issues.append(_issue("cctvTargetUncovered", [target, prim, *[result.GetStage().GetPrimAtPath(p)
            for p in result.GetRelationship("aeco:cctvCoverage:blockers").GetTargets() if result.GetStage().GetPrimAtPath(p)]], "%s: %g px/m required, best %g px/m (%s)%s" % (
            target.GetName(), required, density, level,
            ("; sightlines blocked by " + ", ".join(blockers)) if blockers else ""), "error"))
    return issues


def _ptz_sole(prim, time_range):
    if not prim.HasAPI("AecoCctvStudyAPI") or not _has_results(prim):
        return []
    if prim.GetAttribute("aeco:cctvStudy:ptzPolicy").Get() != "presetsNotSole":
        return []
    issues = []
    for result in _results_of(prim):
        if not result.GetRelationship("aeco:cctvCoverage:views").GetTargets():
            continue
        if result.GetAttribute("aeco:cctvCoverage:fixedCoverage").Get():
            continue
        target = _target_of(result)
        blockers = _blockers(result)
        issues.append(_issue("cctvPtzSoleCoverage", [target], "%s: %g px/m only from motorised presets, duty fraction %.3f%s" % (
            target.GetName(), result.GetAttribute("aeco:cctvCoverage:requiredDensity").Get(),
            result.GetAttribute("aeco:cctvCoverage:dutyFraction").Get(),
            ("; fixed sightlines blocked by " + ", ".join(blockers)) if blockers else "")))
    return issues


def _too_far(prim, time_range):
    if not prim.HasAPI("AecoCctvStudyAPI") or not _has_results(prim):
        return []
    limit = prim.GetAttribute("aeco:cctvStudy:maxTargetDistance").Get() or 0
    if limit <= 0:
        return []
    issues = []
    for result in _results_of(prim):
        if not result.GetRelationship("aeco:cctvCoverage:views").GetTargets():
            continue
        nearest = result.GetAttribute("aeco:cctvCoverage:nearestViewDistance").Get()
        if nearest > limit + LENGTH_TOLERANCE:
            target = _target_of(result)
            issues.append(_issue("cctvTargetTooFar", [target], "%s: nearest covering view at %.2f m exceeds maxTargetDistance %g m"
                                 % (target.GetName(), nearest, limit)))
    return issues


def _exclusion_covered(prim, time_range):
    if not prim.HasAPI("AecoCctvStudyAPI") or not _has_results(prim):
        return []
    stage = prim.GetStage()
    issues = []
    for path in prim.GetRelationship("aeco:cctvStudy:exclusionsCovered").GetTargets():
        site = stage.GetPrimAtPath(path)
        responsible = {}
        for spec in prim.GetAttribute("aeco:cctvStudy:inputHash").GetPropertyStack():
            data = spec.layer.customLayerData
            if data.get("aeco:cctv:study") == str(prim.GetPath()):
                responsible = dict(data.get("aeco:cctv:exclusionViews", {}).get(str(path), {}))
                break
        views = [stage.GetPrimAtPath(p) for p in sorted(set(responsible.values())) if stage.GetPrimAtPath(p)]
        issues.append(_issue("cctvExclusionCovered", [site if site else prim, prim, *views],
                             "%s is an exclusion of %s; visible from %s" %
                             (path, prim.GetPath(), ", ".join(sorted(responsible)) or "unrecorded view"), "error"))
    return issues


def _in_view(rule, rel, reason):
    def check(prim, time_range):
        if not prim.HasAPI("AecoCctvStudyAPI") or not _has_results(prim):
            return []
        stage = prim.GetStage()
        return [_issue(rule, [stage.GetPrimAtPath(path) if stage.GetPrimAtPath(path) else prim],
                       "%s is inside a camera view but %s; counted as an obstacle" % (path.name, reason))
                for path in prim.GetRelationship(rel).GetTargets()]
    check.__name__ = rule
    return check


_unphased = _in_view("cctvUnphasedInView", "aeco:cctvStudy:unphasedInView", "carries no authored aeco:phase")
_unclassified = _in_view("cctvUnclassifiedInView", "aeco:cctvStudy:unclassifiedInView",
                         "is not a usdAeco element (no AecoElementAPI, no phase)")

def _projection(prim, time_range):
    if not prim.HasAPI("AecoCctvStudyAPI"):
        return []
    from .study import Settings, camera_selected, sensor_selected, phase_included
    scope = Settings(prim)
    return [_issue("cctvUnsupportedProjection", [s, prim],
                   "%s uses %s; study %s requires rectilinear projection" %
                   (s.GetPath(), s.GetAttribute(PREFIX + "projection").Get(), prim.GetPath()), "error")
            for c in iter_cameras(prim.GetStage()) if camera_selected(c, scope) and phase_included(c, scope)
            for s in sensors_of(c) if sensor_selected(s, scope) and phase_included(s, scope)
            and s.GetAttribute(PREFIX + "projection").Get() != "rectilinear"]


def _unphased_provider(prim, time_range):
    if not prim.HasAPI("AecoCctvStudyAPI"):
        return []
    from .study import Settings, phase_included, phase_state, camera_selected
    settings = Settings(prim)
    if not settings.includeUnphased:
        return []
    candidates = [c for c in iter_cameras(prim.GetStage()) if camera_selected(c, settings)]
    candidates += [prim.GetStage().GetPrimAtPath(p) for p in settings.targets]
    return [_issue("cctvUnphasedProvider", [p, prim],
                   "%s has no authored phase; included by %s" % (p.GetPath(), prim.GetPath()))
            for p in candidates if p and phase_included(p, settings) and not phase_state(p)[0]]


_STUDY_RULES = (
    ("cctvUnsupportedProjection", _projection, False, "Error: an included study sensor is not rectilinear."),
    ("cctvStudyIncomplete", _incomplete, False, "Warn: result set is missing, duplicated or invalid."),
    ("cctvUnphasedProvider", _unphased_provider, False, "Warn: an included camera or target has no authored phase."),
    ("cctvStudyMissingResults", _missing_results, False, "Info: the study has never been run."),
    ("cctvStudyStale", _stale, False, "Warn: the study's inputHash differs from the current inputs."),
    ("cctvTargetUncovered", _uncovered, False, "Error: no view covers the target at its requirement; names the blockers."),
    ("cctvTargetMostlyEnclosed", _mostly_enclosed, False, "Warn: more than half the target samples are enclosed; redefine the region."),
    ("cctvPtzSoleCoverage", _ptz_sole, False, "Warn: under presetsNotSole the requirement is met only by motorised presets (duty fraction in the message)."),
    ("cctvTargetTooFar", _too_far, False, "Warn: no covering view within the study's maxTargetDistance."),
    ("cctvExclusionCovered", _exclusion_covered, False, "Error: a view sees a sample point of an exclusion."),
    ("cctvUnphasedInView", _unphased, False, "Warn: an element without an authored aeco:phase inside a view (counted as an obstacle)."),
    ("cctvUnclassifiedInView", _unclassified, False, "Warn: a non-element gprim inside a view (counted as an obstacle)."),
)


STUDY_RULES = tuple(row[0] for row in _STUDY_RULES)

_RULES = (
    ("cctvInvalidSampling", _sampling, False, "Error: sample grid/fraction drivers are invalid."),
    ("cctvMissingOptics", _missing_optics, False, "Error: a sensor has no source optics evidence."),
    ("cctvUnsupportedTopology", _topology, False, "Error: a mesh has unsupported or malformed topology."),
    ("cctvKindMismatch", _kind, False, "Warn: camera API classification must be IfcAudioVisualAppliance or a registered camera class."),
    ("cctvSensorMissing", _sensor_missing, False, "Error: a camera element needs a Camera child wearing AecoCctvSensorAPI."),
    ("cctvSensorOrphan", _sensor_orphan, False, "Warn: sensor API on a Camera whose parent is not a camera element."),
    ("cctvOutOfEnvelope", _envelope, False, "Error: pan, tilt or focal length (presets included) outside the sensor's ranges."),
    ("cctvNativeCameraAuthored", _native, False, "Warn: stock camera attributes or xformOps authored on a sensor outside the derived layer."),
    ("cctvDerivedMismatch", _mismatch, False, "Warn: derived hfov/vfov/effectiveWidth disagree with a recomputation from the drivers."),
    ("cctvMountFrame", _mount_frame, True, "Warn: downward mount tilted upward, or mount height outside a study's mountHeightRange."),
    ("cctvSystemCapacity", _system_capacity, False, "Warn: camera members of a system exceed its recorderCapacity."),
) + _STUDY_RULES


def register():
    """Idempotently register all rules, including after a Python module reload."""
    from pathlib import Path
    import sys
    from pxr import Plug
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    Plug.Registry().RegisterPlugins(str(root / "usdAecoCctvValidators"))
    reg = UsdValidation.ValidationRegistry()
    for meta in reg.GetValidatorMetadataForKeyword(KEYWORD):
        reg.GetOrLoadValidatorByName(meta.name)


def register_core_validators():
    """Require the core Python plugin, not just its discoverable metadata."""
    from pxr import Plug
    from usdaeco_tools import validators as core
    core.register()
    try:
        import_module("usdAecoValidators")
    except ImportError as exc:
        raise RuntimeError("Cannot import usdAecoValidators; set PYTHONPATH to the core repository root") from exc
    plugin = Plug.Registry().GetPluginWithName("usdAecoValidators")
    if not plugin or not plugin.isPythonModule or not plugin.Load() or not plugin.isLoaded:
        raise RuntimeError("usdAecoValidators Python plugin did not load")
    return core.KEYWORD


def validate_stage(stage, include_core=False, include_builtin=True, profile=None):
    """Validate static/default-time cctv data, optionally with core rules, graded by a profile."""
    register()
    keywords = [KEYWORD]
    if include_core:
        keywords.append(register_core_validators())
    if include_builtin and load_profile(profile).get("include_builtin", True):
        keywords.append("UsdCoreValidators")
    # The shared adapter rejects empty keywords and partially loaded rule sets.
    from usdaeco_check.validation import run
    return apply_profile(run(stage, keywords), profile)


def split(issues):
    return ([e for e in issues if e.GetType() == UsdValidation.ValidationErrorType.Error],
            [e for e in issues if e.GetType() != UsdValidation.ValidationErrorType.Error])
