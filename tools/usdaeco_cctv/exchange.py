"""USD attribute coercion for the dependency-free camera contract reader."""
from pxr import Gf, Sdf, Vt

from .contract import CONTRACT, PSET, SENSOR, HOUSING, SENSOR_DRIVERS, decode, drivers
from . import contract


def coerce(attr, value):
    kind = attr.GetTypeName()
    if kind == Sdf.ValueTypeNames.Bool:
        if not isinstance(value, bool):
            raise ValueError('%s requires a JSON boolean' % attr.GetPath())
    vectors = {Sdf.ValueTypeNames.Double2: (2, Gf.Vec2d), Sdf.ValueTypeNames.Double3: (3, Gf.Vec3d),
               Sdf.ValueTypeNames.Int2: (2, Gf.Vec2i)}
    if kind in vectors:
        size, make = vectors[kind]
        if not isinstance(value, (tuple, list, Gf.Vec2d, Gf.Vec3d, Gf.Vec2i)) or len(value) != size:
            raise ValueError('%s requires %d components' % (attr.GetPath(), size))
        return make(*value)
    if kind == Sdf.ValueTypeNames.TokenArray:
        return Vt.TokenArray(value)
    return value


def conflict(label, name, authoritative, mirror):
    if isinstance(authoritative, (Gf.Vec2d, Gf.Vec2i, Gf.Vec3d)):
        authoritative = tuple(authoritative)
    if isinstance(mirror, (Gf.Vec2d, Gf.Vec2i, Gf.Vec3d)):
        mirror = tuple(mirror)
    return contract.conflict(label, name, authoritative, mirror)
