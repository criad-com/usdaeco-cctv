"""Ownership, geometric fallback and conservative type sharing for IFC exports."""
import hashlib
import json
import os
from pathlib import Path

import pytest
import ifcopenshell
import numpy as np
from pxr import Gf, Sdf, Usd, UsdGeom

from fixtures import convert
from revit_fixture import build_revit_fixture
from usdaeco_cctv import camera_type_of, iter_cameras, sensors_of
from usdaeco_cctv.importer import Fact, Row, _is_sub, identity, import_cctv, read_ifc


def prepare(path, **kw):
    model = build_revit_fixture(path / "source.ifc", **kw)
    convert(path / "source.ifc", path / "core.usda", geometry=False)
    return model


def run_import(path, **kw):
    stats = import_cctv(path / "core.usda", path / "source.ifc", path / "kind.usda", **kw)
    return stats, Usd.Stage.Open(str(path / "kind.usda"))


def by_name(stage, name):
    return next(p for p in stage.TraverseAll() if p.GetName() == name)


def mapping(stage, key="foldMapping"):
    return json.loads(stage.GetRootLayer().customLayerData.get("aeco:cctv:" + key, "{}"))


@pytest.mark.parametrize("relation", ["IfcRelAggregates", "IfcRelNests"])
def test_nested_ownership_beats_nearer_camera_and_supercomponent(tmp_path, relation):
    model = prepare(tmp_path, relation=relation)
    rows = read_ifc(tmp_path / "source.ifc")
    camera_rows = [r for r in rows if r.code.endswith(".CAMERA")]
    helper = next(r for r in rows if r.name.startswith("FOV-AXIS"))
    symbol = next(r for r in rows if r.name.startswith("AXIS 2D"))
    assert helper.parents == (camera_rows[0].uid,)
    assert symbol.parents == (helper.uid,)
    # Move camera 0 far away; camera 1 now sits on both helper pictures.
    core = Usd.Stage.Open(str(tmp_path / "core.usda"))
    edit = Sdf.Layer.CreateNew(str(tmp_path / "poses.usda"))
    core.GetRootLayer().subLayerPaths.insert(0, "poses.usda")
    core.SetEditTarget(edit)
    UsdGeom.Xformable(by_name(core, "Camera_0")).MakeMatrixXform().Set(Gf.Matrix4d().SetTranslate((20, 0, 3)))
    UsdGeom.Xformable(by_name(core, "Camera_1")).MakeMatrixXform().Set(Gf.Matrix4d().SetTranslate((0, 0, 3)))
    edit.Save()
    core.GetRootLayer().Save()
    from ifcopenshell.api import run
    proxy = model.by_guid(ifcopenshell.guid.compress(helper.uid.replace("-", "")))
    ps = run("pset.add_pset", model, product=proxy, name="Source")
    run("pset.edit_pset", model, pset=ps, properties={"SuperComponent": camera_rows[1].uid})
    model.write(str(tmp_path / "source.ifc"))
    stats, stage = run_import(tmp_path)
    assert (stats["cameras"], stats["sensors"], stats["subInstancesFolded"], stats["unmatched"], stats["types"]) == (2, 2, 2, 0, 1)
    assert {entry["owner"] for entry in mapping(stage).values()} == {camera_rows[0].uid}
    assert {entry["method"] for entry in mapping(stage).values()} == {"ownership"}
    assert len(list(iter_cameras(stage))) == 2


@pytest.mark.parametrize("anonymous", [False, True])
def test_top_level_helper_with_optics_folds_beyond_legacy_radius(tmp_path, anonymous):
    prepare(tmp_path, relation=None, anonymous=anonymous)
    stats, stage = run_import(tmp_path)
    assert stats["unmatched"] == 0 and stats["subInstancesFolded"] == 2
    extended = [r for r in mapping(stage).values() if r["method"] == "nearest-optics"]
    assert len(extended) == 1 and extended[0]["distanceMetres"] == pytest.approx(.77)


@pytest.mark.parametrize("case", ["far", "tie", "other-level", "no-level", "missing-owner", "wrong-optics", "name-only"])
def test_unsafe_fallback_stays_unmatched(tmp_path, case):
    model = prepare(tmp_path, relation=None, distance=1.01 if case == "far" else .77)
    from ifcopenshell.api import run
    helper = next(p for p in model.by_type("IfcBuildingElementProxy") if p.Name.startswith("FOV-AXIS"))
    cameras = model.by_type("IfcAudioVisualAppliance")
    if case == "tie":
        run("geometry.edit_object_placement", model, product=cameras[1],
            matrix=np.array([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 3], [0, 0, 0, 1]], dtype=float))
    elif case == "other-level":
        run("spatial.assign_container", model, relating_structure=model.by_type("IfcBuildingStorey")[1], products=[helper])
    elif case == "no-level":
        run("spatial.unassign_container", model, products=[helper, *cameras])
    elif case == "missing-owner":
        ps = run("pset.add_pset", model, product=helper, name="Source")
        run("pset.edit_pset", model, pset=ps, properties={"SuperComponent": "missing-camera"})
    elif case == "wrong-optics":
        for ps in model.by_type("IfcPropertySet"):
            for prop in ps.HasProperties:
                if prop.Name == "Horizontal Res":
                    prop.NominalValue.wrappedValue = 1000
    elif case == "name-only":
        for rel in list(helper.IsDefinedBy):
            model.remove(rel)
    model.write(str(tmp_path / "changed.ifc"))
    convert(tmp_path / "changed.ifc", tmp_path / "changed.usda", geometry=False)
    stats = import_cctv(tmp_path / "changed.usda", tmp_path / "changed.ifc", tmp_path / "kind.usda")
    stage = Usd.Stage.Open(str(tmp_path / "kind.usda"))
    assert stats["unmatched"] >= 1
    assert not mapping(stage)[identity(helper.GlobalId)]["matched"]
    assert any(p.GetAttribute("aeco:id").Get() == identity(helper.GlobalId) for p in stage.Traverse())


def test_explicit_mapping_precedes_ifc_ownership(tmp_path):
    model = prepare(tmp_path)
    camera = model.by_type("IfcAudioVisualAppliance")[1]
    children = [p.GlobalId for p in model.by_type("IfcBuildingElementProxy")]
    stats, stage = run_import(tmp_path, subinstances={camera.GlobalId: children})
    assert stats["unmatched"] == 0
    assert {r["owner"] for r in mapping(stage).values()} == {identity(camera.GlobalId)}


@pytest.mark.parametrize("case", ["cycle", "multiple"])
def test_invalid_ifc_ownership_refuses_publication(tmp_path, case):
    model = prepare(tmp_path)
    proxies = model.by_type("IfcBuildingElementProxy")
    if case == "cycle":
        owner, child = proxies[1], proxies[0]
    else:
        owner, child = model.by_type("IfcAudioVisualAppliance")[1], proxies[0]
    model.create_entity("IfcRelNests", GlobalId=ifcopenshell.guid.new(),
                        RelatingObject=owner, RelatedObjects=[child])
    model.write(str(tmp_path / "source.ifc"))
    with pytest.raises(ValueError, match="cycle|multiple IFC camera owners"):
        run_import(tmp_path)
    assert not (tmp_path / "kind.usda").exists()


def test_camera_and_symbol_choice_remain_referents():
    assert not _is_sub(Row("camera", "FOV-AXIS", "IfcAudioVisualAppliance.CAMERA", parents=("owner",)), [])
    assert not _is_sub(Row("proxy", "Camera"), [Fact("Graphics", "2D Symbol", "AXIS 2D Symbol")])


def test_distinct_occurrence_head_layouts_do_not_share_catalogs(tmp_path):
    model = prepare(tmp_path)
    from ifcopenshell.api import run
    camera = model.by_type("IfcAudioVisualAppliance")[1]
    ps = run("pset.add_pset", model, product=camera, name="Heads")
    run("pset.edit_pset", model, pset=ps, properties={"FOV 1 Pan": 0., "FOV 2 Pan": 90.})
    model.write(str(tmp_path / "source.ifc"))
    stats, stage = run_import(tmp_path)
    assert stats["types"] == 2 and stats["sensors"] == 3
    assert sorted(len(sensors_of(c)) for c in iter_cameras(stage)) == [1, 2]
    assert not mapping(stage, "typeMapping")


def test_shared_catalog_keeps_occurrence_optics_override(tmp_path):
    model = prepare(tmp_path)
    from ifcopenshell.api import run
    camera = model.by_type("IfcAudioVisualAppliance")[1]
    ps = run("pset.add_pset", model, product=camera, name="Stream")
    run("pset.edit_pset", model, pset=ps,
        properties={"FOV Horizontal Resolution": 1920, "FOV Vertical Resolution": 1080})
    model.write(str(tmp_path / "source.ifc"))
    stats, stage = run_import(tmp_path)
    assert stats["types"] == 1
    cameras = sorted(iter_cameras(stage), key=lambda p: p.GetName())
    assert [tuple(sensors_of(c)[0].GetAttribute("aeco:cctvSensor:pixels").Get()) for c in cameras] == [(2592, 1944), (1920, 1080)]


@pytest.mark.parametrize("optics_change,metadata_change,expected", [(False, False, 1), (True, False, 2), (False, True, 2)])
def test_catalog_sharing_preserves_identity_pose_and_unknown_evidence(tmp_path, optics_change, metadata_change, expected):
    prepare(tmp_path, optics_change=optics_change, metadata_change=metadata_change)
    paths = list(tmp_path.glob("core.*")) + [tmp_path / "source.ifc"]
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    base = Usd.Stage.Open(str(tmp_path / "core.usda"))
    stats, stage = run_import(tmp_path)
    cameras = sorted(iter_cameras(stage), key=lambda p: p.GetName())
    assert stats["types"] == expected
    assert len({camera_type_of(p).GetPath() for p in cameras}) == expected
    for i, camera in enumerate(cameras):
        original = base.GetPrimAtPath(camera.GetPath())
        assert camera.GetAttribute("aeco:id").Get() == original.GetAttribute("aeco:id").Get()
        assert UsdGeom.XformCache().GetLocalToWorldTransform(camera) == UsdGeom.XformCache().GetLocalToWorldTransform(original)
        sensor = sensors_of(camera)[0]
        assert sensor.GetAttribute("aeco:cctvSensor:pan").Get() == 15 + i * 165
        assert sensor.GetAttribute("aeco:cctvSensor:focalLength").Get() == 4 + i * 2
        assert camera.GetAttribute("aeco:props:Optics:Vendor_Note").Get() == ("Distinct" if metadata_change and i else "Preserve type evidence")
        assert not sensor.GetAttribute("aeco:id").HasAuthoredValueOpinion()
    report = mapping(stage, "typeMapping")
    assert len(report) == (2 if expected == 1 else 0)
    if report:
        assert {r["typeId"] for r in report.values()} == {min(report)}
    assert before == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def test_demo_revit_export_when_available(tmp_path):
    source = Path(os.environ.get("AECO_DEMO_REVIT_IFC", "../usdaeco-datacentre/out/revit/demo-datacentre-01-revit.ifc"))
    if not source.is_file():
        pytest.skip("demo Revit IFC export is unavailable; set AECO_DEMO_REVIT_IFC")
    from bench_import import load_reference
    try:
        reference = load_reference()
    except ValueError as exc:
        pytest.skip(str(exc))
    # Fresh conversion is part of the claim; an existing core layer cannot
    # conceal an export/converter mismatch. Geometry runs in a subprocess.
    convert(source, tmp_path / "core.usda")
    paths = [source, *tmp_path.glob("core.*")]
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    stats = import_cctv(tmp_path / "core.usda", source, tmp_path / "kind.usda")
    reference_stats = reference.import_cctv(tmp_path / "core.usda", source, tmp_path / "reference.usda")
    assert stats == reference_stats
    assert (tmp_path / "kind.usda").read_bytes() == (tmp_path / "reference.usda").read_bytes()
    assert (stats["cameras"], stats["sensors"], stats["types"], stats["unmatched"]) == (45, 45, 3, 0)
    assert stats["subInstancesFolded"] == 49
    base = Usd.Stage.Open(str(tmp_path / "core.usda"))
    stage = Usd.Stage.Open(str(tmp_path / "kind.usda"))
    cameras = list(iter_cameras(stage))
    assert len(cameras) == 45 and sum(len(sensors_of(c)) for c in cameras) == 45
    assert len({camera_type_of(c).GetPath() for c in cameras}) == 3
    assert len(mapping(stage, "typeMapping")) == 24
    assert len(mapping(stage)) == 49 and all(r["matched"] for r in mapping(stage).values())
    assert len({r["owner"] for r in mapping(stage).values()}) == 45
    for camera in cameras:
        original = base.GetPrimAtPath(camera.GetPath())
        assert camera.GetAttribute("aeco:id").Get() == original.GetAttribute("aeco:id").Get()
        assert UsdGeom.XformCache().GetLocalToWorldTransform(camera) == UsdGeom.XformCache().GetLocalToWorldTransform(original)
    assert before == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
