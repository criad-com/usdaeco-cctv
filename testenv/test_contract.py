"""The reference contract can be consumed without USD or a registered plugin."""
import json
import os
from pathlib import Path
import subprocess
import sys

import ifcopenshell
import pytest

from exchange_fixture import OPTICS, POSE, PRESETS, TYPE, write_contract
from usdaeco_cctv import contract


@pytest.mark.parametrize("millimetres,radians", [(True, False), (False, False), (True, True), (False, True)])
@pytest.mark.parametrize("tier_a", [True, False])
def test_entity_reader_units(tmp_path, millimetres, radians, tier_a):
    source = tmp_path / "camera.ifc"
    write_contract(source, millimetres, radians, tier_a)
    camera = ifcopenshell.open(str(source)).by_type("IfcAudioVisualAppliance")[0]
    before = camera.file.to_string()
    data = contract.read(camera)
    assert camera.file.to_string() == before
    json.dumps(data, allow_nan=False)
    assert data["drivers"]["aeco:cctvType:outdoor"] is True
    assert len(data["sensors"]) == (2 if tier_a else 1)
    sensor = data["sensors"][0]
    for name in ("pan", "tilt", "focalLength"):
        assert sensor["drivers"][contract.SENSOR + name] == pytest.approx(POSE[name], abs=1e-6)
    assert sensor["presets"] == PRESETS
    if tier_a:
        assert data["type"]["drivers"] == TYPE
        assert sensor["drivers"] == {contract.SENSOR + k: v for k, v in {**OPTICS, **POSE}.items()}
        assert data["sensors"][1]["drivers"][contract.SENSOR + "focalRange"] == [3., 20.]
        assert data["sensors"][1]["drivers"][contract.SENSOR + "motorised"] is False
        assert sensor["tour"] == list(PRESETS)


@pytest.mark.parametrize("tier_a", [True, False])
def test_removing_last_preset_survives_reopen(tmp_path, tier_a):
    source = tmp_path / "camera.ifc"
    for empty in (False, True):
        write_contract(source, tier_a=tier_a, empty_presets=empty)
        camera = ifcopenshell.open(str(source)).by_type("IfcAudioVisualAppliance")[0]
        assert contract.read(camera)["sensors"][0]["presets"] == ({} if empty else PRESETS)


def test_authoritative_conflict_names_driver_and_both_values(tmp_path):
    model = write_contract(tmp_path / "camera.ifc")
    camera = model.by_type("IfcAudioVisualAppliance")[0]
    prop = next(p for p in model.by_type("IfcPropertySingleValue") if p.Name == "PanHorizontal")
    prop.NominalValue.wrappedValue = 55.
    with pytest.warns(UserWarning, match=r"pan tier A 15.0 conflicts with tier B 55.0"):
        data = contract.read(camera)
    assert data["sensors"][0]["drivers"][contract.SENSOR + "pan"] == 15.


@pytest.mark.parametrize("bounded", [False, True])
def test_property_units_override_project_units(tmp_path, bounded):
    model = write_contract(tmp_path / "camera.ifc", radians=True, tier_a=False, bounded=bounded)
    from ifcopenshell.api import run
    degree = run("unit.add_conversion_based_unit", model, name="degree")
    millimetre = run("unit.add_si_unit", model, unit_type="LENGTHUNIT", prefix="MILLI")
    from ifcopenshell.util import element
    camera = model.by_type("IfcAudioVisualAppliance")[0]
    standard = model.by_id(element.get_psets(camera, should_inherit=False)[contract.STANDARD]["id"])
    for prop in standard.HasProperties:
        if prop.Name in ("TiltHorizontal", "Zoom"):
            prop.Unit = degree if prop.Name == "TiltHorizontal" else millimetre
            for field in (("LowerBoundValue", "UpperBoundValue") if bounded else ("NominalValue",)):
                getattr(prop, field).wrappedValue = -30. if prop.Name == "TiltHorizontal" else 4.
    data = contract.read(model.by_type("IfcAudioVisualAppliance")[0])["sensors"][0]["drivers"]
    assert data[contract.SENSOR + "tilt"] == 30.
    assert data[contract.SENSOR + "focalLength"] == 4.


def test_reader_import_and_entity_read_without_usd(tmp_path):
    source = tmp_path / "camera.ifc"
    write_contract(source)
    script = """
import importlib.abc, sys
class NoUsd(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'pxr' or fullname.startswith('pxr.'):
            raise AssertionError('contract must not import USD')
sys.meta_path.insert(0, NoUsd())
sys.path.insert(0, sys.argv[1])
from usdaeco_cctv.contract import read
import ifcopenshell
assert len(read(ifcopenshell.open(sys.argv[2]).by_type('IfcAudioVisualAppliance')[0])['sensors']) == 2
assert not any(n == 'pxr' or n.startswith('pxr.') for n in sys.modules)
"""
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PXR_PLUGINPATH_NAME")}
    result = subprocess.run([sys.executable, "-c", script,
        str(Path(__file__).resolve().parents[1] / "tools"), str(source)],
        env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
