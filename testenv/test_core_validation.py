"""Core validation must execute even when plugin discovery succeeds first."""
import pytest
from pxr import Plug, Sdf, Usd, UsdGeom, UsdValidation

from usdaeco_cctv import validators
from usdaeco_cctv.derive import derive


def test_missing_core_python_module_fails_loudly(monkeypatch):
    def unavailable(name):
        assert name == "usdAecoValidators"
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(validators, "import_module", unavailable)
    with pytest.raises(RuntimeError, match="Cannot import usdAecoValidators.*PYTHONPATH"):
        validators.validate_stage(Usd.Stage.CreateInMemory(), include_core=True, include_builtin=False)


def test_fresh_derive_guides_pass_core_and_seeded_exact_mesh_fails(tmp_path):
    from build_example import build
    stage = build(tmp_path / "lobby.usda")
    layer = Sdf.Layer.CreateAnonymous("derived.usda")
    derive(stage, layer)
    guides = [p for p in stage.Traverse() if p.IsA(UsdGeom.Mesh) and layer.GetPrimAtPath(p.GetPath())]
    assert {p.GetName() for p in guides} >= {"Sector", "Shell_identify", "Envelope"}
    assert all(p.GetAttribute("aeco:derived:approx").Get() == "tessellated"
               and not p.GetAttribute("aeco:derived:tolerance").HasAuthoredValueOpinion() for p in guides)
    assert not [e for e in validators.validate_stage(stage, include_core=True, include_builtin=False)
                if e.GetType() != UsdValidation.ValidationErrorType.Info]
    assert Plug.Registry().GetPluginWithName("usdAecoValidators").isLoaded
    with Usd.EditContext(stage, layer):
        guides[0].GetAttribute("aeco:derived:approx").Set("exact")
    issues = validators.validate_stage(stage, include_core=True, include_builtin=False)
    assert {e.GetName() for e in issues} >= {"DerivedExactOnMesh", "ExactWithoutTolerance"}
