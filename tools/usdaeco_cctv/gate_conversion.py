"""Run the IFC converter in isolation so IFC geometry loads before pxr.

The IFC converter handles IFC4 spatial dispatch and blank-property headings natively.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time


def convert(source, destination, threads=4):
    root = Path(__file__).resolve().parents[2]
    core = Path(os.environ.get("AECO_CORE_ROOT", os.environ.get("CORE_DIR", root.parent / "usdaeco-core")))
    sys.path.insert(0, str(Path(os.environ.get("AECO_IFC_ROOT", core.parent / "usdaeco-ifc")) / "tools"))
    sys.path.insert(0, str(Path(os.environ.get("TOOLCHAIN_DIR", core.parent / "usdaeco-toolchain")) / "tools"))
    import ifcopenshell
    from usdaeco_ifc.convert.geometry import extract_geometry
    started = time.perf_counter()
    model = ifcopenshell.open(str(source))
    geometry = extract_geometry(model, threads=threads)
    # Import USD only after tessellation finishes.
    from usdaeco_ifc.convert import author
    stats = author.author(model, geometry, str(destination))
    stats.pop("out", None)
    stats.update(ifc4FacilityAdapted=0,
                 seconds=round(time.perf_counter() - started, 3))
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()
    if args.destination.exists():
        parser.error("destination must be new")
    print(json.dumps(convert(args.source, args.destination, args.threads), sort_keys=True))
