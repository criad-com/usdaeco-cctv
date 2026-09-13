"""Optional queries and registration for the codeless usdAecoCctv library."""
import json
import os
from pathlib import Path
import sys

__version__ = "0.5.7"
ROOT = Path(__file__).resolve().parents[2]
APIS = tuple("AecoCctv" + name + "API" for name in (
    "Camera", "CameraType", "Sensor", "Preset", "Study", "Coverage", "Target", "Sightline", "System"))
ANGLE_TOLERANCE = 1e-6    # degrees; derived angles are compared against a recomputation
LENGTH_TOLERANCE = 1e-6   # metres, independent of the stage's geometry units
DENSITY_TOLERANCE = 1e-6  # px/m


def core_root():
    return Path(os.environ.get("AECO_CORE_ROOT", os.environ.get(
        "CORE_DIR", ROOT.parent / "usdaeco-core")))


def registry(name):
    """Return the shipped registry <name>.json (density ladders, scenarios, classes)."""
    return json.loads((ROOT / "registries" / (name + ".json")).read_text())


def _register(paths):
    kit = Path(os.environ.get("TOOLCHAIN_DIR", ROOT.parent / "usdaeco-toolchain"))
    if str(kit / "tools") not in sys.path:
        sys.path.insert(0, str(kit / "tools"))
    from usdaeco_check import plugin_requires
    result = plugin_requires(paths)
    if not result:
        raise RuntimeError(result.detail)


def _manifest():
    manifest = json.loads((ROOT / "library.json").read_text())
    return {key: manifest[key] for key in ("version", "tier", "requires")}


def register_core():
    """Register core first and reject missing/incompatible dependency metadata."""
    from pxr import Plug
    _register([os.environ.get("CORE_PLUGIN_DIR", core_root() / "out/plugins/usdAeco/resources")])
    from usdaeco_check.plugins import check_requirements
    core = Plug.Registry().GetPluginWithName("usdAeco")
    if not core or core.metadata["aeco"]["tier"] != "core":
        raise RuntimeError("usdAeco with core tier metadata is required")
    try:
        check_requirements({"usdAeco": core.metadata["aeco"], "usdAecoCctv": _manifest()})
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    if "aecoDerived" not in core.metadata.get("SdfMetadata", {}):
        raise RuntimeError("usdAeco must register the aecoDerived property metadata")
    return core


def register_plugins(plugin_root=None, *, plugin_dir=None):
    """Load core then cctv before opening a stage or instantiating SchemaRegistry.

    The shared toolchain checks the full dependency closure and versions.
    plugin_root retains the legacy plugins/ layout; plugin_dir and
    CCTV_PLUGIN_DIR select a built resource directory directly.
    """
    from pxr import Plug, Usd
    register_core()
    if plugin_root is not None and plugin_dir is not None:
        raise ValueError("Supply plugin_root or plugin_dir, not both")
    path = plugin_dir or (Path(plugin_root) / "usdAecoCctv/resources" if plugin_root else
                         os.environ.get("CCTV_PLUGIN_DIR", ROOT / "usdAecoCctv"))
    _register([os.environ.get("CORE_PLUGIN_DIR", core_root() / "out/plugins/usdAeco/resources"), path])
    plugin = Plug.Registry().GetPluginWithName("usdAecoCctv")
    if not plugin or plugin.metadata.get("aeco") != _manifest():
        raise RuntimeError("Rebuild usdAecoCctv from its library.json manifest")
    if Usd.SchemaRegistry().FindAppliedAPIPrimDefinition("AecoCctvSensorAPI") is None:
        raise RuntimeError("Register cctv plugins before the first SchemaRegistry/stage; restart this process")
    return plugin


def iter_cameras(stage):
    """Active, defined camera occurrences; catalog classes are excluded."""
    return (p for p in stage.Traverse() if p.HasAPI("AecoCctvCameraAPI"))


def iter_studies(stage):
    """Study scopes in path order; callers select one explicitly."""
    return sorted((p for p in stage.Traverse() if p.HasAPI("AecoCctvStudyAPI")),
                  key=lambda p: str(p.GetPath()))


def sensors_of(camera):
    """Camera-typed sensor children, including inherited catalog children."""
    from pxr import UsdGeom
    if not camera:
        return []
    return [p for p in camera.GetAllChildren()
            if p.IsActive() and p.IsA(UsdGeom.Camera) and p.HasAPI("AecoCctvSensorAPI")]


def camera_type_of(prim):
    """First catalog class carrying the type API, in inherit strength order."""
    if not prim:
        return None
    if prim.IsAbstract() and prim.HasAPI("AecoCctvCameraTypeAPI"):
        return prim
    for path in prim.GetInherits().GetAllDirectInherits():
        candidate = prim.GetStage().GetPrimAtPath(path)
        if candidate and candidate.IsAbstract() and candidate.HasAPI("AecoCctvCameraTypeAPI"):
            return candidate
    return None


def presets_of(sensor):
    """Ordered mapping of preset instance names to resolved driver values."""
    prefix = "AecoCctvPresetAPI:"
    return {api[len(prefix):]: {name: sensor.GetAttribute(
        "aeco:cctvPreset:" + api[len(prefix):] + ":" + name).Get()
        for name in ("pan", "tilt", "focalLength", "dwell", "home")}
        for api in sensor.GetAppliedSchemas() if api.startswith(prefix)}
