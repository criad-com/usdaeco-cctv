"""Fresh IFC to core to import to derive: no USD receipt or cached camera facts."""
import json
from pathlib import Path
import pytest
from pxr import Usd
pytest.importorskip("ifcopenshell")
pytest.importorskip("openpyxl")
from fixtures import convert
from exchange_fixture import write_contract, TYPE, OPTICS, POSE, PRESETS
from usdaeco_cctv import iter_cameras, sensors_of, camera_type_of, presets_of
from usdaeco_cctv.importer import import_cctv
from usdaeco_cctv.derive import derive_file


@pytest.mark.parametrize('millimetres,radians', [(True,False), (False,False), (True,True), (False,True)])
@pytest.mark.parametrize('tier_a', [True, False])
def test_exchange_units(tmp_path, millimetres, radians, tier_a):
    source, core, kind = [tmp_path / n for n in ('camera.ifc', 'core.usda', 'kind.usda')]
    write_contract(source, millimetres, radians, tier_a)
    convert(source, core)
    stats = import_cctv(core, source, kind)
    stage = Usd.Stage.Open(str(kind))
    camera = list(iter_cameras(stage))[0]
    heads = sensors_of(camera)
    assert stats['sensors'] == (2 if tier_a else 1)
    for name in ('pan', 'tilt', 'focalLength'):
        assert heads[0].GetAttribute('aeco:cctvSensor:' + name).Get() == pytest.approx(POSE[name], abs=1e-6)
    assert presets_of(heads[0]) == PRESETS
    if tier_a:
        for name, value in TYPE.items():
            actual = camera_type_of(camera).GetAttribute(name).Get()
            assert actual == pytest.approx(value, abs=1e-6) if isinstance(value, float) else actual == value
        for i, head in enumerate(heads):
            expected = {**OPTICS, **POSE}
            if i:
                expected.update(motorised=False, focalRange=[3.,20.])
            for name, value in expected.items():
                actual = head.GetAttribute('aeco:cctvSensor:' + name).Get()
                if isinstance(value, (float, list)):
                    assert actual == pytest.approx(value, abs=1e-6), name
                else:
                    assert actual == value, name
            assert list(head.GetAttribute('aeco:cctvSensor:tour').Get()) == (['Home','MainDoor'] if i == 0 else [])
        assert camera.GetAttribute('aeco:cctv:scenario').Get() == 'door'
        assert camera.GetAttribute('aeco:cctv:mount').Get() == 'ceiling'
    else:
        assert not heads[0].GetAttribute('aeco:cctvSensor:motorised').Get()
    derived = derive_file(kind, tmp_path / 'derived.usda')
    assert derived['sensors'] == len(heads) and not derived['skipped']


@pytest.mark.parametrize('tier_a', [True, False])
def test_last_preset_removed(tmp_path, tier_a):
    for empty in (False, True):
        directory = tmp_path / str(empty)
        directory.mkdir()
        write_contract(directory / 'camera.ifc', tier_a=tier_a, empty_presets=empty)
        convert(directory / 'camera.ifc', directory / 'core.usda')
        import_cctv(directory / 'core.usda', directory / 'camera.ifc', directory / 'kind.usda')
        stage = Usd.Stage.Open(str(directory / 'kind.usda'))
        head = sensors_of(list(iter_cameras(stage))[0])[0]
        assert presets_of(head) == ({} if empty else PRESETS)


@pytest.mark.parametrize('fault', ['no-optics', 'not-camera', 'bounded', 'conflict', 'unknown-major'])
def test_exchange_refusals_and_precedence(tmp_path, fault):
    source, core, kind = [tmp_path / n for n in ('camera.ifc', 'core.usda', 'kind.usda')]
    model = write_contract(source, millimetres=False, radians=True, tier_a=fault not in ('bounded','no-optics'), bounded=fault=='bounded')
    if fault == 'no-optics':
        for pset in model.by_type('IfcPropertySet'):
            if pset.Name == 'Optical data':
                pset.HasProperties = []
    if fault == 'not-camera':
        model.by_type('IfcAudioVisualAppliance')[0].PredefinedType = 'SPEAKER'
    if fault == 'conflict':
        for prop in model.by_type('IfcPropertySingleValue'):
            if prop.Name == 'PanHorizontal':
                prop.NominalValue.wrappedValue = 55.
    if fault == 'unknown-major':
        for prop in model.by_type('IfcPropertySingleValue'):
            if prop.Name == 'Contract':
                prop.NominalValue.wrappedValue = 'usdaeco-cctv-ifc/2.0'
    model.write(str(source))
    convert(source, core)
    if fault in ('no-optics','unknown-major'):
        if fault == 'unknown-major':
            with pytest.warns(UserWarning, match='unknown camera contract'):
                with pytest.raises(ValueError, match='cctvMissingOptics.*Sensor_0.*focalRange'):
                    import_cctv(core, source, kind)
        else:
            with pytest.raises(ValueError, match='cctvMissingOptics.*Sensor_0.*focalRange'):
                import_cctv(core, source, kind)
        assert not kind.exists()
        return
    if fault == 'conflict':
        with pytest.warns(UserWarning, match='pan.*tier A.*tier B'):
            import_cctv(core, source, kind)
    else:
        import_cctv(core, source, kind)
    stage = Usd.Stage.Open(str(kind))
    cameras = list(iter_cameras(stage))
    if fault == 'not-camera':
        assert not cameras
    else:
        head = sensors_of(cameras[0])[0]
        assert head.GetAttribute('aeco:cctvSensor:pan').Get() == pytest.approx(15., abs=1e-6)
        assert head.GetAttribute('aeco:cctvSensor:tilt').Get() == pytest.approx(30., abs=1e-6)
        assert head.GetAttribute('aeco:cctvSensor:focalLength').Get() == pytest.approx(4., abs=1e-6)


def test_fixed_varifocal_is_fixed_coverage(tmp_path, kernel='numpy'):
    from pxr import Gf
    from usdaeco_cctv.study import Settings, enumerate_views, run_study
    source, core, kind = [tmp_path / n for n in ('camera.ifc', 'core.usda', 'kind.usda')]
    write_contract(source, tier_a=False)
    convert(source, core)
    import_cctv(core, source, kind)
    stage = Usd.Stage.Open(str(kind))
    study = stage.DefinePrim('/Study', 'Scope')
    study.ApplyAPI('AecoCctvStudyAPI')
    views, _ = enumerate_views(stage, Settings(study))
    assert len(views) == 1 and not views[0].motorised
    target = stage.DefinePrim('/Target', 'Scope')
    target.ApplyAPI('AecoCctvTargetAPI')
    point = views[0].origin - views[0].basis[2] * 3.
    target.GetAttribute('aeco:cctvTarget:points').Set([Gf.Vec3d(*point)])
    Usd.CollectionAPI(study, 'targets').CreateIncludesRel().SetTargets([target.GetPath()])
    report = run_study(stage, '/Study', tmp_path / 'study.usda', kernel=kernel)
    assert report['results']['/Target']['fixedCoverage']
