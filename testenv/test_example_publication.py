"""Archiving derived opinions must preserve their complete USD composition."""
from pxr import Sdf, Usd

from build_example import build
from usdaeco_cctv.derive import derive_file, is_derived_layer
from usdaeco_cctv.example import _split_derived


def test_split_derived_preserves_composed_opinions(tmp_path):
    source = tmp_path / 'lobby.usda'
    build(source)
    path = tmp_path / 'derived.usda'
    derive_file(source, path)
    stage = Usd.Stage.Open(str(path))
    before = stage.Flatten(addSourceFileComment=False).ExportToString()
    _split_derived(path)
    assert stage.Flatten(addSourceFileComment=False).ExportToString() == before
    parts = list(tmp_path.glob('derived-cameras-*.usda'))
    assert parts
    assert all(p.stat().st_size < 2_000_000 and is_derived_layer(Sdf.Layer.FindOrOpen(str(p)))
               for p in [path, *parts])
