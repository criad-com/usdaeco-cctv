"""The coverage study (Tier B): scene-dependent occlusion and coverage.

    aeco-cctv study <stage> <studyPath> [-o analysis/cctv.<name>.usda] [--kernel embree|numpy] [--recompute]

Reads drivers only (camera and sensor drivers, type optics, obstacle
geometry and phases, the study's own drivers and collections), casts a
depth map per view against the phase-filtered obstacles and writes, into
its own layer: one Coverage shell per view under the sensor, one result
prim per target under <study>/Results wearing AecoCctvCoverageAPI, the
derived relationships and the input hash on the study, and the layer's
customLayerData (study, tool, time, hash, kernel, per-view cache).
Existing prims receive only overs; the layer adds prims of its own.
Muting the layer restores the stage.
"""
import datetime
import hashlib
import json
import math
from pathlib import Path
from collections import Counter
from copy import copy, deepcopy
from time import perf_counter
from functools import lru_cache

import numpy as np
from pxr import Gf, Sdf, Tf, Usd, UsdGeom, Vt

from . import __version__, iter_cameras, camera_type_of, presets_of, registry, sensors_of
from .density import density_at_range, level_name, optics, range_at_density
from .frames import sensor_matrix
from .derive import level_datum
from .output import isolated_stage, publish, refuse_input
from .raycast import Frustum, Scene
from .performance import ContentCache, Timings, clear_stage_memos, recording, span, stage_memo, timed, file_token

TOOL = "aeco-cctv study " + __version__
ALGORITHM_REVISION = "4"
_GEOMETRY_CACHE = ContentCache(96 * 1024**2, 16000)
_RAW_HASH_CACHE = ContentCache(4 * 1024**2, 4096)
_LOCAL_MESH_CACHE = ContentCache(32 * 1024**2, 4096)
_BUFFERS_CACHE = ContentCache(64 * 1024**2, 4)
_SCENE_CACHE = ContentCache(128 * 1024**2, 2)


def clear_caches():
    """Drop disposable process caches, including retained BVHs (cold benchmark)."""
    for cache in (_GEOMETRY_CACHE, _RAW_HASH_CACHE, _LOCAL_MESH_CACHE, _BUFFERS_CACHE, _SCENE_CACHE):
        cache.clear()
    _shell_topology.cache_clear()
    clear_stage_memos()

ENVELOPE_STEP = 15.0            # degrees between envelope sample views
PRIMARY_HEIGHT = 1.5            # m above the target's base for the primary sample point
INSET_ACROSS = 0.35             # m either side of the centre for the corner samples
INSET_HEIGHTS = (0.5, 2.0)      # m above the base for the corner samples
SPACE_OFFSET = 0.1              # m toward the containing space's centre
NEAR = 0.05                     # m; the camera's own near clip
RADAR_MAX_HFOV = 170.0          # a rectilinear frustum cannot exceed 180 degrees
PURPOSES = (UsdGeom.Tokens.default_, UsdGeom.Tokens.render)


def _num(value):
    """Lossless JSON rendering of resolved USD driver values."""
    if isinstance(value, (Gf.Vec2d, Gf.Vec3d, Gf.Vec2f, Gf.Vec3f, Gf.Vec2i, Gf.Vec3i)):
        return [_num(v) for v in value]
    if isinstance(value, (list, tuple)) or type(value).__name__.endswith("Array"):
        return [_num(v) for v in value]
    if isinstance(value, Sdf.Path):
        return str(value)
    return value


@timed("geometryHashing")
def _digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _matrix(gf):
    return np.asarray(gf, dtype=np.float64)


def _element_of(prim):
    while prim and not prim.IsPseudoRoot():
        if prim.HasAPI("AecoElementAPI"):
            return prim
        prim = prim.GetParent()
    return None


def _ancestor_of_type(prim, type_name):
    prim = prim.GetParent()
    while prim and not prim.IsPseudoRoot():
        if prim.GetTypeName() == type_name:
            return prim
        prim = prim.GetParent()
    return None


def _get(prim, name, default=None):
    attr = prim.GetAttribute(name)
    if not attr:
        return default
    value = attr.Get()
    return default if value is None else value


def phase_state(prim):
    """Use the prim's authored phase, or its owning element's for sensor/body children."""
    attr = prim.GetAttribute("aeco:phase")
    if attr and attr.HasAuthoredValueOpinion():
        return True, attr.Get()
    owner = _element_of(prim)
    if owner and owner != prim:
        attr = owner.GetAttribute("aeco:phase")
        if attr and attr.HasAuthoredValueOpinion():
            return True, attr.Get()
    return False, None


def phase_included(prim, settings):
    authored, phase = phase_state(prim)
    return phase in settings.phases if authored else bool(settings.includeUnphased)


def _collection_paths(study, name):
    collection = Usd.CollectionAPI(study, name)
    paths = sorted(p for p in Usd.CollectionAPI.ComputeIncludedPaths(
        collection.ComputeMembershipQuery(), study.GetStage()) if p.IsPrimPath())
    query = collection.ComputeMembershipQuery()
    # Keep explicitly requested missing/inactive targets visible to completeness checks.
    for path in collection.GetIncludesRel().GetForwardedTargets():
        prim = study.GetStage().GetPrimAtPath(path)
        if path.IsPrimPath() and (not prim or not prim.IsActive()) and query.IsPathIncluded(path):
            paths.append(path)
    paths = sorted(set(paths))
    # Expanding an included element includes its Body too. It remains one
    # target, sampled from the element's bound, rather than two requirements.
    return [p for p in paths if not any(p != q and p.HasPrefix(q) for q in paths)]


class Settings:
    """The study's drivers, resolved with schema fallbacks."""

    NAMES = ("phases", "includeUnphased", "requiredDensity", "levelSystem", "densityModel", "ptzPolicy", "night",
             "glazingTransparent", "raySamples", "maxTargetDistance", "mountHeightRange", "excludeEnclosedSamples")

    def __init__(self, study):
        if not study or not study.HasAPI("AecoCctvStudyAPI"):
            raise ValueError("%s is not a prim wearing AecoCctvStudyAPI" % (study.GetPath() if study else "the study"))
        self.prim = study
        for name in self.NAMES:
            setattr(self, name, _get(study, "aeco:cctvStudy:" + name))
        self.writeShells = bool(_get(study, "aeco:cctvStudy:writeShells", True))
        self.phases = list(self.phases or [])
        self.nx, self.ny = int(self.raySamples[0]), int(self.raySamples[1])
        if self.nx < 2 or self.ny < 2:
            raise ValueError("raySamples must be at least 2 x 2")
        if self.ptzPolicy not in ("ignore", "presets", "presetsNotSole", "envelope"):
            raise ValueError("unknown ptzPolicy " + str(self.ptzPolicy))
        try:
            self.ladder = registry("density_levels")[self.levelSystem]
        except KeyError:
            raise ValueError("levelSystem %s is not in registries/density_levels.json" % self.levelSystem)
        self.transparent_classes = set(registry("sightline_transparent_classes")) if self.glazingTransparent else set()
        self.camera_collection = Usd.CollectionAPI(study, "cameras")
        self.camera_query = self.camera_collection.ComputeMembershipQuery()
        self.all_cameras = not self.camera_collection.GetIncludesRel().GetTargets() and not self.camera_collection.GetIncludeRootAttr().Get()
        self.cameras = _collection_paths(study, "cameras")
        self.targets = _collection_paths(study, "targets")
        self.exclusions = _collection_paths(study, "exclusions")
        self.mpu = UsdGeom.GetStageMetersPerUnit(study.GetStage())
        self.up = 1 if UsdGeom.GetStageUpAxis(study.GetStage()) == UsdGeom.Tokens.y else 2
        self.obstacle_inputs, self.camera_inputs = {}, {}
        if self.densityModel not in ("plane", "arc"):
            raise ValueError("densityModel must be plane or arc")

    def digest(self):
        return _digest({"algorithmRevision": ALGORITHM_REVISION, "libraryVersion": __version__,
                        "study": str(self.prim.GetPath()), "metresPerUnit": self.mpu, "up": self.up,
                        "ladder": self.ladder, "transparentClasses": sorted(self.transparent_classes),
                        "drivers": {n: _num(getattr(self, n)) for n in self.NAMES},
                        "cameras": [str(p) for p in self.cameras], "allCameras": self.all_cameras,
                        "targets": [str(p) for p in self.targets], "exclusions": [str(p) for p in self.exclusions],
                        "targetPhases": {str(p): phase_state(self.prim.GetStage().GetPrimAtPath(p))
                                         for p in self.targets + self.exclusions if self.prim.GetStage().GetPrimAtPath(p)}})


def sensor_selected(sensor, settings):
    if any(p.IsPrimPath() and sensor.GetPath().HasPrefix(p)
           for p in settings.camera_collection.GetExcludesRel().GetForwardedTargets()):
        return False
    return settings.all_cameras or settings.camera_query.IsPathIncluded(sensor.GetPath()) or (
        settings.camera_collection.GetExpansionRuleAttr().Get() == "explicitOnly" and
        settings.camera_query.IsPathIncluded(sensor.GetParent().GetPath()))


def camera_selected(camera, settings):
    return any(sensor_selected(s, settings) for s in sensors_of(camera))


def study_name(study):
    """Stable basename, disambiguated when two studies share a name."""
    peers = [p for p in study.GetStage().Traverse() if p.HasAPI("AecoCctvStudyAPI") and p.GetName() == study.GetName()]
    return study.GetName() + ("_" + hashlib.sha256(str(study.GetPath()).encode()).hexdigest()[:12] if len(peers) > 1 else "")


# ----------------------------------------------------------------------------
# Obstacles
# ----------------------------------------------------------------------------

class Owner:
    """One obstacle: an element (all its gprims) or one bare gprim."""

    def __init__(self, index, path, kind, element, phase, unphased, through, ignore):
        self.index, self.path, self.kind, self.element = index, path, kind, element
        self.phase, self.unphased, self.through, self.ignore = phase, unphased, through, ignore
        self.triangles = []
        self.inputs = []
        self.bounds = []

    @property
    def flagged(self):
        return "unclassified" if self.kind == "unclassified" else ("unphased" if self.unphased else None)


class UnsupportedTopology(ValueError):
    rule = "cctvUnsupportedTopology"
    severity = "error"

    def __init__(self, prim, reason):
        self.path = prim.GetPath()
        super().__init__("%s: %s: %s" % (self.rule, self.path, reason))


def _ear_clip(points, refuse):
    """Deterministic triangulation of a simple planar face, preserving winding."""
    points = points - points[0]
    scale = max(float(np.ptp(points, axis=0).max()), 1e-30)
    normal = np.cross(points, np.roll(points, -1, axis=0)).sum(axis=0)
    if np.linalg.norm(normal) <= scale * scale * 1e-12:
        refuse("degenerate or self-intersecting face")
    normal /= np.linalg.norm(normal)
    if np.max(np.abs(points @ normal)) > scale * 1e-6:
        refuse("non-planar polygon")
    P = np.delete(points, int(np.argmax(np.abs(normal))), axis=1)
    eps = scale * scale * 1e-12
    def cross(a, b, c):
        u, v = P[b] - P[a], P[c] - P[a]
        return u[0] * v[1] - u[1] * v[0]
    n = len(P)
    for i in range(n):
        if np.linalg.norm(P[i] - P[(i + 1) % n]) <= scale * 1e-12:
            refuse("degenerate edge")
        for j in range(i + 1, n):
            a, b, c, d = i, (i + 1) % n, j, (j + 1) % n
            if len({a, b, c, d}) < 4:
                continue
            if (np.maximum(np.minimum(P[a], P[b]), np.minimum(P[c], P[d])) <=
                    np.minimum(np.maximum(P[a], P[b]), np.maximum(P[c], P[d])) + scale * 1e-12).all():
                x, y, z, w = cross(a, b, c), cross(a, b, d), cross(c, d, a), cross(c, d, b)
                if ((x <= eps and y >= -eps) or (y <= eps and x >= -eps)) and \
                        ((z <= eps and w >= -eps) or (w <= eps and z >= -eps)):
                    refuse("self-intersecting face")
    area = sum(P[i, 0] * P[(i+1) % n, 1] - P[(i+1) % n, 0] * P[i, 1] for i in range(n))
    sign = 1 if area > 0 else -1
    remaining, triangles = list(range(n)), []
    while len(remaining) > 3:
        for i, b in enumerate(remaining):
            a, c = remaining[i - 1], remaining[(i + 1) % len(remaining)]
            turn = sign * cross(a, b, c)
            if abs(turn) <= eps:
                remaining.pop(i)
                break
            if turn < 0:
                continue
            if any(min(sign * cross(a, b, p), sign * cross(b, c, p), sign * cross(c, a, p)) >= -eps
                   for p in remaining if p not in (a, b, c)):
                continue
            triangles.append((a, b, c))
            remaining.pop(i)
            break
        else:
            refuse("face cannot be triangulated")
    if len(remaining) != 3 or abs(cross(*remaining)) <= eps:
        refuse("degenerate face")
    triangles.append(tuple(remaining))
    return triangles


@timed("triangulation")
def _triangulate(mesh, world, time):
    """Validate mesh arrays before triangulating; hole faces contribute no triangles."""
    def refuse(reason):
        raise UnsupportedTopology(mesh.GetPrim(), reason)
    P = np.asarray(mesh.GetPointsAttr().Get(time) or [], dtype=np.float64).reshape(-1, 3)
    counts = np.asarray(mesh.GetFaceVertexCountsAttr().Get(time) or [], dtype=np.int64)
    idx = np.asarray(mesh.GetFaceVertexIndicesAttr().Get(time) or [], dtype=np.int64)
    holes = set(mesh.GetHoleIndicesAttr().Get(time) or [])
    if not np.isfinite(P).all() or not np.isfinite(world).all():
        refuse("non-finite points or transform")
    if np.any(counts < 3) or counts.sum() != len(idx):
        refuse("face counts do not match indices")
    if np.any(idx < 0) or np.any(idx >= len(P)):
        refuse("face vertex index outside points")
    if any(h < 0 or h >= len(counts) for h in holes):
        refuse("hole index outside faces")
    P = P @ world[:3, :3] + world[3, :3]
    if np.all(counts == 3):
        tri = idx.reshape(-1, 3)
        if holes:
            tri = tri[~np.isin(np.arange(len(tri)), list(holes))]
        T = P[tri]
        if len(T):
            edges = T[:, 1:] - T[:, :1]
            size = np.linalg.norm(edges[:, 0], axis=1) * np.linalg.norm(edges[:, 1], axis=1)
            if np.any(np.linalg.norm(np.cross(edges[:, 0], edges[:, 1]), axis=1) <= size * 1e-12):
                refuse("degenerate triangle")
        return T, P
    fast = {}
    quad_ids = np.flatnonzero((counts == 4) & ~np.isin(np.arange(len(counts)), list(holes)))
    if len(quad_ids):
        starts = np.cumsum(counts) - counts
        faces = idx[starts[quad_ids, None] + np.arange(4)]
        quads = P[faces]
        if len(quads):
            q = quads - quads[:, :1]
            scale = np.maximum(np.ptp(q, axis=1).max(axis=1), 1e-30)
            normals = np.cross(q, np.roll(q, -1, axis=1)).sum(axis=1)
            length = np.linalg.norm(normals, axis=1)
            unit = normals / np.maximum(length[:, None], 1e-300)
            edges = np.roll(q, -1, axis=1) - q
            turns = np.einsum("fvi,fi->fv", np.cross(edges, np.roll(edges, -1, axis=1)), unit)
            convex = ((length > scale**2 * 1e-12) &
                      (np.abs(np.einsum("fvi,fi->fv", q, unit)).max(axis=1) <= scale * 1e-6) &
                      (turns > scale[:, None]**2 * 1e-12).all(axis=1))
        else:
            convex = np.zeros(0, dtype=bool)
        if np.all(counts == 4) and convex.all():
            # Same diagonal and order as the first ear: preserves exact tie behaviour.
            return P[faces[:, [(3, 0, 1), (1, 2, 3)]].reshape(-1, 3)], P
        fast = {int(i): face for i, face in zip(quad_ids[convex], faces[convex])}
    tris, k = [], 0
    for face_index, count in enumerate(counts):
        face = idx[k:k + count]
        k += count
        if face_index in fast:
            quad = fast[face_index]
            tris.extend((quad[[3, 0, 1]], quad[[1, 2, 3]]))
        elif face_index not in holes:
            tris.extend(tuple(face[j] for j in triangle) for triangle in _ear_clip(P[face], refuse))
    return P[np.asarray(tris, dtype=np.int64).reshape(-1, 3)], P


@timed("triangulation")
def _gprim_triangles(prim, world, time):
    """Tessellate meshes and analytic solids. Other gprims use their bound
    conservatively; their source points/widths are included in the input hash."""
    if prim.IsA(UsdGeom.Mesh):
        return _triangulate(UsdGeom.Mesh(prim), world, time)
    bounds = UsdGeom.Boundable.ComputeExtentFromPlugins(UsdGeom.Boundable(prim), time)
    if bounds is None or len(bounds) != 2:
        raise ValueError("cannot obtain obstacle extent for " + str(prim.GetPath()))
    lo, hi = np.asarray(bounds, dtype=np.float64)
    kind = prim.GetTypeName()
    faces, points = [], []
    if kind in ("Sphere", "Cylinder", "Cone", "Capsule"):
        radius = float(_get(prim, "radius", 1.0))
        height = float(_get(prim, "height", 2.0))
        if kind == "Sphere":
            rings = [(radius * math.cos(t), radius * math.sin(t)) for t in np.linspace(-math.pi/2, math.pi/2, 17)]
        elif kind == "Capsule":
            rings = [(radius * math.cos(t), -height/2 + radius * math.sin(t)) for t in np.linspace(-math.pi/2, 0, 9)]
            rings += [(radius * math.cos(t), height/2 + radius * math.sin(t)) for t in np.linspace(0, math.pi/2, 9)]
        else:
            rings = [(0, -height/2), (radius, -height/2), (0 if kind == "Cone" else radius, height/2), (0, height/2)]
        for r, z in rings:
            points.extend((r*math.cos(t), r*math.sin(t), z) for t in np.arange(24) * (2*math.pi/24))
        for j in range(len(rings)-1):
            for i in range(24):
                a, b, c, d = j*24+i, j*24+(i+1)%24, (j+1)*24+(i+1)%24, (j+1)*24+i
                faces.extend(((a,b,c), (a,c,d)))
        points = np.array(points)
        axis = _get(prim, "axis", "Z")
        if axis == "X":
            points = points[:, [2, 0, 1]]
        elif axis == "Y":
            points = points[:, [1, 2, 0]]
    else:
        corners = np.array([[0,0,0], [1,0,0], [1,1,0], [0,1,0], [0,0,1], [1,0,1], [1,1,1], [0,1,1]])
        points = lo + corners * (hi-lo)
        for a,b,c,d in ((0,3,2,1), (4,5,6,7), (0,1,5,4), (1,2,6,5), (2,3,7,6), (3,0,4,7)):
            faces.extend(((a,b,c), (a,c,d)))
    points = points @ world[:3,:3] + world[3,:3]
    return points[np.asarray(faces)], points


@timed("geometryHashing")
def _geometry_inputs(prim, time, raw=None):
    """Hash numeric geometry arrays without expanding them into Python lists.

    Include the original attributes as well as tessellated points/triangles,
    so edits to any geometry input still invalidate the analysis.
    """
    result = {}
    names = ("points", "faceVertexCounts", "faceVertexIndices", "holeIndices") if prim.IsA(UsdGeom.Mesh) else (
        "points", "faceVertexCounts", "faceVertexIndices", "holeIndices", "widths",
        "curveVertexCounts", "basis", "type", "wrap", "radius", "height", "size", "axis")
    for name in names:
        attr = prim.GetAttribute(name)
        if not attr:
            continue
        value = attr.Get(time)
        if raw is not None:
            raw[name] = value
        if value is not None and attr.GetTypeName().isArray:
            array = np.asarray(value)
            if array.dtype.kind in "biuf":
                result[name] = dict(dtype=str(array.dtype), shape=list(array.shape),
                                    sha256=hashlib.sha256(array.tobytes()).hexdigest())
                continue
        result[name] = _num(value)
    return result



def _cached_geometry(prim, world, time, mpu):
    with span("geometryHashing"):
        raw = {}
        geometry = _geometry_inputs(prim, time, raw)
        if not prim.IsA(UsdGeom.Mesh):
            geometry["computedExtent"] = _num(UsdGeom.Boundable.ComputeExtentFromPlugins(UsdGeom.Boundable(prim), time))
        world_digest = hashlib.sha256(world.tobytes()).hexdigest()
        geometry_key = tuple((name, value["dtype"], tuple(value["shape"]), value["sha256"])
                             if isinstance(value, dict) else (name, json.dumps(value))
                             for name, value in geometry.items())
        raw_digest = _RAW_HASH_CACHE.get(geometry_key)
        if raw_digest is None:
            raw_digest = _RAW_HASH_CACHE.put(geometry_key, _digest(geometry), 1024)
        key = (ALGORITHM_REVISION, prim.GetTypeName(), raw_digest, world_digest, mpu)
    result = _GEOMETRY_CACHE.get(key)
    if result is None:
        if prim.IsA(UsdGeom.Mesh) and np.all(np.asarray(raw["faceVertexCounts"]) == 3):
            # Already-triangulated local shapes recur at many placements.
            # Translation leaves topology unchanged. Recheck degeneracy in
            # world space in one batch before returning gathered geometry.
            linear = world.copy()
            linear[3, :3] = 0.
            local_key = (ALGORITHM_REVISION, raw_digest, hashlib.sha256(linear.tobytes()).hexdigest())
            local = _LOCAL_MESH_CACHE.get(local_key)
            if local is None:
                local = _gprim_triangles(prim, linear, time)
                for array in local:
                    array.flags.writeable = False
                _LOCAL_MESH_CACHE.put(local_key, local, sum(a.nbytes for a in local))
            with span("triangulation"):
                tris, points = (a + world[3, :3] for a in local)
        else:
            tris, points = _gprim_triangles(prim, world, time)
        tris, points = tris * mpu, points * mpu
        tris.flags.writeable = points.flags.writeable = False
        with span("geometryHashing"):
            fingerprints = {"points": hashlib.sha256(points.tobytes()).hexdigest(),
                            "triangles": hashlib.sha256(tris.tobytes()).hexdigest(),
                            "world": world_digest, "geometry": raw_digest}
        result = _GEOMETRY_CACHE.put(key, (tris, points, fingerprints), tris.nbytes + points.nbytes + 1024)
    return result

def is_extent(prim):
    return prim.IsA(UsdGeom.Gprim) and prim.HasAPI("AecoDerivedGeometryAPI") and _get(prim, "aeco:derived:role") == "extent"


def extent_sources(prim):
    """Extent gprims owned by this target; nested spaces/elements own their own extents."""
    sources = []
    for child in Usd.PrimRange(prim):
        if not is_extent(child):
            continue
        parent = child.GetParent()
        while parent and parent != prim and not parent.IsPseudoRoot():
            if parent.HasAPI("AecoElementAPI") or parent.GetTypeName() in ("AecoSpace", "AecoLevel", "AecoFacility", "AecoSite", "AecoFacilityPart"):
                break
            parent = parent.GetParent()
        if child == prim or parent == prim:
            sources.append(child)
    return sources


def _target_bound(prim, bbox):
    sources = extent_sources(prim)
    if not sources:
        return bbox.ComputeWorldBound(prim).ComputeAlignedRange()
    bound = Gf.Range3d()
    for source in sources:
        extent_bbox = UsdGeom.BBoxCache(bbox.GetTime(), [*PURPOSES, UsdGeom.Tokens.guide])
        bound.UnionWith(extent_bbox.ComputeWorldBound(source).ComputeAlignedRange())
    return bound


def _grid_in_extent(points, sources, cache, time, mpu, up):
    """Keep grid points in the projected extent footprint, including concave boundaries."""
    if not sources:
        return points
    axes = [i for i in range(3) if i != up]
    samples = points[:, axes]
    inside = np.zeros(len(points), dtype=bool)
    for source in sources:
        triangles, _ = _gprim_triangles(source, _matrix(cache.GetLocalToWorldTransform(source)), time)
        for triangle in triangles[:, :, axes] * mpu:
            a, b, c = triangle
            u, v = b - a, c - a
            area = u[0] * v[1] - u[1] * v[0]
            if abs(area) <= 1e-12:
                continue
            q = samples - a
            s = (q[:, 0] * v[1] - q[:, 1] * v[0]) / area
            t = (u[0] * q[:, 1] - u[1] * q[:, 0]) / area
            inside |= (s >= -1e-9) & (t >= -1e-9) & (s + t <= 1 + 1e-9)
    if not inside.any():
        raise ValueError("cctvInvalidSampling: extent contains no grid points: " + str(sources[0].GetPath()))
    return points[inside]


@timed("usdTraversalGather")
def gather_obstacles(stage, settings, time=Usd.TimeCode.Default()):
    """Mesh gprims of purpose default/render, grouped by element (or by
    themselves when no element owns them), filtered by the study's phases.
    Returns (owners, triangles (T,3,3), owner index per triangle)."""
    cache = UsdGeom.XformCache(time)
    owners, by_path = [], {}
    mesh_checks = []
    study_path = settings.prim.GetPath()
    settings.geometry_paths = set()
    settings.level_prims = []
    for prim in stage.Traverse(Usd.TraverseInstanceProxies()):
        if not prim.IsInstanceProxy() and prim.GetTypeName() == "AecoLevel":
            settings.level_prims.append(prim)
        if not prim.IsA(UsdGeom.Gprim):
            continue
        settings.geometry_paths.add(prim.GetPath())
        if is_extent(prim) or prim.GetPath().HasPrefix(study_path):
            continue
        purpose = UsdGeom.Imageable(prim).ComputePurpose()
        if purpose not in PURPOSES:
            continue
        element = _element_of(prim)
        physics = prim if prim.HasAPI("AecoCctvSightlineAPI") else (element if element else prim)
        phase_source = element if element else prim
        phase_attr = phase_source.GetAttribute("aeco:phase")
        settings.obstacle_inputs[str(prim.GetPath())] = {
            "purpose": purpose, "element": str(element.GetPath()) if element else None,
            "phaseAuthored": bool(phase_attr and phase_attr.HasAuthoredValueOpinion()),
            "phase": _get(phase_source, "aeco:phase"),
            "classification": _get(physics, "aeco:class:ifc:code"),
            "sightlineAPI": physics.HasAPI("AecoCctvSightlineAPI"),
            "ignore": _get(physics, "aeco:cctvSightline:ignore"),
            "transmittance": _get(physics, "aeco:cctvSightline:transmittance")}
        resolved = settings.obstacle_inputs[str(prim.GetPath())]
        authored, phase = resolved["phaseAuthored"], resolved["phase"]
        if (authored and phase not in settings.phases) or (not authored and not settings.includeUnphased):
            continue
        key = prim.GetPath() if physics == prim else element.GetPath()
        sightline = resolved["sightlineAPI"]
        ignore = bool(sightline and resolved["ignore"])
        through = bool(sightline and float(resolved["transmittance"] or 0.) > 0)
        if element and not sightline and settings.transparent_classes:
            code = str(resolved["classification"] or "")
            through = code in settings.transparent_classes or code.split(".")[0] in settings.transparent_classes
        kind = "element" if element else "unclassified"
        if ignore:
            continue
        owner = by_path.get(key)
        if owner is None:
            owner = Owner(len(owners), element.GetPath() if element else key, kind, element.GetPath() if element else None,
                          phase if authored else None, not authored, through, ignore)
            owner.key = key
            owners.append(owner)
            by_path[key] = owner
        world = _matrix(cache.GetLocalToWorldTransform(prim))
        tris, points, fingerprints = _cached_geometry(prim, world, time, settings.mpu)
        if prim.IsA(UsdGeom.Mesh):
            mesh_checks.append((prim.GetPath(), tris))
        owner.triangles.append(tris)
        owner.bounds.append((tris.min(axis=(0, 1)), tris.max(axis=(0, 1)), len(tris)) if len(tris) else (np.full(3, np.inf), np.full(3, -np.inf), 0))
        owner.inputs.append({"gprim": str(prim.GetPath()), "phase": owner.phase, "unphased": owner.unphased,
                             "through": owner.through, "kind": kind,
                             **fingerprints, "filter": settings.obstacle_inputs[str(prim.GetPath())]})
    # Batch the original world-space triangle degeneracy predicate. Native
    # analytic tessellation is excluded: its polar caps have their own policy.
    with span("triangulation"):
        meshes = np.concatenate([t for _, t in mesh_checks]) if mesh_checks else np.empty((0, 3, 3))
        if len(meshes):
            edges = meshes[:, 1:] - meshes[:, :1]
            size = np.linalg.norm(edges[:, 0], axis=1) * np.linalg.norm(edges[:, 1], axis=1)
            bad = np.flatnonzero(np.linalg.norm(np.cross(edges[:, 0], edges[:, 1]), axis=1) <= size * 1e-12)
            if len(bad):
                i = np.searchsorted(np.cumsum([len(t) for _, t in mesh_checks]), bad[0], side="right")
                raise UnsupportedTopology(stage.GetPrimAtPath(mesh_checks[i][0]), "degenerate triangle")
    tri_list, idx_list = [], []
    for owner in owners:
        tris = (owner.triangles[0] if len(owner.triangles) == 1 else np.concatenate(owner.triangles)) if owner.triangles else np.zeros((0, 3, 3))
        owner.triangles = tris
        owner.digest = _digest(sorted(owner.inputs, key=lambda d: d["gprim"]))
        tri_list.append(tris)
        idx_list.append(np.full(len(tris), owner.index, dtype=np.int64))
    groups = [(lo, hi, count, o.index) for o in owners for lo, hi, count in o.bounds]
    settings.gprim_groups = (np.array([[lo, hi] for lo, hi, _, _ in groups]).reshape(-1, 2, 3),
                             np.array([n for _, _, n, _ in groups], dtype=np.int64),
                             np.array([i for _, _, _, i in groups], dtype=np.int64))
    settings.scene_key = _digest([(str(o.key), o.digest) for o in owners])
    buffers = _BUFFERS_CACHE.get(settings.scene_key)
    if buffers is None:
        triangles = np.concatenate(tri_list) if tri_list else np.zeros((0, 3, 3))
        index = np.concatenate(idx_list) if idx_list else np.zeros(0, dtype=np.int64)
        triangles.flags.writeable = index.flags.writeable = False
        buffers = _BUFFERS_CACHE.put(settings.scene_key, (triangles, index), triangles.nbytes + index.nbytes)
    triangles, index = buffers
    return owners, triangles, index


# ----------------------------------------------------------------------------
# Views
# ----------------------------------------------------------------------------

class View:
    """A sensor at a pose: the default pose, a preset, or an envelope sample."""

    def __init__(self, sensor, camera, key, preset, pose, opt, rng, motorised, dwell_fraction, spectrum, mount_height):
        self.sensor, self.camera, self.key, self.preset = sensor, camera, key, preset
        self.origin, self.basis = pose
        self.hfov, self.vfov, self.pixels, self.focal = opt["hfov"], opt["vfov"], opt["pixels"], opt["focalLength"]
        self.range, self.motorised, self.dwell_fraction, self.spectrum = rng, motorised, dwell_fraction, spectrum
        self.mount_height = mount_height
        self.frustum = Frustum(self.origin, self.basis, math.tan(math.radians(self.hfov) / 2),
                               math.tan(math.radians(self.vfov) / 2), rng, NEAR)
        self.inputs = None

    @property
    def id(self):
        return str(self.sensor.GetPath()) + ("|" + self.key if self.key else "")

    @property
    def label(self):
        return self.sensor.GetParent().GetName() + "/" + self.sensor.GetName() + ((":" + self.key) if self.key else "")

    @property
    def shell_name(self):
        return "Coverage_" + self.study_name + ("_" + Tf.MakeValidIdentifier(self.key) if self.key else "")

    def density(self, distance, model):
        if self.pixels[0] <= 0 or distance <= 0:
            return 0.0
        return density_at_range(self.pixels[0], self.hfov, distance, model)


def _sensor_drivers(sensor):
    names = ("projection", "spectrum", "focalRange", "hfovRange", "vfovRange", "sensorSize", "pixels", "offset",
             "panRange", "tiltRange", "motorised", "pan", "tilt", "roll", "focalLength", "range", "targetDensity", "tour")
    return {n: _get(sensor, "aeco:cctvSensor:" + n) for n in names}


def _pose(camera_world, drivers, pan, tilt, focal):
    local = sensor_matrix(tuple(drivers["offset"]), pan, tilt, drivers["roll"])
    world = local * camera_world
    origin = np.array(world.Transform(Gf.Vec3d(0, 0, 0)), dtype=np.float64)
    basis = np.array([list(world.TransformDir(Gf.Vec3d(*axis))) for axis in ((1, 0, 0), (0, 1, 0), (0, 0, 1))], dtype=np.float64)
    basis /= np.linalg.norm(basis, axis=1)[:, None]
    return origin, basis


def _optics(drivers, focal):
    fr = tuple(drivers["focalRange"])
    pixels = tuple(int(v) for v in drivers["pixels"])
    if drivers["spectrum"] == "radar" or pixels[0] <= 0:
        hfov = float(drivers["hfovRange"][0]) or 90.0
        vfov = float(drivers["vfovRange"][0]) or 30.0
        return {"hfov": min(hfov, RADAR_MAX_HFOV), "vfov": min(vfov, RADAR_MAX_HFOV), "pixels": (0, 0), "focalLength": 0.0}
    f = focal if focal and focal > 0 else fr[0]
    opt = optics(f, fr, tuple(drivers["hfovRange"]), tuple(drivers["vfovRange"]), pixels, tuple(drivers["sensorSize"]))
    opt["pixels"] = pixels
    return opt


@timed("usdTraversalGather")
def enumerate_views(stage, settings, time=Usd.TimeCode.Default()):
    """Views per ptzPolicy; every view carries its own input digest."""
    cache = UsdGeom.XformCache(time)
    views, skipped = [], []
    shell_prefix = study_name(settings.prim)
    levels = []
    for prim in getattr(settings, "level_prims", stage.Traverse()):
        if prim.GetTypeName() == "AecoLevel":
            origin = cache.GetLocalToWorldTransform(prim).Transform(Gf.Vec3d(0))[settings.up]
            levels.append(origin if UsdGeom.Xformable(prim).GetOrderedXformOps() else origin + _get(prim, "aeco:elevation", 0.0))
    for camera in sorted(iter_cameras(stage), key=lambda p: str(p.GetPath())):
        if not camera_selected(camera, settings):
            continue
        camera_world = cache.GetLocalToWorldTransform(camera)
        camera_world.SetTranslateOnly(camera_world.ExtractTranslation() * settings.mpu)
        ctype = camera_type_of(camera)
        ir_range = float(_get(camera, "aeco:cctvType:irRange",
                              _get(ctype, "aeco:cctvType:irRange", 0.0) if ctype else 0.0))
        level = _ancestor_of_type(camera, "AecoLevel")
        elevation = level_datum(stage, camera, camera_world.ExtractTranslation()[settings.up] / settings.mpu, cache, settings.up, level_datums=levels) * settings.mpu
        camera_inputs = {"camera": str(camera.GetPath()), "id": _get(camera, "aeco:id", ""),
                         "mount": _get(camera, "aeco:cctv:mount"), "scenario": _get(camera, "aeco:cctv:scenario"),
                         "phase": phase_state(camera), "irRange": ir_range, "elevation": elevation,
                         "world": hashlib.sha256(_matrix(camera_world).tobytes()).hexdigest()}
        for sensor in sensors_of(camera):
            if not sensor_selected(sensor, settings):
                continue
            drivers = _sensor_drivers(sensor)
            presets = presets_of(sensor)
            motorised = bool(drivers["motorised"])
            sensor_inputs = {"sensor": str(sensor.GetPath()), "drivers": {k: _num(v) for k, v in drivers.items()},
                             "presets": {k: {n: _num(x) for n, x in v.items()} for k, v in presets.items()}}
            settings.camera_inputs[str(sensor.GetPath())] = dict(camera_inputs, **sensor_inputs)
            settings.camera_inputs[str(sensor.GetPath())]["sensorPhase"] = phase_state(sensor)
            if not phase_included(camera, settings) or not phase_included(sensor, settings):
                continue
            if drivers["projection"] != "rectilinear":
                raise ValueError("cctvUnsupportedProjection: %s: %s in %s" %
                                 (sensor.GetPath(), drivers["projection"], settings.prim.GetPath()))
            tour = [t for t in (drivers["tour"] or []) if t in presets]
            tour_total = sum(float(presets[t]["dwell"] or 0.0) for t in tour)
            tour_counts = Counter(tour)
            if any(float(p["dwell"]) < 0 for p in presets.values()):
                raise ValueError("preset dwell must be nonnegative")
            spectrum = drivers["spectrum"]
            required = float(drivers["targetDensity"] or 0.0) or float(settings.requiredDensity)

            def make(key, preset, pan, tilt, focal, dwell_fraction):
                opt = _optics(drivers, focal)
                rng = float(drivers["range"] or 0.0)
                if rng <= 0 and opt["pixels"][0] > 0 and required > 0:
                    rng = range_at_density(opt["pixels"][0], opt["hfov"], required, settings.densityModel)
                if settings.night and spectrum == "visible":
                    rng = min(rng, max(0.0, ir_range))
                if rng <= 0:
                    skipped.append((str(sensor.GetPath()), key, "no reach"))
                    return None
                pose = _pose(camera_world, drivers, pan, tilt, focal)
                height = pose[0][settings.up] - elevation
                view = View(sensor, camera, key, preset, pose, opt, rng, motorised, dwell_fraction, spectrum, height)
                view.study_name = shell_prefix
                view.inputs = dict(camera_inputs, **sensor_inputs, key=key,
                                   pose={"pan": _num(pan), "tilt": _num(tilt), "focal": _num(focal)})
                return view

            if motorised and settings.ptzPolicy == "ignore":
                skipped.append((str(sensor.GetPath()), "", "motorised head ignored by policy"))
                continue
            if motorised and settings.ptzPolicy == "envelope":
                pan_range = tuple(drivers["panRange"]) if tuple(drivers["panRange"]) != (0, 0) else (drivers["pan"], drivers["pan"])
                tilt_range = tuple(drivers["tiltRange"]) if tuple(drivers["tiltRange"]) != (0, 0) else (drivers["tilt"], drivers["tilt"])
                pans = np.arange(pan_range[0], pan_range[1] + 1e-9, ENVELOPE_STEP)
                tilts = np.arange(tilt_range[0], tilt_range[1] + 1e-9, ENVELOPE_STEP)
                for pan in pans:
                    for tilt in tilts:
                        view = make("env_%g_%g" % (pan, tilt), None, float(pan), float(tilt), drivers["focalLength"], 0.0)
                        if view:
                            view.envelope = (pan_range, tilt_range)
                            views.append(view)
                continue
            if motorised and presets:
                for name, values in presets.items():
                    fraction = (tour_counts[name] * float(values["dwell"] or 0.0) / tour_total) if (name in tour and tour_total > 0) else 0.0
                    view = make(name, name, float(values["pan"]), float(values["tilt"]), float(values["focalLength"] or 0.0), fraction)
                    if view:
                        views.append(view)
                continue
            if motorised:
                skipped.append((str(sensor.GetPath()), "", "no presets"))
                continue
            view = make("", None, float(drivers["pan"]), float(drivers["tilt"]), float(drivers["focalLength"] or 0.0),
                        0.0 if motorised else 1.0)
            if view:
                views.append(view)
    return views, skipped


# ----------------------------------------------------------------------------
# Targets
# ----------------------------------------------------------------------------

def sampling(prim):
    spacing = float(_get(prim, "aeco:cctvTarget:gridSpacing", 0.) or 0.)
    fraction = float(_get(prim, "aeco:cctvTarget:passFraction", 0.) or 0.)
    if not math.isfinite(spacing) or spacing < 0 or not math.isfinite(fraction) or not 0 <= fraction <= 1:
        raise ValueError("cctvInvalidSampling: %s requires gridSpacing >= 0 and passFraction in [0, 1]" % prim.GetPath())
    return spacing, fraction


def _area_grid(lo, hi, up, spacing):
    horizontal = [i for i in range(3) if i != up]
    axes = [np.arange(lo[i] + spacing / 2, hi[i], spacing) for i in horizontal]
    axes = [a if len(a) else np.array([(lo[i] + hi[i]) / 2]) for i, a in zip(horizontal, axes)]
    a, b = np.meshgrid(*axes)
    points = np.zeros((a.size, 3))
    points[:, horizontal[0]], points[:, horizontal[1]] = a.ravel(), b.ravel()
    points[:, up] = lo[up] + min(PRIMARY_HEIGHT, max(0., hi[up] - lo[up]))
    return points


@timed("usdTraversalGather")
def target_points(stage, prim, time=Usd.TimeCode.Default(), *, caches=None):
    """World metres: local explicit points or five inset bbox samples; move
    each point 0.1 m horizontally toward its containing space's bbox centre."""
    cache, bbox = caches or (UsdGeom.XformCache(time), UsdGeom.BBoxCache(time, list(PURPOSES)))
    mpu = UsdGeom.GetStageMetersPerUnit(stage)
    up = 1 if UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.y else 2
    world = _matrix(cache.GetLocalToWorldTransform(prim))
    spacing, _ = sampling(prim)
    local = _get(prim, "aeco:cctvTarget:points", []) if prim.HasAPI("AecoCctvTargetAPI") else []
    if local:
        points = np.asarray(local, dtype=np.float64) @ world[:3, :3] + world[3, :3] * mpu
        if not np.isfinite(points).all():
            raise ValueError("cctvInvalidSampling: non-finite target points at " + str(prim.GetPath()))
    else:
        box = _target_bound(prim, bbox)
        if box.IsEmpty():
            points = world[3:4, :3] * mpu
            points[0, up] += PRIMARY_HEIGHT
        else:
            lo, hi = np.array(box.GetMin()) * mpu, np.array(box.GetMax()) * mpu
            if spacing > 0:
                return _grid_in_extent(_area_grid(lo, hi, up, spacing), extent_sources(prim), cache, time, mpu, up)
            centre = (lo + hi) / 2
            across = max((i for i in range(3) if i != up), key=lambda i: hi[i] - lo[i])
            centre[up] = lo[up] + min(PRIMARY_HEIGHT, (hi[up] - lo[up]) / 2 if hi[up] - lo[up] < PRIMARY_HEIGHT else PRIMARY_HEIGHT)
            points = np.broadcast_to(centre, (5, 3)).copy()
            inset = min(INSET_ACROSS, max(0, (hi[across] - lo[across]) / 2 - 0.1))
            points[1:, across] += np.repeat([-inset, inset], 2)
            points[1:, up] = lo[up] + np.tile(np.minimum(INSET_HEIGHTS, max(0, hi[up] - lo[up] - 0.1)), 2)
    space = _ancestor_of_type(prim, "AecoSpace")
    if space:
        bounds = _target_bound(space, bbox)
        if not bounds.IsEmpty():
            centre = (np.array(bounds.GetMin()) + np.array(bounds.GetMax())) * (mpu / 2)
            toward = centre - points
            toward[:, up] = 0
            length = np.linalg.norm(toward, axis=1)
            points += SPACE_OFFSET * toward / np.maximum(length[:, None], 1e-12)
    return points


@timed("geometryHashing")
def _target_digest(stage, prim, points):
    return _digest({"target": str(prim.GetPath()), "points": points.tolist(), "sampling": sampling(prim),
                    "extentGeometry": {str(p.GetPath()): _geometry_inputs(p, Usd.TimeCode.Default()) for p in extent_sources(prim)},
                    "requiredAuthored": bool(prim.GetAttribute("aeco:cctvTarget:requiredDensity")
                        and prim.GetAttribute("aeco:cctvTarget:requiredDensity").HasAuthoredValueOpinion()),
                    "required": _num(_get(prim, "aeco:cctvTarget:requiredDensity", 0.0)) if prim.HasAPI("AecoCctvTargetAPI") else 0.0,
                    "phase": _get(prim, "aeco:phase")})


# ----------------------------------------------------------------------------
# Meshes
# ----------------------------------------------------------------------------

@lru_cache(maxsize=8)
def _shell_topology(nx, ny):
    """Apex + nx*ny grid points; quads over the grid, fans to the apex on the rim."""
    g = lambda i, j: 1 + j * nx + i
    faces = [[g(i, j), g(i + 1, j), g(i + 1, j + 1), g(i, j + 1)] for j in range(ny - 1) for i in range(nx - 1)]
    faces += [[0, g(i + 1, 0), g(i, 0)] for i in range(nx - 1)]
    faces += [[0, g(i, ny - 1), g(i + 1, ny - 1)] for i in range(nx - 1)]
    faces += [[0, g(0, j), g(0, j + 1)] for j in range(ny - 1)]
    faces += [[0, g(nx - 1, j + 1), g(nx - 1, j)] for j in range(ny - 1)]
    return [len(f) for f in faces], [i for f in faces for i in f]


@timed("resultWriting")
def _write_shell(stage, path, source_id, origin, points, nx, ny, colour):
    all_points = np.vstack([origin[None, :], points]) / UsdGeom.GetStageMetersPerUnit(stage)
    counts, indices = _shell_topology(nx, ny)
    values = {
        "points": (Sdf.ValueTypeNames.Point3fArray, Vt.Vec3fArray.FromNumpy(all_points.astype(np.float32))),
        "faceVertexCounts": (Sdf.ValueTypeNames.IntArray, Vt.IntArray(counts)),
        "faceVertexIndices": (Sdf.ValueTypeNames.IntArray, Vt.IntArray(indices)),
        "extent": (Sdf.ValueTypeNames.Float3Array, Vt.Vec3fArray.FromNumpy(np.array([all_points.min(0), all_points.max(0)], dtype=np.float32))),
        "xformOpOrder": (Sdf.ValueTypeNames.TokenArray, Vt.TokenArray(["!resetXformStack!"])),
        "subdivisionScheme": (Sdf.ValueTypeNames.Token, "none"),
        "purpose": (Sdf.ValueTypeNames.Token, "guide"),
        "doubleSided": (Sdf.ValueTypeNames.Bool, True),
        "primvars:displayColor": (Sdf.ValueTypeNames.Color3fArray, Vt.Vec3fArray([Gf.Vec3f(*colour)])),
        "primvars:displayOpacity": (Sdf.ValueTypeNames.FloatArray, Vt.FloatArray([.45])),
    }
    # A depth-grid Mesh is tessellated; ray spacing is not a tolerance claim.
    for name, value in (("source", source_id), ("role", "coverage"), ("approx", "tessellated"), ("stamp", TOOL)):
        values["aeco:derived:" + name] = (Sdf.ValueTypeNames.String if name in ("source", "stamp") else Sdf.ValueTypeNames.Token, value)
    with Sdf.ChangeBlock():
        prim = Sdf.CreatePrimInLayer(stage.GetEditTarget().GetLayer(), path)
        prim.specifier, prim.typeName = Sdf.SpecifierDef, "Mesh"
        prim.SetInfo("apiSchemas", Sdf.TokenListOp.Create(prependedItems=["AecoDerivedGeometryAPI"]))
        for name, (kind, value) in values.items():
            attr = Sdf.AttributeSpec(prim, name, kind)
            attr.default = value
            if name.startswith("primvars:"):
                attr.SetInfo("interpolation", "constant")
    return UsdGeom.Mesh(stage.GetPrimAtPath(path))


# ----------------------------------------------------------------------------
# The study
# ----------------------------------------------------------------------------

def _layer_in_stage(stage, layer):
    return any(l == layer for l in stage.GetLayerStack(includeSessionLayers=True))


def _load_cache(layer):
    data = layer.customLayerData or {}
    views = data.get("aeco:cctv:views") or {}
    return {k: dict(v) for k, v in views.items()} if isinstance(views, dict) else {}


@timed("targetRays")
def _evaluate_view(scene, view, owners, settings, targets, exclusions, model, depth=True):
    """Depth map (unless depth is False), flags, and per-target / per-exclusion
    outcomes for one view. Blockers and flags also come from the target rays."""
    transparent = scene.transparent
    skip = scene.owner_lookup.get(view.camera.GetPath(), set()) | transparent
    flags = {"unphased": set(), "unclassified": set()}
    seen_through = set()
    points = None
    if depth:
        # Self/target geometry is excluded but is not a transparent object to
        # report. Tracing discarded self hits also triggered the expensive
        # exact near-surface fallback for camera housings in real exports.
        dirs, dist, hit_owner, through = scene.depth(view.frustum, settings.nx, settings.ny, skip,
                                                   record_skip=transparent)
        points = view.origin[None, :] + dirs * dist[:, None]
        for o in np.unique(hit_owner[hit_owner >= 0]):
            if owners[o].flagged:
                flags[owners[o].flagged].add(str(owners[o].path))
        for lst in through:
            for o in lst:
                if owners[o].through:
                    seen_through.add(str(owners[o].path))
                    if owners[o].flagged:
                        flags[owners[o].flagged].add(str(owners[o].path))
    candidates = view.candidates
    outcomes = {}
    for path, (prim, pts, required, owner_index) in targets.items():
        inside = view.inside_targets.get(path) if hasattr(view, "inside_targets") else view.frustum.contains(pts)
        if inside is None or not inside.any():
            continue
        d0 = float(np.linalg.norm(pts[0] - view.origin))
        density = view.density(d0, model)
        would_cover = (required <= 0 and view.pixels[0] <= 0) or (view.pixels[0] > 0 and density >= required) \
            or (required <= 0)
        tskip = skip | scene.owner_lookup.get(prim.GetPath(), set())
        blocked, blocker, thr = scene.occlusion(view.origin, pts, tskip, candidates, record_skip=transparent)
        for b in set(int(i) for i in blocker[blocker >= 0]):
            if owners[b].flagged:
                flags[owners[b].flagged].add(str(owners[b].path))
        dens_i = np.array([view.density(float(np.linalg.norm(p - view.origin)), model) for p in pts])
        meets = (dens_i >= required) if view.pixels[0] > 0 else np.full(len(pts), required <= 0)
        point_ok = inside & ~blocked & meets
        entry = {"inside": bool(inside[0]), "visible": bool(inside[0] and not blocked[0]), "density": density, "distance": d0,
                 "covered": bool(inside[0] and not blocked[0] and would_cover), "points": [bool(v) for v in point_ok],
                 "fraction": round(float(point_ok.mean()), 4), "wouldCover": bool(inside[0] and would_cover),
                 "blocker": str(owners[int(blocker[0])].path) if blocked[0] and blocker[0] >= 0 else None,
                 "through": sorted({str(owners[o].path) for o in thr[0] if owners[o].through})}
        entry["densities"] = np.where(inside & ~blocked, dens_i, 0.).tolist()
        outcomes[path] = entry
    exclusion_hits = {}
    for path, (prim, pts, owner_index) in exclusions.items():
        inside = view.inside_exclusions.get(path) if hasattr(view, "inside_exclusions") else view.frustum.contains(pts)
        if inside is None or not inside.any():
            continue
        tskip = skip | scene.owner_lookup.get(prim.GetPath(), set())
        blocked, _blocker, _thr = scene.occlusion(view.origin, pts, tskip, candidates, record_skip=())
        seen = inside & ~blocked
        if seen.any():
            exclusion_hits[path] = int(seen.sum())
    return {"points": points, "flags": {k: sorted(v) for k, v in flags.items()}, "through": sorted(seen_through),
            "targets": outcomes, "exclusions": exclusion_hits, "candidates": [int(c) for c in candidates]}


@timed("depthCasting")
def _envelope_points(scene, view, owners, settings):
    """A spherical depth map over the mechanical reach (device frame) for the
    envelope shell; returns (points, flagged owner paths by kind)."""
    pan_range, tilt_range = view.envelope
    skip = {o.index for o in owners if o.element == view.camera.GetPath()} | {o.index for o in owners if o.through}
    pans = np.radians(np.linspace(pan_range[0], pan_range[1], settings.nx))
    tilts = np.radians(np.linspace(tilt_range[0], tilt_range[1], settings.ny))
    P, T = np.meshgrid(pans, tilts)
    device = np.stack([np.cos(P) * np.cos(T), np.sin(P) * np.cos(T), -np.sin(T)], axis=-1).reshape(-1, 3)
    cache = UsdGeom.XformCache(Usd.TimeCode.Default())
    world = _matrix(cache.GetLocalToWorldTransform(view.camera))
    dirs = device @ world[:3, :3]
    dirs /= np.linalg.norm(dirs, axis=1)[:, None]
    origins = np.repeat(view.origin[None, :], len(dirs), axis=0)
    t, owner, _through = scene._cast_through(origins, dirs, skip)
    within = t <= view.range
    flags = {"unphased": set(), "unclassified": set()}
    for o in set(int(i) for i in owner[(owner >= 0) & within]):
        if owners[o].flagged:
            flags[owners[o].flagged].add(str(owners[o].path))
    dist = np.minimum(t, view.range)
    return view.origin[None, :] + dirs * dist[:, None], {k: sorted(v) for k, v in flags.items()}


def summarize_results(views, targets, settings, sample_counts=None):
    """Aggregate per-view samples without gathering geometry or casting rays."""
    results = {}
    for path, (prim, pts, required, _oi) in targets.items():
        covering = [(v, v.result["targets"][path]) for v in views if path in v.result["targets"] and v.result["targets"][path]["covered"]]
        blocked = [(v, v.result["targets"][path]) for v in views if path in v.result["targets"]
                   and v.result["targets"][path]["wouldCover"] and not v.result["targets"][path]["visible"]]
        union = np.zeros(len(pts), dtype=bool)
        for v in views:
            entry = v.result["targets"].get(path)
            if entry is None:
                continue
            union |= np.array(entry["points"], dtype=bool)
        visible = [(v, v.result["targets"][path]) for v in views if path in v.result["targets"] and v.result["targets"][path]["visible"]]
        density = max((e["density"] for v, e in visible), default=0.0)
        fixed = any(not v.motorised for v, e in covering)
        pass_fraction = sampling(prim)[1]
        if pass_fraction > 0 and len(pts):
            entries = [(v, v.result["targets"][path]) for v in views if path in v.result["targets"]]
            contributors = [(v, e) for v, e in entries if any(e["points"])]
            covering = contributors if union.mean() + 1e-12 >= pass_fraction else []
            fixed_points = np.zeros(len(pts), dtype=bool)
            best_density = np.zeros(len(pts))
            for v, entry in entries:
                best_density = np.maximum(best_density, entry["densities"])
                if not v.motorised:
                    fixed_points |= np.asarray(entry["points"], dtype=bool)
            fixed = bool(covering and fixed_points.mean() + 1e-12 >= pass_fraction)
            density = float(np.sort(best_density)[::-1][max(0, math.ceil(pass_fraction * len(pts)) - 1)])
            visible = entries
        duty_by_sensor = {}
        for v, e in covering:
            if pass_fraction > 0 and not fixed and not v.motorised:
                continue
            key = str(v.sensor.GetPath())
            duty_by_sensor[key] = duty_by_sensor.get(key, 0.0) + v.dwell_fraction
        duty = 1.0 if fixed else min(1.0, max(duty_by_sensor.values(), default=0.0))
        nearest = min((e["distance"] for v, e in covering), default=0.0)
        radar_only = bool(covering) and all(v.pixels[0] <= 0 for v, e in covering)
        level = (min(settings.ladder, key=settings.ladder.get) if radar_only else level_name(density, settings.ladder)) if visible else "none"
        notes = []
        for v, e in covering:
            note = "%s %.0f px/m %.0f%%" % (v.label, e["density"], e["fraction"] * 100)
            if v.pixels[0] <= 0:
                note = "%s radar presence %.0f%%" % (v.label, e["fraction"] * 100)
            if e["through"]:
                note += " through " + ", ".join(Sdf.Path(p).name for p in e["through"])
            if v.motorised:
                note += " (motorised, duty %.3f)" % v.dwell_fraction
            lo_h, hi_h = settings.mountHeightRange
            if (lo_h, hi_h) != (0, 0) and v.mount_height is not None and not (lo_h <= v.mount_height <= hi_h):
                note += " mount %.2f m outside %g-%g m" % (v.mount_height, lo_h, hi_h)
            notes.append(note)
        for v, e in blocked:
            notes.append("%s blocked by %s" % (v.label, Sdf.Path(e["blocker"]).name if e["blocker"] else "unknown"))
        results[path] = {"target": path, "required": required, "density": density, "level": level,
                         "fraction": float(union.mean()) if len(pts) else 0.0, "fixedCoverage": fixed, "dutyFraction": duty,
                         "nearestViewDistance": nearest, "views": sorted({str(v.sensor.GetPath()) for v, e in covering}),
                         "viewNotes": notes, "blockers": sorted({e["blocker"] for v, e in blocked if e["blocker"]})}
        total, enclosed = (sample_counts or {}).get(path, (len(pts), 0))
        results[path].update(sampleCount=total, enclosedSamples=enclosed, evaluatedSamples=len(pts))
    return results


@timed("usdTraversalGather")
def _prepare(stage, settings, memo):
    key = (str(settings.prim.GetPath()), settings.digest())
    if key in memo.prepared:
        return memo.prepared[key]
    owners, triangles, index = gather_obstacles(stage, settings)
    owner_by_path = {o.path: o for o in owners}
    views, skipped = enumerate_views(stage, settings)
    targets, exclusions = {}, {}
    target_caches = (UsdGeom.XformCache(), UsdGeom.BBoxCache(Usd.TimeCode.Default(), list(PURPOSES)))
    for path in settings.targets:
        prim = stage.GetPrimAtPath(path)
        if not prim or not prim.IsActive():
            continue
        if not phase_included(prim, settings):
            continue
        pts = target_points(stage, prim, caches=target_caches)
        required = float(_get(prim, "aeco:cctvTarget:requiredDensity", 0.0) or 0.0) if prim.HasAPI("AecoCctvTargetAPI") else 0.0
        attr = prim.GetAttribute("aeco:cctvTarget:requiredDensity")
        required = required if attr and attr.HasAuthoredValueOpinion() else float(settings.requiredDensity)
        oi = owner_by_path[path].index if path in owner_by_path else None
        targets[str(path)] = (prim, pts, required, oi)
    for path in settings.exclusions:
        prim = stage.GetPrimAtPath(path)
        if not prim or not phase_included(prim, settings):
            continue
        oi = owner_by_path[path].index if path in owner_by_path else None
        exclusions[str(path)] = (prim, target_points(stage, prim, caches=target_caches), oi)
    target_digests = {p: _target_digest(stage, v[0], v[1]) for p, v in targets.items()}
    exclusion_digests = {p: _target_digest(stage, v[0], v[1]) for p, v in exclusions.items()}
    result = dict(settings=settings, owners=owners, triangles=triangles, index=index,
                  views=views, skipped=skipped, targets=targets, exclusions=exclusions,
                  target_digests=target_digests, exclusion_digests=exclusion_digests)
    result["inputHash"] = _digest({"study": settings.digest(), "cameras": settings.camera_inputs,
        "filterInputs": settings.obstacle_inputs, "owners": {str(o.key): o.digest for o in owners},
        "targets": target_digests, "exclusions": exclusion_digests})
    memo.dependencies.update(settings.geometry_paths)
    memo.dependencies.update(Sdf.Path(p) for p in settings.obstacle_inputs)
    memo.dependencies.update(Sdf.Path(p) for p in settings.camera_inputs)
    memo.dependencies.update(settings.targets + settings.exclusions + [settings.prim.GetPath()])
    memo.dependencies.update(p.GetPath() for p in settings.level_prims)
    # Retain at most two gathered studies per stage; large buffers also have
    # their own byte-limited caches. Reports contain no geometry buffers.
    if len(memo.prepared) >= 2:
        memo.prepared.pop(next(iter(memo.prepared)))
    memo.prepared[key] = result
    memo.values[key] = result["inputHash"]
    return result


def run_study(stage, study_path, layer_path=None, *, kernel="auto", recompute=False, time=None, cull=True, format=None):
    """Run and atomically publish; timings include validation and publication."""
    timings = Timings()
    started = perf_counter()
    with recording(timings), span("resultWriting"):
        report = _publish_study(stage, study_path, layer_path, kernel=kernel,
                                recompute=recompute, time=time, cull=cull, format=format)
        study = stage.GetPrimAtPath(report["study"])
        stage_memo(stage).values[(str(study.GetPath()), Settings(study).digest())] = report["inputHash"]
    report["seconds"] = perf_counter() - started
    report["timings"] = timings.seconds
    return report


def _publish_study(stage, study_path, layer_path=None, *, kernel="auto", recompute=False, time=None, cull=True, format=None):
    """Write an owned analysis layer atomically with respect to the open stage.
    Preserve the caller's edit target and restore the layer on any failure.
    A file-backed input is never saved or modified by this function."""
    study = study_path if isinstance(study_path, Usd.Prim) else stage.GetPrimAtPath(study_path)
    settings = Settings(study)  # fail before creating a file when the study is invalid
    memo = stage_memo(stage)
    memo.refresh()
    if layer_path is None:
        root = Path(stage.GetRootLayer().realPath).parent if stage.GetRootLayer().realPath else Path.cwd()
        layer_path = root / "analysis" / ("cctv.%s.%s" % (study.GetName(), format or "usdc"))
    layer_path = Path(layer_path).resolve()
    if format and layer_path.suffix in (".usda", ".usdc") and layer_path.suffix[1:] != format:
        raise ValueError("output suffix conflicts with format; use .usd or the matching suffix")
    refuse_input(stage, layer_path)
    existing = Sdf.Layer.FindOrOpen(str(layer_path)) if layer_path.exists() else None
    if existing and existing.customLayerData.get("aeco:cctv:study") != str(study.GetPath()):
        raise ValueError("output is not a layer owned by this study")
    settings_key = (str(study.GetPath()), settings.digest())
    output_key = (str(layer_path), kernel, cull, settings.writeShells, format)
    cached_report = memo.reports.get((settings_key, output_key))
    if not recompute and time is None and existing and cached_report:
        token, saved_layer, report = cached_report
        if (file_token(layer_path) == token and existing == saved_layer and
                not existing.dirty):
            # Reuse only a previously validated, byte-identical publication.
            stage.GetSessionLayer().subLayerPaths.insert(0, existing.identifier)
            result = deepcopy(report)
            result["viewsReused"] = result["viewsComputed"] + result["viewsReused"]
            result["viewsComputed"] = []
            result["outputChanged"] = False
            return result
    prepared = _prepare(stage, settings, memo)
    pending = Sdf.Layer.CreateAnonymous("cctv-study.usda")
    work = isolated_stage(stage)
    report = _run_study(work, study.GetPath(), pending, prepared=prepared, previous_layer=existing,
                        kernel=kernel, recompute=recompute, time=time, cull=cull)
    from .validators import _result_problems
    problems = _result_problems(work.GetPrimAtPath(study.GetPath()))
    if problems:
        raise ValueError("cctvStudyIncomplete: " + "; ".join(reason for _, reason in problems))
    # publish validates this exact pending layer before atomic replacement.
    if work.GetCompositionErrors():
        raise ValueError("study output does not compose")
    unchanged = False
    if existing and not recompute and time is None and not report["viewsComputed"]:
        # A fresh caller still validates complete results and shells. Ignore
        # only the receipt time when comparing the resulting layer contents.
        data = pending.customLayerData
        receipt = data["aeco:cctv:time"]
        data["aeco:cctv:time"] = existing.customLayerData.get("aeco:cctv:time", "")
        pending.customLayerData = data
        unchanged = (format is None or existing.GetFileFormat().formatId == format) and pending.ExportToString() == existing.ExportToString()
        if not unchanged:
            data["aeco:cctv:time"] = receipt
            pending.customLayerData = data
    published = existing if unchanged else publish(pending, layer_path, format=format)
    memo.published(published)
    stage.GetSessionLayer().subLayerPaths.insert(0, published.identifier)
    report["layer"] = str(layer_path)
    report["outputChanged"] = not unchanged
    if len(memo.reports) >= 4:
        memo.reports.pop(next(iter(memo.reports)))
    memo.reports[(settings_key, output_key)] = (file_token(layer_path), published, deepcopy(report))
    return report


def _run_study(stage, study_path, layer, *, prepared, previous_layer=None, kernel="auto", recompute=False, time=None, cull=True):
    """Compute into an anonymous layer; publication belongs to run_study."""
    study = stage.GetPrimAtPath(study_path)
    settings = prepared["settings"]
    started = datetime.datetime.now(datetime.timezone.utc)
    previous = _load_cache(previous_layer) if previous_layer and not recompute else {}
    snapshot = previous_layer if previous else None
    stage.GetSessionLayer().subLayerPaths.insert(0, layer.identifier)
    stage.SetEditTarget(Usd.EditTarget(layer))

    owners, triangles, index = (prepared[k] for k in ("owners", "triangles", "index"))
    settings = prepared["settings"]
    views = [copy(v) for v in prepared["views"]]
    skipped, targets, exclusions = (prepared[k] for k in ("skipped", "targets", "exclusions"))
    target_digests, exclusion_digests = (prepared[k] for k in ("target_digests", "exclusion_digests"))
    study_digest = settings.digest()
    scene_key = (settings.scene_key, kernel)
    scene = _SCENE_CACHE.get(scene_key)
    if scene is None:
        with span("bvhBuild"):
            scene = Scene(triangles, index, kernel, groups=settings.gprim_groups)
        _SCENE_CACHE.put(scene_key, scene, triangles.nbytes * 8 + index.nbytes)
    scene.owner_lookup = {}
    scene.transparent = {o.index for o in owners if o.through}
    scene.flagged = {o.index for o in owners if o.flagged and len(o.triangles)}
    for owner in owners:
        for path in {owner.path, owner.element} - {None}:
            scene.owner_lookup.setdefault(path, set()).add(owner.index)
    scene.cull = cull
    model = settings.densityModel

    # Classify against the complete opaque scene before frustum culling or
    # camera/target self-exclusion. In particular, opaque door leaves count.
    paths = list(targets)
    lengths = [len(targets[p][1]) for p in paths]
    points = np.concatenate([targets[p][1] for p in paths]) if paths else np.empty((0, 3))
    enclosed = scene.enclosed(points, skip=scene.transparent)
    targets, sample_counts = dict(targets), {}
    target_digests = dict(target_digests)
    offset = 0
    for path, count in zip(paths, lengths):
        prim, pts, required, oi = targets[path]
        mask = enclosed[offset:offset + count]
        offset += count
        sample_counts[path] = (count, int(mask.sum()))
        if settings.excludeEnclosedSamples:
            targets[path] = (prim, pts[~mask], required, oi)
        # Classification can change outside a view's candidate set. Include
        # it explicitly in saved-view keys without invalidating unrelated views.
        target_digests[path] = _digest([target_digests[path], mask.tolist()])

    # per view: hash, reuse or compute
    view_cache, computed, reused = {}, [], []
    def sample_table(items):
        paths = list(items)
        lengths = [len(v[1]) for v in items.values()]
        points = np.concatenate([v[1] for v in items.values()]) if paths else np.empty((0, 3))
        return paths, np.cumsum([0] + lengths), points
    target_table, exclusion_table = sample_table(targets), sample_table(exclusions)
    for view in views:
        view.inside_targets, view.inside_exclusions = {}, {}
        for table, dest in ((target_table, view.inside_targets), (exclusion_table, view.inside_exclusions)):
            paths, offsets, points = table
            included = view.frustum.contains(points)
            for i, path in enumerate(paths):
                inside = included[offsets[i]:offsets[i+1]]
                if inside.any():
                    dest[path] = inside
        near_targets = list(view.inside_targets)
        near_excl = list(view.inside_exclusions)
        segments = [targets[p][1] for p in near_targets] + [exclusions[p][1] for p in near_excl]
        candidates = scene.candidates(view.frustum, segments)
        view.candidates = candidates
        view_hash = _digest({"view": view.inputs, "study": study_digest, "tool": TOOL, "kernel": scene.kernel.name, "cull": cull,
                             "owners": {str(owners[c].key): owners[c].digest for c in candidates},
                             "targets": {p: target_digests[p] for p in near_targets},
                             "exclusions": {p: exclusion_digests[p] for p in near_excl}})
        shell_path = view.sensor.GetPath().AppendChild(view.shell_name)
        cached = previous.get(view.id)
        if cached and cached.get("hash") == view_hash and (not settings.writeShells or snapshot.GetPrimAtPath(shell_path)):
            result = json.loads(cached["cache"])
            if settings.writeShells:
                with Sdf.ChangeBlock():
                    Sdf.CreatePrimInLayer(layer, shell_path.GetParentPath())
                    Sdf.CopySpec(snapshot, shell_path, layer, shell_path)
            reused.append(view.id)
        else:
            source = _get(view.camera, "aeco:id", "")
            colour = (0.9, 0.6, 0.1) if not view.motorised else (0.6, 0.3, 0.9)
            result = _evaluate_view(scene, view, owners, settings, targets, exclusions, model)
            if settings.writeShells:
                _write_shell(stage, shell_path, source, view.origin, result["points"], settings.nx, settings.ny, colour)
            result = {k: v for k, v in result.items() if k != "points"}
            computed.append(view.id)
        view_cache[view.id] = {"hash": view_hash, "cache": json.dumps(result, sort_keys=True)}
        view.result = result

    envelope_sensors = set()
    for view in views:
        if not settings.writeShells or not getattr(view, "envelope", None) or view.sensor.GetPath() in envelope_sensors:
            continue
        envelope_sensors.add(view.sensor.GetPath())
        shell = view.sensor.GetPath().AppendChild("Coverage_" + study_name(study) + "_Envelope")
        group = [v for v in views if v.sensor == view.sensor]
        if snapshot and all(v.id in reused for v in group) and snapshot.GetPrimAtPath(shell):
            Sdf.CopySpec(snapshot, shell, layer, shell)
        else:
            points, _flags = _envelope_points(scene, view, owners, settings)
            _write_shell(stage, shell, _get(view.camera, "aeco:id", ""), view.origin, points,
                         settings.nx, settings.ny, (0.6, 0.3, 0.9))

    results = summarize_results(views, targets, settings, sample_counts)
    unphased = sorted({p for v in views for p in v.result["flags"]["unphased"]})
    unclassified = sorted({p for v in views for p in v.result["flags"]["unclassified"]})
    excl_covered = sorted({p for v in views for p in v.result["exclusions"]})

    # Author derived specs in one Sdf transaction; never query USD while its
    # composition is suspended. Schema declarations supply types/variability.
    input_hash = prepared["inputHash"]
    definitions = Usd.SchemaRegistry().FindAppliedAPIPrimDefinition("AecoCctvCoverageAPI")
    columns = (("requiredDensity", "required"), ("density", "density"), ("level", "level"),
               ("fraction", "fraction"), ("fixedCoverage", "fixedCoverage"),
               ("dutyFraction", "dutyFraction"), ("nearestViewDistance", "nearestViewDistance"),
               ("viewNotes", "viewNotes"), ("enclosedSamples", "enclosedSamples"))
    specs = {name: definitions.GetSchemaAttributeSpec("aeco:cctvCoverage:" + name) for name, _ in columns}
    result_path = study.GetPath().AppendChild("Results")
    study_path = study.GetPath()
    names = Counter(Sdf.Path(p).name for p in results)
    with Sdf.ChangeBlock():
        scope = Sdf.CreatePrimInLayer(layer, result_path)
        scope.specifier, scope.typeName = Sdf.SpecifierDef, "Scope"
        for path, r in results.items():
            name = Sdf.Path(path).name
            if names[name] > 1:
                name += "_" + hashlib.sha256(path.encode()).hexdigest()[:12]
            prim = Sdf.CreatePrimInLayer(layer, result_path.AppendChild(name))
            prim.specifier, prim.typeName = Sdf.SpecifierDef, "Scope"
            prim.SetInfo("apiSchemas", Sdf.TokenListOp.Create(prependedItems=["AecoCctvCoverageAPI"]))
            for name, key in columns:
                schema = specs[name]
                attr = Sdf.AttributeSpec(prim, "aeco:cctvCoverage:" + name, schema.typeName, schema.variability)
                attr.default = Vt.StringArray(r[key]) if name == "viewNotes" else r[key]
            for name, targets_ in (("target", [path]), ("views", r["views"]), ("blockers", r["blockers"])):
                targets_editor = Sdf.RelationshipSpec(prim, "aeco:cctvCoverage:" + name, custom=False).targetPathList
                targets_editor.ClearEditsAndMakeExplicit()
                if targets_:
                    targets_editor.explicitItems = [Sdf.Path(p) for p in targets_]
        prim = Sdf.CreatePrimInLayer(layer, study_path)
        for name, paths in (("unphasedInView", unphased), ("unclassifiedInView", unclassified), ("exclusionsCovered", excl_covered)):
            targets_editor = Sdf.RelationshipSpec(prim, "aeco:cctvStudy:" + name, custom=False).targetPathList
            targets_editor.ClearEditsAndMakeExplicit()
            if paths:
                targets_editor.explicitItems = [Sdf.Path(p) for p in paths]
        for name, value in (("inputHash", input_hash), ("stamp", TOOL)):
            Sdf.AttributeSpec(prim, "aeco:cctvStudy:" + name, Sdf.ValueTypeNames.String).default = value
    stamp_time = (time or started).isoformat(timespec="seconds").replace("+00:00", "Z")
    layer.customLayerData = {"aeco:cctv:study": str(study.GetPath()), "aeco:cctv:tool": TOOL,
                             "aeco:cctv:time": stamp_time, "aeco:cctv:inputHash": input_hash,
                             "aeco:cctv:exclusionViews": {p: {v.id: str(v.sensor.GetPath()) for v in views if p in v.result["exclusions"]} for p in excl_covered},
                             "aeco:cctv:kernel": scene.kernel.name, "aeco:cctv:views": view_cache,
                             "aeco:cctv:writeShells": settings.writeShells, "aeco:cctv:cull": cull,
                             "aeco:cctv:algorithmRevision": ALGORITHM_REVISION, "aeco:cctv:libraryVersion": __version__}
    stage.SetEditTarget(Usd.EditTarget(stage.GetRootLayer()))
    seconds = (datetime.datetime.now(datetime.timezone.utc) - started).total_seconds()
    return {"study": str(study.GetPath()), "layer": layer.identifier, "kernel": scene.kernel.name, "seconds": round(seconds, 3),
            "triangles": int(len(triangles)), "obstacles": len(owners), "views": len(views),
            "viewsComputed": computed, "viewsReused": reused, "skipped": skipped,
            "rays": int(len(views) * settings.nx * settings.ny), "inputHash": input_hash,
            "results": results, "unphasedInView": unphased, "unclassifiedInView": unclassified,
            "exclusionsCovered": excl_covered}


def input_hash(stage, study_path, *, timings=None):
    """Fingerprint inputs without casting; optionally fill exclusive stage timings."""
    measured = Timings()
    with recording(measured), span("usdTraversalGather"):
        study = study_path if isinstance(study_path, Usd.Prim) else stage.GetPrimAtPath(study_path)
        # Read registry/algorithm settings even when USD has sent no change notice.
        key = (str(study.GetPath()), Settings(study).digest())
        memo = stage_memo(stage)
        memo.refresh()
        if key not in memo.values:
            memo.values[key] = _input_hash(stage, study)
        result = memo.values[key]
    if timings is not None:
        timings.update(measured.seconds)
    return result


def _input_hash(stage, study_path):
    """Recompute the hash of every input the study reads, without casting a ray."""
    study = stage.GetPrimAtPath(Sdf.Path(study_path)) if not isinstance(study_path, Usd.Prim) else study_path
    memo = stage_memo(stage)
    return _prepare(stage, Settings(study), memo)["inputHash"]


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(prog="aeco-cctv study", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stage")
    parser.add_argument("studyPath")
    parser.add_argument("-o", "--output", help="analysis layer (default analysis/cctv.<study>.usdc beside the stage)")
    parser.add_argument("--format", choices=("usdc", "usda"), help="output encoding; default usdc (explicit .usda paths retain text)")
    parser.add_argument("--kernel", choices=("auto", "embree", "numpy"), default="auto")
    parser.add_argument("--recompute", action="store_true", help="ignore the per-view cache of an existing layer")
    args = parser.parse_args(argv)
    from . import register_plugins
    register_plugins()
    stage = Usd.Stage.Open(args.stage)
    if not stage:
        parser.error("cannot open " + args.stage)
    report = run_study(stage, args.studyPath, args.output, kernel=args.kernel, recompute=args.recompute, format=args.format)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
