"""The coverage study on the lobby example: results, hash, layers, incremental runs."""
from pathlib import Path

import pytest
from pxr import Sdf, Usd, UsdGeom

from usdaeco_cctv import validators
from usdaeco_cctv.study import input_hash, run_study as study_run, target_points

def run_study(stage, path, output, **kwargs):
    # Repeated publication explicitly detaches the previous output first.
    paths = stage.GetSessionLayer().subLayerPaths
    if str(output) in paths:
        paths.remove(str(output))
    return study_run(stage, path, output, **kwargs)

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "lobby.usda"
STUDY = "/CctvLobby/Analyses/DoorCoverage"
LOBBY = "/CctvLobby/Site/Building/L0/Lobby"


def _copy():
    source = Sdf.Layer.FindOrOpen(str(EXAMPLE))
    layer = Sdf.Layer.CreateAnonymous("lobby-test.usda")
    layer.TransferContent(source)
    return Usd.Stage.Open(layer)


def _flat(stage):
    return stage.Flatten().ExportToString()


def test_numeric_geometry_fingerprint_survives_serialization_and_detects_edits(tmp_path):
    from usdaeco_cctv.study import _geometry_inputs
    stage = _copy()
    mesh = next(p for p in stage.Traverse() if p.IsA(UsdGeom.Mesh))
    before = _geometry_inputs(mesh, Usd.TimeCode.Default())
    filename = str(tmp_path / "geometry.usda")
    stage.GetRootLayer().Export(filename)
    reopened = Usd.Stage.Open(filename)
    other = reopened.GetPrimAtPath(mesh.GetPath())
    assert _geometry_inputs(other, Usd.TimeCode.Default()) == before
    indices = list(other.GetAttribute("faceVertexIndices").Get())
    indices[0], indices[1] = indices[1], indices[0]
    other.GetAttribute("faceVertexIndices").Set(indices)
    assert _geometry_inputs(other, Usd.TimeCode.Default()) != before


def test_triangle_mesh_fast_path_preserves_holes_and_world_frame():
    import numpy as np
    from usdaeco_cctv.study import _triangulate
    stage = Usd.Stage.CreateInMemory()
    mesh = UsdGeom.Mesh.Define(stage, "/Mesh")
    mesh.CreatePointsAttr([(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0)])
    mesh.CreateFaceVertexCountsAttr([3, 3])
    mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 1, 3, 2])
    mesh.CreateHoleIndicesAttr([0])
    world = np.eye(4)
    world[3, :3] = (2, 3, 4)
    triangles, points = _triangulate(mesh, world, Usd.TimeCode.Default())
    assert np.array_equal(triangles, [[[3, 3, 4], [3, 4, 4], [2, 4, 4]]])
    assert points.shape == (4, 3)


def test_baseline_results_and_layer(tmp_path):
    stage = _copy()
    before = _flat(stage)
    report = run_study(stage, STUDY, tmp_path / "cctv.DoorCoverage.usda", kernel="numpy", recompute=True)
    assert report["views"] == 6 and report["rays"] == 6 * 96 * 54
    for name in ("Door_1", "Door_2", "Door_3"):
        r = report["results"][LOBBY + "/" + name]
        assert r["level"] == "identify" and r["fixedCoverage"] and r["dutyFraction"] == 1.0
    layer = Sdf.Layer.FindOrOpen(str(tmp_path / "cctv.DoorCoverage.usda"))
    data = layer.customLayerData
    assert {"aeco:cctv:study", "aeco:cctv:tool", "aeco:cctv:time", "aeco:cctv:inputHash", "aeco:cctv:kernel"} <= set(data)
    assert data["aeco:cctv:inputHash"] == report["inputHash"]
    # only overs on existing prims; new prims are the shells and the results
    for prim in layer.rootPrims:
        assert prim.specifier == Sdf.SpecifierOver
    result = stage.GetPrimAtPath(STUDY + "/Results/Door_1")
    assert result.HasAPI("AecoCctvCoverageAPI")
    assert result.GetAttribute("aeco:cctvCoverage:nearestViewDistance").Get() == pytest.approx(2.93, abs=0.01)
    shell = stage.GetPrimAtPath(LOBBY + "/Cam_1/Sensor_0/Coverage_DoorCoverage")
    assert shell and UsdGeom.Imageable(shell).ComputePurpose() == UsdGeom.Tokens.guide
    assert shell.GetAttribute("aeco:derived:role").Get() == "coverage"
    shells = [p for p in stage.Traverse() if p.IsA(UsdGeom.Mesh) and p.GetName().startswith("Coverage_")]
    assert len(shells) == 6
    assert all(p.GetAttribute("aeco:derived:approx").Get() == "tessellated"
               and not p.GetAttribute("aeco:derived:tolerance").HasAuthoredValueOpinion() for p in shells)
    assert not validators.validate_stage(stage, include_core=True, include_builtin=False)
    # muting the analysis layer restores the stage bit-identically
    stage.MuteLayer(layer.identifier)
    assert _flat(stage) == before


def test_hash_stable_and_sensitive(tmp_path):
    stage = _copy()
    first = run_study(stage, STUDY, tmp_path / "a.usda", kernel="numpy", recompute=True)["inputHash"]
    assert input_hash(stage, STUDY) == first
    assert run_study(stage, STUDY, tmp_path / "a.usda", kernel="numpy", recompute=True)["inputHash"] == first
    stage.SetEditTarget(stage.GetRootLayer())
    sensor = stage.GetPrimAtPath(LOBBY + "/Cam_2/Sensor_0")
    sensor.GetAttribute("aeco:cctvSensor:pan").Set(-80.0)
    assert input_hash(stage, STUDY) != first
    sensor.GetAttribute("aeco:cctvSensor:pan").Set(-90.0)
    assert input_hash(stage, STUDY) == first


def test_incremental_reuse_and_agreement(tmp_path):
    from pxr import Gf
    stage = _copy()
    out = tmp_path / "inc.usda"
    run_study(stage, STUDY, out, kernel="numpy", recompute=True)
    again = run_study(stage, STUDY, out, kernel="numpy")
    assert again["viewsComputed"] == [] and len(again["viewsReused"]) == 6
    stage.SetEditTarget(stage.GetRootLayer())
    tray = stage.GetPrimAtPath(LOBBY + "/CableTray_1")
    UsdGeom.Xformable(tray).AddTranslateOp(opSuffix="edit").Set(Gf.Vec3d(0, 0, -0.65))
    moved = run_study(stage, STUDY, out, kernel="numpy")
    assert LOBBY + "/Cam_2/Sensor_0" in moved["viewsReused"]
    full = run_study(stage, STUDY, tmp_path / "full.usda", kernel="numpy", recompute=True)
    assert moved["results"] == full["results"] and moved["inputHash"] == full["inputHash"]
    door = moved["results"][LOBBY + "/Door_1"]
    assert not door["fixedCoverage"] and door["dutyFraction"] == pytest.approx(0.375)
    assert door["blockers"] == [LOBBY + "/CableTray_1"]


def test_target_points_default_sampling():
    stage = _copy()
    pts = target_points(stage, stage.GetPrimAtPath(LOBBY + "/Door_1"))
    assert pts.shape == (5, 3)
    assert pts[0][2] == pytest.approx(1.5) and pts[0][1] < 7.975       # 0.1 m into the lobby
    assert sorted(round(p[2], 3) for p in pts[1:]) == [0.5, 0.5, 2.0, 2.0]


def test_settings_reject_unknown_ladder():
    stage = _copy()
    stage.GetPrimAtPath(STUDY).GetAttribute("aeco:cctvStudy:levelSystem").Set("nope")
    with pytest.raises(ValueError):
        input_hash(stage, STUDY)


def test_hash_covers_topology_tiny_edits_skipped_heads_and_excludes_outputs(tmp_path):
    from pxr import Gf, Vt
    stage = _copy()
    baseline = input_hash(stage, STUDY)
    mesh = UsdGeom.Mesh(stage.GetPrimAtPath(LOBBY + '/CableTray_1/Body'))
    indices = mesh.GetFaceVertexIndicesAttr().Get()
    mesh.GetFaceVertexIndicesAttr().Set(Vt.IntArray(list(reversed(indices))))
    assert input_hash(stage, STUDY) != baseline
    mesh.GetFaceVertexIndicesAttr().Set(indices)
    op = UsdGeom.Xformable(mesh.GetPrim()).AddTranslateOp()
    op.Set(Gf.Vec3d(1e-10, 0, 0))
    assert input_hash(stage, STUDY) != baseline
    op.Set(Gf.Vec3d(0))
    stage.GetPrimAtPath(STUDY).GetAttribute('aeco:cctvStudy:ptzPolicy').Set('ignore')
    before = input_hash(stage, STUDY)
    sensor = stage.GetPrimAtPath(LOBBY + '/Cam_4/Sensor_0')
    sensor.GetAttribute('aeco:cctvPreset:Home:pan').Set(10)
    assert input_hash(stage, STUDY) != before
    report = run_study(stage, STUDY, tmp_path / 'hash.usda', kernel='numpy')
    stage.GetPrimAtPath(STUDY + '/Results/Door_1').GetAttribute('aeco:cctvCoverage:density').Set(99999)
    sensor.GetAttribute('aeco:cctvSensor:hfov').Set(1)
    assert input_hash(stage, STUDY) == report['inputHash']


def test_analysis_on_derived_stage_keeps_world_shell_pose(tmp_path):
    from pxr import Gf
    from usdaeco_cctv.derive import derive
    stage = _copy()
    base = run_study(stage, STUDY, tmp_path / 'base.usda', kernel='numpy')
    layer = Sdf.Layer.CreateAnonymous('tier-a.usda')
    derive(stage, layer)
    report = run_study(stage, STUDY, tmp_path / 'posed.usda', kernel='numpy')
    assert report['inputHash'] == base['inputHash'] and report['results'] == base['results']
    shell = stage.GetPrimAtPath(LOBBY + '/Cam_4/Sensor_0/Coverage_DoorCoverage_Door_1')
    apex = UsdGeom.Mesh(shell).GetPointsAttr().Get()[0]
    for tc in (0, 96, 240, 384):
        matrix = UsdGeom.XformCache(Usd.TimeCode(tc)).GetLocalToWorldTransform(shell)
        assert matrix.Transform(Gf.Vec3d(apex)) == Gf.Vec3d(apex)


def test_failed_run_restores_layer_and_callers_edit_target(tmp_path, monkeypatch):
    import usdaeco_cctv.study as engine
    stage = _copy()
    output = tmp_path / 'atomic.usda'
    run_study(stage, STUDY, output, kernel='numpy')
    contents = output.read_bytes()
    stage.GetSessionLayer().subLayerPaths.remove(str(output))
    before = _flat(stage)
    edit = Sdf.Layer.CreateAnonymous('intent.usda')
    stage.GetSessionLayer().subLayerPaths.insert(0, edit.identifier)
    stage.SetEditTarget(edit)
    def fail(*args, **kwargs):
        raise RuntimeError('injected write failure')
    monkeypatch.setattr(engine, '_write_shell', fail)
    with pytest.raises(RuntimeError, match='injected'):
        run_study(stage, STUDY, output, kernel='numpy', recompute=True)
    assert stage.GetEditTarget().GetLayer() == edit
    assert output.read_bytes() == contents and _flat(stage) == before
    with pytest.raises(ValueError, match='overwrite an input'):
        run_study(Usd.Stage.Open(str(EXAMPLE)), STUDY, EXAMPLE, kernel='numpy')


def test_explicit_local_points_zero_density_and_night_without_ir(tmp_path):
    from pxr import Gf, Vt
    stage = _copy()
    target = stage.GetPrimAtPath(LOBBY + '/Door_1')
    target.ApplyAPI('AecoCctvTargetAPI')
    target.GetAttribute('aeco:cctvTarget:points').Set(Vt.Vec3dArray([Gf.Vec3d(0,0,1.5)]))
    target.GetAttribute('aeco:cctvTarget:requiredDensity').Set(0)
    points = target_points(stage, target)
    assert points.shape == (1,3) and points[0,2] == 1.5
    report = run_study(stage, STUDY, tmp_path / 'zero.usda', kernel='numpy')
    assert report['results'][str(target.GetPath())]['required'] == 0
    stage.GetPrimAtPath(STUDY).GetAttribute('aeco:cctvStudy:night').Set(True)
    for name in ('Dome_P3277', 'Ptz_Q6088'):
        stage.GetPrimAtPath('/_TypeCatalog/' + name).GetAttribute('aeco:cctvType:irRange').Set(0)
    report = run_study(stage, STUDY, tmp_path / 'night.usda', kernel='numpy')
    assert report['views'] == 0
    assert all(r['level'] == 'none' for r in report['results'].values())


def test_scene_units_preserve_physical_results(tmp_path):
    from pxr import Gf
    stage = _copy()
    baseline = run_study(stage, STUDY, tmp_path / 'metres.usda', kernel='numpy')
    # Express the same geometry in centimetres; lens offsets remain SI metres.
    from pxr import Vt
    import numpy as np
    UsdGeom.SetStageMetersPerUnit(stage, 0.01)
    for prim in stage.Traverse():
        if prim.IsA(UsdGeom.Mesh) and UsdGeom.Imageable(prim).ComputePurpose() != 'guide':
            mesh = UsdGeom.Mesh(prim)
            mesh.GetPointsAttr().Set(Vt.Vec3fArray.FromNumpy(np.array(mesh.GetPointsAttr().Get()) * 100))
            mesh.GetExtentAttr().Set(Vt.Vec3fArray.FromNumpy(np.array(mesh.GetExtentAttr().Get()) * 100))
        if prim.IsA(UsdGeom.Xformable):
            for op in UsdGeom.Xformable(prim).GetOrderedXformOps():
                if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
                    op.Set(op.Get() * 100)
    result = run_study(stage, STUDY, tmp_path / 'cm.usda', kernel='numpy')
    for path, r in result['results'].items():
        assert r['density'] == pytest.approx(baseline['results'][path]['density'], rel=1e-6)
        assert r['nearestViewDistance'] == pytest.approx(baseline['results'][path]['nearestViewDistance'], rel=1e-6)


def test_non_mesh_obstacle_phase_and_transparency():
    from pxr import Gf
    from usdaeco_cctv.study import Settings, gather_obstacles
    stage = _copy()
    prim = UsdGeom.Cube.Define(stage, LOBBY + '/LooseCube').GetPrim()
    UsdGeom.Xformable(prim).AddTranslateOp().Set(Gf.Vec3d(10,2,1))
    settings = Settings(stage.GetPrimAtPath(STUDY))
    owners, triangles, ids = gather_obstacles(stage, settings)
    owner = next(o for o in owners if o.path == prim.GetPath())
    assert owner.flagged == 'unclassified' and len(owner.triangles) == 12
    settings.includeUnphased = False
    assert not any(o.path == prim.GetPath() for o in gather_obstacles(stage, settings)[0])
    settings.includeUnphased = True
    prim.ApplyAPI('AecoCctvSightlineAPI')
    prim.GetAttribute('aeco:cctvSightline:transmittance').Set(0.4)
    assert next(o for o in gather_obstacles(stage, settings)[0] if o.path == prim.GetPath()).through
    prim.GetAttribute('aeco:cctvSightline:ignore').Set(True)
    assert not any(o.path == prim.GetPath() for o in gather_obstacles(stage, settings)[0])


def test_glazing_registry_and_gprim_sightline_override():
    from usdaeco_cctv.study import Settings, gather_obstacles
    stage = _copy()
    element = stage.GetPrimAtPath(LOBBY + '/CableTray_1')
    element.GetAttribute('aeco:class:ifc:code').Set('IfcWindow')
    body = element.GetChild('Body')
    settings = Settings(stage.GetPrimAtPath(STUDY))
    def obstacle():
        return next(o for o in gather_obstacles(stage, settings)[0] if o.path == element.GetPath())
    assert obstacle().through
    body.ApplyAPI('AecoCctvSightlineAPI')
    assert not obstacle().through  # explicit opaque overrides the glazing registry
    body.GetAttribute('aeco:cctvSightline:transmittance').Set(0.3)
    assert obstacle().through
    body.GetAttribute('aeco:cctvSightline:ignore').Set(True)
    assert not any(o.path == element.GetPath() for o in gather_obstacles(stage, settings)[0])
