"""Acceptance probes for overlapping footprints and misleading render pixels."""
import numpy as np
import pytest
from pxr import Usd, UsdGeom

from usdaeco_cctv.example_metrics import plan_union_area, projected_triangles, measure_render, render_limits


def square(x, y, width=1., height=1.):
    return np.array([[[x, y], [x + width, y], [x + width, y + height]],
                     [[x, y], [x + width, y + height], [x, y + height]]])


def test_overlaps_count_once_and_clip_to_hall_union():
    triangles = np.concatenate([square(0, 0, 2, 2), square(1, 0, 2, 2)])
    assert plan_union_area(triangles, [[-1, -1, 4, 3]]) == pytest.approx(6)
    assert plan_union_area(triangles, [[.5, .5, 2.5, 1.5], [1, .5, 2, 1.5]]) == pytest.approx(2)


def test_occlusion_gap_is_not_filled_by_a_convex_hull():
    triangles = np.concatenate([square(0, 0), square(3, 0)])
    assert plan_union_area(triangles, [[0, 0, 4, 1]]) == pytest.approx(2)
    assert plan_union_area(triangles, [[1, 0, 3, 1]]) == 0


def test_projection_respects_world_shell_and_sensor_local_sector():
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(stage, 1.)
    sensor = UsdGeom.Xform.Define(stage, '/Sensor')
    sensor.AddTranslateOp().Set((5, 7, 0))
    mesh = UsdGeom.Mesh.Define(stage, '/Sensor/Coverage_Test')
    mesh.CreatePointsAttr([(0, 0, 2), (1, 0, 2), (0, 1, 3)])
    mesh.CreateFaceVertexCountsAttr([3])
    mesh.CreateFaceVertexIndicesAttr([0, 1, 2])
    mesh.SetResetXformStack(True)
    assert plan_union_area(projected_triangles(mesh), [[0, 0, 1, 1]]) == pytest.approx(.5)
    mesh.SetResetXformStack(False)
    assert plan_union_area(projected_triangles(mesh), [[0, 0, 1, 1]]) == 0


@pytest.mark.parametrize('condition', ['white', 'small', 'uniform', 'legible'])
def test_render_quality_rejects_blown_out_sparse_and_uniform_images(tmp_path, condition):
    from usdaeco_check.images import write_png
    rgb = np.zeros((100, 100, 3))
    if condition == 'white':
        rgb[:] = 1
        rgb[:5] = (.3, .5, .8)
    elif condition == 'small':
        rgb[:10] = (.3, .5, .8)
    elif condition == 'uniform':
        rgb[:] = .5
    else:
        rgb[:30] = (.3, .5, .8)
    path = tmp_path / 'render.png'
    write_png(path, rgb)
    if condition == 'uniform':
        with pytest.raises(ValueError, match='uniform'):
            measure_render(path, render_limits()['views']['lookthrough'])
    else:
        measured = measure_render(path, render_limits()['views']['lookthrough'])
        assert measured['passed'] == (condition == 'legible')
