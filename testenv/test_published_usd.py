"""The published USD adapter must preserve the unit-explicit camera contract."""
import hashlib
import warnings

import pytest
from pxr import Usd
from exchange_fixture import write_contract
from fixtures import convert
from usdaeco_cctv import iter_cameras, sensors_of
from usdaeco_cctv.importer import import_cctv


@pytest.mark.parametrize('millimetres,radians', [(True, False), (False, True)])
def test_usd_tier_a_matches_ifc_drivers(tmp_path, millimetres, radians):
    source, core = tmp_path / 'camera.ifc', tmp_path / 'core.usda'
    write_contract(source, millimetres=millimetres, radians=radians, tier_a=True)
    convert(source, core)
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()}
    a, b = tmp_path / 'from_ifc.usda', tmp_path / 'from_usd.usda'
    original = import_cctv(core, source, a)
    with warnings.catch_warnings(record=True):
        imported = import_cctv(core, core, b)
    assert imported['cameras'] == original['cameras'] == 1
    assert imported['sensors'] == original['sensors'] == 2
    assert imported['presets'] == original['presets']
    assert imported['unmatched'] == 0
    def drivers(path):
        stage = Usd.Stage.Open(str(path))
        return [(str(head.GetPath()), [(a.GetName(), str(a.Get())) for a in head.GetAttributes()
                                      if a.GetName().startswith(('aeco:cctvSensor:', 'aeco:cctvPreset:'))])
                for camera in iter_cameras(stage) for head in sensors_of(camera)]
    assert drivers(a) == drivers(b)
    assert before == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before}
