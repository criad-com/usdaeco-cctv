"""Kernels, frustum culling and pass-through casting, pure numpy."""
import math

import numpy as np
import pytest

from usdaeco_cctv import bench
from usdaeco_cctv.raycast import EmbreeKernel, Frustum, NumpyKernel, Scene, embree_available, make_kernel


def _scene(kernel="numpy"):
    tris, owners = bench.boxes(40, extent=(30.0, 20.0, 4.0), seed=7)
    return Scene(tris, owners, kernel)


def test_numpy_kernel_hits_and_misses():
    tris, owners = bench.boxes(1, extent=(0.0, 0.0, 0.0), seed=3)     # one box around the origin
    lo, hi = tris.reshape(-1, 3).min(axis=0), tris.reshape(-1, 3).max(axis=0)
    kernel = NumpyKernel()
    kernel.build(tris, owners)
    origins = np.array([[lo[0] - 5, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2], [lo[0] - 5, hi[1] + 5, hi[2] + 5]])
    dirs = np.array([[1.0, 0, 0], [1.0, 0, 0]])
    t, owner, tri = kernel.cast(origins, dirs)
    assert math.isclose(t[0], 5.0, abs_tol=1e-9) and owner[0] == 0 and tri[0] >= 0
    assert np.isinf(t[1]) and owner[1] == -1 and tri[1] == -1


def test_frustum_contains_and_grid():
    basis = np.array([[1.0, 0, 0], [0, 0, 1.0], [0, -1.0, 0]])      # looks along +Y, up is +Z
    f = Frustum((0, 0, 0), basis, math.tan(math.radians(45)), math.tan(math.radians(30)), 10.0)
    inside = f.contains([[0, 5, 0], [4.9, 5, 0], [0, 5, 2.8]])
    outside = f.contains([[0, -5, 0], [6, 5, 0], [0, 5, 3.2], [0, 11, 0]])
    assert inside.all() and not outside.any()
    dirs = f.grid(8, 4)
    assert dirs.shape == (32, 3) and np.allclose(np.linalg.norm(dirs, axis=1), 1)
    assert (dirs[:, 1] > 0).all()
    centre, radius = f.sphere()
    assert np.allclose(centre, [0, 5, 0]) and 5 <= radius <= 10


def test_frustum_box_culling():
    basis = np.array([[1.0, 0, 0], [0, 0, 1.0], [0, -1.0, 0]])
    f = Frustum((0, 0, 0), basis, math.tan(math.radians(45)), math.tan(math.radians(30)), 10.0)
    assert f.intersects_box([-1, 4, -1], [1, 6, 1])
    assert not f.intersects_box([-1, -6, -1], [1, -4, 1])            # behind
    assert not f.intersects_box([-1, 20, -1], [1, 22, 1])            # beyond the far sphere
    assert not f.intersects_box([20, 4, -1], [22, 6, 1])             # beside, outside the side plane
    assert not f.intersects_box([-1, 4, 8], [1, 6, 9])               # above the top plane


def test_pass_through_and_occlusion():
    tris, owners = bench.boxes(1, extent=(0.0, 0.0, 0.0), seed=3)
    tris2 = tris + np.array([4.0, 0, 0])
    scene = Scene(np.concatenate([tris, tris2]), np.concatenate([owners, owners + 1]), "numpy")
    lo = tris.reshape(-1, 3).min(axis=0)
    hi = tris.reshape(-1, 3).max(axis=0)
    centre = (lo + hi) / 2
    origin = np.array([lo[0] - 3, centre[1], centre[2]])
    point = np.array([hi[0] + 4 + 3, centre[1], centre[2]])
    blocked, blocker, through = scene.occlusion(origin, [point])
    assert blocked[0] and blocker[0] == 0 and through[0] == []
    blocked, blocker, through = scene.occlusion(origin, [point], skip={0})
    assert blocked[0] and blocker[0] == 1 and through[0] == [0]
    blocked, blocker, through = scene.occlusion(origin, [point], skip={0, 1})
    assert not blocked[0] and blocker[0] == -1 and through[0] == [0, 1]
    # A study excludes both its housing and transparent geometry, but only
    # the latter belongs in the reported list of through hits.
    blocked, blocker, through = scene.occlusion(origin, [point], skip={0, 1}, record_skip={1})
    assert not blocked[0] and blocker[0] == -1 and through[0] == [1]


def test_candidates_follow_the_frustum():
    scene = _scene()
    basis = np.array([[1.0, 0, 0], [0, 0, 1.0], [0, -1.0, 0]])
    everything = Frustum((15, -50, 2), basis, 10.0, 10.0, 1000.0)
    nothing = Frustum((15, 50, 2), basis, 0.1, 0.1, 1.0)
    assert scene.candidates(everything) == list(range(scene.owner_count))
    assert scene.candidates(nothing) == []


def test_vectorized_candidates_equal_exhaustive_boxes():
    scene = _scene()
    basis = np.array([[1., 0, 0], [0, 0, 1.], [0, -1., 0]])
    for x in (-10., 0., 15., 40.):
        for far in (1., 10., 100.):
            frustum = Frustum((x, -5., 2.), basis, 1., .6, far)
            expected = [i for i, (lo, hi) in enumerate(scene.bounds) if frustum.intersects_box(lo, hi)]
            assert scene.candidates(frustum) == expected


def test_depth_map_is_clipped_to_range():
    scene = _scene()
    basis = np.array([[1.0, 0, 0], [0, 0, 1.0], [0, -1.0, 0]])
    f = Frustum((15, -5, 2), basis, 1.0, 0.6, 3.0)
    dirs, dist, owner, through = scene.depth(f, 6, 4)
    assert dirs.shape == (24, 3) and (dist <= 3.0 + 1e-9).all()
    assert ((owner == -1) == (dist >= 3.0 - 1e-9)).all()


@pytest.mark.skipif(not embree_available(), reason="embreex not importable in this interpreter")
def test_kernel_parity_embree():
    tris, owners = bench.boxes(200, seed=11)
    origins, dirs = bench.sensor_rays(3, 32, 18)
    _s, t_np, o_np = bench.time_kernel(NumpyKernel(), tris, owners, origins, dirs)
    _s, t_em, o_em = bench.time_kernel(EmbreeKernel(), tris, owners, origins, dirs)
    stats = bench.parity_stats(t_np, o_np, t_em, o_em)
    assert stats["identicalOwners"], stats


def test_make_kernel_names():
    assert make_kernel("numpy").name == "numpy"
    with pytest.raises(ValueError):
        make_kernel("cuda")


@pytest.mark.parametrize('kind', ['numpy', 'embree'])
def test_empty_scene_masks_and_public_occluded(kind):
    if kind == 'embree' and not embree_available():
        pytest.skip('embree unavailable')
    kernel = make_kernel(kind)
    kernel.build([], [])
    t, owner, tri = kernel.cast([[0, 0, 0]], [[1, 0, 0]])
    assert np.isinf(t).all() and list(owner) == [-1] and list(tri) == [-1]
    kernel.build([[[2,-1,-1],[2,1,-1],[2,0,1]], [[4,-1,-1],[4,1,-1],[4,0,1]]], [0, 1])
    assert kernel.occluded([0,0,0], [[3,0,0]], exclude_owner=0).tolist() == [False]
    assert kernel.occluded([0,0,0], [[5,0,0]], exclude_owner=0).tolist() == [True]
    t, owner, tri = kernel.cast([[0,0,0]], [[1,0,0]], [False,True])
    assert t[0] == pytest.approx(4) and owner[0] == 1 and tri[0] == 1


def test_transparent_hits_end_at_target_or_blocker():
    tri = np.array([[[d,-1,-1],[d,1,-1],[d,0,1]] for d in range(1, 102)])
    scene = Scene(tri, np.arange(101), 'numpy')
    blocked, owner, through = scene.occlusion([0,0,0], [[100.5,0,0]], skip=set(range(100)))
    assert not blocked[0] and owner[0] == -1 and len(through[0]) == 100
    blocked, owner, through = scene.occlusion([0,0,0], [[0.5,0,0]], skip=set(range(100)))
    assert not blocked[0] and through == [[]]


def test_numpy_triangle_chunks_keep_global_nearest():
    tri = np.array([[[d,-1,-1],[d,1,-1],[d,0,1]] for d in range(8200, 0, -1)])
    kernel = NumpyKernel()
    kernel.build(tri, np.arange(len(tri)))
    t, owner, triangle = kernel.cast([[0,0,0]], [[1,0,0]])
    assert t[0] == 1 and owner[0] == 8199 and triangle[0] == 8199


@pytest.mark.skipif(not embree_available(), reason='embree unavailable')
def test_kernel_surface_origin_uses_same_epsilon():
    tri = [[[0,-1,-1],[0,1,-1],[0,0,1]], [[2,-1,-1],[2,1,-1],[2,0,1]]]
    for kernel in (NumpyKernel(), EmbreeKernel()):
        kernel.build(tri, [0,1])
        t, owner, index = kernel.cast([[0,0,0]], [[1,0,0]])
        assert t[0] == 2 and owner[0] == 1 and index[0] == 1
