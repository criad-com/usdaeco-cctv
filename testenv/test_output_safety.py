"""Output aliases and publication failures preserve every input and prior output."""
from pathlib import Path
import pytest
from pxr import Sdf, Usd
from usdaeco_cctv.derive import derive, derive_file
from usdaeco_cctv.study import run_study
from test_study import _copy, STUDY, EXAMPLE


@pytest.mark.parametrize('operation', ['derive', 'study'])
@pytest.mark.parametrize('alias', ['same', 'symlink', 'sublayer'])
def test_output_alias_refused(tmp_path, operation, alias):
    source = tmp_path / 'source.usda'
    source.write_bytes(EXAMPLE.read_bytes())
    output = source
    root = source
    if alias == 'symlink':
        output = tmp_path / 'alias.usda'
        output.symlink_to(source)
    elif alias == 'sublayer':
        root = tmp_path / 'stack.usda'
        layer = Sdf.Layer.CreateNew(str(root))
        layer.subLayerPaths = ['./source.usda']
        layer.Save()
    before = {p: p.read_bytes() for p in (source, root)}
    with pytest.raises(ValueError, match='overwrite an input'):
        if operation == 'derive':
            derive_file(root, output)
        else:
            run_study(Usd.Stage.Open(str(root)), STUDY, output, kernel='numpy')
    assert all(p.read_bytes() == data for p, data in before.items())


def test_anonymous_input_refused():
    stage = _copy()
    before = stage.GetRootLayer().ExportToString()
    with pytest.raises(ValueError, match='overwrite an input'):
        derive(stage, stage.GetRootLayer())
    assert stage.GetRootLayer().ExportToString() == before


@pytest.mark.parametrize('operation', ['derive', 'study'])
def test_failed_publish_preserves_previous(tmp_path, monkeypatch, operation):
    import usdaeco_cctv.output as publication
    source = tmp_path / 'source.usda'
    source.write_bytes(EXAMPLE.read_bytes())
    output = tmp_path / 'output.usda'
    def run():
        if operation == 'derive':
            return derive_file(source, output)
        return run_study(Usd.Stage.Open(str(source)), STUDY, output, kernel='numpy', recompute=True)
    run()
    contents = output.read_bytes()
    def fail(*args):
        raise OSError('injected rename failure')
    monkeypatch.setattr(publication.os, 'replace', fail)
    with pytest.raises(OSError, match='injected'):
        run()
    assert output.read_bytes() == contents
    assert not list(tmp_path.glob('.cctv-*'))


def test_study_in_stack_refused_even_when_owned(tmp_path):
    stage = _copy()
    output = tmp_path / 'study.usda'
    run_study(stage, STUDY, output, kernel='numpy')
    before = output.read_bytes()
    with pytest.raises(ValueError, match='overwrite an input'):
        run_study(stage, STUDY, output, kernel='numpy')
    assert output.read_bytes() == before

@pytest.mark.parametrize('shells', [True, False])
def test_binary_publication_and_optional_shells(tmp_path, shells):
    stage = _copy()
    stage.GetPrimAtPath(STUDY).GetAttribute('aeco:cctvStudy:writeShells').Set(shells)
    output = tmp_path / 'analysis.usdc'
    report = run_study(stage, STUDY, output, kernel='numpy')
    assert output.read_bytes().startswith(b'PXR-USDC')
    layer = Sdf.Layer.FindOrOpen(str(output))
    probe = Usd.Stage.Open(layer)
    meshes = [p for p in probe.TraverseAll() if p.GetTypeName() == 'Mesh']
    assert bool(meshes) == shells
    assert len(report['results']) == 3
    stage.GetSessionLayer().subLayerPaths.remove(report['layer'])
    repeated = run_study(stage, STUDY, output, kernel='numpy')
    assert repeated['results'] == report['results']
    assert len(repeated['viewsReused']) == repeated['views']


def test_format_selection_preserves_results(tmp_path):
    binary = run_study(_copy(), STUDY, tmp_path / 'binary.usd', kernel='numpy', format='usdc')
    text = run_study(_copy(), STUDY, tmp_path / 'text.usd', kernel='numpy', format='usda')
    assert Path(binary['layer']).read_bytes().startswith(b'PXR-USDC')
    assert Path(text['layer']).read_text().startswith('#usda')
    assert binary['results'] == text['results']

@pytest.mark.parametrize('format,suffix', [('usda', 'usdc'), ('usdc', 'usda')])
def test_conflicting_format_fails_before_writing(tmp_path, format, suffix):
    output = tmp_path / ('output.' + suffix)
    with pytest.raises(ValueError, match='suffix conflicts'):
        run_study(_copy(), STUDY, output, kernel='numpy', format=format)
    assert not output.exists()
