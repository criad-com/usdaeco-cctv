"""Portable closed guide meshes: view sector, level shells, PTZ envelope.

Sector and shell coordinates use the USD camera frame (look down -Z, +Y up)
so they sit directly under the sensor prim; the envelope is in DEVICE
coordinates about the lens pivot (pan 0 = +X, positive tilt down). Every mesh
is apex + (nu+1)(nv+1) cap points, the cap quads plus four triangle fans to
the apex, so it is closed and consistently oriented.
"""
import math
import numpy as np

from .density import range_at_density

PROJECTIONS = ("rectilinear", "fisheye", "cylindrical")


def _topology(nu, nv):
    if not isinstance(nu, int) or not isinstance(nv, int) or nu < 1 or nv < 1:
        raise ValueError("sector subdivisions must be positive integers")

    def g(i, j):
        return 1 + j * (nu + 1) + i
    faces = [[g(i, j), g(i, j + 1), g(i + 1, j + 1), g(i + 1, j)] for j in range(nv) for i in range(nu)]
    faces += [[0, g(i, 0), g(i + 1, 0)] for i in range(nu)]
    faces += [[0, g(i + 1, nv), g(i, nv)] for i in range(nu)]
    faces += [[0, g(0, j + 1), g(0, j)] for j in range(nv)]
    faces += [[0, g(nu, j), g(nu, j + 1)] for j in range(nv)]
    return [len(f) for f in faces], [i for f in faces for i in f]


def sector_mesh(hfov, vfov, radius, nu=24, nv=14, projection="rectilinear"):
    """Apex + (nu+1)(nv+1) cap points at the radius, closed with fans; (points, counts, indices).

    Rectilinear caps use a normalized perspective grid (the four frustum
    planes meet the sphere of the radius). Fisheye is an angular dome;
    cylindrical uses azimuth on a cylinder and a vertical extent from vfov.
    These two shapes make no dewarped density claim.
    """
    counts, indices = _topology(nu, nv)
    if not math.isfinite(radius) or radius <= 0:
        raise ValueError("sector radius must be finite and positive")
    if projection not in PROJECTIONS:
        raise ValueError("unknown projection")
    if not (0 < hfov <= 360 and 0 < vfov <= 180):
        raise ValueError("invalid sector angles")
    if projection == "rectilinear" and (hfov >= 180 or vfov >= 180):
        raise ValueError("rectilinear angles must be below 180 degrees")
    if projection == "cylindrical" and vfov >= 180:
        raise ValueError("cylindrical vertical angle must be below 180 degrees")
    u, v = np.meshgrid(np.linspace(-1, 1, nu + 1), np.linspace(-1, 1, nv + 1))
    a, b = u * math.radians(hfov) / 2, v * math.radians(vfov) / 2
    if projection == "rectilinear":
        points = np.stack((u * math.tan(math.radians(hfov) / 2), v * math.tan(math.radians(vfov) / 2),
                           -np.ones_like(u)), axis=-1)
        points /= np.linalg.norm(points, axis=-1)[..., None]
    elif projection == "fisheye":
        points = np.stack((np.sin(a) * np.cos(b), np.sin(b), -np.cos(a) * np.cos(b)), axis=-1)
    else:
        points = np.stack((np.sin(a), v * math.tan(math.radians(vfov) / 2), -np.cos(a)), axis=-1)
    return np.vstack((np.zeros((1, 3)), points.reshape(-1, 3) * radius)), counts, indices


def level_ranges(hfov, pixels, ladder, model="plane"):
    """{level: distance} at which each ladder density is met; empty for zero pixels."""
    if pixels <= 0:
        return {}
    return {name: range_at_density(pixels, hfov, threshold, model) for name, threshold in ladder.items()}


def level_shells(hfov, vfov, pixels, radius, ladder, model="plane", **kwargs):
    """Named closed shells strictly inside the design sector, one per attainable level."""
    return {name: sector_mesh(hfov, vfov, d, **kwargs)
            for name, d in level_ranges(hfov, pixels, ladder, model).items() if 0 < d < radius}


def ptz_envelope(pan_range, tilt_range, radius, nu=48, nv=18):
    """Mechanical pivot reach in DEVICE coordinates; positive tilt down.

    Lens FOV is not added to mechanical reach. The derivation places this
    fixed device-space zone under the sensor with the inverse of the
    sensor's rotation so it stays put while the head moves.
    """
    counts, indices = _topology(nu, nv)
    if radius <= 0 or not math.isfinite(radius):
        raise ValueError("envelope radius must be finite and positive")
    if (any(not math.isfinite(v) for v in (*pan_range, *tilt_range))
            or pan_range[1] < pan_range[0] or tilt_range[1] < tilt_range[0]):
        raise ValueError("invalid mechanical ranges")
    pan, tilt = np.meshgrid(np.radians(np.linspace(*pan_range, nu + 1)),
                            np.radians(np.linspace(*tilt_range, nv + 1)))
    points = np.stack((np.cos(pan) * np.cos(tilt), np.sin(pan) * np.cos(tilt), -np.sin(tilt)), axis=-1)
    return np.vstack((np.zeros((1, 3)), points.reshape(-1, 3) * radius)), counts, indices


def is_closed(counts, indices):
    """True when every edge is shared by exactly two faces with opposite direction."""
    edges, k = {}, 0
    for count in counts:
        face = indices[k:k + count]
        k += count
        for i in range(count):
            edge = (face[i], face[(i + 1) % count])
            edges[edge] = edges.get(edge, 0) + 1
    return all(n == 1 and (b, a) in edges for (a, b), n in edges.items())
