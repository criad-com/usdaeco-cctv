"""Study integrity regressions independent of ray-kernel implementation."""
import pytest
from pathlib import Path
from pxr import Gf, Sdf, Usd, UsdGeom, UsdValidation
from test_study import _copy, LOBBY, STUDY
from usdaeco_cctv.study import Settings, enumerate_views, run_study
from usdaeco_cctv.validators import validate_stage


@pytest.mark.parametrize('phase,include,count', [('demolished', True, 5), ('temporary', True, 5),
                                               ('existing', False, 6), (None, False, 5), (None, True, 6)])
def test_provider_phase(phase, include, count):
    stage = _copy()
    camera = stage.GetPrimAtPath(LOBBY + '/Cam_1')
    attr = camera.GetAttribute('aeco:phase')
    attr.Set(phase) if phase else attr.Clear()
    study = stage.GetPrimAtPath(STUDY)
    study.GetAttribute('aeco:cctvStudy:includeUnphased').Set(include)
    assert len(enumerate_views(stage, Settings(study))[0]) == count
    found = [i for i in validate_stage(stage) if i.GetName() == 'cctvUnphasedProvider']
    assert len(found) == int(phase is None and include)


@pytest.mark.parametrize('role', ['targets', 'exclusions'])
@pytest.mark.parametrize('phase,include,present', [('demolished', True, False), (None, False, False), (None, True, True)])
def test_target_and_exclusion_phase(tmp_path, role, phase, include, present):
    stage = _copy()
    target = stage.GetPrimAtPath(LOBBY + '/Door_1')
    attr = target.GetAttribute('aeco:phase')
    attr.Set(phase) if phase else attr.Clear()
    study = stage.GetPrimAtPath(STUDY)
    study.GetAttribute('aeco:cctvStudy:includeUnphased').Set(include)
    Usd.CollectionAPI(study, role).CreateIncludesRel().SetTargets([target.GetPath()])
    report = run_study(stage, STUDY, tmp_path / 'phase.usda', kernel='numpy')
    collection = report['results'] if role == 'targets' else report['exclusionsCovered']
    assert (str(target.GetPath()) in collection) == present


@pytest.mark.parametrize('fault', ['missing', 'deactivated', 'duplicate', 'dangling', 'nan', 'fraction', 'duty', 'level', 'fixed'])
def test_result_completeness(tmp_path, fault):
    stage = _copy()
    out = tmp_path / 'complete.usda'
    run_study(stage, STUDY, out, kernel='numpy')
    layer = Sdf.Layer.FindOrOpen(str(out))
    result = stage.GetPrimAtPath(STUDY + '/Results/Door_1')
    with Usd.EditContext(stage, layer):
        if fault == 'missing':
            stage.RemovePrim(result.GetPath())
        elif fault == 'deactivated':
            result.SetActive(False)
        elif fault == 'duplicate':
            Sdf.CopySpec(layer, result.GetPath(), layer, result.GetPath().GetParentPath().AppendChild('Duplicate'))
        elif fault == 'dangling':
            result.GetRelationship('aeco:cctvCoverage:target').SetTargets(['/Absent'])
        else:
            name, value = {'nan': ('density', float('nan')), 'fraction': ('fraction', 2.),
                           'duty': ('dutyFraction', -1.), 'level': ('level', 'none'),
                           'fixed': ('fixedCoverage', False)}[fault]
            result.GetAttribute('aeco:cctvCoverage:' + name).Set(value)
    issues = validate_stage(stage, profile=Path(__file__).resolve().parents[1] / 'conformance/profiles/security.json')
    incomplete = [i for i in issues if i.GetName() == 'cctvStudyIncomplete']
    assert incomplete and all(i.GetType() == UsdValidation.ValidationErrorType.Error for i in incomplete)
    assert all(i.GetSites()[0].GetPrim().GetPath() == Sdf.Path(STUDY) for i in incomplete)


def test_never_run_security_grade():
    stage = _copy()
    for profile, grade in [(None, UsdValidation.ValidationErrorType.Info),
                           (Path(__file__).resolve().parents[1] / 'conformance/profiles/security.json', UsdValidation.ValidationErrorType.Error)]:
        issues = [i for i in validate_stage(stage, profile=profile) if i.GetName() == 'cctvStudyMissingResults']
        assert len(issues) == 1 and issues[0].GetType() == grade


def test_two_studies_order_and_shells(tmp_path, kernel="numpy"):
    from usdaeco_cctv.study import input_hash
    outputs = []
    for order in [('Day', 'Night'), ('Night', 'Day')]:
        stage = _copy()
        for name, density, night in [('Day', 125., False), ('Night', 250., True)]:
            layer = stage.GetRootLayer()
            path = Sdf.Path(STUDY).GetParentPath().AppendChild(name)
            Sdf.CopySpec(layer, Sdf.Path(STUDY), layer, path)
            study = stage.GetPrimAtPath(path)
            study.GetAttribute('aeco:cctvStudy:requiredDensity').Set(density)
            study.GetAttribute('aeco:cctvStudy:night').Set(night)
        reports = {}
        for name in order:
            path = Sdf.Path(STUDY).GetParentPath().AppendChild(name)
            report = run_study(stage, path, tmp_path / ('-'.join(order) + name + '.usda'), kernel=kernel)
            reports[name] = report['results'], report['inputHash']
        for name in order:
            path = Sdf.Path(STUDY).GetParentPath().AppendChild(name)
            assert input_hash(stage, path) == reports[name][1]
            assert stage.GetPrimAtPath(LOBBY + '/Cam_1/Sensor_0/Coverage_' + name)
        outputs.append(reports)
    assert outputs[0] == outputs[1]


def test_camera_scope_and_mount_band(tmp_path):
    from usdaeco_cctv.derive import derive
    stage = _copy()
    study = stage.GetPrimAtPath(STUDY)
    Usd.CollectionAPI(study, 'cameras').CreateIncludesRel().SetTargets([LOBBY + '/Cam_4'])
    study.GetAttribute('aeco:cctvStudy:mountHeightRange').Set(Gf.Vec2d(2.5, 3.0))
    views, _ = enumerate_views(stage, Settings(study))
    assert len(views) == 3 and all(v.camera.GetName() == 'Cam_4' for v in views)
    derive(stage, Sdf.Layer.CreateAnonymous('derived.usda'))
    assert not [e for e in validate_stage(stage) if e.GetName() == 'cctvMountFrame']


def test_density_settings_are_explicit():
    from usdaeco_cctv.derive import Reader, settings_of
    stage = _copy()
    study = stage.GetPrimAtPath(STUDY)
    study.GetAttribute('aeco:cctvStudy:densityModel').Set('arc')
    assert settings_of(stage, Reader(stage))['model'] == 'plane'
    assert settings_of(stage, Reader(stage), study_path=STUDY)['model'] == 'arc'
    assert settings_of(stage, Reader(stage), model='plane', study_path=STUDY)['model'] == 'plane'


@pytest.mark.parametrize('change', ['driver', 'phase', 'geometry', 'collection', 'ir', 'algorithm', 'version', 'noop'])
def test_stale_matrix(tmp_path, monkeypatch, change):
    import usdaeco_cctv.study as engine
    stage = _copy()
    run_study(stage, STUDY, tmp_path / 'stale.usda', kernel='numpy')
    sensor = stage.GetPrimAtPath(LOBBY + '/Cam_1/Sensor_0')
    if change in ('driver', 'noop'):
        attr = sensor.GetAttribute('aeco:cctvSensor:pan')
        attr.Set(attr.Get() + (1 if change == 'driver' else 0))
    elif change == 'phase':
        stage.GetPrimAtPath(LOBBY + '/Cam_1').GetAttribute('aeco:phase').Set('demolished')
    elif change == 'geometry':
        UsdGeom.Xformable(stage.GetPrimAtPath(LOBBY + '/CableTray_1')).AddTranslateOp(opSuffix='edit').Set(Gf.Vec3d(0,0,.1))
    elif change == 'collection':
        Usd.CollectionAPI(stage.GetPrimAtPath(STUDY), 'targets').CreateIncludesRel().SetTargets([LOBBY + '/Door_2'])
    elif change == 'ir':
        stage.GetPrimAtPath(LOBBY + '/Cam_1').GetAttribute('aeco:cctvType:irRange').Set(1.)
    elif change == 'algorithm':
        monkeypatch.setattr(engine, 'ALGORITHM_REVISION', 'next')
    elif change == 'version':
        monkeypatch.setattr(engine, '__version__', 'next')
    stale = [e for e in validate_stage(stage) if e.GetName() == 'cctvStudyStale']
    assert bool(stale) == (change != 'noop')


def test_occurrence_ir_controls_night_reach(tmp_path):
    stage = _copy()
    stage.GetPrimAtPath(STUDY).GetAttribute('aeco:cctvStudy:night').Set(True)
    for i in range(1, 5):
        stage.GetPrimAtPath(LOBBY + '/Cam_' + str(i)).GetAttribute('aeco:cctvType:irRange').Set(0.)
    report = run_study(stage, STUDY, tmp_path / 'night.usda', kernel='numpy')
    assert report['views'] == 0


@pytest.mark.parametrize('projection', ['fisheye', 'cylindrical'])
def test_study_refuses_unsupported_projection(tmp_path, projection):
    stage = _copy()
    head = stage.GetPrimAtPath(LOBBY + '/Cam_1/Sensor_0')
    head.GetAttribute('aeco:cctvSensor:projection').Set(projection)
    with pytest.raises(ValueError, match='cctvUnsupportedProjection.*Cam_1/Sensor_0'):
        run_study(stage, STUDY, tmp_path / 'unsupported.usda', kernel='numpy')
    assert not (tmp_path / 'unsupported.usda').exists()
    issue = next(e for e in validate_stage(stage) if e.GetName() == 'cctvUnsupportedProjection')
    assert issue.GetType() == UsdValidation.ValidationErrorType.Error
    assert issue.GetSites()[0].GetPrim() == head


def test_camera_collection_excludes_sensor():
    stage = _copy()
    study = stage.GetPrimAtPath(STUDY)
    collection = Usd.CollectionAPI(study, 'cameras')
    collection.CreateIncludesRel().SetTargets([LOBBY + '/Cam_1', LOBBY + '/Cam_2'])
    collection.CreateExcludesRel().SetTargets([LOBBY + '/Cam_1/Sensor_0'])
    views, _ = enumerate_views(stage, Settings(study))
    assert len(views) == 1 and views[0].camera.GetName() == 'Cam_2'


@pytest.mark.parametrize('missing', [True, False])
def test_unresolved_collection_target_refuses_publication(tmp_path, missing):
    stage = _copy()
    path = LOBBY + '/Door_1'
    if missing:
        stage.RemovePrim(path)
    else:
        stage.GetPrimAtPath(path).SetActive(False)
    with pytest.raises(ValueError, match='cctvStudyIncomplete'):
        run_study(stage, STUDY, tmp_path / 'invalid.usda', kernel='numpy')
    assert not (tmp_path / 'invalid.usda').exists()
