#!/pxrpythonsubst
import unittest
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tools"), str(ROOT)]
from usdaeco_cctv import register_plugins, validators
register_plugins()
from pxr import Plug, Usd, UsdValidation
Plug.Registry().RegisterPlugins(str(ROOT / "usdAecoCctvValidators"))
validators.register()

class TestValidators(unittest.TestCase):
    def compare(self, name):
        stage = Usd.Stage.Open(str(ROOT / "examples/lobby.usda"))
        rule = next(row for row in validators._RULES if row[0] == name)
        qualified = "usdAecoCctvValidators:" + name[0].upper() + name[1:] + "Checker"
        wrapped = UsdValidation.ValidationRegistry().GetOrLoadValidatorByName(qualified)
        self.assertTrue(wrapped)
        expected = rule[1](stage, None) if rule[2] else [e for p in stage.Traverse() for e in rule[1](p, None)]
        actual = UsdValidation.ValidationContext([wrapped]).Validate(stage)
        def normalized(errors):
            return sorted((e.GetName(), str(e.GetType()), e.GetMessage(), tuple(str(s.GetPrim().GetPath()) for s in e.GetSites())) for e in errors)
        self.assertEqual(normalized(actual), normalized(expected))

    def test_CctvInvalidSampling(self):
        self.compare('cctvInvalidSampling')

    def test_CctvMissingOptics(self):
        self.compare('cctvMissingOptics')

    def test_CctvUnsupportedTopology(self):
        self.compare('cctvUnsupportedTopology')

    def test_CctvKindMismatch(self):
        self.compare('cctvKindMismatch')

    def test_CctvSensorMissing(self):
        self.compare('cctvSensorMissing')

    def test_CctvSensorOrphan(self):
        self.compare('cctvSensorOrphan')

    def test_CctvOutOfEnvelope(self):
        self.compare('cctvOutOfEnvelope')

    def test_CctvNativeCameraAuthored(self):
        self.compare('cctvNativeCameraAuthored')

    def test_CctvDerivedMismatch(self):
        self.compare('cctvDerivedMismatch')

    def test_CctvMountFrame(self):
        self.compare('cctvMountFrame')

    def test_CctvSystemCapacity(self):
        self.compare('cctvSystemCapacity')

    def test_CctvUnsupportedProjection(self):
        self.compare('cctvUnsupportedProjection')

    def test_CctvStudyIncomplete(self):
        self.compare('cctvStudyIncomplete')

    def test_CctvUnphasedProvider(self):
        self.compare('cctvUnphasedProvider')

    def test_CctvStudyMissingResults(self):
        self.compare('cctvStudyMissingResults')

    def test_CctvStudyStale(self):
        self.compare('cctvStudyStale')

    def test_CctvTargetUncovered(self):
        self.compare('cctvTargetUncovered')

    def test_CctvTargetMostlyEnclosed(self):
        self.compare('cctvTargetMostlyEnclosed')

    def test_CctvPtzSoleCoverage(self):
        self.compare('cctvPtzSoleCoverage')

    def test_CctvTargetTooFar(self):
        self.compare('cctvTargetTooFar')

    def test_CctvExclusionCovered(self):
        self.compare('cctvExclusionCovered')

    def test_CctvUnphasedInView(self):
        self.compare('cctvUnphasedInView')

    def test_CctvUnclassifiedInView(self):
        self.compare('cctvUnclassifiedInView')

if __name__ == "__main__":
    unittest.main()
