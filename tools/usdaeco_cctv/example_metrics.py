"""Measured plan footprints and image acceptance for the published example."""
import math

import numpy as np
from pxr import Usd, UsdGeom


def projected_triangles(mesh):
    """Project authored mesh faces into world XY, respecting resetXformStack."""
    points = np.asarray(mesh.GetPointsAttr().Get(), dtype=float)
    world = np.asarray(UsdGeom.Xformable(mesh).ComputeLocalToWorldTransform(Usd.TimeCode.Default()))
    points = (points @ world[:3, :3] + world[3, :3])[:, :2]
    indices = np.asarray(mesh.GetFaceVertexIndicesAttr().Get())
    triangles, start = [], 0
    for count in mesh.GetFaceVertexCountsAttr().Get():
        face = indices[start:start + count]
        triangles.extend(points[[face[0], face[i], face[i + 1]]] for i in range(1, count - 1))
        start += count
    return np.asarray(triangles).reshape(-1, 3, 2)


def _union_length(intervals):
    if not len(intervals):
        return 0.
    intervals = intervals[np.argsort(intervals[:, 0])]
    previous = np.r_[-np.inf, np.maximum.accumulate(intervals[:, 1])[:-1]]
    return float(np.maximum(0, intervals[:, 1] - np.maximum(intervals[:, 0], previous)).sum())


def plan_union_area(triangles, rectangles, spacing=.01):
    """Integrate the union of triangle projections clipped to rectangular halls.

    X intervals are unioned exactly on each scanline (overlaps count once).
    Midpoint integration in Y uses strips no wider than spacing metres, split
    at hall boundaries. This is a plan-area estimate, not surface area or a
    visibility claim between depth samples. No convex hull fills occlusion gaps.
    """
    triangles = np.asarray(triangles, dtype=float).reshape(-1, 3, 2)
    rectangles = np.asarray(rectangles, dtype=float).reshape(-1, 4)
    if spacing <= 0 or not math.isfinite(spacing):
        raise ValueError('positive finite scanline spacing required')
    if not len(triangles) or not len(rectangles):
        return 0.
    low, high = triangles.min(axis=1), triangles.max(axis=1)
    area = 0.
    boundaries = sorted(set(rectangles[:, [1, 3]].ravel()))
    for bottom, top in zip(boundaries, boundaries[1:]):
        count = math.ceil((top - bottom) / spacing)
        step = (top - bottom) / count
        for y in bottom + (np.arange(count) + .5) * step:
            halls = rectangles[(rectangles[:, 1] <= y) & (y < rectangles[:, 3])]
            if not len(halls):
                continue
            active = triangles[(low[:, 1] <= y) & (y < high[:, 1])]
            if not len(active):
                continue
            end = np.roll(active, -1, axis=1)
            crosses = ((active[:, :, 1] <= y) & (y < end[:, :, 1])) | ((end[:, :, 1] <= y) & (y < active[:, :, 1]))
            delta = end - active
            x = active[:, :, 0] + (y - active[:, :, 1]) * delta[:, :, 0] / np.where(crosses, delta[:, :, 1], 1.)
            left = np.where(crosses, x, np.inf).min(axis=1)
            right = np.where(crosses, x, -np.inf).max(axis=1)
            intervals = np.concatenate([np.column_stack((np.maximum(left, x0), np.minimum(right, x1)))
                                        for x0, _, x1, _ in halls])
            intervals = intervals[intervals[:, 0] < intervals[:, 1]]
            area += step * _union_length(intervals)
    return float(area)


def coverage_footprint(stage):
    """Compare the displayed study shells with the same sensors' nominal sectors."""
    shells = [p for p in stage.Traverse() if p.IsA(UsdGeom.Mesh) and p.GetName().startswith('Coverage_')
              and UsdGeom.Imageable(p).ComputeVisibility() != 'invisible']
    sensors = {p.GetParent().GetPath() for p in shells}
    sectors = [stage.GetPrimAtPath(p.AppendChild('Sector')) for p in sorted(sensors)]
    if not shells or not all(sectors):
        raise ValueError('coverage comparison needs shells and matching nominal sectors')
    bbox = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ['guide'])
    rectangles, halls = [], []
    for prim in stage.Traverse():
        if prim.GetAttribute('aeco:props:DC_Space:SpaceType').Get() != 'hall':
            continue
        level = prim.GetParent()
        while level and level.GetTypeName() != 'AecoLevel':
            level = level.GetParent()
        if not level or level.GetName() != 'L00_Ground':
            continue
        extent = next(p for p in prim.GetChildren() if p.GetAttribute('aeco:derived:role').Get() == 'extent')
        bounds = bbox.ComputeWorldBound(extent).ComputeAlignedRange()
        low, high = bounds.GetMin(), bounds.GetMax()
        rectangles.append([low[0], low[1], high[0], high[1]])
        halls.append(str(prim.GetPath()))
    if not halls:
        raise ValueError('L00 hall extents are required')
    measured = {}
    convergence = {}
    for label, prims in [('shell', shells), ('sector', sectors)]:
        triangles = np.concatenate([projected_triangles(UsdGeom.Mesh(p)) for p in prims])
        fine = plan_union_area(triangles, rectangles, .01)
        coarse = plan_union_area(triangles, rectangles, .02)
        measured[label + 'UnionAreaM2'] = round(fine, 4)
        convergence[label + 'AreaDeltaM2'] = round(abs(fine - coarse), 4)
    return {'name': 'CoverageFootprint', 'method': 'XY projection; scanline interval union',
            'scanlineSpacingM': .01, 'coarseSpacingM': .02, 'halls': sorted(halls),
            'sensors': len(sensors), 'shells': len(shells), 'sectors': len(sectors),
            **measured, **convergence}


def render_limits():
    """Stable numeric acceptance; actual pixel measurements live beside renders."""
    return {'name': 'RenderMetrics', 'views': {view: {
        'minimumForegroundFraction': .20, 'maximumSaturatedWhiteFraction': .40,
        'minimumChannelRange': .05, 'foregroundThreshold': .02, 'whiteThreshold': .98,
        'maximumDimension': 1600, 'maximumBytes': 400000,
    } for view in ('overview', 'lookthrough')}}


def measure_render(path, limits):
    from usdaeco_check.images import image_info, pixels
    info = image_info(path)
    rgb = pixels(path)
    metrics = {'foregroundFraction': float((rgb.max(axis=2) > limits['foregroundThreshold']).mean()),
               'saturatedWhiteFraction': float((rgb.min(axis=2) >= limits['whiteThreshold']).mean()),
               'channelRange': float(np.ptp(rgb, axis=(0, 1)).max()), **info}
    metrics['passed'] = bool(
        metrics['foregroundFraction'] >= limits['minimumForegroundFraction']
        and metrics['saturatedWhiteFraction'] <= limits['maximumSaturatedWhiteFraction']
        and metrics['channelRange'] >= limits['minimumChannelRange']
        and max(info['width'], info['height']) <= limits['maximumDimension']
        and info['bytes'] <= limits['maximumBytes'])
    return metrics
