#!/usr/bin/env python3
"""Run the unmodified upstream structure lint."""
import os
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tools"), str(Path(os.environ.get("TOOLCHAIN_DIR", ROOT.parent / "usdaeco-toolchain")) / "tools")]
from usdaeco_cctv import core_root, register_plugins
register_plugins()
from usdaeco_check.structure import check_structure
from usdaeco_check.structure import print_results
raise SystemExit(print_results(list(check_structure(ROOT, deps=[os.environ.get("CORE_PLUGIN_DIR", core_root() / "out/plugins/usdAeco/resources")]))))
