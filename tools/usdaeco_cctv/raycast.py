"""Ray casting behind one interface: Embree 4 (embreex, optional) and chunked numpy.

Pure numpy; no pxr import, so the kernels and the benchmark run in any
interpreter. Coordinates are world metres; directions are unit vectors.
A hit is (t, owner, triangle); a miss is (inf, -1, -1). Owners are the
integer ids the caller assigned per triangle (one per element or bare gprim).
"""
import math

import numpy as np

from .performance import ContentCache, timed

EPS = 1e-6                # ray parameter below which a hit is the origin's own surface
_ELEMENT_BUDGET = 2_000_000   # rays x triangles per numpy chunk (bounded float64 working set)


def embree_available():
    try:
        import embreex  # noqa: F401
        from embreex import rtcore_scene  # noqa: F401
        return True
    except Exception:
        return False


class Kernel:
    """build(triangles (T,3,3), owners (T,)) then cast(origins (R,3), dirs (R,3))."""
    name = ""

    @timed("bvhBuild")
    def build(self, triangles, owners):
        raise NotImplementedError

    def occluded(self, origin, points, exclude_owner=None):
        """Return a boolean per point, excluding the given owner from obstruction."""
        points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
        delta = points - np.asarray(origin, dtype=np.float64)
        distance = np.linalg.norm(delta, axis=1)
        dirs = delta / np.maximum(distance[:, None], EPS)
        mask = self.owner != exclude_owner if exclude_owner is not None else None
        t, _owner, _tri = self.cast(np.broadcast_to(origin, points.shape), dirs, mask)
        return t < distance - EPS

    def cast(self, origins, dirs, candidates=None):
        """Nearest hit per ray: (t (R,), owner (R,), tri (R,)); candidates is an
        optional boolean mask over triangles restricting the search."""
        raise NotImplementedError


class NumpyKernel(Kernel):
    """Möller–Trumbore over (rays x triangles) chunks; no backface culling."""
    name = "numpy"

    @timed("bvhBuild")
    def build(self, triangles, owners):
        self.tri = np.ascontiguousarray(np.asarray(triangles, dtype=np.float64).reshape(-1, 3, 3))
        self.owner = np.asarray(owners, dtype=np.int64).reshape(-1)
        if len(self.owner) != len(self.tri):
            raise ValueError("one owner per triangle")
        self.e1 = self.tri[:, 1] - self.tri[:, 0]
        self.e2 = self.tri[:, 2] - self.tri[:, 0]

    def cast(self, origins, dirs, candidates=None):
        origins = np.asarray(origins, dtype=np.float64).reshape(-1, 3)
        dirs = np.asarray(dirs, dtype=np.float64).reshape(-1, 3)
        R = len(dirs)
        t_out = np.full(R, np.inf)
        tri_out = np.full(R, -1, dtype=np.int64)
        if candidates is None:
            index = np.arange(len(self.tri))
        else:
            index = np.flatnonzero(np.asarray(candidates, dtype=bool))
        if R == 0 or len(index) == 0:
            return t_out, np.full(R, -1, dtype=np.int64), tri_out
        # Bound both dimensions: even a single ray must not allocate T-sized
        # temporaries for an arbitrarily large input mesh.
        for ts in range(0, len(index), 8192):
            ids = index[ts:ts + 8192]
            V0, E1, E2 = self.tri[ids, 0], self.e1[ids], self.e2[ids]
            chunk = max(1, _ELEMENT_BUDGET // len(ids))
            for s in range(0, R, chunk):
                D = dirs[s:s + chunk]
                O = origins[s:s + chunk]
                if np.all(O == O[0]):
                    O = O[:1]  # a view's rays all share their origin
                P = np.cross(D[:, None, :], E2[None, :, :])
                det = np.einsum("tk,rtk->rt", E1, P)
                ok = np.abs(det) > 1e-12
                inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
                T0 = O[:, None, :] - V0[None, :, :]
                u = np.sum(T0 * P, axis=2) * inv
                Q = np.cross(T0, E1[None, :, :])
                v = np.sum(D[:, None, :] * Q, axis=2) * inv
                t = np.sum(E2[None, :, :] * Q, axis=2) * inv
                valid = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > EPS)
                t = np.where(valid, t, np.inf)
                j = np.argmin(t, axis=1)
                tm = t[np.arange(len(D)), j]
                better = tm < t_out[s:s + chunk]
                t_out[s:s + chunk][better] = tm[better]
                tri_out[s:s + chunk][better] = ids[j[better]]
        owner = np.where(tri_out >= 0, self.owner[np.maximum(tri_out, 0)], -1)
        return t_out, owner, tri_out


class EmbreeKernel(Kernel):
    """Embree 4 through embreex; import guarded, construct only when available."""
    name = "embree"

    def __init__(self, device=None):
        self.device = device
        from embreex import rtcore as rtc, rtcore_scene as rtcs
        from embreex.mesh_construction import TriangleMesh
        self._rtc, self._rtcs, self._TriangleMesh = rtc, rtcs, TriangleMesh

    @timed("bvhBuild")
    def build(self, triangles, owners):
        tri = np.ascontiguousarray(np.asarray(triangles, dtype=np.float32).reshape(-1, 3, 3))
        self.owner = np.asarray(owners, dtype=np.int64).reshape(-1)
        if len(self.owner) != len(tri):
            raise ValueError("one owner per triangle")
        self.tri = tri
        self._exact_tri = np.asarray(triangles, dtype=np.float64).reshape(-1, 3, 3)
        self._subsets = ContentCache(48 * 1024**2, 64)
        if self.device is None:
            self.device = self._rtc.EmbreeDevice()
        self.scene = self._rtcs.EmbreeScene(self.device)
        # Packed triangles preserve source order without an indexed upload loop.
        self.mesh = self._TriangleMesh(self.scene, tri) if len(tri) else None
        self.count = len(tri)
        if self.count:
            # Embree commits lazily on the first query; account that work as BVH build.
            self.scene.run(np.zeros((1, 3), dtype=np.float32), np.array([[1, 0, 0]], dtype=np.float32))

    def cast(self, origins, dirs, candidates=None):
        if candidates is not None:
            mask = np.asarray(candidates, dtype=bool)
            key = np.packbits(mask).tobytes()
            if not mask.all():
                subset = self._subsets.get(key)
                if subset is None:
                    subset = EmbreeKernel(self.device)
                    subset.build(self._exact_tri[mask], self.owner[mask])
                    self._subsets.put(key, subset, subset.tri.nbytes * 8 + subset.owner.nbytes + len(key))
                t, owner, tri = subset.cast(origins, dirs)
                hit = tri >= 0
                tri[hit] = np.flatnonzero(mask)[tri[hit]]
                return t, owner, tri
        origins = np.ascontiguousarray(np.asarray(origins, dtype=np.float32).reshape(-1, 3))
        dirs = np.ascontiguousarray(np.asarray(dirs, dtype=np.float32).reshape(-1, 3))
        R = len(dirs)
        if R == 0 or self.count == 0:
            return np.full(R, np.inf), np.full(R, -1, dtype=np.int64), np.full(R, -1, dtype=np.int64)
        res = self.scene.run(origins, dirs, output=1)
        tri = np.asarray(res["primID"], dtype=np.int64)
        hit = tri >= 0
        t = np.where(hit, np.asarray(res["tfar"], dtype=np.float64), np.inf)
        owner = np.where(hit, self.owner[np.maximum(tri, 0)], -1)
        # embreex fixes tnear=0. Match the public EPS contract for rays that
        # start on a surface without perturbing every ray's floating origin.
        near = hit & (t <= EPS)
        if near.any():
            fallback = NumpyKernel()
            fallback.build(self._exact_tri, self.owner)
            t[near], owner[near], tri[near] = fallback.cast(origins[near], dirs[near])
        return t, owner, tri


def make_kernel(name="auto"):
    """'embree' (error when absent), 'numpy', or 'auto' (Embree when importable)."""
    if name == "numpy":
        return NumpyKernel()
    if name == "embree":
        return EmbreeKernel()
    if name == "auto":
        return EmbreeKernel() if embree_available() else NumpyKernel()
    raise ValueError("kernel must be embree, numpy or auto")


class Frustum:
    """A view sector: apex, camera basis (rows: +X right, +Y up, +Z back in world),
    tangent half-extents and far radius. Local = (p - origin) @ basis.T; the camera
    looks down local -Z. Bounding sphere and conservative AABB test for culling."""

    def __init__(self, origin, basis, tan_x, tan_y, far, near=0.05):
        self.origin = np.asarray(origin, dtype=np.float64).reshape(3)
        self.basis = np.asarray(basis, dtype=np.float64).reshape(3, 3)
        self.tan_x, self.tan_y = float(tan_x), float(tan_y)
        self.far, self.near = float(far), float(near)
        look = -self.basis[2]
        cos_theta = 1.0 / math.sqrt(1.0 + self.tan_x ** 2 + self.tan_y ** 2)
        self.centre = self.origin + look * (self.far / 2.0)
        self.radius = self.far * math.sqrt(max(1.25 - cos_theta, 0.25))

    def sphere(self):
        return self.centre, self.radius

    def to_local(self, points):
        return (np.asarray(points, dtype=np.float64).reshape(-1, 3) - self.origin) @ self.basis.T

    def contains(self, points):
        local = self.to_local(points)
        z = -local[:, 2]
        dist = np.linalg.norm(np.asarray(points, dtype=np.float64).reshape(-1, 3) - self.origin, axis=1)
        return ((z > self.near) & (dist <= self.far) &
                (np.abs(local[:, 0]) <= self.tan_x * z) & (np.abs(local[:, 1]) <= self.tan_y * z))

    def grid(self, nx, ny):
        """Unit ray directions over the frustum, row-major (ny rows of nx), pixel centres."""
        xs = (-1 + 2 * (np.arange(nx) + 0.5) / nx) * self.tan_x
        ys = (-1 + 2 * (np.arange(ny) + 0.5) / ny) * self.tan_y
        X, Y = np.meshgrid(xs, ys)
        local = np.stack([X.ravel(), Y.ravel(), -np.ones(X.size)], axis=1)
        local /= np.linalg.norm(local, axis=1)[:, None]
        return local @ self.basis

    def intersects_boxes(self, lo, hi, margin=1e-4):
        """Conservative vector AABB/half-space test, in world metres.

        Reject only boxes wholly outside a side/apex plane or the far sphere.
        The 0.1 mm margin grows with coordinate magnitude for float32 Embree.
        The near clip is deliberately NOT an obstacle plane: a blocker between
        the apex and near clip can still hide a distant target.
        """
        lo, hi = np.asarray(lo, dtype=np.float64).reshape(-1, 3), np.asarray(hi, dtype=np.float64).reshape(-1, 3)
        valid = np.all(lo <= hi, axis=1) & np.isfinite(lo).all(axis=1) & np.isfinite(hi).all(axis=1)
        if not np.allclose(self.basis @ self.basis.T, np.eye(3), rtol=0, atol=1e-10):
            # A sheared device frame has no orthogonal half-space/range proof.
            return valid
        lo, hi = np.where(valid[:, None], lo, 0.), np.where(valid[:, None], hi, 0.)
        padding = margin + 8 * np.finfo(np.float32).eps * np.maximum(
            np.maximum(np.abs(lo).max(axis=1), np.abs(hi).max(axis=1)), np.abs(self.origin).max())
        centre = (lo + hi) * .5 - self.origin
        half = (hi - lo) * .5 + padding[:, None]
        nearest = np.maximum(np.abs(centre) - half, 0.)
        possible = valid & (np.linalg.norm(nearest, axis=1) <= self.far + padding)
        x, y, back = self.basis
        normals = np.array([-back, -x - self.tan_x*back, x - self.tan_x*back,
                            -y - self.tan_y*back, y - self.tan_y*back])
        return possible & ((centre @ normals.T + half @ np.abs(normals).T) >= 0.).all(axis=1)

    def intersects_box(self, lo, hi):
        return bool(self.intersects_boxes(lo, hi)[0])


class Candidates(list):
    """Owner ids plus a conservative gprim mask; integer owner identity is stable."""
    def __init__(self, owners, mask):
        super().__init__(owners)
        self.mask = mask
        self.indices = np.flatnonzero(mask)


class Scene:
    """Triangles with owners, per-owner bounds, one kernel, and the two study queries."""

    def __init__(self, triangles, owners, kernel="auto", groups=None):
        self.cull = True
        self.triangles = np.asarray(triangles, dtype=np.float64).reshape(-1, 3, 3)
        self.owners = np.asarray(owners, dtype=np.int64).reshape(-1)
        self.kernel = kernel if isinstance(kernel, Kernel) else make_kernel(kernel)
        self.kernel.build(self.triangles, self.owners)
        self.owner_count = int(self.owners.max()) + 1 if len(self.owners) else 0
        self.bounds = np.empty((self.owner_count, 2, 3))
        self.bounds[:, 0] = np.inf
        self.bounds[:, 1] = -np.inf
        if len(self.owners):
            np.minimum.at(self.bounds[:, 0], self.owners, self.triangles.min(axis=1))
            np.maximum.at(self.bounds[:, 1], self.owners, self.triangles.max(axis=1))

        self.groups = groups
        self.triangle_lo = self.triangles.min(axis=1)
        self.triangle_hi = self.triangles.max(axis=1)
        self.flagged = set()

    def candidates(self, frustum, segments=()):
        """Owner ids whose bounds meet the frustum (bounding sphere, then planes)."""
        if not self.cull:
            return list(range(self.owner_count))
        if self.groups is not None:
            bounds, counts, owner_ids = self.groups
            keep = self._candidate_bounds(frustum, bounds, segments)
            return Candidates(sorted(set(owner_ids[keep].tolist())), np.repeat(keep, counts))
        keep = self._candidate_bounds(frustum, self.bounds, segments)
        return list(np.flatnonzero(keep))

    @staticmethod
    def _candidate_bounds(frustum, bounds, segments):
        keep = frustum.intersects_boxes(bounds[:, 0], bounds[:, 1])
        # Partly included targets still report every sample's blocker and flags.
        # Retain their entire finite sightline segments, including outside FOV/range.
        for points in segments:
            lo = np.minimum(np.min(points, axis=0), frustum.origin)
            hi = np.maximum(np.max(points, axis=0), frustum.origin)
            margin = 1e-4 + 8 * np.finfo(np.float32).eps * max(np.abs(lo).max(), np.abs(hi).max())
            keep |= ((bounds[:, 0] <= hi + margin) & (bounds[:, 1] >= lo - margin)).all(axis=1)
        return keep

    def candidate_mask(self, owner_ids):
        if isinstance(owner_ids, Candidates):
            return owner_ids.mask
        return np.isin(self.owners, np.asarray(list(owner_ids), dtype=np.int64))

    def _cast_through(self, origins, dirs, skip, candidates=None, max_distance=np.inf, record_skip=None, exact=False):
        """Cast blocking geometry once; record skipped owners only before the
        nearest blocker/endpoint. Masking avoids tunnelling or an iteration cap."""
        origins = np.asarray(origins, dtype=np.float64).reshape(-1, 3)
        dirs = np.asarray(dirs, dtype=np.float64).reshape(-1, 3)
        skip = set(skip)
        if candidates is not None:
            # Owners outside the candidate set cannot contribute a blocker
            # or a through hit. Avoid a full triangle-array scan per distant
            # transparent owner on every view/target query.
            skip.intersection_update(candidates)
        mask = self.candidate_mask(candidates) if candidates is not None else np.ones(len(self.owners), dtype=bool)
        kernel = self.kernel
        owner_ids = self.owners
        if exact and len(dirs):
            # Finite target segments need only their world AABB. The small exact
            # kernel avoids a new Embree BVH for every excluded target and gives
            # coincident blockers a stable tie order (source triangle order).
            endpoints = origins + dirs * np.asarray(max_distance)[..., None]
            lo = np.minimum(origins.min(axis=0), endpoints.min(axis=0)) - EPS
            hi = np.maximum(origins.max(axis=0), endpoints.max(axis=0)) + EPS
            indices = candidates.indices if isinstance(candidates, Candidates) else np.flatnonzero(mask)
            indices = indices[((self.triangle_lo[indices] <= hi) & (self.triangle_hi[indices] >= lo)).all(axis=1)]
            kernel = NumpyKernel()
            owner_ids = self.owners[indices]
            kernel.build(self.triangles[indices], owner_ids)
            mask = np.ones(len(owner_ids), dtype=bool)
        blocking = mask & ~np.isin(owner_ids, list(skip))
        t, owner, _tri = kernel.cast(origins, dirs, blocking)
        through = [[] for _ in dirs]
        limit = np.minimum(t, max_distance)
        recorded = skip if record_skip is None else set(skip).intersection(record_skip)
        for o in sorted(recorded):
            part = mask & (owner_ids == o)
            if not part.any():
                continue
            distance, _owner, _tri = kernel.cast(origins, dirs, part)
            for ray in np.flatnonzero(distance < limit):
                through[ray].append(int(o))
        return t, owner, through

    @timed("depthCasting")
    def depth(self, frustum, nx, ny, skip=(), record_skip=None):
        """Depth map over the frustum grid, clipped to far: (dirs, distance, owner, through)."""
        dirs = frustum.grid(nx, ny)
        origins = np.repeat(frustum.origin[None, :], len(dirs), axis=0)
        if self.kernel.name == "embree" and not skip:
            # Every depth ray lies inside this frustum. Embree already traverses
            # its BVH spatially, so cropping it builds redundant per-view BVHs.
            # Views excluding actual owners use a cropped subset, avoiding a
            # near-full rebuild for each camera housing. Numpy keeps the same
            # conservative gprim mask to bound its ray/triangle products.
            cands = None
        else:
            cands = self.candidates(frustum)
        t, owner, through = self._cast_through(origins, dirs, skip, cands, frustum.far, record_skip)
        if self.flagged and self.kernel.name == "embree":
            # Only owner classification contributes to depth-map findings. At a
            # flagged bound, resolve ties exactly so BVH layout cannot change a
            # finding when classified and unclassified surfaces coincide.
            points = origins + dirs * np.minimum(t, frustum.far)[:, None]
            resolve = np.zeros(len(dirs), dtype=bool)
            for o in self.flagged:
                lo, hi = self.bounds[o]
                margin = 1e-4 + 8*np.finfo(np.float32).eps*max(np.abs(lo).max(), np.abs(hi).max())
                resolve |= ((points >= lo - margin) & (points <= hi + margin)).all(axis=1)
            if resolve.any():
                exact_t, exact_owner, exact_through = self._cast_through(
                    origins[resolve], dirs[resolve], skip, cands, frustum.far, record_skip, exact=True)
                t[resolve], owner[resolve] = exact_t, exact_owner
                for i, hits in zip(np.flatnonzero(resolve), exact_through):
                    through[i] = hits
        beyond = t > frustum.far
        owner = np.where(beyond, -1, owner)
        return dirs, np.minimum(t, frustum.far), owner, through

    def occlusion(self, origin, points, skip=(), candidates=None, record_skip=None):
        """Is each point hidden from origin? (blocked (P,), blocker owner (P,), through)."""
        origin = np.asarray(origin, dtype=np.float64).reshape(3)
        points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
        vec = points - origin
        length = np.linalg.norm(vec, axis=1)
        length = np.where(length > 0, length, 1.0)
        dirs = vec / length[:, None]
        origins = np.repeat(origin[None, :], len(points), axis=0)
        t, owner, through = self._cast_through(origins, dirs, skip, candidates, length, record_skip, exact=True)
        blocked = t < length - 1e-6
        return blocked, np.where(blocked, owner, -1), through

    @timed("targetRays")
    def enclosed(self, points, skip=()):
        """Inside any opaque body, by majority parity on three world axes.

        Each axis must have odd crossings in both directions. This rejects
        single open faces; two agreeing axes tolerate an opening on the third.
        Bounds only select bodies, never establish enclosure. Separate gprims
        are unioned so overlapping bodies of one element cannot cancel parity.
        Points within EPS of body bounds are retained for coverage.
        """
        points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
        enclosed = np.zeros(len(points), dtype=bool)
        if not len(points) or not len(self.triangles):
            return enclosed
        if self.groups is None:
            bounds, owner_ids = self.bounds, np.arange(self.owner_count)
            offsets = None
        else:
            bounds, counts, owner_ids = self.groups
            offsets = np.cumsum(np.r_[0, counts])
        eligible = ~np.isin(owner_ids, list(skip))
        # Bound the broad-phase working set for large target grids.
        for start in range(0, len(points), 256):
            batch = points[start:start + 256]
            inside = ((batch[:, None] > bounds[None, :, 0] + EPS) &
                      (batch[:, None] < bounds[None, :, 1] - EPS)).all(axis=2)
            inside &= eligible
            for body in np.flatnonzero(inside.any(axis=0)):
                indices = start + np.flatnonzero(inside[:, body])
                indices = indices[~enclosed[indices]]
                if not len(indices):
                    continue
                triangles = (self.triangles[self.owners == body] if offsets is None else
                             self.triangles[offsets[body]:offsets[body + 1]])
                centre = bounds[body].mean(axis=0)
                # Body-local coordinates retain thin surfaces far from origin.
                kernel = make_kernel(self.kernel.name)
                kernel.build(triangles - centre, np.zeros(len(triangles), dtype=np.int64))
                origins = np.repeat(points[indices] - centre, 6, axis=0)
                directions = np.tile(np.r_[np.eye(3), -np.eye(3)], (len(indices), 1))
                parity = np.zeros(len(origins), dtype=bool)
                active = np.arange(len(origins))
                step = max(4 * EPS, 4 * np.finfo(np.float32).eps * np.ptp(triangles, axis=(0, 1)).max())
                # Advance beyond coincident triangle hits at face diagonals.
                # No arbitrary crossing cap: each hit passes a new surface.
                while len(active):
                    distance, _, _ = kernel.cast(origins[active], directions[active])
                    hit = np.isfinite(distance)
                    active = active[hit]
                    parity[active] ^= True
                    origins[active] += directions[active] * (distance[hit, None] + step)
                parity = parity.reshape(-1, 6)
                enclosed[indices] |= (parity[:, :3] & parity[:, 3:]).sum(axis=1) >= 2
        return enclosed


def bounding_sphere_hits_box(centre, radius, lo, hi):
    nearest = np.clip(np.asarray(centre, dtype=np.float64), np.asarray(lo), np.asarray(hi))
    return bool(np.linalg.norm(nearest - centre) <= radius)
