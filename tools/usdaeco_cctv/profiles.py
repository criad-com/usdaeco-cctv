"""Apply the core conformance profile JSON format to validation findings."""
import json
from pathlib import Path
from pxr import UsdValidation


def load_profile(profile):
    if profile is None:
        return {"include_builtin": True, "severity_overrides": {}}
    data = json.loads(Path(profile).read_text()) if isinstance(profile, (str, Path)) else dict(profile)
    if not isinstance(data.get("include_builtin", True), bool):
        raise ValueError("include_builtin must be a boolean")
    overrides = data.get("severity_overrides", {})
    if not isinstance(overrides, dict) or any(v not in ("error", "warn", "info") for v in overrides.values()):
        raise ValueError("severity_overrides must map rule names to error, warn or info")
    return data


def apply_profile(issues, profile):
    """Re-grade named rules, retaining their sites and messages; never add rules."""
    overrides = load_profile(profile).get("severity_overrides", {})
    levels = {"error": UsdValidation.ValidationErrorType.Error,
              "warn": UsdValidation.ValidationErrorType.Warn,
              "info": UsdValidation.ValidationErrorType.Info}
    return [UsdValidation.ValidationError(e.GetName(), levels[overrides[e.GetName()]],
                e.GetSites(), e.GetMessage()) if e.GetName() in overrides else e
            for e in issues]
