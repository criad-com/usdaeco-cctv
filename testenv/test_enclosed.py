"""Opaque solid sampling: both kernels, denominator semantics and honest findings."""
import numpy as np
import pytest
from pxr import Gf, Sdf, Usd, UsdGeom, UsdValidation

from test_study import _copy, LOBBY, STUDY, run_study
from usdaeco_cctv.bench import boxes
from usdaeco_cctv.raycast import Scene, embree_available
from usdaeco_cctv.study import input_hash, target_points
from usdaeco_cctv.validators import validate_stage

KERNELS = ["numpy", pytest.param("embree", marks=pytest.mark.skipif(
    not embree_available(), reason="embree unavailable"))]


def box_triangles(lo=(-1, -1, -1), hi=(1, 1, 1)):
    triangles, _ = boxes(1, extent=(0, 0, 0))
    low, high = triangles.min(axis=(0, 1)), triangles.max(axis=(0, 1))
    return (triangles - low) / (high - low) * (np.array(hi) - lo) + lo


@pytest.mark.parametrize("kernel", KERNELS)
@pytest.mark.parametrize("shape", ["closed", "open_top", "two_open_axes", "plane", "hollow", "overlap", "remote"])
def test_axis_parity(kernel, shape):
    triangles = box_triangles()
    point = np.zeros((1, 3))  # Axis rays land on shared triangle diagonals.
    expected = shape in ("closed", "open_top", "overlap", "remote")
    owners = np.zeros(len(triangles), dtype=int)
    if shape == "open_top":
        triangles = triangles[~(triangles[:, :, 2] == 1).all(axis=1)]
    elif shape == "two_open_axes":
        triangles = triangles[~((triangles[:, :, 2] == 1).all(axis=1) |
                                (triangles[:, :, 0] == 1).all(axis=1))]
    elif shape == "plane":
        triangles = np.array([[[-1, -1, -1], [1, 1, -1], [1, 1, 1]]])
    elif shape == "hollow":
        triangles = np.concatenate([triangles, box_triangles((-.5,)*3, (.5,)*3)])
    elif shape == "overlap":
        triangles = np.concatenate([triangles, triangles + .25])
        owners = np.repeat([0, 1], 12)
    elif shape == "remote":
        triangles += 1e6
        point += 1e6
    if shape != "overlap":
        owners = np.zeros(len(triangles), dtype=int)
    scene = Scene(triangles, owners, kernel)
    assert scene.enclosed(point).tolist() == [expected]
    assert not scene.enclosed(point, skip=set(owners)).any()
    assert not scene.enclosed([[5, 5, 5]]).any()
    assert scene.enclosed([]).size == 0


@pytest.mark.parametrize("kernel", KERNELS)
def test_surface_and_overlapping_gprims(kernel):
    triangles = box_triangles()
    scene = Scene(triangles, np.zeros(12, dtype=int), kernel)
    assert scene.enclosed([[1, 0, 0], [-1, -1, -1], [.999, 0, 0]]).tolist() == [False, False, True]
    # Two separate Body gprims of one owner must form a union, not XOR.
    other = triangles + .25
    bounds = np.array([[t.min(axis=(0, 1)), t.max(axis=(0, 1))] for t in (triangles, other)])
    scene = Scene(np.concatenate([triangles, other]), np.zeros(24, dtype=int), kernel,
                  groups=(bounds, np.array([12, 12]), np.array([0, 0])))
    assert scene.enclosed([[0, 0, 0]]).tolist() == [True]


def cube(stage, path, lo, hi):
    lo, hi = np.array(lo, dtype=float), np.array(hi, dtype=float)
    body = UsdGeom.Cube.Define(stage, path)
    body.CreateSizeAttr(2.)
    body.CreateExtentAttr([Gf.Vec3f(-1), Gf.Vec3f(1)])
    body.AddTranslateOp().Set(Gf.Vec3d(*(lo + hi) / 2))
    body.AddScaleOp().Set(Gf.Vec3f(*(hi - lo) / 2))
    return body.GetPrim()


@pytest.mark.parametrize("kernel", KERNELS)
@pytest.mark.parametrize("count", [1, 3, 5])
def test_lobby_crate(tmp_path, kernel, count):
    stage = _copy()
    door = stage.GetPrimAtPath(LOBBY + "/Door_1")
    door.ApplyAPI("AecoCctvTargetAPI")
    door.GetAttribute("aeco:cctvTarget:passFraction").Set(1.)
    study = stage.GetPrimAtPath(STUDY)
    Usd.CollectionAPI(study, "targets").GetIncludesRel().SetTargets([door.GetPath()])
    baseline = run_study(stage, STUDY, tmp_path / "baseline.usda", kernel=kernel)
    stage.GetSessionLayer().subLayerPaths.clear()
    points = target_points(stage, door)
    assert baseline["results"][str(door.GetPath())]["fraction"] == 1.
    # Small crates isolate enclosure from obstruction of the remaining samples.
    for i, point in enumerate(points[:count]):
        cube(stage, "/Crate_%d" % i, point - .025, point + .025)
    output = tmp_path / "crate.usda"
    report = run_study(stage, STUDY, output, kernel=kernel)
    result = report["results"][str(door.GetPath())]
    assert result["sampleCount"] == 5 and result["enclosedSamples"] == count
    assert result["evaluatedSamples"] == 5 - count
    assert result["fraction"] == (1. if count < 5 else 0.)
    assert bool(result["views"]) == (count < 5)
    result_prim = stage.GetPrimAtPath(STUDY + "/Results/Door_1")
    assert result_prim.GetAttribute("aeco:cctvCoverage:enclosedSamples").Get() == count
    findings = validate_stage(stage)
    mostly = [e for e in findings if e.GetName() == "cctvTargetMostlyEnclosed"]
    assert bool(mostly) == (count > 2)
    if mostly:
        assert mostly[0].GetType() == UsdValidation.ValidationErrorType.Warn
        assert "%d/5" % count in mostly[0].GetMessage()
        assert door in [s.GetPrim() for s in mostly[0].GetSites()]
    assert not any(e.GetName() in ("cctvTargetUncovered", "cctvStudyIncomplete") for e in findings)
    cached = run_study(stage, STUDY, output, kernel=kernel)
    assert cached["results"] == report["results"] and not cached["viewsComputed"]
    stage.GetSessionLayer().subLayerPaths.clear()
    study.GetAttribute("aeco:cctvStudy:excludeEnclosedSamples").Set(False)
    assert input_hash(stage, STUDY) != report["inputHash"]
    legacy = run_study(stage, STUDY, output, kernel=kernel)["results"][str(door.GetPath())]
    assert legacy["enclosedSamples"] == count and legacy["evaluatedSamples"] == 5
    assert legacy["fraction"] == pytest.approx((5 - count) / 5)
    assert not legacy["views"]


def yard_fixture():
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(stage, 1.)
    UsdGeom.SetStageUpAxis(stage, "Z")
    study = stage.DefinePrim("/Study", "Scope")
    study.ApplyAPI("AecoCctvStudyAPI")
    study.GetAttribute("aeco:cctvStudy:raySamples").Set(Gf.Vec2i(8, 6))
    study.GetAttribute("aeco:cctvStudy:requiredDensity").Set(25.)
    camera = UsdGeom.Xform.Define(stage, "/Camera").GetPrim()
    camera.ApplyAPI("AecoElementAPI")
    camera.ApplyAPI("AecoCctvCameraAPI")
    camera.GetAttribute("aeco:phase").Set("proposed")
    UsdGeom.Xformable(camera).AddTranslateOp().Set(Gf.Vec3d(3, 0, 8))
    sensor = UsdGeom.Camera.Define(stage, "/Camera/Sensor_0").GetPrim()
    sensor.ApplyAPI("AecoCctvSensorAPI")
    for name, value in {"focalRange": Gf.Vec2d(3), "hfovRange": Gf.Vec2d(110),
                        "vfovRange": Gf.Vec2d(90), "pixels": Gf.Vec2i(4096, 3072),
                        "tilt": 90., "range": 20.}.items():
        sensor.GetAttribute("aeco:cctvSensor:" + name).Set(value)
    area = stage.DefinePrim("/Yard", "Xform")
    area.ApplyAPI("AecoCctvTargetAPI")
    area.GetAttribute("aeco:cctvTarget:gridSpacing").Set(.5)
    area.GetAttribute("aeco:cctvTarget:passFraction").Set(1.)
    extent = cube(stage, "/Yard/Extent", (1, -2, .1), (5, 2, 3))
    extent.ApplyAPI("AecoDerivedGeometryAPI")
    extent.GetAttribute("aeco:derived:role").Set("extent")
    Usd.CollectionAPI(study, "targets").GetIncludesRel().SetTargets([area.GetPath()])
    # Two elevated equipment pads and a horizontal cylindrical pipe at 1.6 m.
    cube(stage, "/Pad_1", (1, -2, 0), (2, -1, 2))
    cube(stage, "/Pad_2", (4, 1, 0), (5, 2, 2))
    pipe = UsdGeom.Cylinder.Define(stage, "/Pipe")
    pipe.CreateAxisAttr("X")
    pipe.CreateHeightAttr(4.)
    pipe.CreateRadiusAttr(.3)
    pipe.AddTranslateOp().Set(Gf.Vec3d(3, 0, 1.6))
    return stage, study, area


@pytest.mark.parametrize("kernel", KERNELS)
def test_yard_grid(tmp_path, kernel):
    stage, study, area = yard_fixture()
    assert target_points(stage, area).shape == (64, 3)
    report = run_study(stage, "/Study", tmp_path / "yard.usda", kernel=kernel)
    result = report["results"]["/Yard"]
    assert result["sampleCount"] == 64 and result["enclosedSamples"] == 24
    assert result["evaluatedSamples"] == 40 and result["fraction"] == 1.
    assert result["fixedCoverage"] and result["dutyFraction"] == 1.
    uncropped = run_study(stage, "/Study", tmp_path / "uncropped.usda", kernel=kernel, cull=False)
    assert uncropped["results"] == report["results"]
    stage.GetSessionLayer().subLayerPaths.clear()
    study.GetAttribute("aeco:cctvStudy:excludeEnclosedSamples").Set(False)
    legacy = run_study(stage, "/Study", tmp_path / "legacy.usda", kernel=kernel)
    assert legacy["results"]["/Yard"]["fraction"] == 40 / 64
    assert not legacy["results"]["/Yard"]["views"]


@pytest.mark.parametrize("kernel", KERNELS)
@pytest.mark.parametrize("filter", ["opaque", "transparent", "ignore", "phase", "extent"])
def test_obstacle_policy_and_primary(tmp_path, kernel, filter):
    stage = _copy()
    points = target_points(stage, stage.GetPrimAtPath(LOBBY + "/Door_1"))
    door = stage.DefinePrim("/Door", "Xform")
    door.ApplyAPI("AecoElementAPI")
    door.ApplyAPI("AecoCctvTargetAPI")
    door.GetAttribute("aeco:phase").Set("proposed")
    door.GetAttribute("aeco:cctvTarget:points").Set([Gf.Vec3d(*p) for p in points])
    study = stage.GetPrimAtPath(STUDY)
    Usd.CollectionAPI(study, "targets").GetIncludesRel().SetTargets([door.GetPath()])
    point = points[0]
    # Geometry owned by the target still counts for enclosure, although
    # target-ray visibility excludes the target's own body.
    body = cube(stage, str(door.GetPath()) + "/OpaqueLeaf", point - .025, point + .025)
    if filter in ("transparent", "ignore"):
        body.ApplyAPI("AecoCctvSightlineAPI")
        body.GetAttribute("aeco:cctvSightline:" + ("ignore" if filter == "ignore" else "transmittance")).Set(
            True if filter == "ignore" else 1.)
    elif filter == "phase":
        body.ApplyAPI("AecoElementAPI")
        body.GetAttribute("aeco:phase").Set("demolished")
    elif filter == "extent":
        body.ApplyAPI("AecoDerivedGeometryAPI")
        body.GetAttribute("aeco:derived:role").Set("extent")
    report = run_study(stage, STUDY, tmp_path / "policy.usda", kernel=kernel)
    result = report["results"][str(door.GetPath())]
    assert result["enclosedSamples"] == (1 if filter == "opaque" else 0)
    assert result["fixedCoverage"] and result["fraction"] == 1.


@pytest.mark.parametrize("kernel", KERNELS)
def test_half_enclosed_is_not_mostly_and_validator_never_casts(tmp_path, kernel, monkeypatch):
    stage, study, area = yard_fixture()
    area.GetAttribute("aeco:cctvTarget:points").Set([Gf.Vec3d(1.5, -1.5, 1.6), Gf.Vec3d(3, 1, 1.6)])
    run_study(stage, "/Study", tmp_path / "half.usda", kernel=kernel)
    def refuse(*args, **kwargs):
        raise AssertionError("validator cast rays")
    monkeypatch.setattr(Scene, "enclosed", refuse)
    monkeypatch.setattr(Scene, "occlusion", refuse)
    findings = validate_stage(stage)
    assert not any(e.GetName() in ("cctvTargetMostlyEnclosed", "cctvStudyIncomplete") for e in findings)


@pytest.mark.parametrize("fault", ["missing", "negative", "excess"])
def test_enclosure_count_integrity(tmp_path, fault):
    stage, study, _ = yard_fixture()
    run_study(stage, "/Study", tmp_path / "result.usda", kernel="numpy")
    attr = stage.GetPrimAtPath("/Study/Results/Yard").GetAttribute("aeco:cctvCoverage:enclosedSamples")
    layer = Sdf.Layer.FindOrOpen(str(tmp_path / "result.usda"))
    with Usd.EditContext(stage, layer):
        if fault == "missing":
            attr.Clear()
        else:
            attr.Set(-1 if fault == "negative" else 65)
    assert any(e.GetName() == "cctvStudyIncomplete" for e in validate_stage(stage))
