"""Severity overlays preserve findings and reject malformed profiles."""
from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from pxr import UsdValidation
from usdaeco_cctv.profiles import apply_profile, load_profile


def test_severity_overlay_preserves_evidence():
    issue = UsdValidation.ValidationError("seed", UsdValidation.ValidationErrorType.Warn, [], "evidence")
    result = apply_profile([issue], {"severity_overrides": {"seed": "error"}})
    assert len(result) == 1 and result[0].GetType() == UsdValidation.ValidationErrorType.Error
    assert result[0].GetName() == "seed" and result[0].GetMessage() == "evidence"
    assert issue.GetType() == UsdValidation.ValidationErrorType.Warn
    assert apply_profile([issue], {"severity_overrides": {"unrelated": "error"}})[0] == issue


@pytest.mark.parametrize("bad", [{"severity_overrides": {"seed": "fatal"}}, {"include_builtin": "false"}, {"severity_overrides": []}])
def test_bad_profile_fails_closed(bad):
    with pytest.raises(ValueError):
        load_profile(bad)


def test_validator_accepts_profile(tmp_path):
    from usdaeco_cctv import register_plugins, validators
    from pxr import Usd
    register_plugins()
    stage = Usd.Stage.CreateInMemory()
    prim = stage.DefinePrim("/Element", "Xform")
    prim.ApplyAPI("AecoCctvCameraAPI")
    path = tmp_path / "exchange.json"
    path.write_text('{"include_builtin": false, "severity_overrides": {"cctvSensorMissing": "info"}}')
    issues = validators.validate_stage(stage, profile=path)
    matching = [e for e in issues if e.GetName() == "cctvSensorMissing"]
    assert matching and all(e.GetType() == UsdValidation.ValidationErrorType.Info for e in matching)
    assert matching[0].GetSites()[0].GetPrim() == prim
