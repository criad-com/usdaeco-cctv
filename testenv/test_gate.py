"""The opt-in gate preserves inputs and publishes numeric summaries only."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

pytest.importorskip("ifcopenshell")
pytest.importorskip("openpyxl")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import real_data_gate
from usdaeco_cctv import register_plugins
from usdaeco_cctv.importer import import_cctv
from fixtures import build_baseline, write_cobie, convert


def test_real_gate_requires_explicit_opt_in(tmp_path):
    env = dict(os.environ)
    env.pop("AECO_REAL_DATA_ROOT", None)
    env.pop("PYTHONPATH", None)
    result = subprocess.run([sys.executable, str(real_data_gate.ROOT / "tools/real_data_gate.py"),
                             "-o", str(tmp_path / "summary.json")], env=env, capture_output=True, text=True)
    assert result.returncode != 0 and "AECO_REAL_DATA_ROOT" in result.stderr
    assert not (tmp_path / "summary.json").exists()


def test_real_gate_never_runs_in_ci(tmp_path):
    env = dict(os.environ, CI="1", AECO_REAL_DATA_ROOT=str(tmp_path))
    env.pop("PYTHONPATH", None)
    result = subprocess.run([sys.executable, str(real_data_gate.ROOT / "tools/real_data_gate.py")],
                            env=env, capture_output=True, text=True)
    assert result.returncode != 0 and "never runs in CI" in result.stderr


def test_summary_cannot_overwrite_any_input(tmp_path):
    register_plugins()
    for name in real_data_gate.REQUIRED:
        (tmp_path / name).write_text("preserve input")
    with pytest.raises(ValueError, match="distinct"):
        real_data_gate.run_gate(tmp_path, tmp_path / "work", tmp_path / "security.ifc", fixture=True)
    assert (tmp_path / "security.ifc").read_text() == "preserve input"


def test_shared_level_federation_preserves_identity_and_placement(tmp_path):
    from pxr import Usd, UsdGeom
    register_plugins()
    for name, architecture in (("security", False), ("architecture", True)):
        build_baseline(tmp_path / (name + ".ifc"), architecture=architecture)
        convert(tmp_path / (name + ".ifc"), tmp_path / (name + ".usda"))
    before = {p: real_data_gate.digest(p) for p in tmp_path.iterdir()}
    assert real_data_gate.federate(tmp_path / "security.usda", tmp_path / "architecture.usda", tmp_path / "federated.usda") == 1
    stats = import_cctv(tmp_path / "federated.usda", tmp_path / "security.ifc", tmp_path / "kind.usda")
    assert stats["cameras"] == 3 and stats["unmatched"] == 0
    stage = Usd.Stage.Open(str(tmp_path / "federated.usda"))
    doors = [p for p in stage.Traverse() if p.GetAttribute("aeco:class:ifc:code").Get() == "IfcDoor"]
    assert len(doors) == 3 and len({p.GetParent().GetPath() for p in doors}) == 1
    arch = Usd.Stage.Open(str(tmp_path / "architecture.usda"))
    originals = {p.GetAttribute("aeco:id").Get(): p for p in arch.Traverse() if p.HasAPI("AecoElementAPI")}
    for p in doors:
        original = originals[p.GetAttribute("aeco:id").Get()]
        assert UsdGeom.XformCache().GetLocalToWorldTransform(p) == UsdGeom.XformCache().GetLocalToWorldTransform(original)
    assert all(real_data_gate.digest(p) == h for p, h in before.items())


def test_fixture_gate_reports_coverage_and_independent_parity(tmp_path):
    pytest.importorskip("usdaeco_cctv.study", reason="coverage engine lands in C2")
    register_plugins()
    source = tmp_path / "source"
    source.mkdir()
    build_baseline(source / "security.ifc", millimetres=True)
    build_baseline(source / "architecture.ifc", millimetres=True, architecture=True)
    write_cobie(source / "security.ifc", source / "cobie.xlsx", oracle=True)
    (source / "mapping.txt").write_text("Synthetic camera parameters\n")
    output = tmp_path / "summary.json"
    result = real_data_gate.run_gate(source, tmp_path / "work", output, fixture=True, kernel="numpy")
    assert result["cameras"] == 3 and result["study"]["targets"] == 3
    # This fixture has no approach-side points or containing spaces: its
    # default door samples lie inside the leaves and are not coverage evidence.
    assert sum(v["enclosedTargets"] for v in result["study"]["levels"]) == 3
    assert sum(v["enclosedSamples"] for v in result["study"]["levels"]) == 15
    assert sum(v["covered"] + v["uncovered"] for v in result["study"]["levels"]) == 0
    assert result["inputsUnchanged"] == 1 and result["derivedSkipped"] == 0
    assert result["parity"]["cobieAngles"] == 3
    assert result["parity"]["halfAngleRadians"]["max"] <= 1e-3
    assert result["parity"]["hostRadiusMetres"]["max"] <= .001

    def numeric(value):
        if isinstance(value, dict):
            return all(numeric(v) for v in value.values())
        if isinstance(value, list):
            return all(numeric(v) for v in value)
        return isinstance(value, (int, float))
    assert numeric(json.loads(output.read_text()))


def test_native_ifc4_converter_preserves_spatial_tree(tmp_path):
    from pxr import Usd
    from usdaeco_cctv import ROOT
    source, output = tmp_path / 'fixture.ifc', tmp_path / 'core.usda'
    build_baseline(source, schema='IFC4')
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    process = subprocess.run([sys.executable, str(ROOT / 'tools/usdaeco_cctv/gate_conversion.py'),
                              str(source), str(output)], env=env, text=True, capture_output=True, check=True)
    stats = json.loads(process.stdout)
    assert stats['ifc4FacilityAdapted'] == 0 and stats['unparented'] == 0
    stage = Usd.Stage.Open(str(output))
    assert sum(p.GetTypeName() == 'AecoFacility' for p in stage.Traverse()) == 1
    assert sum(p.GetTypeName() == 'AecoLevel' for p in stage.Traverse()) == 1
