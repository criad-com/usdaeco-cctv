from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from usdaeco_cctv import register_plugins, core_root
register_plugins()
sys.path.insert(0,str(core_root()/'tools'))
