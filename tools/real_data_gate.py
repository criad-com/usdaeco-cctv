#!/usr/bin/env python3
"""Opt-in real-data gate; publishes counts only, never model data.

AECO_REAL_DATA_ROOT must contain security.ifc, architecture.ifc, mapping.txt and
cobie.xlsx. --fixture exercises the same pipeline with synthetic inputs.
All intermediate models stay in a private temporary directory (or --work).
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
import os
# Honour the original private gate's opt-in variable as a compatibility alias.
for suffix in ("ROOT", "WORK"):
    legacy = "AECO_" + "PA" + "R02_" + suffix
    if legacy in os.environ:
        os.environ.setdefault("AECO_REAL_DATA_" + suffix, os.environ[legacy])

from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
REQUIRED = ("security.ifc", "architecture.ifc", "mapping.txt", "cobie.xlsx")


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def convert(source, destination):
    """Run the core converter adapter isolated from this process's USD."""
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    log = destination.with_suffix(".conversion.json")
    if destination.exists() and log.exists():
        previous = json.loads(log.read_text())
        if previous["sourceHash"] != digest(source):
            raise ValueError("Cached conversion does not match its input")
        return previous["counts"]
    result = subprocess.run([sys.executable, str(ROOT / "tools/usdaeco_cctv/gate_conversion.py"),
                             str(source), str(destination)], env=env, text=True, capture_output=True)
    if result.returncode:
        # Full converter diagnostics remain in private work, not the baseline.
        destination.with_suffix(".conversion.log").write_text(result.stdout + result.stderr)
        raise RuntimeError("Reference IFC conversion failed; see private conversion log")
    counts = json.loads(result.stdout.strip().splitlines()[-1])
    log.write_text(json.dumps({"sourceHash": digest(source), "counts": counts}, indent=2) + "\n")
    return counts


def _name(prim):
    from usdaeco_cctv.importer import _norm
    value = prim.GetAttribute("aeco:class:ifc:name").Get() or prim.GetName()
    return _norm(value)


def federate(security, architecture, output):
    """Align shared level namespaces by name, preserving world placement.

    Core converter elements already have world matrices beneath identity
    spatial containers. Moving a level therefore requires no coordinate
    adjustment. Relationships and connections are repathed explicitly.
    The aligned architecture is a sublayer beside the security stage.
    """
    from pxr import Sdf, Usd, UsdGeom
    sec, arch = Usd.Stage.Open(str(security)), Usd.Stage.Open(str(architecture))
    security_levels = [p for p in sec.Traverse() if p.GetTypeName() == "AecoLevel"]
    levels = {_name(p): p.GetPath() for p in security_levels}
    if not levels:
        raise ValueError("Security conversion has no levels")
    if len(levels) != len(security_levels):
        raise ValueError("Security has ambiguous duplicate level names")
    arch_levels = [p for p in arch.Traverse() if p.GetTypeName() == "AecoLevel"]
    if len({_name(p) for p in arch_levels}) != len(arch_levels):
        raise ValueError("Architecture has ambiguous duplicate level names")
    root_from, root_to = arch.GetDefaultPrim().GetPath(), sec.GetDefaultPrim().GetPath()
    moves = [(p.GetPath(), levels[_name(p)]) for p in arch_levels if _name(p) in levels]
    flattened = arch.Flatten(False)
    # A distinct temporary root avoids name collisions while levels move.
    temp_root = Sdf.Path("/ArchitectureSource")
    edit = Sdf.BatchNamespaceEdit()
    edit.Add(root_from, temp_root)
    if not flattened.Apply(edit):
        raise ValueError("Cannot prepare architecture namespace")
    mapping = []
    for old, new in moves:
        current = old.ReplacePrefix(root_from, temp_root)
        Sdf.CreatePrimInLayer(flattened, new.GetParentPath())
        edit = Sdf.BatchNamespaceEdit()
        edit.Add(current, new)
        if not flattened.Apply(edit):
            raise ValueError("Cannot align an architecture level")
        mapping.append((old, new))
    # Shared levels may leave empty copies of their site/facility ancestors.
    # Remove those empty containers instead of retaining a second identity.
    view = Usd.Stage.Open(flattened)
    ancestors = sorted((p.GetPath() for p in view.TraverseAll()
                        if p.GetPath().HasPrefix(temp_root) and p.GetTypeName().startswith("Aeco")
                        and p.GetTypeName() in ("AecoSite", "AecoFacility", "AecoFacilityPart")),
                       key=lambda p: len(str(p)), reverse=True)
    for path in ancestors:
        prim = view.GetPrimAtPath(path)
        if prim and not prim.GetAllChildren():
            view.RemovePrim(path)
    # Keep unmatched levels, uncontained geometry, catalogs and systems in a
    # distinct branch. This also avoids catalog-name collisions across IFCs.
    remainder = root_to.AppendChild("Architecture")
    Sdf.CreatePrimInLayer(flattened, root_to)
    edit = Sdf.BatchNamespaceEdit()
    edit.Add(temp_root, remainder)
    if not flattened.Apply(edit):
        raise ValueError("Cannot retain the architecture remainder")
    remainder_spec = flattened.GetPrimAtPath(remainder)
    remainder_spec.ClearInfo("apiSchemas")
    for name in list(remainder_spec.properties.keys()):
        if name.startswith("aeco:project:"):
            remainder_spec.RemoveProperty(remainder_spec.properties[name])
    mapping.append((root_from, remainder))
    mapping.sort(key=lambda pair: len(str(pair[0])), reverse=True)

    def repath(path):
        for old, new in mapping:
            if path.HasPrefix(old):
                return path.ReplacePrefix(old, new)
        return path

    aligned = Usd.Stage.Open(flattened)
    aligned.SetDefaultPrim(aligned.GetPrimAtPath(root_to))
    def repair(path):
        if not path.IsPropertyPath():
            return
        spec = flattened.GetObjectAtPath(path)
        if isinstance(spec, Sdf.RelationshipSpec):
            targets = spec.targetPathList.GetAppliedItems()
            if targets:
                spec.targetPathList.explicitItems = [repath(p) for p in targets]
        elif isinstance(spec, Sdf.AttributeSpec):
            targets = spec.connectionPathList.GetAppliedItems()
            if targets:
                spec.connectionPathList.explicitItems = [repath(p) for p in targets]
    flattened.Traverse(Sdf.Path.absoluteRootPath, repair)
    aligned_path = output.parent / "architecture.aligned.usdc"
    flattened.Export(str(aligned_path))
    root = Sdf.Layer.CreateNew(str(output))
    root.subLayerPaths = [os.path.relpath(security, output.parent), aligned_path.name]
    for key in ("defaultPrim", "upAxis", "metersPerUnit", "fallbackPrimTypes"):
        root.pseudoRoot.SetInfo(key, sec.GetMetadata(key))
    # Union typed fallbacks so architecture-only types compose in vanilla USD.
    fallbacks = dict(arch.GetMetadata("fallbackPrimTypes") or {})
    fallbacks.update(dict(sec.GetMetadata("fallbackPrimTypes") or {}))
    root.pseudoRoot.SetInfo("fallbackPrimTypes", fallbacks)
    root.Save()
    return len(moves)


def sidecar(path):
    if not path.exists():
        return {}
    source = json.loads(path.read_text())
    return {key: (value["fov"] if isinstance(value, dict) else value) for key, value in source.items()}


def enrich_scenarios(stage, workbook):
    """Supplement an IFC export's absent Scenario with the COBie GUID join."""
    from usdaeco_cctv import iter_cameras, registry
    from usdaeco_cctv.importer import Values, read_cobie, _norm
    cameras = {p.GetAttribute("aeco:id").Get(): p for p in iter_cameras(stage)}
    count = 0
    rows = read_cobie(workbook)
    for row in rows:
        prim = cameras.get(row.uid)
        value = Values(row.facts).pick("Scenario")
        if prim is None or value is None or prim.GetAttribute("aeco:cctv:scenario").Get():
            continue
        scenario = str(value).lower()
        if scenario in {s.lower() for s in registry("scenarios")}:
            prim.GetAttribute("aeco:cctv:scenario").Set(scenario)
            for attr in prim.GetAttributes():
                if attr.GetName().startswith("aeco:props:") and _norm(attr.GetBaseName()) == "scenario":
                    attr.Block()
            count += 1
    return rows, count


def _range_summary(errors, missing=0):
    return dict(count=len(errors), missing=missing, min=min(errors, default=0.), max=max(errors, default=0.))


def parity(stage, ifc_rows, cobie_rows, mapping):
    """Compare independently derived optics with host half-angles and radii.

    The workbook may omit shared FOV instances; exported IFC FOV properties
    then supply the host observations. Both populations are reported. The
    host's 26/62 detect/observe formulas and standard 25/62.5 ladder are
    reported separately, without adjusting the standard registry.
    """
    from usdaeco_cctv import iter_cameras, sensors_of, presets_of
    from usdaeco_cctv.importer import Values, identity, _norm, _number
    from usdaeco_cctv.density import arc_range, optics
    from usdaeco_cctv.derive import Reader, drivers_of
    by_ifc = {r.uid: r for r in ifc_rows}
    by_cobie = {r.uid: r for r in cobie_rows}
    reader = Reader(stage)
    angles, radii, standard = [], [], []
    missing_angles = missing_radii = cobie_values = fallback_values = 0
    mismatched_heads = 0
    for camera in iter_cameras(stage):
        owner = camera.GetAttribute("aeco:id").Get()
        heads = sensors_of(camera)
        source_ids = mapping.get(owner, [])
        if not source_ids:
            # Direct camera Attribute values work without a shared-instance map.
            source_ids = [owner]
        for i, source_id in enumerate(source_ids):
            uid = identity(source_id)
            cr = by_cobie.get(uid)
            ir = by_ifc.get(uid)
            host = Values((cr.facts if cr else []) + (ir.facts if ir else []))
            half = host.pick("Horizontal Angle", convert=_number)
            if half is None:
                missing_angles += 1
                missing_radii += 5
                continue
            if cr and Values(cr.facts).pick("Horizontal Angle") is not None:
                cobie_values += 1
            else:
                fallback_values += 1
            head = heads[min(i, len(heads) - 1)]
            d = drivers_of(head, reader)
            ps = list(presets_of(head).values())
            focal = ps[min(i, len(ps) - 1)]["focalLength"] if ps else d["focalLength"]
            o = optics(focal, d["focalRange"], d["hfovRange"], d["vfovRange"], d["pixels"], d["sensorSize"])
            angles.append(abs(math.radians(o["hfov"] / 2 - half)))
            far = host.pick("FOV Distance to Object", convert="length")
            far = far if far is not None else d["range"]
            density = host.pick("T_Res_UD", "FOV Target Pixel Density", convert=_number) or d["targetDensity"]
            for band, rho, conventional in (("Det", 26., 25.), ("Obs", 62., 62.5),
                                             ("Rec", 125., 125.), ("Id", 250., 250.), ("UD", density, density)):
                value = host.pick("RG_Length_" + band, convert="length")
                if value is None or not rho:
                    missing_radii += 1
                    continue
                radius = arc_range(d["pixels"][0], o["hfov"], rho)
                standard_radius = arc_range(d["pixels"][0], o["hfov"], conventional)
                radii.append(abs(min(radius, far) - value) if far > 0 else abs(radius - value))
                standard.append(abs(min(standard_radius, far) - value) if far > 0 else abs(standard_radius - value))
    return dict(halfAngleRadians=_range_summary(angles, missing_angles),
                hostRadiusMetres=_range_summary(radii, missing_radii),
                standardRadiusMetres=_range_summary(standard, missing_radii),
                cobieAngles=cobie_values, ifcFallbackAngles=fallback_values,
                halfAngleFailures=sum(e > 1e-3 for e in angles),
                hostRadiusFailures=sum(e > .001 for e in radii),
                standardRadiusFailures=sum(e > .001 for e in standard))


def _study(stage, work, kernel):
    from pxr import Gf, Usd, Sdf
    from usdaeco_cctv import iter_cameras
    from usdaeco_cctv.importer import _level
    from usdaeco_cctv.study import run_study
    all_cameras = list(iter_cameras(stage))
    selected = [p for p in all_cameras if p.GetAttribute("aeco:cctv:scenario").Get() == "door"]
    shared_levels = {_level(p) for p in all_cameras}
    targets = [p for p in stage.Traverse() if (p.GetAttribute("aeco:class:ifc:code").Get() or "").split(".")[0] == "IfcDoor"
               and _level(p) in shared_levels]
    if not selected or not targets:
        raise ValueError("Door study requires classified door cameras and architectural doors on shared levels")
    definition = stage.DefinePrim(stage.GetDefaultPrim().GetPath().AppendPath("Analyses/DoorCoverage"), "Scope")
    definition.ApplyAPI("AecoCctvStudyAPI")
    for name, value in (("requiredDensity", 125.), ("densityModel", "arc"), ("ptzPolicy", "presetsNotSole"),
                        ("maxTargetDistance", 3.), ("mountHeightRange", Gf.Vec2d(2.3, 3.))):
        definition.GetAttribute("aeco:cctvStudy:" + name).Set(value)
    Usd.CollectionAPI(definition, "targets").GetIncludesRel().SetTargets([p.GetPath() for p in targets])
    # The current study schema has no camera collection. A separate study
    # input layer disables other camera APIs while retaining housing obstacles.
    selected_paths = {p.GetPath() for p in selected}
    for camera in all_cameras:
        if camera.GetPath() not in selected_paths:
            camera.RemoveAPI("AecoCctvCameraAPI")
    stage.GetRootLayer().Save()
    result = run_study(stage, definition.GetPath(), str(work / "door.analysis.usda"), kernel=kernel, recompute=True)
    levels = defaultdict(lambda: dict(cameras=0, targets=0, covered=0, uncovered=0,
                                     enclosedTargets=0, enclosedSamples=0, evaluatedSamples=0))
    for camera in selected:
        levels[_level(camera)]["cameras"] += 1
    for row in result["results"].values():
        target = stage.GetPrimAtPath(row["target"])
        counts = levels[_level(target)]
        counts["targets"] += 1
        counts["enclosedSamples"] += row["enclosedSamples"]
        counts["evaluatedSamples"] += row["evaluatedSamples"]
        if row["enclosedSamples"] and not row["evaluatedSamples"]:
            counts["enclosedTargets"] += 1
        else:
            counts["covered" if row["views"] else "uncovered"] += 1
    return dict(cameras=len(selected), targets=len(result["results"]), views=result["views"],
                triangles=result["triangles"], rays=result["rays"], seconds=result["seconds"],
                kernelEmbree=int(result["kernel"] == "embree"), skipped=len(result["skipped"]),
                levels=[levels[key] for key in sorted(levels, key=str)])


def run_gate(source, work, output, *, kernel="auto", fixture=False):
    from usdaeco_cctv import register_plugins, iter_cameras
    from usdaeco_cctv.importer import import_cctv, read_ifc, identity
    from usdaeco_cctv.derive import derive_file
    from pxr import Sdf, Usd
    register_plugins()
    source, work, output = map(Path, (source, work, output))
    if not fixture and (os.environ.get("CI") or not os.environ.get("AECO_REAL_DATA_ROOT")):
        raise ValueError("Real-data gate requires AECO_REAL_DATA_ROOT and never runs in CI")
    if any(not (source / name).is_file() for name in REQUIRED):
        raise ValueError("Gate inputs require security.ifc, architecture.ifc, mapping.txt and cobie.xlsx")
    work.mkdir(parents=True, exist_ok=True)
    inputs = [source / n for n in REQUIRED]
    if (source / "subinstances.json").exists():
        inputs.append(source / "subinstances.json")
    if output.resolve() in {p.resolve() for p in inputs}:
        raise ValueError("Summary output must be distinct from every input")
    before = {p: digest(p) for p in inputs}
    print("STAGE gate conversion", flush=True)
    conversion = {name: convert(source / (name + ".ifc"), work / (name + ".usda"))
                  for name in ("security", "architecture")}
    conversion_work = work
    # Each attempt keeps its own reviewable derived artifacts; only verified
    # conversions are cached. Failed/previous kind layers are never overwritten.
    work = Path(tempfile.mkdtemp(prefix="run-", dir=work))
    print("STAGE gate federation", flush=True)
    shared = federate(conversion_work / "security.usda", conversion_work / "architecture.usda", work / "federated.usda")
    mapping = sidecar(source / "subinstances.json")
    print("STAGE gate promotion", flush=True)
    started = time.perf_counter()
    imported = import_cctv(work / "federated.usda", source / "security.ifc", work / "kind.usda", subinstances=mapping)
    promotion_seconds = time.perf_counter() - started
    layer = Sdf.Layer.CreateNew(str(work / "study-input.usda"))
    layer.subLayerPaths = ["kind.usda"]
    stage = Usd.Stage.Open(layer)
    base = Usd.Stage.Open(str(work / "kind.usda"))
    for key in ("defaultPrim", "upAxis", "metersPerUnit", "fallbackPrimTypes"):
        layer.pseudoRoot.SetInfo(key, base.GetMetadata(key))
    cobie_rows, enriched = enrich_scenarios(stage, source / "cobie.xlsx")
    layer.Save()
    print("STAGE gate derive", flush=True)
    started = time.perf_counter()
    derived = derive_file(work / "study-input.usda", work / "derived.usda", model="arc")
    derive_seconds = time.perf_counter() - started
    composed = Usd.Stage.Open(str(work / "derived.usda"))
    rows = read_ifc(source / "security.ifc")
    mapping = {identity(k): v for k, v in mapping.items()}
    print("STAGE gate parity", flush=True)
    comparison = parity(composed, rows, cobie_rows, mapping)
    (work / "parity.json").write_text(json.dumps(comparison, indent=2) + "\n")
    # The study owns a new edit layer above derivation, never its source.
    study_layer = Sdf.Layer.CreateNew(str(work / "door-study.usda"))
    study_layer.subLayerPaths = ["derived.usda"]
    for key in ("defaultPrim", "upAxis", "metersPerUnit", "fallbackPrimTypes"):
        study_layer.pseudoRoot.SetInfo(key, composed.GetMetadata(key))
    study_stage = Usd.Stage.Open(study_layer)
    print("STAGE gate door study", flush=True)
    study = _study(study_stage, work, kernel)
    summary = dict(**imported, sharedLevels=shared, scenariosSupplemented=enriched,
                   promotionSeconds=round(promotion_seconds, 3), deriveSeconds=round(derive_seconds, 3),
                   derivedSensors=derived["sensors"], derivedSkipped=len(derived["skipped"]),
                   conversion=conversion, parity=comparison, study=study,
                   inputsUnchanged=int(all(digest(p) == h for p, h in before.items())))
    summary["studyWithin60Seconds"] = int(study["seconds"] < 60)
    summary["hostParityWithinTolerance"] = int(comparison["halfAngleFailures"] == comparison["hostRadiusFailures"] == 0
                                               and comparison["halfAngleRadians"]["count"] > 0
                                               and comparison["hostRadiusMetres"]["count"] > 0)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", action="store_true")
    parser.add_argument("--work", type=Path, help="private work directory; completed conversions may be reused")
    parser.add_argument("-o", "--output", type=Path,
                        help="numeric summary; defaults to baselines/real-data-door-study.json (artifacts/ for a fixture)")
    parser.add_argument("--kernel", choices=("auto", "embree", "numpy"), default="auto")
    args = parser.parse_args(argv)
    if not args.fixture and (os.environ.get("CI") or not os.environ.get("AECO_REAL_DATA_ROOT")):
        parser.error("set AECO_REAL_DATA_ROOT to the private inputs; the real gate never runs in CI")
    with tempfile.TemporaryDirectory(prefix="aeco-cctv-gate-") as temp:
        if args.fixture:
            sys.path.insert(0, str(ROOT / "testenv"))
            from fixtures import build_baseline, write_cobie
            source = Path(temp) / "source"
            source.mkdir()
            build_baseline(source / "security.ifc", millimetres=True)
            build_baseline(source / "architecture.ifc", millimetres=True, architecture=True)
            write_cobie(source / "security.ifc", source / "cobie.xlsx", oracle=True)
            (source / "mapping.txt").write_text("Synthetic camera parameters\n")
        else:
            source = Path(os.environ["AECO_REAL_DATA_ROOT"])
        try:
            output = args.output or ROOT / ("artifacts/fixture-door-study.json" if args.fixture else "baselines/real-data-door-study.json")
            result = run_gate(source, args.work or Path(temp) / "work", output, kernel=args.kernel, fixture=args.fixture)
        except (ValueError, RuntimeError, ImportError) as exc:
            parser.exit(1, "gate: " + str(exc) + "\n")
        print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
