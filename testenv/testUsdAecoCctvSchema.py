#!/pxrpythonsubst
import unittest
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT)]
from usdaeco_cctv import register_plugins
register_plugins()
from pxr import Plug
Plug.Registry().RegisterPlugins(str(ROOT / "usdAecoCctvValidators"))
from usdaeco_cctv.schema_contract import unchanged

class TestSchema(unittest.TestCase):
    def test_baked_meshes_declare_tessellation(self):
        from pxr import Usd, UsdGeom
        stage = Usd.Stage.Open(str(ROOT / "examples/lobby.derived.usda"))
        guides = [p for p in stage.Traverse() if p.IsA(UsdGeom.Mesh) and p.HasAPI("AecoDerivedGeometryAPI")
                  and p.GetAttribute("aeco:derived:role").Get() in ("sector", "coverage")]
        self.assertTrue(guides)
        self.assertTrue(all(p.GetAttribute("aeco:derived:approx").Get() == "tessellated" for p in guides))
        self.assertTrue(all(not p.GetAttribute("aeco:derived:tolerance").HasAuthoredValueOpinion() for p in guides))

    def test_released_properties_unchanged(self):
        baseline = unchanged(ROOT)
        self.assertEqual(len(baseline), 9)
        self.assertEqual(sum(len(p) for p in baseline.values()), 71)

if __name__ == "__main__":
    unittest.main()
