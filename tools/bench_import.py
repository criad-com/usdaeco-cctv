#!/usr/bin/env python3
"""Benchmark fresh-process IFC imports and compare with the pre-optimization importer."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from time import perf_counter
import types

ROOT = Path(__file__).resolve().parents[1]
# Instrumentation only, with the 0.4.6 import and matching algorithms intact.
REFERENCE_REF = "b35622a9c18f69a2f22d3d54e7075f21caf4e4da"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_reference(ref=REFERENCE_REF):
    """Run trusted repository history against the same installed dependencies.

    The library release stamp comes from the current package, as it does for
    the optimized importer; every other output byte must match without edits.
    """
    try:
        result = subprocess.run(["git", "show", ref + ":tools/usdaeco_cctv/importer.py"],
                                cwd=ROOT, capture_output=True, text=True)
    except OSError as exc:
        raise ValueError("reference importer is unavailable; git and repository history are required") from exc
    if result.returncode:
        raise ValueError("reference importer is unavailable; fetch repository history")
    name = "usdaeco_cctv._reference_importer"
    module = types.ModuleType(name)
    module.__package__ = "usdaeco_cctv"
    sys.modules[name] = module
    exec(compile(result.stdout, "<reference-importer>", "exec"), module.__dict__)
    return module


def worker(args):
    from usdaeco_cctv import importer
    implementation = load_reference(args.reference_ref) if args.worker == "reference" else importer
    timings = {}
    start = perf_counter()
    counts = implementation.import_cctv(args.core, args.source, args.out, timings=timings)
    wall = perf_counter() - start
    return dict(counts=counts, wallSeconds=wall, timingsSeconds=timings,
                sha256=digest(args.out), bytes=args.out.stat().st_size)


def summary(runs):
    import numpy as np

    def percentiles(values):
        return dict(p50=float(np.percentile(values, 50)),
                    p95=float(np.percentile(values, 95)), maximum=max(values))

    return dict(wallSeconds=percentiles([r["wallSeconds"] for r in runs]),
                timingsSeconds={key: percentiles([r["timingsSeconds"][key] for r in runs])
                                for key in runs[0]["timingsSeconds"]})


def benchmark(args):
    from pxr import Usd
    from usdaeco_cctv import __version__, core_root, register_plugins
    register_plugins()
    stage = Usd.Stage.Open(str(args.core))
    if not stage or stage.GetCompositionErrors():
        raise ValueError("core stage must compose")
    inputs = [args.source, *(Path(layer.realPath) for layer in stage.GetUsedLayers() if layer.realPath)]
    before = {path: digest(path) for path in inputs}
    # Fail before trials if history is absent. No existing outputs are replaced.
    load_reference(args.reference_ref)
    args.out.mkdir(parents=True, exist_ok=False)
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    environment.pop("PYTHONPATH", None)
    runs = {"reference": [], "optimized": []}
    expected_hash = expected_counts = None
    equal = 0
    for trial in range(args.trials):
        # Alternate order to reduce a systematic page-cache/order advantage.
        order = ("reference", "optimized") if trial % 2 == 0 else ("optimized", "reference")
        for mode in order:
            print("== stage: import %s %d/%d" % (mode, trial + 1, args.trials), flush=True)
            destination = args.out / ("%s-%02d.usda" % (mode, trial))
            result = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                "--worker", mode, "--core", str(args.core), "--source", str(args.source),
                "--out", str(destination), "--reference-ref", args.reference_ref],
                env=environment, check=True, capture_output=True, text=True)
            run = json.loads(result.stdout)
            runs[mode].append(run)
            expected_hash = expected_hash or run["sha256"]
            expected_counts = expected_counts or run["counts"]
            if run["sha256"] != expected_hash or run["counts"] != expected_counts:
                raise ValueError("import output or counters differ from the reference")
        equal += 1
    if before != {path: digest(path) for path in inputs}:
        raise ValueError("import changed a source input")
    core_manifest = json.loads((core_root() / "library.json").read_text())
    report = dict(libraryVersion=__version__, referenceRevision=args.reference_ref,
        referenceStampPolicy="same current library version; no output normalization",
        sourceSha256=before[args.source], coreRootSha256=before[args.core],
        inputLayersUnchanged=len(inputs) - 1, sourceUnchanged=True,
        kindLayerSha256=expected_hash, counts=expected_counts,
        trialsPerMode=args.trials, equalTrials=equal, runs=runs,
        summary={mode: summary(values) for mode, values in runs.items()},
        methodology="fresh process per call; no warmup; alternating order; numpy linear percentiles",
        scope="complete import API wall time including teardown; excludes Python startup and core conversion",
        environment=dict(system=platform.system(), release=platform.release(), machine=platform.machine(),
                         python=platform.python_version(), coreVersion=core_manifest["version"],
                         packages={name: importlib.metadata.version(name)
                                   for name in ("usd-core", "ifcopenshell", "numpy")}))
    report["budgetSeconds"] = 3.
    report["budgetPassed"] = report["summary"]["optimized"]["wallSeconds"]["p95"] < 3.
    (args.out / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({key: report[key] for key in ("counts", "equalTrials", "summary", "budgetPassed")}, indent=2))
    return 1 if args.enforce and not report["budgetPassed"] else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core", required=True, type=Path)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path, help="new directory for trials and report")
    parser.add_argument("--trials", type=int, default=12)
    parser.add_argument("--reference-ref", default=REFERENCE_REF)
    parser.add_argument("--enforce", action="store_true")
    parser.add_argument("--worker", choices=("reference", "optimized"), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    args.core, args.source, args.out = (p.resolve() for p in (args.core, args.source, args.out))
    if args.worker:
        print(json.dumps(worker(args), sort_keys=True))
        return 0
    if args.trials < 10:
        parser.error("at least 10 trials are required")
    return benchmark(args)


if __name__ == "__main__":
    raise SystemExit(main())
