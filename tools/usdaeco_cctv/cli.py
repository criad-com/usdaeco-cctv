"""aeco-cctv: command-line access to the derivation (Tier A) and the coverage study (Tier B)."""
import argparse
import json
from pathlib import Path
import sys


def _main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "import":
        from .importer import main as import_main
        return import_main(argv[1:])
    parser = argparse.ArgumentParser(prog="aeco-cctv", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("import", help="promote IFC, COBie or published USD camera properties")
    derive = sub.add_parser("derive", help="write Tier A (camera attributes, sectors, shells, tours)")
    derive.add_argument("stage", type=Path)
    derive.add_argument("-o", "--output", type=Path,
                        help="derived layer; default <stage directory>/analysis/cctv.derived.usda")
    derive.add_argument("--model", choices=("plane", "arc"),
                        help="density model for target ranges; default plane; --study selects study settings")
    derive.add_argument("--study", help="explicit study path supplying density settings and camera scope")
    derive.add_argument("--bare", action="store_true",
                        help="do not sublayer the stage into the output (for stacks that compose it)")
    study = sub.add_parser("study", help="run a coverage study (Tier B) and write its analysis layer")
    study.add_argument("stage", type=Path)
    study.add_argument("studyPath", help="path of the Scope wearing AecoCctvStudyAPI")
    study.add_argument("-o", "--output", type=Path,
                       help="analysis layer; default <stage directory>/analysis/cctv.<study name>.usda")
    study.add_argument("--kernel", choices=("auto", "embree", "numpy"), default="auto",
                       help="ray-cast kernel; auto = Embree when embreex imports, else numpy")
    study.add_argument("--recompute", action="store_true",
                       help="ignore the per-view cache of an existing layer and recompute every view")
    args = parser.parse_args(argv)
    from . import register_plugins
    register_plugins()
    if args.command == "derive":
        from .derive import derive_file
        output = args.output or args.stage.resolve().parent / "analysis" / "cctv.derived.usda"
        stats = derive_file(args.stage, output, args.model, args.bare, study_path=args.study)
        print(json.dumps(stats, sort_keys=True))
        return 1 if stats["skipped"] else 0
    if args.command == "study":
        from pxr import Usd
        from .study import run_study
        stage = Usd.Stage.Open(str(args.stage))
        if not stage:
            parser.error("cannot open " + str(args.stage))
        report = run_study(stage, args.studyPath, args.output, kernel=args.kernel, recompute=args.recompute)
        print(json.dumps(report, sort_keys=True))
        return 0
    return 2


def main(argv=None):
    try:
        return _main(argv)
    except (ValueError, RuntimeError, OSError) as exc:
        print("aeco-cctv: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
