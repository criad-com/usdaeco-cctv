"""Explicit samples and area fractions control acceptance and finding attribution."""
import numpy as np
import pytest
from pxr import Gf, Sdf, Usd, UsdGeom
from test_study import _copy, LOBBY, STUDY
from usdaeco_cctv.study import run_study, target_points, sampling
from usdaeco_cctv.validators import validate_stage


@pytest.mark.parametrize('fraction,covered', [(0.,True), (.5,True), (1.,False)])
def test_explicit_pass_fraction(tmp_path, fraction, covered):
    stage = _copy()
    target = stage.GetPrimAtPath(LOBBY + '/Door_1')
    target.ApplyAPI('AecoCctvTargetAPI')
    target.GetAttribute('aeco:cctvTarget:points').Set([Gf.Vec3d(0,0,1.5), Gf.Vec3d(50,50,1.5)])
    target.GetAttribute('aeco:cctvTarget:passFraction').Set(fraction)
    report = run_study(stage, STUDY, tmp_path / 'fraction.usda', kernel='numpy')
    result = report['results'][str(target.GetPath())]
    assert bool(result['views']) == covered
    assert result['fixedCoverage'] == covered
    assert result['fraction'] == .5
    assert not [e for e in validate_stage(stage) if e.GetName() == 'cctvStudyIncomplete']


def test_grid_spacing():
    stage = _copy()
    target = stage.DefinePrim('/Area', 'Scope')
    target.ApplyAPI('AecoCctvTargetAPI')
    target.GetAttribute('aeco:cctvTarget:gridSpacing').Set(.5)
    cube = UsdGeom.Cube.Define(stage, '/Area/Body')
    cube.CreateSizeAttr(2.)
    cube.CreateExtentAttr([Gf.Vec3f(-1), Gf.Vec3f(1)])
    points = target_points(stage, target)
    assert points.shape == (16,3)
    assert np.all(points[:,2] == .5)
    assert sorted(set(points[:,0])) == [-.75,-.25,.25,.75]


@pytest.mark.parametrize('name,value', [('gridSpacing', -1.), ('gridSpacing', float('nan')),
                                        ('passFraction', 1.1), ('passFraction', float('inf'))])
def test_invalid_sampling(name, value):
    stage = _copy()
    target = stage.GetPrimAtPath(LOBBY + '/Door_1')
    target.ApplyAPI('AecoCctvTargetAPI')
    target.GetAttribute('aeco:cctvTarget:' + name).Set(value)
    with pytest.raises(ValueError, match='cctvInvalidSampling'):
        sampling(target)
    assert any(e.GetName() == 'cctvInvalidSampling' for e in validate_stage(stage))


def test_exclusion_sites_name_study_target_and_view(tmp_path):
    stage = _copy()
    study = stage.GetPrimAtPath(STUDY)
    target = stage.GetPrimAtPath(LOBBY + '/Door_1')
    Usd.CollectionAPI(study, 'exclusions').CreateIncludesRel().SetTargets([target.GetPath()])
    run_study(stage, STUDY, tmp_path / 'exclusion.usda', kernel='numpy')
    issue = next(e for e in validate_stage(stage) if e.GetName() == 'cctvExclusionCovered')
    sites = [s.GetPrim() for s in issue.GetSites()]
    assert target in sites and study in sites
    assert any(p.HasAPI('AecoCctvSensorAPI') for p in sites)
    assert STUDY in issue.GetMessage() and '/Cam_1/Sensor_0' in issue.GetMessage()


def extent_fixture():
    stage = _copy()
    target = stage.DefinePrim('/Area', 'AecoSpace')
    target.ApplyAPI('AecoCctvTargetAPI')
    target.GetAttribute('aeco:cctvTarget:gridSpacing').Set(.5)
    body = UsdGeom.Mesh.Define(stage, '/Area/Extent')
    body.CreatePointsAttr([(-1,-1,-1), (1,-1,-1), (1,1,-1), (-1,1,-1),
                           (-1,-1,1), (1,-1,1), (1,1,1), (-1,1,1)])
    faces = [(0,3,2,1), (4,5,6,7), (0,1,5,4), (1,2,6,5), (2,3,7,6), (3,0,4,7)]
    body.CreateFaceVertexCountsAttr([3] * 12)
    body.CreateFaceVertexIndicesAttr([i for a,b,c,d in faces for i in (a,b,c,a,c,d)])
    body.CreateExtentAttr([Gf.Vec3f(-1), Gf.Vec3f(1)])
    body.CreatePurposeAttr('guide')
    body.GetPrim().ApplyAPI('AecoDerivedGeometryAPI')
    body.GetPrim().GetAttribute('aeco:derived:role').Set('extent')
    return stage, target, body


def test_extent_is_sample_source_never_obstacle():
    from usdaeco_cctv.study import Settings, gather_obstacles
    stage, target, body = extent_fixture()
    before = target_points(stage, target)
    clutter = UsdGeom.Cube.Define(stage, '/Area/Equipment')
    UsdGeom.Xformable(clutter).AddTranslateOp().Set(Gf.Vec3d(50,50,50))
    assert np.array_equal(target_points(stage, target), before)
    owners, _, _ = gather_obstacles(stage, Settings(stage.GetPrimAtPath(STUDY)))
    assert not any(o.path == body.GetPath() for o in owners)
    assert next(o for o in owners if o.path == clutter.GetPath()).flagged == 'unclassified'
    assert before.shape == (16,3)


@pytest.mark.parametrize('kernel', ['numpy', 'embree'])
def test_extent_target_study(tmp_path, kernel):
    from usdaeco_cctv.study import Settings, gather_obstacles
    stage, target, body = extent_fixture()
    # A thin region just inside the lobby, facing a fixed door camera.
    centre = target_points(stage, stage.GetPrimAtPath(LOBBY + "/Door_1"))[0]
    body.GetPointsAttr().Set([p * .05 for p in body.GetPointsAttr().Get()])
    body.GetExtentAttr().Set([Gf.Vec3f(-.05), Gf.Vec3f(.05)])
    target.GetAttribute("aeco:cctvTarget:gridSpacing").Set(.025)
    UsdGeom.Xformable(body).AddTranslateOp().Set(Gf.Vec3d(*centre))
    study = stage.GetPrimAtPath(STUDY)
    Usd.CollectionAPI(study, 'targets').CreateIncludesRel().SetTargets([target.GetPath()])
    target.GetAttribute('aeco:cctvTarget:passFraction').Set(.5)
    report = run_study(stage, STUDY, tmp_path / 'extent.usda', kernel=kernel)
    result = report['results']['/Area']
    assert result['fixedCoverage'] and result['fraction'] >= .5
    assert not any(e.GetName() == 'cctvStudyIncomplete' for e in validate_stage(stage))
