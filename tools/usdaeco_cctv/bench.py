"""Kernel benchmark and parity, pure numpy (runs without pxr).

bench:  a synthetic building-scale scene (axis-aligned boxes) and N sensors x
        (nx x ny) rays; build and cast times per kernel. The numpy kernel is
        timed on a small subset and its throughput extrapolated.
parity: cast the rays of a saved .npz (triangles, owners, origins, dirs) with
        both kernels and compare hit owners.

    python -m usdaeco_cctv.bench bench [--boxes 20000 --sensors 457]
    python -m usdaeco_cctv.bench parity scene.npz
"""
import argparse
import json
import math
import sys
import time

import numpy as np

if __package__:
    from .raycast import EmbreeKernel, NumpyKernel, embree_available
else:  # run as a file in an interpreter without pxr (the package imports pxr)
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    from raycast import EmbreeKernel, NumpyKernel, embree_available


def boxes(count, extent=(200.0, 120.0, 12.0), seed=1):
    """Random boxes: 12 triangles each; owner = box index."""
    rng = np.random.default_rng(seed)
    centres = rng.uniform([0, 0, 0], extent, size=(count, 3))
    sizes = rng.uniform([0.2, 0.2, 0.2], [6, 6, 3.5], size=(count, 3))
    lo, hi = centres - sizes / 2, centres + sizes / 2
    corner = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]])
    pts = lo[:, None, :] + corner[None, :, :] * (hi - lo)[:, None, :]        # (N,8,3)
    faces = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    tri_idx = np.array([[f[0], f[1], f[2]] for f in faces] + [[f[0], f[2], f[3]] for f in faces])
    tris = pts[:, tri_idx, :].reshape(-1, 3, 3)
    owners = np.repeat(np.arange(count), 12)
    return tris, owners


def sensor_rays(count, nx=96, ny=54, hfov=88.0, vfov=57.0, extent=(200.0, 120.0), seed=1):
    rng = np.random.default_rng(seed)
    tx, ty = math.tan(math.radians(hfov) / 2), math.tan(math.radians(vfov) / 2)
    xs = (-1 + 2 * (np.arange(nx) + 0.5) / nx) * tx
    ys = (-1 + 2 * (np.arange(ny) + 0.5) / ny) * ty
    X, Y = np.meshgrid(xs, ys)
    local = np.stack([X.ravel(), Y.ravel(), -np.ones(X.size)], 1)
    local /= np.linalg.norm(local, axis=1)[:, None]
    origins, dirs = [], []
    for o in rng.uniform([0, 0, 2.5], [extent[0], extent[1], 3.3], size=(count, 3)):
        yaw, tilt = rng.uniform(0, 2 * math.pi), math.radians(-35)
        Rz = np.array([[math.cos(yaw), -math.sin(yaw), 0], [math.sin(yaw), math.cos(yaw), 0], [0, 0, 1]])
        Rx = np.array([[1, 0, 0], [0, math.cos(tilt), -math.sin(tilt)], [0, math.sin(tilt), math.cos(tilt)]])
        B = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]])     # camera -Z -> +Y, +Y -> +Z
        d = local @ B @ Rx @ Rz
        origins.append(np.repeat(o[None, :], len(d), 0))
        dirs.append(d)
    return np.concatenate(origins), np.concatenate(dirs)


def time_kernel(kernel, tris, owners, origins, dirs):
    t0 = time.perf_counter()
    kernel.build(tris, owners)
    t1 = time.perf_counter()
    t, owner, _tri = kernel.cast(origins, dirs)
    t2 = time.perf_counter()
    return {"kernel": kernel.name, "build": t1 - t0, "cast": t2 - t1, "rays": int(len(dirs)),
            "triangles": int(len(tris)), "hits": int((owner >= 0).sum()),
            "mraysPerSecond": len(dirs) / (t2 - t1) / 1e6}, t, owner


def run_bench(box_count=20000, sensor_count=457, nx=96, ny=54, numpy_boxes=2000, numpy_sensors=4):
    """Full scene with Embree when available; the numpy kernel on a subset."""
    report = {"scene": {"boxes": box_count, "sensors": sensor_count, "raysPerSensor": nx * ny}}
    tris, owners = boxes(box_count)
    origins, dirs = sensor_rays(sensor_count, nx, ny)
    report["scene"]["triangles"] = int(len(tris))
    report["scene"]["rays"] = int(len(dirs))
    if embree_available():
        stats, _t, _o = time_kernel(EmbreeKernel(), tris, owners, origins, dirs)
        report["embree"] = stats
    else:
        report["embree"] = {"kernel": "embree", "available": False, "note": "embree unavailable"}
    small_tris, small_owners = boxes(min(box_count, numpy_boxes))
    n = min(len(dirs), numpy_sensors * nx * ny)
    stats, t_np, o_np = time_kernel(NumpyKernel(), small_tris, small_owners, origins[:n], dirs[:n])
    tests = n * len(small_tris)
    stats["mtestsPerSecond"] = tests / stats["cast"] / 1e6
    stats["projectedFullSceneSeconds"] = len(dirs) * len(tris) / (tests / stats["cast"])
    report["numpy"] = stats
    if embree_available():
        estats, t_em, o_em = time_kernel(EmbreeKernel(), small_tris, small_owners, origins[:n], dirs[:n])
        report["paritySubset"] = parity_stats(t_np, o_np, t_em, o_em)
    return report


def parity_stats(t_a, owner_a, t_b, owner_b):
    """Owners equal per ray; ties at shared surfaces are reported, not hidden."""
    same = owner_a == owner_b
    both = np.isfinite(t_a) & np.isfinite(t_b)
    close = np.zeros(len(t_a), dtype=bool)
    close[both] = np.abs(t_a[both] - t_b[both]) <= 1e-3 * np.maximum(1.0, np.abs(t_a[both]))
    return {"rays": int(len(t_a)), "ownerMismatches": int((~same).sum()),
            "hitsA": int(np.isfinite(t_a).sum()), "hitsB": int(np.isfinite(t_b).sum()),
            "distanceWithin1e-3": int(close.sum()), "identicalOwners": bool(same.all())}


def parity_file(path):
    data = np.load(path)
    tris, owners, origins, dirs = data["triangles"], data["owners"], data["origins"], data["dirs"]
    if not embree_available():
        return {"available": False, "note": "embree unavailable"}
    _s, t_np, o_np = time_kernel(NumpyKernel(), tris, owners, origins, dirs)
    _s, t_em, o_em = time_kernel(EmbreeKernel(), tris, owners, origins, dirs)
    result = parity_stats(t_np, o_np, t_em, o_em)
    result["available"] = True
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("bench")
    b.add_argument("--boxes", type=int, default=20000)
    b.add_argument("--sensors", type=int, default=457)
    b.add_argument("--json", action="store_true")
    p = sub.add_parser("parity")
    p.add_argument("npz")
    args = parser.parse_args(argv)
    if args.command == "bench":
        report = run_bench(args.boxes, args.sensors)
        if args.json:
            print(json.dumps(report))
        else:
            s = report["scene"]
            print("scene: %d boxes = %d triangles; %d sensors x %d rays = %d rays"
                  % (s["boxes"], s["triangles"], s["sensors"], s["raysPerSensor"], s["rays"]))
            e = report["embree"]
            if e.get("available", True):
                print("  embree: build %.3f s, cast %.3f s (%.1f Mrays/s), hits %d"
                      % (e["build"], e["cast"], e["mraysPerSecond"], e["hits"]))
            else:
                print("  embree: unavailable")
            n = report["numpy"]
            print("  numpy: %d rays x %d triangles; build %.3f s, cast %.2f s (%.1f Mtests/s); full scene projected %.0f s"
                  % (n["rays"], n["triangles"], n["build"], n["cast"], n["mtestsPerSecond"], n["projectedFullSceneSeconds"]))
            if "paritySubset" in report:
                print("  parity on the subset: %s" % json.dumps(report["paritySubset"]))
        return 0
    print(json.dumps(parity_file(args.npz)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
