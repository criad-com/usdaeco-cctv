"""Dependency bounds are enforced by the same checker used before builds."""
import json

import pytest

from usdaeco_cctv import ROOT
from usdaeco_check.plugins import check_requirements, read_json


@pytest.mark.parametrize("version,accepted", [
    ("0.9.0", True), ("0.9.1", True), ("0.8.4", False), ("1.0.0", False),
])
@pytest.mark.parametrize("source", ["manifest", "plugin"])
def test_core_requirement_bounds(version, accepted, source):
    if source == "manifest":
        metadata = read_json(ROOT / "library.json")
    else:
        metadata = read_json(ROOT / "usdAecoCctv/plugInfo.json")["Plugins"][0]["Info"]["aeco"]
    closure = {"usdAecoCctv": metadata,
               "usdAeco": {"version": version, "tier": "core", "requires": {}}}
    if accepted:
        assert check_requirements(closure) == ["usdAeco", "usdAecoCctv"]
    else:
        with pytest.raises(ValueError, match="requires usdAeco .*found " + version):
            check_requirements(closure)


def test_packaging_requirement_and_source_pin_agree():
    pins = json.loads((ROOT / "dependencies.json").read_text())
    manifest = read_json(ROOT / "library.json")
    assert manifest["requires"] == {"usdAeco": ">=0.9,<1.0"}
    from usdaeco_check.contracts import pins as validate_pins
    validate_pins(pins, manifest["requires"])
