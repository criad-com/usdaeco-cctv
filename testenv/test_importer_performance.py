"""Output equivalence and boundary regressions for importer acceleration."""
from dataclasses import asdict
import math

import ifcopenshell
import ifcopenshell.util.element as element
import numpy as np
import pytest
from pxr import Gf, Usd, UsdGeom

from bench_import import load_reference
from fixtures import build_baseline, convert, write_cobie
from revit_fixture import build_revit_fixture
from usdaeco_cctv import register_plugins, sensors_of
from usdaeco_cctv import importer


@pytest.fixture(scope="module")
def reference():
    try:
        return load_reference()
    except ValueError as exc:
        pytest.skip(str(exc))


@pytest.mark.parametrize("dialect", ["ifc", "cobie", "revit"])
def test_optimized_import_is_byte_identical_to_previous_algorithm(tmp_path, reference, dialect):
    register_plugins()
    source, core = tmp_path / "source.ifc", tmp_path / "core.usda"
    if dialect == "revit":
        build_revit_fixture(source, relation=None)
    else:
        build_baseline(source)
    convert(source, core, geometry=False)
    # Compare every source fact as well as the final layer: unknown properties
    # and their units must survive caching even when no camera uses them.
    assert [asdict(row) for row in importer.read_ifc(source)] == [asdict(row) for row in reference.read_ifc(source)]
    if dialect == "cobie":
        workbook = tmp_path / "source.xlsx"
        write_cobie(source, workbook)
        source = workbook
    old, new = tmp_path / "old.usda", tmp_path / "new.usda"
    assert reference.import_cctv(core, source, old) == importer.import_cctv(core, source, new)
    assert old.read_bytes() == new.read_bytes()


def test_shared_psets_are_read_once_and_same_name_merges_preserve_order(tmp_path, monkeypatch, reference):
    path = tmp_path / "shared.ifc"
    model = build_revit_fixture(path, relation=None)
    cameras = model.by_type("IfcAudioVisualAppliance")
    shared = cameras[0].IsDefinedBy[0].RelatingPropertyDefinition
    rel = next(r for r in cameras[0].IsDefinedBy if r.RelatingPropertyDefinition == shared)
    rel.RelatedObjects = tuple(cameras)
    replacement = model.create_entity("IfcPropertySet", GlobalId=ifcopenshell.guid.new(),
        Name=shared.Name, HasProperties=[model.create_entity("IfcPropertySingleValue", Name="FOV Pan",
            NominalValue=model.create_entity("IfcReal", 123.))])
    model.create_entity("IfcRelDefinesByProperties", GlobalId=ifcopenshell.guid.new(),
                        RelatedObjects=tuple(cameras), RelatingPropertyDefinition=replacement)
    model.write(str(path))
    expected = [asdict(row) for row in reference.read_ifc(path)]
    original, reads = element.get_property_definition, []

    def measured(definition, *args, **kwargs):
        reads.append(definition.id())
        return original(definition, *args, **kwargs)

    monkeypatch.setattr(element, "get_property_definition", measured)
    assert [asdict(row) for row in importer.read_ifc(path)] == expected
    assert reads and len(reads) == len(set(reads))
    # A new read must not retain stale values or collide with another model's ids.
    replacement.HasProperties[0].NominalValue.wrappedValue = 124.
    model.write(str(path))
    assert [asdict(row) for row in importer.read_ifc(path)] != expected


def test_value_index_preserves_alias_priority_and_blocks_all_copies(reference):
    facts = [importer.Fact("Set", "_FOV Pan", 999),
             importer.Fact("Set", "FOV Camera Rotation", 12),
             importer.Fact("Set", "FOV Pan", None),
             importer.Fact("Set", "FOV_Pan", 0),
             importer.Fact("Other Set", "FOV Pan", 42),
             importer.Fact("Set", "unknown", float("nan"))]
    for names in [("FOV Pan", "FOV Camera Rotation"), ("_FOV Pan",), ("missing",)]:
        for pset in (None, "Set", "Other_Set"):
            old, new = reference.Values(facts), importer.Values(facts)
            assert old.pick(*names, pset=pset) == new.pick(*names, pset=pset)
            assert old.used == new.used


def select(near, limit):
    return near[0] if near and near[0][0] <= limit and (len(near) == 1 or near[1][0] - near[0][0] > 1e-6) else None


def exhaustive(cameras, point, level, observations, cache, metres):
    near = sorted(((cache.GetLocalToWorldTransform(prim).ExtractTranslation() * metres - point).GetLength(), uid)
                  for uid, prim in cameras.items() if importer._level(prim) == level)
    if observations:
        near = [(distance, uid) for distance, uid in near if any(
            all(all(math.isclose(a, b, rel_tol=0., abs_tol=1e-6)
                    for a, b in zip(observed, sensor.GetAttribute(importer.SENSOR + key).Get()))
                for key, observed in observations.items()) for sensor in sensors_of(cameras[uid]))]
    return near


def add_camera(stage, path, position, metres, heads=1):
    camera = UsdGeom.Xform.Define(stage, path).GetPrim()
    UsdGeom.Xformable(camera).AddTranslateOp().Set(Gf.Vec3d(*position) / metres)
    for n in range(heads):
        sensor = UsdGeom.Camera.Define(stage, path + "/Sensor_%d" % n).GetPrim()
        sensor.ApplyAPI("AecoCctvSensorAPI")
        for key, value in {"focalRange": Gf.Vec2d(3 + n, 8.5), "hfovRange": Gf.Vec2d(104, 34),
                           "vfovRange": Gf.Vec2d(76, 26), "pixels": Gf.Vec2i(2592, 1944)}.items():
            sensor.GetAttribute(importer.SENSOR + key).Set(value)
    return camera


@pytest.mark.parametrize("metres", [1., .001])
def test_index_matches_exhaustive_search_across_levels_cells_and_sensor_overrides(metres):
    register_plugins()
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(stage, metres)
    rng = np.random.default_rng(847)
    levels = [stage.DefinePrim("/L%d" % i, "AecoLevel").GetPath() for i in range(2)]
    cameras = {str(i): add_camera(stage, str(levels[i % 2]) + "/Camera_%d" % i,
                rng.uniform(-3, 3, 3), metres, heads=1 + i % 3) for i in range(100)}
    cache = UsdGeom.XformCache()
    index = importer._CameraIndex(cameras, cache, metres)
    optics = {key: tuple(sensors_of(cameras["2"])[2].GetAttribute(importer.SENSOR + key).Get())
              for key in importer._CameraIndex.KEYS}
    for observations, limit in (({}, .5), (optics, 1.)):
        for point in [Gf.Vec3d(*v) for v in rng.uniform(-3, 3, (60, 3))] + list(index.origins.values()):
            for level in levels:
                assert select(index.near(point, level, observations, limit), limit) == select(
                    exhaustive(cameras, point, level, observations, cache, metres), limit)


@pytest.mark.parametrize("limit", [.5, 1.])
@pytest.mark.parametrize("gap", [0., .5e-6, 2e-6])
def test_tie_just_outside_radius_still_refuses(limit, gap):
    register_plugins()
    stage = Usd.Stage.CreateInMemory()
    level = stage.DefinePrim("/Level", "AecoLevel").GetPath()
    cameras = {str(i): add_camera(stage, "/Level/C%d" % i, (-limit - i * gap, 0, 0), 1.)
               for i in range(2)}
    index = importer._CameraIndex(cameras, UsdGeom.XformCache(), 1.)
    result = select(index.near(Gf.Vec3d(0), level, {}, limit), limit)
    assert result == ((limit, "0") if gap > 1e-6 else None)
