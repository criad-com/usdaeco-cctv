"""The three frames of a sensor (design §3.3). Gf uses row vectors; offsets are SI metres.

world --(element xformOps: host placement)--> device frame
device --(translate(offset) . rotZ(pan-90) . rotX(-tilt) . rotY(roll) . boresight)--> sensor frame
sensor: USD camera convention, looks down -Z, +Y up

Pan 0 looks along device +X; tilt is positive DOWN from the horizontal (the
right-hand rotation about X is therefore by -tilt); roll is about the optical
axis (90 = corridor format); the boresight is +90 degrees about X, taking the
camera's -Z look to device +Y and its +Y up to device +Z.
"""
import math
import numpy as np
from pxr import Gf

BORESIGHT_DEGREES = 90.0


def _rotation(axis, degrees):
    return Gf.Matrix4d().SetRotate(Gf.Rotation(Gf.Vec3d(*axis), degrees))


def sensor_matrix(offset=(0, 0, 0), pan=0, tilt=0, roll=0):
    """Return the sensor's local matrix in the device frame (a USD xformOp:transform).

    Column notation: T(offset) . Rz(pan-90) . Rx(-tilt) . Ry(roll) . Rx(90).
    Gf multiplication reverses this column-vector chain. Angles are degrees,
    the offset is metres. All values must be finite.
    """
    if not all(math.isfinite(float(v)) for v in (*offset, pan, tilt, roll)):
        raise ValueError("pose values must be finite")
    return (_rotation((1, 0, 0), BORESIGHT_DEGREES) * _rotation((0, 1, 0), roll) *
            _rotation((1, 0, 0), -tilt) * _rotation((0, 0, 1), pan - 90) *
            Gf.Matrix4d().SetTranslate(Gf.Vec3d(*offset)))


def decompose(matrix):
    """Read (pan, tilt, roll) in degrees back from a rigid sensor_matrix.

    Canonical tilt is [-90, 90], pan and roll [-180, 180). At a vertical
    boresight pan and roll are coupled: roll = 0 is chosen and the matrix is
    still reproduced exactly. Reflections, scales and shears belong to the
    device placement and are refused.
    """
    m = np.asarray(matrix, dtype=float)
    if (m.shape != (4, 4) or not np.isfinite(m).all()
            or not np.allclose(m[:3, 3], 0, atol=1e-12, rtol=0) or abs(m[3, 3] - 1) > 1e-12
            or not np.allclose(m[:3, :3] @ m[:3, :3].T, np.eye(3), atol=1e-10, rtol=0)
            or abs(np.linalg.det(m[:3, :3]) - 1) > 1e-10):
        raise ValueError("sensor matrix must be a finite rigid transform")
    bore = np.asarray(_rotation((1, 0, 0), BORESIGHT_DEGREES))[:3, :3].T
    r = m[:3, :3].T @ bore.T
    b = math.atan2(r[2, 1], math.hypot(r[0, 1], r[1, 1]))
    if math.hypot(r[0, 1], r[1, 1]) > 1e-12:
        a = math.atan2(-r[0, 1], r[1, 1])
        c = math.atan2(-r[2, 0], r[2, 2])
    else:
        a = math.atan2(r[1, 0], r[0, 0])
        c = 0.0

    def wrap(value):
        return (math.degrees(value) + 180) % 360 - 180
    return ((math.degrees(a) + 270) % 360 - 180, -math.degrees(b), wrap(c))


def offset_of(matrix):
    """The lens pivot (translation part) of a sensor matrix, in device metres."""
    return tuple(float(v) for v in np.asarray(matrix, dtype=float)[3, :3])
