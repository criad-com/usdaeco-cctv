"""Cache freshness and conservative culling across world-space level boundaries."""
import os
from pathlib import Path

import numpy as np
import pytest
from pxr import Gf, Sdf, Usd, UsdGeom, Vt

import usdaeco_cctv.study as engine
from usdaeco_cctv.raycast import Frustum, embree_available
from test_study import LOBBY, STUDY, _copy
from bench_facility import assert_culling_parity, author_study

KERNELS = ["numpy"] + (["embree"] if embree_available() else [])


def test_geometry_and_scene_buffers_are_immutable_and_reused(monkeypatch):
    stage = _copy()
    engine.clear_caches()
    settings = engine.Settings(stage.GetPrimAtPath(STUDY))
    owners, triangles, index = engine.gather_obstacles(stage, settings)
    assert not triangles.flags.writeable and not index.flags.writeable
    assert all(not t.flags.writeable for t in [o.triangles for o in owners])
    def unexpected(*args, **kwargs):
        raise AssertionError("unchanged geometry was triangulated")
    monkeypatch.setattr(engine, "_gprim_triangles", unexpected)
    repeated = engine.gather_obstacles(stage, engine.Settings(stage.GetPrimAtPath(STUDY)))
    assert repeated[1] is triangles and repeated[2] is index
    engine.input_hash(stage, STUDY)
    timings = {}
    engine.input_hash(stage, STUDY, timings=timings)
    assert timings["triangulation"] == timings["bvhBuild"] == timings["depthCasting"] == 0


def test_bvh_reuse_and_complete_timing_accounting(tmp_path):
    from usdaeco_cctv.performance import STAGES
    stage = _copy()
    engine.clear_caches()
    first = engine.run_study(stage, STUDY, tmp_path / "cached.usda", kernel="numpy")
    stage.GetSessionLayer().subLayerPaths.remove(first["layer"])
    second = engine.run_study(stage, STUDY, tmp_path / "cached.usda", kernel="numpy")
    assert second["results"] == first["results"]
    assert len(second["viewsReused"]) == second["views"]
    assert second["timings"]["bvhBuild"] == second["timings"]["triangulation"] == 0
    assert set(second["timings"]) == set(STAGES)
    assert sum(second["timings"].values()) == pytest.approx(second["seconds"], abs=.005)


@pytest.mark.parametrize("change", ["points", "indices", "holes", "transform", "phase", "purpose", "inherit", "session", "registry", "algorithm", "noop"])
def test_cache_invalidates_effective_inputs(monkeypatch, change):
    stage = _copy()
    before = engine.input_hash(stage, STUDY)
    mesh = next(p for p in stage.Traverse() if p.IsA(UsdGeom.Mesh))
    if change == "points":
        # Translate all points to preserve planar quads.
        mesh.GetAttribute("points").Set([p + Gf.Vec3f(.1, 0, 0) for p in mesh.GetAttribute("points").Get()])
    elif change == "indices":
        idx = list(mesh.GetAttribute("faceVertexIndices").Get())
        counts = list(mesh.GetAttribute("faceVertexCounts").Get())
        idx[:counts[0]] = idx[:counts[0]][::-1]
        mesh.GetAttribute("faceVertexIndices").Set(idx)
    elif change == "holes":
        mesh.GetAttribute("holeIndices").Set([0])
    elif change == "transform":
        UsdGeom.Xformable(mesh.GetParent()).AddTranslateOp(opSuffix="cacheTest").Set((.01, 0, 0))
    elif change == "phase":
        stage.GetPrimAtPath(LOBBY + "/Cam_1").GetAttribute("aeco:phase").Set("demolished")
    elif change == "purpose":
        UsdGeom.Imageable(mesh).CreatePurposeAttr("guide")
    elif change == "inherit":
        from usdaeco_cctv import camera_type_of
        parent = camera_type_of(stage.GetPrimAtPath(LOBBY + "/Cam_1"))
        sensor = next(c for c in parent.GetAllChildren() if c.IsA(UsdGeom.Camera))
        sensor.GetAttribute("aeco:cctvSensor:pixels").Set(Gf.Vec2i(1000, 1000))
    elif change == "session":
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            stage.GetPrimAtPath(LOBBY + "/Cam_1/Sensor_0").GetAttribute("aeco:cctvSensor:pan").Set(15.)
    elif change == "registry":
        original = engine.registry
        def registry(name):
            result = original(name)
            if name == "density_levels":
                result["dori2015"]["identify"] += 1
            return result
        monkeypatch.setattr(engine, "registry", registry)
    elif change == "algorithm":
        monkeypatch.setattr(engine, "ALGORITHM_REVISION", "test-revision")
    else:
        mesh.GetAttribute("points").Set(mesh.GetAttribute("points").Get())
    after = engine.input_hash(stage, STUDY)
    assert (after == before) == (change == "noop")
    engine.clear_caches()
    assert engine.input_hash(stage, STUDY) == after


def test_quad_fast_path_keeps_winding_holes_and_refusals(monkeypatch):
    stage = Usd.Stage.CreateInMemory()
    mesh = UsdGeom.Mesh.Define(stage, "/Quads")
    mesh.CreatePointsAttr([(0, 0, 0), (3, 0, 0), (2, 2, 0), (0, 2, 0)])
    mesh.CreateFaceVertexCountsAttr([4, 4])
    mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 3, 3, 2, 1, 0])
    mesh.CreateHoleIndicesAttr([1])
    def unexpected(*args):
        raise AssertionError("convex quad called ear clipping")
    monkeypatch.setattr(engine, "_ear_clip", unexpected)
    tri, points = engine._triangulate(mesh, np.eye(4), Usd.TimeCode.Default())
    assert np.array_equal(tri, points[[(3, 0, 1), (1, 2, 3)], :])
    mesh.GetHoleIndicesAttr().Set([0, 1])
    assert len(engine._triangulate(mesh, np.eye(4), Usd.TimeCode.Default())[0]) == 0


def cross_level_stage():
    """Tall hall, stair landing and an exterior camera looking across two levels."""
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(stage, 1.)
    UsdGeom.SetStageUpAxis(stage, "Z")
    stage.SetMetadata("fallbackPrimTypes", {"AecoLevel": Vt.TokenArray(["Xform"])})
    low = stage.DefinePrim("/Model/Lower", "AecoLevel")
    high = stage.DefinePrim("/Model/Upper", "AecoLevel")
    high.GetAttribute("aeco:elevation").Set(5.)
    cameras = []
    for name, container, position, tilt in (("TallHall", low, (0, 0, 8), 40.),
                                             ("Stair", high, (0, 3, 6), 35.),
                                             ("ExteriorYard", low, (-8, -4, 9), 25.)):
        camera = UsdGeom.Xform.Define(stage, container.GetPath().AppendChild(name)).GetPrim()
        camera.ApplyAPI("AecoElementAPI")
        camera.ApplyAPI("AecoCctvCameraAPI")
        camera.GetAttribute("aeco:phase").Set("proposed")
        UsdGeom.Xformable(camera).AddTranslateOp().Set(position)
        sensor = UsdGeom.Camera.Define(stage, camera.GetPath().AppendChild("Sensor_0")).GetPrim()
        sensor.ApplyAPI("AecoCctvSensorAPI")
        for key, value in {"hfovRange": Gf.Vec2d(104, 34), "vfovRange": Gf.Vec2d(76, 26),
                           "focalRange": Gf.Vec2d(3, 8.5), "pixels": Gf.Vec2i(2592, 1944),
                           "tilt": tilt, "range": 35.}.items():
            sensor.GetAttribute("aeco:cctvSensor:" + key).Set(value)
        cameras.append(camera.GetPath())
    targets = []
    for name, position in (("HallThreshold", (9, 0, 1.5)), ("Landing", (7, 3, 1.5)), ("YardGate", (8, -4, 1.5))):
        target = UsdGeom.Xform.Define(stage, low.GetPath().AppendChild(name)).GetPrim()
        target.ApplyAPI("AecoCctvTargetAPI")
        target.GetAttribute("aeco:cctvTarget:points").Set([Gf.Vec3f(*position)])
        targets.append(target.GetPath())
    # Both blockers belong to a different nominal level from at least one camera.
    for name, container, position, scale in (("TallScreen", high, (5, 0, 4), (.15, 1, 8)),
                                             ("StairScreen", low, (4, 3, 3.5), (.15, 1, 4))):
        cube = UsdGeom.Cube.Define(stage, container.GetPath().AppendChild(name))
        cube.CreateSizeAttr(1.)
        UsdGeom.Xformable(cube).AddTranslateOp().Set(position)
        UsdGeom.Xformable(cube).AddScaleOp().Set(scale)
    study = stage.DefinePrim("/Model/Study", "Scope")
    study.ApplyAPI("AecoCctvStudyAPI")
    study.GetAttribute("aeco:cctvStudy:requiredDensity").Set(25.)
    study.GetAttribute("aeco:cctvStudy:raySamples").Set(Gf.Vec2i(24, 14))
    Usd.CollectionAPI(study, "targets").GetIncludesRel().SetTargets(targets)
    Usd.CollectionAPI(study, "cameras").GetIncludesRel().SetTargets(cameras)
    return stage, study


@pytest.mark.parametrize("kernel", KERNELS)
@pytest.mark.parametrize("fixture", ["lobby", "cross-level"])
def test_every_culled_result_and_blocker_matches_uncropped(tmp_path, kernel, fixture):
    stage, study = (_copy(), STUDY) if fixture == "lobby" else cross_level_stage()
    cropped = engine.run_study(stage, study, tmp_path / "cropped.usda", kernel=kernel, cull=True)
    uncropped = engine.run_study(stage, study, tmp_path / "uncropped.usda", kernel=kernel, cull=False)
    comparison = assert_culling_parity(cropped, uncropped)
    assert comparison["viewTargets"] > 0 and comparison["targets"] == 3
    if fixture == "cross-level":
        assert comparison["blockers"] >= 2
        assert any(r["fixedCoverage"] for r in cropped["results"].values())


def test_culling_retains_near_origin_blockers_and_large_bounds():
    frustum = Frustum(np.zeros(3), np.eye(3), 1., 1., 20.)
    lo = np.array([[-.01, -.01, -.02], [-100, -100, -100], [30, 30, 30]])
    hi = np.array([[.01, .01, -.01], [100, 100, 100], [31, 31, 31]])
    assert frustum.intersects_boxes(lo, hi).tolist() == [True, True, False]


@pytest.mark.parametrize("kernel", KERNELS)
def test_partial_target_keeps_outside_sample_blocker(tmp_path, kernel):
    stage, study = cross_level_stage()
    target = stage.GetPrimAtPath("/Model/Lower/HallThreshold")
    # The primary point lies outside all frusta; a secondary point lies inside.
    target.GetAttribute("aeco:cctvTarget:points").Set([(0, -30, 1.5), (9, 0, 1.5)])
    screen = UsdGeom.Cube.Define(stage, "/Model/Lower/OutsideScreen")
    screen.CreateSizeAttr(1.)
    UsdGeom.Xformable(screen).AddTranslateOp().Set((0, -20, 3))
    UsdGeom.Xformable(screen).AddScaleOp().Set((2, .2, 10))
    cropped = engine.run_study(stage, study, tmp_path / "cropped.usda", kernel=kernel)
    uncropped = engine.run_study(stage, study, tmp_path / "uncropped.usda", kernel=kernel, cull=False)
    assert_culling_parity(cropped, uncropped)
    assert "/Model/Lower/OutsideScreen" in cropped["unclassifiedInView"]


@pytest.mark.parametrize("configuration", ["fixed32", "generated"])
def test_facility_culling(tmp_path, configuration):
    path = os.environ.get("AECO_FACILITY_STAGE")
    if not path:
        pytest.skip("set AECO_FACILITY_STAGE to the generated, imported facility stage")
    stage = Usd.Stage.Open(str(Path(path).resolve()))
    study, census = author_study(stage, configuration)
    assert census["doors"] == 41 and census["generatedCameras"] == 29
    cropped = engine.run_study(stage, study, tmp_path / "cropped.usda", kernel="embree", cull=True)
    uncropped = engine.run_study(stage, study, tmp_path / "uncropped.usda", kernel="embree", cull=False)
    assert cropped["triangles"] >= 86840
    assert_culling_parity(cropped, uncropped)


def test_facility_cross_level_culling(tmp_path):
    path = os.environ.get("AECO_FACILITY_STAGE")
    if not path:
        pytest.skip("set AECO_FACILITY_STAGE to the generated, imported facility stage")
    stage = Usd.Stage.Open(str(Path(path).resolve()))
    study, _ = author_study(stage, "fixed32")
    stage.SetEditTarget(stage.GetSessionLayer())
    doors = [stage.GetPrimAtPath(p) for p in engine.Settings(study).targets]
    cameras = []
    for i, (name, door_name, delta) in enumerate((
            ("TallHall", "door_hall_a_s", (1., 2., 9.)),
            ("Stair", "door_stair_0", (2., 1., 6.)),
            ("ExteriorYard", "door_hall_b_e", (12., 0., 7.)))):
        door = next(p for p in doors if p.GetName() == door_name)
        target = engine.target_points(stage, door)[0]
        origin = target + delta
        camera = stage.GetPrimAtPath("/Bench/Cam_%02d" % i)
        camera.SetDisplayName(name)
        camera.GetAttribute("xformOp:translate").Set(Gf.Vec3d(*origin))
        sensor = stage.GetPrimAtPath(camera.GetPath().AppendChild("Sensor_0"))
        direction = target - origin
        sensor.GetAttribute("aeco:cctvSensor:pan").Set(float(np.degrees(np.arctan2(direction[1], direction[0]))))
        sensor.GetAttribute("aeco:cctvSensor:tilt").Set(float(np.degrees(np.arctan2(-direction[2], np.linalg.norm(direction[:2])))))
        sensor.GetAttribute("aeco:cctvSensor:range").Set(40.)
        cameras.append(camera.GetPath())
    Usd.CollectionAPI(study, "cameras").GetIncludesRel().SetTargets(cameras)
    cropped = engine.run_study(stage, study, tmp_path / "cross-cropped.usda", kernel="embree")
    uncropped = engine.run_study(stage, study, tmp_path / "cross-uncropped.usda", kernel="embree", cull=False)
    comparison = assert_culling_parity(cropped, uncropped)
    assert comparison["views"] == 3 and comparison["viewTargets"] >= 3

@pytest.mark.parametrize('kernel', KERNELS)
@pytest.mark.parametrize('shells', [True, False])
def test_unchanged_publication_is_not_rewritten(tmp_path, monkeypatch, kernel, shells):
    import hashlib
    stage = _copy()
    stage.GetPrimAtPath(STUDY).GetAttribute('aeco:cctvStudy:writeShells').Set(shells)
    output = tmp_path / 'cached.usdc'
    first = engine.run_study(stage, STUDY, output, kernel=kernel)
    signature = (output.stat().st_mtime_ns, hashlib.sha256(output.read_bytes()).digest())
    stage.GetSessionLayer().subLayerPaths.remove(first['layer'])
    def unexpected(*args, **kwargs):
        raise AssertionError('unchanged scene was gathered or hashed')
    with monkeypatch.context() as patch:
        patch.setattr(engine, 'gather_obstacles', unexpected)
        patch.setattr(engine, '_geometry_inputs', unexpected)
        second = engine.run_study(stage, STUDY, output, kernel=kernel)
    assert not second['outputChanged']
    assert second['results'] == first['results']
    assert second['timings']['triangulation'] == 0
    assert signature == (output.stat().st_mtime_ns, hashlib.sha256(output.read_bytes()).digest())
    stage.GetSessionLayer().subLayerPaths.remove(first['layer'])
    engine.clear_caches()
    third = engine.run_study(stage, STUDY, output, kernel=kernel)
    assert not third['outputChanged']
    assert signature == (output.stat().st_mtime_ns, hashlib.sha256(output.read_bytes()).digest())


@pytest.mark.parametrize('location', ['root', 'session'])
@pytest.mark.parametrize('note_path', ['/Notes', LOBBY + '/Notes'])
def test_unrelated_non_obstacle_edit_reuses_gather(tmp_path, monkeypatch, location, note_path):
    stage = _copy()
    note = stage.DefinePrim(note_path, 'Scope')
    note.CreateAttribute('label', Sdf.ValueTypeNames.String).Set('before')
    first = engine.input_hash(stage, STUDY)
    with Usd.EditContext(stage, stage.GetRootLayer() if location == 'root' else stage.GetSessionLayer()):
        note.GetAttribute('label').Set('after')
    def unexpected(*args, **kwargs):
        raise AssertionError('unrelated note invalidated scene gathering')
    monkeypatch.setattr(engine, 'gather_obstacles', unexpected)
    assert engine.input_hash(stage, STUDY) == first


@pytest.mark.parametrize('change', ['new-gprim', 'muted-layer', 'guide-purpose', 'dirty-twice', 'session-transform'])
def test_gather_memo_invalidation_matches_fresh(change):
    stage = _copy()
    extra = Sdf.Layer.CreateAnonymous()
    stage.GetSessionLayer().subLayerPaths.append(extra.identifier)
    with Usd.EditContext(stage, extra):
        cube = UsdGeom.Cube.Define(stage, '/Extra')
        cube.CreateSizeAttr(1.)
        cube.CreatePurposeAttr('guide' if change == 'guide-purpose' else 'default')
    first = engine.input_hash(stage, STUDY)
    stage.SetEditTarget(extra)
    if change == 'new-gprim':
        UsdGeom.Cube.Define(stage, '/New')
    elif change == 'muted-layer':
        stage.MuteLayer(extra.identifier)
    elif change == 'guide-purpose':
        cube.GetPurposeAttr().Set('default')
    elif change == 'dirty-twice':
        cube.GetSizeAttr().Set(2.)
        assert engine.input_hash(stage, STUDY) != first
        cube.GetSizeAttr().Set(3.)
    else:
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            UsdGeom.Xformable(cube).AddTranslateOp().Set((1., 2., 3.))
    second = engine.input_hash(stage, STUDY)
    assert second != first
    engine.clear_caches()
    assert engine.input_hash(stage, STUDY) == second

@pytest.mark.parametrize('change', ['attribute', 'deleted-file'])
def test_cached_publication_repaired_after_output_change(tmp_path, change):
    stage = _copy()
    output = tmp_path / 'analysis.usdc'
    first = engine.run_study(stage, STUDY, output, kernel='numpy')
    stage.GetSessionLayer().subLayerPaths.remove(first['layer'])
    layer = Sdf.Layer.FindOrOpen(str(output))
    if change == 'attribute':
        spec = layer.GetAttributeAtPath(STUDY + '/Results/Door_1.aeco:cctvCoverage:density')
        spec.default = -10.
        layer.Save()
    else:
        output.unlink()
    second = engine.run_study(stage, STUDY, output, kernel='numpy')
    assert second['outputChanged']
    assert second['results'] == first['results']
    from usdaeco_cctv.validators import _result_problems
    assert not _result_problems(stage.GetPrimAtPath(STUDY))


def test_translated_mesh_topology_matches_direct_and_rejects_collapse():
    stage = Usd.Stage.CreateInMemory()
    mesh = UsdGeom.Mesh.Define(stage, '/Shape')
    mesh.CreatePointsAttr([(0, 0, 0), (1, 0, 0), (0, 1, 0)])
    mesh.CreateFaceVertexCountsAttr([3])
    mesh.CreateFaceVertexIndicesAttr([0, 1, 2])
    mesh.GetPrim().ApplyAPI('AecoElementAPI')
    mesh.GetPrim().GetAttribute('aeco:phase').Set('proposed')
    study = stage.DefinePrim('/Study', 'Scope')
    study.ApplyAPI('AecoCctvStudyAPI')
    transform = UsdGeom.Xformable(mesh).AddTranslateOp()
    for position in ((0., 0., 0.), (3., -8., 2.)):
        transform.Set(position)
        _, actual, _ = engine.gather_obstacles(stage, engine.Settings(study))
        world = np.asarray(UsdGeom.XformCache().GetLocalToWorldTransform(mesh.GetPrim()))
        expected, _ = engine._gprim_triangles(mesh.GetPrim(), world, Usd.TimeCode.Default())
        # Default USD units are centimetres.
        assert np.array_equal(actual, expected * UsdGeom.GetStageMetersPerUnit(stage))
    transform.Set((1e20, 1e20, 1e20))
    with pytest.raises(engine.UnsupportedTopology, match='degenerate triangle'):
        engine.gather_obstacles(stage, engine.Settings(study))
