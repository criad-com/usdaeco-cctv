"""Concave polygons, holes and malformed arrays have kernel-independent semantics."""
import numpy as np
import pytest
from pxr import Usd, UsdGeom
from usdaeco_cctv.study import _triangulate, UnsupportedTopology
from usdaeco_cctv.raycast import NumpyKernel, EmbreeKernel


def mesh_fixture():
    stage = Usd.Stage.CreateInMemory()
    mesh = UsdGeom.Mesh.Define(stage, '/NotchedMesh')
    mesh.CreatePointsAttr([(1, y, z) for y, z in [(0,0), (3,0), (3,3), (2,3), (2,1), (1,1), (1,3), (0,3)]])
    mesh.CreateFaceVertexCountsAttr([8])
    mesh.CreateFaceVertexIndicesAttr(list(range(8)))
    return stage, mesh


@pytest.mark.parametrize('kernel', [NumpyKernel, EmbreeKernel])
@pytest.mark.parametrize('hole', [False, True])
def test_notch_and_all_hole(kernel, hole):
    stage, mesh = mesh_fixture()
    if hole:
        mesh.CreateHoleIndicesAttr([0])
    triangles, _ = _triangulate(mesh, np.eye(4), Usd.TimeCode.Default())
    engine = kernel()
    engine.build(triangles, np.zeros(len(triangles), dtype=np.int64))
    distance, owner, _ = engine.cast(np.array([[0,1.5,2], [0,.5,2.]]), np.array([[1.,0,0], [1.,0,0]]))
    assert np.isinf(distance[0]) and owner[0] == -1
    assert np.isinf(distance[1]) if hole else distance[1] == pytest.approx(1.)


@pytest.mark.parametrize('kernel', [NumpyKernel, EmbreeKernel])
@pytest.mark.parametrize('fault', ['index', 'count', 'bowtie', 'degenerate'])
def test_malformed_mesh_refused(kernel, fault):
    stage, mesh = mesh_fixture()
    if fault == 'index':
        mesh.GetFaceVertexIndicesAttr().Set([0,1,2,3,4,5,6,99])
    elif fault == 'count':
        mesh.GetFaceVertexCountsAttr().Set([7])
    else:
        mesh.GetPointsAttr().Set([(1,0,0), (1,1,1), (1,0,1), (1,1,0)] if fault == 'bowtie'
                                 else [(1,0,0), (1,1,0), (1,2,0), (1,3,0)])
        mesh.GetFaceVertexCountsAttr().Set([4])
        mesh.GetFaceVertexIndicesAttr().Set([0,1,2,3])
    with pytest.raises(UnsupportedTopology, match='cctvUnsupportedTopology: /NotchedMesh'):
        triangles, _ = _triangulate(mesh, np.eye(4), Usd.TimeCode.Default())
        kernel().build(triangles, np.zeros(len(triangles), dtype=np.int64))
