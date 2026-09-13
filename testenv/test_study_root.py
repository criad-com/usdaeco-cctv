"""Relocatable analysis on the pinned v0.5.2 full data-centre delivery."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from pxr import Sdf, Usd, UsdGeom, UsdShade

from usdaeco_cctv import ROOT, camera_type_of, iter_cameras, iter_studies
from usdaeco_cctv.derive import derive_file
from usdaeco_cctv.example import hook
from usdaeco_cctv.paths import STUDY_ROOT_KEY, author_study_root, study_root, study_scope
from usdaeco_cctv.validators import _result_problems, _missing_results, _stale


@pytest.mark.parametrize('value', ['', 'Studies/cctv', '/Studies/cctv.attr', '/Studies{choice=cctv}'])
def test_invalid_root_refused_before_publication(tmp_path, monkeypatch, value):
    monkeypatch.setenv('AECO_STUDY_ROOT', value)
    output = tmp_path / 'derived.usda'
    with pytest.raises(ValueError, match='absolute prim path'):
        derive_file(ROOT / 'examples/lobby.usda', output)
    assert not output.exists()


def test_root_setting_is_read_per_call_and_data_takes_precedence(monkeypatch):
    stage = Usd.Stage.CreateInMemory()
    monkeypatch.delenv('AECO_STUDY_ROOT', raising=False)
    assert study_scope(study_root(stage), 'Looks') == Sdf.Path('/AecoCctvLooks')
    for value in ('/Studies/cctv', '/Analysis/Security'):
        monkeypatch.setenv('AECO_STUDY_ROOT', value)
        assert study_root(stage) == Sdf.Path(value)
    author_study_root(stage, study_root(stage))
    monkeypatch.setenv('AECO_STUDY_ROOT', '/Studies/different')
    assert study_root(stage) == Sdf.Path('/Analysis/Security')
    assert all(p.GetTypeName() == 'Scope' for p in stage.Traverse())


def test_root_does_not_retype_existing_ancestor(monkeypatch):
    stage = Usd.Stage.CreateInMemory()
    stage.DefinePrim('/Studies', 'Xform')
    monkeypatch.setenv('AECO_STUDY_ROOT', '/Studies/cctv')
    with pytest.raises(ValueError, match='ancestor must be a Scope'):
        author_study_root(stage, study_root(stage))
    assert stage.GetPrimAtPath('/Studies').GetTypeName() == 'Xform'


@pytest.fixture(scope='module')
def integrated(tmp_path_factory):
    source = Path(os.environ.get('AECO_CCTV_DATACENTRE_ROOT', ROOT.parent / 'usdaeco-datacentre-0.5.2')) / 'dist/full/dc.usda'
    if not source.is_file():
        pytest.skip('v0.5.2 full delivery required; set AECO_CCTV_DATACENTRE_ROOT')
    base = Usd.Stage.Open(str(source))
    before = {Path(layer.realPath): hashlib.sha256(Path(layer.realPath).read_bytes()).hexdigest()
              for layer in base.GetUsedLayers() if layer.realPath}
    out = tmp_path_factory.mktemp('cctv-root')
    layer = Sdf.Layer.CreateNew(str(out / 'example.usda'))
    layer.subLayerPaths = [str(source)]
    stage = Usd.Stage.Open(layer)
    for key in ('defaultPrim', 'upAxis', 'metersPerUnit', 'fallbackPrimTypes'):
        stage.SetMetadata(key, base.GetMetadata(key))
    # Suite-owned render cameras remain separate from the analysis Scope.
    UsdGeom.Scope.Define(stage, '/Renders')
    UsdGeom.Scope.Define(stage, '/Renders/cctv')
    for name in ('overview', 'lookthrough'):
        UsdGeom.Camera.Define(stage, '/Renders/cctv/' + name)
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv('AECO_STUDY_ROOT', '/Studies/cctv')
        findings = hook(stage, out)
    layer.Save()
    assert before == {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in before}
    return stage, out, findings


def test_pinned_full_hook_has_only_project_studies_and_renders(integrated):
    stage, out, findings = integrated
    project = stage.GetDefaultPrim().GetPath()
    assert not stage.GetCompositionErrors()
    assert {p.GetPath() for p in stage.GetPseudoRoot().GetAllChildren()} == {
        project, Sdf.Path('/Studies'), Sdf.Path('/Renders')}
    for path in ('/Studies', '/Studies/cctv', '/Studies/cctv/Looks',
                 '/Studies/cctv/Targets', '/Studies/cctv/Studies'):
        assert stage.GetPrimAtPath(path).GetTypeName() == 'Scope'
    assert {p.GetName() for p in stage.GetPrimAtPath('/Studies/cctv').GetChildren()} == {'Looks', 'Targets', 'Studies'}
    assert not stage.GetPrimAtPath('/_TypeCatalog')
    catalog = project.AppendChild('_TypeCatalog')
    cameras = list(iter_cameras(stage))
    assert len(cameras) == 47
    assert all(camera_type_of(camera).GetPath().HasPrefix(catalog) for camera in cameras)
    assert stage.GetPrimAtPath('/Renders/cctv/lookthrough').IsA(UsdGeom.Camera)
    assert not stage.GetPrimAtPath('/Renders/lookthrough')
    doors = next(row for row in findings if row['name'] == 'CriticalDoors')['results']
    assert len(doors) == sum(row['fixedCoverage'] for row in doors.values()) == 11
    assert not next(row for row in findings if row['name'] == 'Privacy')['exclusionsCovered']


def test_pinned_relationships_materials_and_study_receipts_resolve(integrated, monkeypatch):
    stage, out, _ = integrated
    monkeypatch.delenv('AECO_STUDY_ROOT', raising=False)
    assert study_root(stage) == Sdf.Path('/Studies/cctv')
    studies = list(iter_studies(stage))
    assert {str(p.GetPath()) for p in studies} == {
        '/Studies/cctv/Studies/CriticalDoors', '/Studies/cctv/Studies/Privacy'}
    for study in studies:
        receipt = Sdf.Layer.FindOrOpen(str(out / (study.GetName() + '.usda')))
        assert receipt.customLayerData['aeco:cctv:study'] == str(study.GetPath())
        assert not _result_problems(study)
        assert not _missing_results(study, None)
        assert not _stale(study, None)
    material_bindings = 0
    for prim in stage.Traverse():
        for rel in prim.GetRelationships():
            if rel.GetName().startswith(('aeco:cctv', 'collection:', 'material:')):
                for path in rel.GetTargets():
                    assert stage.GetObjectAtPath(path), (rel.GetPath(), path)
        for attr in prim.GetAttributes():
            for path in attr.GetConnections():
                assert stage.GetObjectAtPath(path), (attr.GetPath(), path)
        binding = prim.GetRelationship('material:binding')
        if binding and binding.GetTargets():
            material_bindings += 1
            assert binding.GetTargets()[0].HasPrefix('/Studies/cctv/Looks')
            assert UsdShade.Material(stage.GetPrimAtPath(binding.GetTargets()[0]))
    assert material_bindings > 45


@pytest.mark.parametrize('flattened', [False, True])
def test_cli_reopens_nested_data_without_authoring_environment(integrated, tmp_path, monkeypatch, flattened):
    stage, out, _ = integrated
    monkeypatch.delenv('AECO_STUDY_ROOT', raising=False)
    source = out / 'example.usda'
    if flattened:
        source = tmp_path / 'example.usdc'
        stage.Flatten().Export(str(source))
    output = tmp_path / 'derived.usda'
    study = next(p for p in iter_studies(stage) if p.GetName() == 'CriticalDoors')
    def cli(*args):
        result = subprocess.run([sys.executable, str(ROOT / 'tools/aeco-cctv'), *map(str, args)],
                                capture_output=True, text=True, check=True)
        return json.loads(result.stdout)
    derived = cli('derive', source, '--study', study.GetPath(), '-o', output)
    assert derived['cameras'] == derived['sensors'] == 11 and not derived['skipped']
    layer = Sdf.Layer.FindOrOpen(str(output))
    assert layer.customLayerData[STUDY_ROOT_KEY] == '/Studies/cctv'
    assert layer.GetPrimAtPath('/Studies/cctv/Looks')
    assert not layer.GetPrimAtPath('/AecoCctvLooks')
    result = cli('study', source, study.GetPath(), '-o', tmp_path / 'study.usda', '--kernel', 'numpy')
    assert len(result['results']) == sum(row['fixedCoverage'] for row in result['results'].values()) == 11
    assert Sdf.Layer.FindOrOpen(str(tmp_path / 'study.usda')).customLayerData['aeco:cctv:study'] == str(study.GetPath())


def test_missing_camera_type_is_added_to_pinned_project_catalog(integrated, tmp_path):
    from usdaeco_cctv.importer import import_cctv
    stage, out, _ = integrated
    source = out / 'source.usda'
    layer = Sdf.Layer.CreateNew(str(tmp_path / 'input.usda'))
    layer.subLayerPaths = [str(source)]
    probe = Usd.Stage.Open(layer)
    probe.SetDefaultPrim(probe.GetPrimAtPath(stage.GetDefaultPrim().GetPath()))
    camera = next(iter_cameras(stage))
    probe.GetPrimAtPath(camera.GetPath()).GetInherits().SetInherits([])
    layer.Save()
    output = tmp_path / 'kind.usda'
    # Keep the original records as source evidence while removing one inherit.
    import_cctv(tmp_path / 'input.usda', source, output)
    result = Usd.Stage.Open(str(output))
    catalog = camera_type_of(result.GetPrimAtPath(camera.GetPath()))
    assert catalog.GetPath().HasPrefix(stage.GetDefaultPrim().GetPath().AppendChild('_TypeCatalog'))
    assert catalog.GetName().startswith('Camera_')
    assert not result.GetPrimAtPath('/_TypeCatalog')
