import numpy as np
import pytest
from pxr import Gf
from usdaeco_cctv.frames import sensor_matrix, decompose


def test_frame_cardinal_directions():
    for pan, direction in [(0,(1,0,0)),(90,(0,1,0)),(-90,(0,-1,0)),(180,(-1,0,0))]:
        assert np.allclose(sensor_matrix(pan=pan).TransformDir(Gf.Vec3d(0,0,-1)),direction,atol=1e-12)
    assert sensor_matrix(tilt=30).TransformDir(Gf.Vec3d(0,0,-1))[2] == pytest.approx(-0.5)


def test_frame_round_trip():
    rng=np.random.default_rng(718)
    for pan,tilt,roll in rng.uniform([-180,-89,-180],[180,89,180],size=(200,3)):
        m=sensor_matrix((1,2,3),pan,tilt,roll)
        angles=decompose(m)
        assert np.max(np.abs(np.array(angles)-[pan,tilt,roll])) < 1e-9
        assert np.max(np.abs(np.array(sensor_matrix((1,2,3),*angles))-np.array(m))) < 1e-9


def test_poles_preserve_pose_and_reject_scale():
    for tilt in (-90,90):
        m=sensor_matrix((0,0,0),42,tilt,71)
        assert np.allclose(sensor_matrix(pan=decompose(m)[0],tilt=decompose(m)[1],roll=decompose(m)[2]),m,atol=1e-9)
    with pytest.raises(ValueError):
        decompose(Gf.Matrix4d().SetScale(2))
