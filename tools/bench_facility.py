#!/usr/bin/env python3
"""Measure complete door studies on the generated demo data centre.

Compose the pinned release in AECO_DATACENTRE_ROOT, or pass --stage pointing
at a converted, camera-imported stage. Converter/import time is excluded.
Default: 30 trials per mode and configuration; --enforce fails unmet budgets.
"""
import argparse
import importlib.metadata
import hashlib
import json
import os
from pathlib import Path
import resource
import platform
import shutil
import subprocess
import sys
from time import perf_counter
import uuid

ROOT = Path(__file__).resolve().parents[1]
BUDGETS = {"cold": 1.0, "cached": .25, "hashOnly": .10}


def marker(name):
    print("== stage: " + name, flush=True)


def python_run(args, cwd=ROOT):
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    environment.pop("PYTHONPATH", None)
    return subprocess.run([sys.executable, *map(str, args)], cwd=cwd, env=environment,
                          check=True, text=True, capture_output=True).stdout


def build_stage(directory, generator):
    marker("compose published facility")
    pin = json.loads((ROOT / "dependencies.json").read_text())["repos"]["datacentre"]
    release = json.loads((Path(generator) / "library.json").read_text())
    if "v" + release["version"] != pin["ref"]:
        raise ValueError("published release differs from dependencies.json")
    core = Path(generator) / "dist/base/dc.usda"
    kind = directory / "kind.usda"
    start = perf_counter()
    imported = json.loads(python_run([ROOT / "tools/aeco-cctv", "import", core, "-o", kind]))
    return kind, dict(generator=pin, sourceMode="pinned", buildSeconds=0.,
                      importSeconds=perf_counter() - start, imported=imported)


def author_study(stage, configuration):
    """All 41 doors; generated cameras or the fixed 8 by 4 camera arrangement."""
    from pxr import Gf, Usd, UsdGeom
    from usdaeco_cctv import iter_cameras
    doors = sorted(p.GetPath() for p in stage.Traverse()
                   if str(p.GetAttribute("aeco:class:ifc:code").Get()).split(".")[0] == "IfcDoor")
    generated = list(iter_cameras(stage))
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        study = stage.DefinePrim("/Bench/Doors", "Scope")
        study.ApplyAPI("AecoCctvStudyAPI")
        study.GetAttribute("aeco:cctvStudy:writeShells").Set(False)
        Usd.CollectionAPI(study, "targets").GetIncludesRel().SetTargets(doors)
        if configuration == "fixed32":
            cameras = []
            for i in range(32):
                camera = UsdGeom.Xform.Define(stage, "/Bench/Cam_%02d" % i).GetPrim()
                camera.ApplyAPI("AecoElementAPI")
                camera.ApplyAPI("AecoCctvCameraAPI")
                camera.GetAttribute("aeco:id").Set(str(uuid.uuid5(uuid.NAMESPACE_URL, "bench:" + str(i))))
                camera.GetAttribute("aeco:phase").Set("proposed")
                UsdGeom.Xformable(camera).AddTranslateOp().Set(Gf.Vec3d((i % 8)*9+1, (i//8)*9+1, 2.8))
                sensor = UsdGeom.Camera.Define(stage, camera.GetPath().AppendChild("Sensor_0")).GetPrim()
                sensor.ApplyAPI("AecoCctvSensorAPI")
                for name, value in {"focalRange": Gf.Vec2d(3, 8.5), "hfovRange": Gf.Vec2d(104, 34),
                                    "vfovRange": Gf.Vec2d(76, 26), "pixels": Gf.Vec2i(2592, 1944),
                                    "range": 18., "tilt": 25., "pan": float((i % 4)*90)}.items():
                    sensor.GetAttribute("aeco:cctvSensor:" + name).Set(value)
                cameras.append(camera.GetPath())
        else:
            cameras = [c.GetPath() for c in generated]
        Usd.CollectionAPI(study, "cameras").GetIncludesRel().SetTargets(cameras)
    return study, dict(doors=len(doors), generatedCameras=len(generated), selectedCameras=len(cameras))


def detach(stage, path):
    paths = stage.GetSessionLayer().subLayerPaths
    if str(path) in paths:
        paths.remove(str(path))


def percentiles(values):
    import numpy as np
    return {"p50": float(np.percentile(values, 50)), "p95": float(np.percentile(values, 95))}


def semantic_report(report):
    return {k: report[k] for k in ("results", "unphasedInView", "unclassifiedInView", "exclusionsCovered")}


def assert_culling_parity(cropped, uncropped):
    """Compare every result and per-view sample/blocker, not just coverage totals."""
    from pxr import Sdf
    if semantic_report(cropped) != semantic_report(uncropped):
        raise AssertionError("culled and uncropped results or blockers differ")
    def views(report):
        layer = Sdf.Layer.FindOrOpen(report["layer"])
        return {key: {k: v for k, v in json.loads(value["cache"]).items() if k != "candidates"}
                for key, value in layer.customLayerData["aeco:cctv:views"].items()}
    a, b = views(cropped), views(uncropped)
    if a != b:
        raise AssertionError("culled and uncropped per-view samples, blockers or flags differ")
    return dict(targets=len(cropped["results"]), views=len(a),
                viewTargets=sum(len(v["targets"]) for v in a.values()),
                blockers=sum(len(r["blockers"]) for r in cropped["results"].values()))


def worker(stage_path, directory, configuration, trials, kernel):
    from usdaeco_cctv import register_plugins
    register_plugins()
    from pxr import Usd
    from usdaeco_cctv import study as engine
    from usdaeco_cctv.performance import STAGES
    stage = Usd.Stage.Open(str(stage_path))
    study, census = author_study(stage, configuration)
    output = directory / (configuration + ".usdc")
    measurements = {}
    unchanged_publications = 0
    reference = None
    for mode in BUDGETS:
        marker(configuration + " " + mode)
        rows = []
        for trial in range(trials):
            if mode != "hashOnly":
                detach(stage, output)
            if mode == "cold" and hasattr(engine, "clear_caches"):
                engine.clear_caches()
            before_output = (output.stat().st_mtime_ns, hashlib.sha256(output.read_bytes()).hexdigest()) if mode == "cached" else None
            start = perf_counter()
            if mode == "hashOnly":
                timings = {}
                hashed = engine.input_hash(stage, study, timings=timings)
                if hashed != reference["inputHash"]:
                    raise AssertionError("hash differs from study")
            else:
                report = engine.run_study(stage, study, output, kernel=kernel, recompute=mode == "cold")
                timings = report["timings"]
            wall = perf_counter() - start
            rows.append(dict(seconds=wall, timings=timings))
            if mode == "cached":
                after_output = (output.stat().st_mtime_ns, hashlib.sha256(output.read_bytes()).hexdigest())
                if before_output != after_output or report["outputChanged"]:
                    raise AssertionError("unchanged cached output was rewritten")
                unchanged_publications += 1
            if mode != "hashOnly":
                if reference and semantic_report(reference) != semantic_report(report):
                    raise AssertionError("trial results or blockers changed")
                reference = report
                if mode == "cached" and len(report["viewsReused"]) != report["views"]:
                    raise AssertionError("unchanged views were not reused")
            if (trial + 1) % 5 == 0:
                marker("%s %s %d/%d" % (configuration, mode, trial + 1, trials))
        measurements[mode] = dict(seconds=percentiles([r["seconds"] for r in rows]),
            stages={s: percentiles([r["timings"][s] for r in rows]) for s in STAGES}, trials=rows)
    marker(configuration + " uncropped correctness oracle")
    detach(stage, output)
    uncropped = engine.run_study(stage, study, directory / (configuration + "-uncropped.usdc"),
                                 kernel=kernel, recompute=True, cull=False)
    comparison = assert_culling_parity(reference, uncropped)
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    rss_bytes = int(rss if sys.platform == "darwin" else rss * 1024)
    eligible = trials >= 30 and census["doors"] == 41 and reference["triangles"] >= 86840
    if configuration == "fixed32":
        eligible = eligible and reference["views"] == census["selectedCameras"] == 32
    acceptance = {m: measurements[m]["seconds"]["p95"] < b for m, b in BUDGETS.items()}
    acceptance.update(peakRss=rss_bytes < 1024**3, identicalResultsAndBlockers=True, eligible=eligible)
    return dict(configuration=configuration, kernel=kernel, trials=trials, census=census,
                triangles=reference["triangles"], obstacles=reference["obstacles"], views=reference["views"],
                rays=reference["rays"], measurements=measurements, peakRssBytes=rss_bytes,
                budgetsSeconds=BUDGETS, acceptance=acceptance, comparison=comparison,
                publication=dict(format="usdc", writeShells=False, unchangedCachedRuns=unchanged_publications),
                versions={"cctv": engine.__version__, "usd": list(Usd.GetVersion()),
                          "python": platform.python_version(), "architecture": platform.machine(),
                          "numpy": importlib.metadata.version("numpy"),
                          "embreex": importlib.metadata.version("embreex") if kernel == "embree" else None})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=Path, help="converted stage with imported camera drivers")
    parser.add_argument("--generator", type=Path, default=os.environ.get("AECO_DATACENTRE_ROOT", ROOT.parent / "usdaeco-datacentre"))
    parser.add_argument("--work", type=Path, default=ROOT / "artifacts/facility")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/facility-benchmark.json")
    parser.add_argument("--trials", type=int, default=30)
    parser.add_argument("--kernel", choices=("embree", "numpy"), default="embree")
    parser.add_argument("--configuration", choices=("both", "fixed32", "generated"), default="both")
    parser.add_argument("--enforce", action="store_true")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.trials < 1:
        parser.error("trials must be positive")
    args.work = args.work.resolve()
    args.work.mkdir(parents=True, exist_ok=True)
    if args.worker:
        result = worker(args.stage, args.work, args.configuration, args.trials, args.kernel)
    else:
        stage, preparation = (args.stage.resolve(), {"suppliedStage": True}) if args.stage else build_stage(args.work, args.generator)
        configurations = ("fixed32", "generated") if args.configuration == "both" else (args.configuration,)
        reports = []
        for config in configurations:
            destination = args.work / (config + "-report.json")
            # Workers keep converter memory and the other configuration out of RSS.
            command = [sys.executable, str(Path(__file__).resolve()), "--worker", "--stage", str(stage),
                       "--configuration", config, "--work", str(args.work), "--output", str(destination),
                       "--trials", str(args.trials), "--kernel", args.kernel]
            environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
            environment.pop("PYTHONPATH", None)
            subprocess.run(command, env=environment, check=True)
            reports.append(json.loads(destination.read_text()))
        result = dict(facility="demo-datacentre-01", preparation=preparation, configurations=reports,
                      coldDefinition="loaded stage, all process input and BVH caches cleared; view reuse disabled",
                      cachedDefinition="same stage and prior validated layer, gathered inputs and all views reused; no output rewrite",
                      hashDefinition="same stage, fingerprint only, no rays or output publication")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    if args.enforce and not args.worker:
        fixed = [r for r in result["configurations"] if r["configuration"] == "fixed32"]
        return int(len(fixed) != 1 or not all(fixed[0]["acceptance"].values()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
