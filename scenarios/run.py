#!/usr/bin/env python3
"""Data-driven coverage scenarios on an anonymous copy of the lobby example.

Each case in cctv_cases.json edits a fresh copy of examples/lobby.usda
(elements, meshes, drivers, collections), runs the study (or deliberately
does not), validates with the cctv rules and compares the findings and the
result values with what the case expects. Runnable standalone and
importable by the family gate:

    python scenarios/run.py [--case V-tray ...] [--kernel numpy|embree|auto] [--output report.json]
    python scenarios/run.py --bench          # the synthetic building-scale ray-cast benchmark

The derive case delegates to the Tier A derivation; every other case is
the coverage engine's.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "scenarios" / "cctv_cases.json"
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "scenarios"))

_SEVERITY = {"Error": "error", "Warn": "warn", "Info": "info"}


def _activate():
    from usdaeco_cctv import register_plugins, core_root
    register_plugins()
    if str(core_root() / "tools") not in sys.path:
        sys.path.insert(0, str(core_root() / "tools"))


def load_cases(path=CASES):
    return json.loads(Path(path).read_text())


def open_copy(example):
    """An anonymous, in-memory copy of the example (the file is never touched)."""
    from pxr import Sdf, Usd
    source = Sdf.Layer.FindOrOpen(str(example))
    if not source:
        raise FileNotFoundError(example)
    layer = Sdf.Layer.CreateAnonymous("cctv-scenario.usda")
    layer.TransferContent(source)
    return Usd.Stage.Open(layer)


# ----------------------------------------------------------------------------
# Setup operations (all authored in the copy's root layer)
# ----------------------------------------------------------------------------

def _box(stage, path, size, centre, purpose="render"):
    from pxr import Gf, UsdGeom, Vt
    sx, sy, sz = [s / 2.0 for s in size]
    cx, cy, cz = centre
    pts = [(cx - sx, cy - sy, cz - sz), (cx + sx, cy - sy, cz - sz), (cx + sx, cy + sy, cz - sz), (cx - sx, cy + sy, cz - sz),
           (cx - sx, cy - sy, cz + sz), (cx + sx, cy - sy, cz + sz), (cx + sx, cy + sy, cz + sz), (cx - sx, cy + sy, cz + sz)]
    faces = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.CreatePointsAttr(Vt.Vec3fArray([Gf.Vec3f(*p) for p in pts]))
    mesh.CreateFaceVertexCountsAttr(Vt.IntArray([4] * 6))
    mesh.CreateFaceVertexIndicesAttr(Vt.IntArray([i for f in faces for i in f]))
    mesh.CreatePurposeAttr(purpose)
    mesh.CreateExtentAttr(Vt.Vec3fArray([Gf.Vec3f(cx - sx, cy - sy, cz - sz), Gf.Vec3f(cx + sx, cy + sy, cz + sz)]))
    return mesh


def _value(attr, value):
    """Coerce a JSON value to the attribute's type."""
    from pxr import Gf, Sdf, Vt
    kind = attr.GetTypeName()
    if kind in (Sdf.ValueTypeNames.Double2, Sdf.ValueTypeNames.Float2):
        return Gf.Vec2d(*value)
    if kind in (Sdf.ValueTypeNames.Double3, Sdf.ValueTypeNames.Float3, Sdf.ValueTypeNames.Point3d):
        return Gf.Vec3d(*value)
    if kind == Sdf.ValueTypeNames.Int2:
        return Gf.Vec2i(*value)
    if kind == Sdf.ValueTypeNames.TokenArray:
        return Vt.TokenArray(list(value))
    if kind == Sdf.ValueTypeNames.StringArray:
        return Vt.StringArray(list(value))
    if kind == Sdf.ValueTypeNames.Point3dArray or kind == Sdf.ValueTypeNames.Double3Array:
        return Vt.Vec3dArray([Gf.Vec3d(*v) for v in value])
    return value


def apply_ops(stage, root, ops):
    from pxr import Gf, Sdf, Usd, UsdGeom
    stage.SetEditTarget(stage.GetRootLayer())
    root = Sdf.Path(root)

    def path_of(text):
        return Sdf.Path(text) if text.startswith("/") else root.AppendPath(text)

    for op in ops:
        kind = op["op"]
        if kind == "translate":
            prim = stage.GetPrimAtPath(path_of(op["prim"]))
            UsdGeom.Xformable(prim).AddTranslateOp(opSuffix="edit").Set(Gf.Vec3d(*op["value"]))
        elif kind == "set":
            prim = stage.GetPrimAtPath(path_of(op["prim"]))
            attr = prim.GetAttribute(op["attr"])
            if not attr:
                raise KeyError("%s has no attribute %s" % (prim.GetPath(), op["attr"]))
            attr.Set(_value(attr, op["value"]))
        elif kind == "apply":
            prim = stage.GetPrimAtPath(path_of(op["prim"]))
            prim.ApplyAPI(op["api"], op["instance"]) if op.get("instance") else prim.ApplyAPI(op["api"])
        elif kind == "collection":
            prim = stage.GetPrimAtPath(path_of(op["prim"]))
            Usd.CollectionAPI(prim, op["name"]).CreateIncludesRel().SetTargets([path_of(t) for t in op["targets"]])
        elif kind == "element":
            parent = path_of(op.get("parent", "."))
            prim = stage.DefinePrim(parent.AppendChild(op["name"]), "Xform")
            prim.ApplyAPI("AecoElementAPI")
            prim.GetAttribute("aeco:id").Set("scenario:" + op["name"])
            prim.ApplyAPI("AecoClassificationAPI", "ifc")
            prim.GetAttribute("aeco:class:ifc:code").Set(op["code"])
            if op.get("phase") is not None:
                prim.GetAttribute("aeco:phase").Set(op["phase"])
            if op.get("position"):
                UsdGeom.Xformable(prim).AddTranslateOp().Set(Gf.Vec3d(*op["position"]))
            if op.get("size"):
                body = _box(stage, prim.GetPath().AppendChild("Body"), op["size"], op["centre"])
                body.GetPrim().ApplyAPI("AecoDerivedGeometryAPI")
                for name, value in (("source", "scenario:" + op["name"]), ("role", "body"), ("approx", "tessellated"), ("stamp", "scenario")):
                    body.GetPrim().GetAttribute("aeco:derived:" + name).Set(value)
            for name, value in op.get("attrs", {}).items():
                attr = prim.GetAttribute(name)
                attr.Set(_value(attr, value))
        elif kind == "space":
            prim = stage.DefinePrim(path_of(op.get("parent", "..")).AppendChild(op["name"]), "AecoSpace")
            prim.GetAttribute("aeco:id").Set("scenario:" + op["name"])
            prim.ApplyAPI("AecoClassificationAPI", "ifc")
            prim.GetAttribute("aeco:class:ifc:code").Set("IfcSpace")
        elif kind == "box":
            _box(stage, path_of(op["prim"]), op["size"], op["centre"])
        elif kind == "mesh":
            _box(stage, path_of(op.get("parent", ".")).AppendChild(op["name"]), op["size"], op["centre"])
        elif kind == "sensor":
            camera = stage.GetPrimAtPath(path_of(op["prim"]))
            sensor = UsdGeom.Camera.Define(stage, camera.GetPath().AppendChild(op.get("name", "Sensor_0"))).GetPrim()
            sensor.ApplyAPI("AecoCctvSensorAPI")
            for name, value in op.get("attrs", {}).items():
                attr = sensor.GetAttribute("aeco:cctvSensor:" + name)
                attr.Set(_value(attr, value))
        else:
            raise ValueError("unknown setup op " + kind)


# ----------------------------------------------------------------------------
# Running and asserting
# ----------------------------------------------------------------------------

def findings_of(stage, profile=None):
    from usdaeco_cctv import validators
    issues = validators.validate_stage(stage, include_core=True, include_builtin=False, profile=profile)
    cctv = sorted([e.GetName(), _SEVERITY[str(e.GetType()).split(".")[-1]], e.GetSites()[0].GetPrim().GetName()]
                  for e in issues if e.GetName().startswith("cctv"))
    others = sorted(e.GetName() + ": " + e.GetMessage() for e in issues
                    if not e.GetName().startswith("cctv") and str(e.GetType()).endswith("Error"))
    messages = {e.GetName() + "@" + e.GetSites()[0].GetPrim().GetName(): e.GetMessage() for e in issues}
    return cctv, others, messages


def result_summary(report):
    keep = ("level", "fixedCoverage", "dutyFraction", "fraction", "nearestViewDistance", "density")
    out = {}
    for path, r in report["results"].items():
        entry = {k: r[k] for k in keep}
        entry["blockers"] = sorted(Path(b).name for b in r["blockers"])
        entry["views"] = sorted(Path(v).parent.name for v in r["views"])
        out[Path(path).name] = entry
    return out


def shells_of(stage, root):
    """Coverage shells per sensor: name -> point count and mean distance to the apex."""
    import numpy as np
    from pxr import Sdf, UsdGeom
    out = {}
    for prim in stage.Traverse():
        if prim.IsA(UsdGeom.Mesh) and prim.GetName().startswith("Coverage") and prim.HasAPI("AecoDerivedGeometryAPI"):
            pts = np.array(UsdGeom.Mesh(prim).GetPointsAttr().Get())
            key = prim.GetParent().GetParent().GetName() + "/" + prim.GetParent().GetName()
            out.setdefault(key, {})[prim.GetName().replace("Coverage_DoorCoverage", "Coverage", 1)] = {"points": int(len(pts)),
                                                       "meanDepth": float(np.linalg.norm(pts[1:] - pts[0], axis=1).mean())}
    return out


def _close(actual, expected, tol=1e-6):
    if isinstance(expected, float) or isinstance(expected, int) and not isinstance(expected, bool):
        return abs(float(actual) - float(expected)) <= tol + 1e-3 * abs(float(expected))
    return actual == expected


def run_case(case, suite, directory, kernel="numpy", previous=None):
    """Execute one case; returns its record (passed, findings, results, notes)."""
    from usdaeco_cctv.study import run_study, input_hash
    if case.get("probe"):
        from integrity import run_probe
        return run_probe(case["id"], directory, kernel)
    stage = open_copy(ROOT / suite["example"])
    root, study_path = suite["root"], suite["studyPath"]
    output = str((Path(directory) / (case["id"] + ".usda")).resolve())
    record = {"id": case["id"], "title": case.get("title", ""), "failures": []}
    fail = record["failures"].append
    apply_ops(stage, root, case.get("before", []))
    if case.get("prerun"):
        run_study(stage, study_path, output, kernel=kernel, recompute=True)
        stage.GetSessionLayer().subLayerPaths.remove(output)
    apply_ops(stage, root, case.get("setup", []))
    for name, value in case.get("study", {}).items():
        attr = stage.GetPrimAtPath(study_path).GetAttribute(name)
        stage.SetEditTarget(stage.GetRootLayer())
        attr.Set(_value(attr, value))
    if case.get("derive"):
        from usdaeco_cctv.derive import derive
        from pxr import Sdf
        layer = Sdf.Layer.CreateNew(str(Path(directory) / (case["id"] + ".derived.usda")))
        stats = derive(stage, layer)
        record["derive"] = {k: v for k, v in stats.items() if not isinstance(v, (list, dict))}
        expect = case["expect"].get("derive", {})
        for key, value in expect.items():
            actual = stats.get(key)
            if not _close(actual, value):
                fail("derive %s: expected %r, got %r" % (key, value, actual))
        for check in case["expect"].get("prims", []):
            prim = stage.GetPrimAtPath(Sdf.Path(root).AppendPath(check["prim"]) if not check["prim"].startswith("/") else check["prim"])
            if not prim:
                fail("missing prim " + check["prim"])
                continue
            for attr in check.get("authored", []):
                a = prim.GetAttribute(attr)
                if not (a and a.HasAuthoredValue()):
                    fail("%s: %s not authored" % (check["prim"], attr))
            for child in check.get("children", []):
                if not prim.GetChild(child):
                    fail("%s: missing child %s" % (check["prim"], child))
            if "timeRange" in check:
                a = prim.GetAttribute(check["timeRange"][0])
                samples = a.GetTimeSamples() if a else []
                span = [min(samples), max(samples)] if samples else None
                if span != check["timeRange"][1]:
                    fail("%s: time samples %s, expected %s" % (check["prim"], span, check["timeRange"][1]))
    if case.get("run", True) and not case.get("derive"):
        report = run_study(stage, study_path, output, kernel=kernel, recompute=not case.get("prerun"))
        record["study"] = {"seconds": report["seconds"], "views": report["views"], "rays": report["rays"],
                           "viewsComputed": len(report["viewsComputed"]), "viewsReused": len(report["viewsReused"]),
                           "inputHash": report["inputHash"], "kernel": report["kernel"]}
        record["results"] = result_summary(report)
        record["shells"] = shells_of(stage, root)
        record["flags"] = {"unphased": [Path(p).name for p in report["unphasedInView"]],
                           "unclassified": [Path(p).name for p in report["unclassifiedInView"]],
                           "exclusionsCovered": [Path(p).name for p in report["exclusionsCovered"]]}
    apply_ops(stage, root, case.get("after", []))
    expect = case["expect"]
    cctv, others, messages = findings_of(stage)
    record["findings"] = cctv
    record["messages"] = messages
    if "findings" in expect and cctv != sorted(expect["findings"]):
        fail("findings %s, expected %s" % (cctv, sorted(expect["findings"])))
    if others:
        fail("non-cctv errors: " + "; ".join(others))
    for key, needle in expect.get("messageContains", {}).items():
        if needle not in messages.get(key, ""):
            fail("message %s lacks %r: %r" % (key, needle, messages.get(key)))
    for profile, wanted in expect.get("profiles", {}).items():
        graded, _o, _m = findings_of(stage, ROOT / "conformance/profiles" / (profile + ".json"))
        record.setdefault("profiles", {})[profile] = graded
        if graded != sorted(wanted):
            fail("profile %s: %s, expected %s" % (profile, graded, sorted(wanted)))
    for name, wanted in expect.get("results", {}).items():
        actual = record.get("results", {}).get(name)
        if actual is None:
            fail("no result for " + name)
            continue
        for key, value in wanted.items():
            if not _close(actual.get(key), value):
                fail("%s.%s: expected %r, got %r" % (name, key, value, actual.get(key)))
    for name in expect.get("absentResults", []):
        if name in record.get("results", {}):
            fail("unexpected result for " + name)
    if expect.get("sameResultsAs"):
        other = (previous or {}).get(expect["sameResultsAs"])
        if not other or other.get("results") != record.get("results"):
            fail("results differ from " + expect["sameResultsAs"])
    for sensor, names in expect.get("shells", {}).items():
        have = record.get("shells", {}).get(sensor, {})
        missing = [n for n in names if n not in have]
        if missing:
            fail("%s lacks shells %s (has %s)" % (sensor, missing, sorted(have)))
    for sensor in expect.get("shellClippedVersus", {}).get("sensors", []):
        other = (previous or {}).get(expect["shellClippedVersus"]["case"], {}).get("shells", {}).get(sensor, {})
        mine = record.get("shells", {}).get(sensor, {})
        if not (other and mine and mine["Coverage"]["meanDepth"] < other["Coverage"]["meanDepth"] - 1e-6):
            fail("%s shell not clipped versus %s" % (sensor, expect["shellClippedVersus"]["case"]))
    if "viewsReusedAtLeast" in expect and record.get("study", {}).get("viewsReused", 0) < expect["viewsReusedAtLeast"]:
        fail("only %s views reused" % record.get("study", {}).get("viewsReused"))
    if "viewsComputed" in expect and record.get("study", {}).get("viewsComputed") != expect["viewsComputed"]:
        fail("views computed %s, expected %s" % (record.get("study", {}).get("viewsComputed"), expect["viewsComputed"]))
    for key, names in expect.get("flags", {}).items():
        if sorted(record.get("flags", {}).get(key, [])) != sorted(names):
            fail("flags %s: %s, expected %s" % (key, record.get("flags", {}).get(key), names))
    record["passed"] = not record["failures"]
    return record


def run_suite(directory, selected=(), kernel="numpy", cases_path=CASES, verbose=True, include_importer=True):
    """Run every case (or the selected ids); returns {"cases": [...], "passed": n, "failed": n}."""
    _activate()
    suite = load_cases(cases_path)
    if not include_importer:
        from integrity import IMPORT_CASES
        suite["cases"] = [c for c in suite["cases"] if c["id"] not in IMPORT_CASES]
    by_id = {case["id"]: case for case in suite["cases"]}
    if selected:
        wanted = set(selected)
        if wanted - by_id.keys():
            raise ValueError("unknown cases: " + ", ".join(sorted(wanted - by_id.keys())))
        while True:
            dependencies = {name for cid in wanted for name in (
                by_id[cid]["expect"].get("sameResultsAs"),
                by_id[cid]["expect"].get("shellClippedVersus", {}).get("case")) if name}
            if dependencies <= wanted:
                break
            wanted |= dependencies
        selected = wanted
    Path(directory).mkdir(parents=True, exist_ok=True)
    records, done = [], {}
    for case in suite["cases"]:
        if selected and case["id"] not in selected:
            continue
        try:
            record = run_case(case, suite, directory, kernel, done)
        except Exception as exc:  # a crash is a failed case, not a crashed suite
            record = {"id": case["id"], "passed": False, "failures": ["%s: %s" % (type(exc).__name__, exc)]}
        done[case["id"]] = record
        records.append(record)
        if verbose:
            print("%s %s%s" % ("PASS" if record["passed"] else "FAIL", case["id"],
                               ("  " + "; ".join(record["failures"])) if record["failures"] else ""), flush=True)
    return {"cases": records, "passed": sum(r["passed"] for r in records), "failed": sum(not r["passed"] for r in records),
            "kernel": kernel}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--case", action="append", default=[], help="run only this case id (repeatable)")
    parser.add_argument("--kernel", choices=("auto", "embree", "numpy"), default="numpy")
    parser.add_argument("--output", type=Path, help="write the JSON report here")
    parser.add_argument("--bench", action="store_true", help="run the synthetic ray-cast benchmark instead of the cases")
    parser.add_argument("--boxes", type=int, default=20000)
    parser.add_argument("--sensors", type=int, default=457)
    args = parser.parse_args(argv)
    if args.bench:
        from usdaeco_cctv import bench
        return bench.main(["bench", "--boxes", str(args.boxes), "--sensors", str(args.sensors)])
    with tempfile.TemporaryDirectory(prefix="aeco-cctv-scenarios-") as tmp:
        report = run_suite(Path(tmp), args.case, args.kernel)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print("%d cases, %d failed" % (len(report["cases"]), report["failed"]))
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
