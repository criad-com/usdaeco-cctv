"""Analysis paths persisted with the data, with a legacy standalone default."""
import os

from pxr import Sdf, UsdGeom

STUDY_ROOT_KEY = "aeco:cctv:studyRoot"
_LEGACY = {"Looks": "AecoCctvLooks", "Targets": "SecurityTargets", "Studies": "SecurityStudies"}


def study_root(stage):
    """Prefer the composed layer stack's receipt over the authoring setting."""
    value = next((layer.customLayerData[STUDY_ROOT_KEY] for layer in stage.GetLayerStack()
                  if STUDY_ROOT_KEY in layer.customLayerData), os.environ.get("AECO_STUDY_ROOT", "/"))
    if not isinstance(value, str) or not Sdf.Path.IsValidPathString(value):
        raise ValueError("AECO_STUDY_ROOT must be an absolute prim path or /")
    path = Sdf.Path(value)
    if not path.IsAbsolutePath() or not path.IsAbsoluteRootOrPrimPath() or path.ContainsPrimVariantSelection():
        raise ValueError("AECO_STUDY_ROOT must be an absolute prim path or /")
    return path


def study_scope(root, name):
    """Keep the published standalone paths; nested studies use short names."""
    return root.AppendChild(_LEGACY[name] if root == Sdf.Path.absoluteRootPath else name)


def author_study_root(stage, root):
    """Record non-default roots and make each ancestor a plain Scope."""
    if root == Sdf.Path.absoluteRootPath:
        return
    for path in root.GetPrefixes():
        prim = stage.GetPrimAtPath(path)
        if prim and prim.GetTypeName() not in ("", "Scope"):
            raise ValueError("study root ancestor must be a Scope: " + str(path))
    for path in root.GetPrefixes():
        UsdGeom.Scope.Define(stage, path)
    layer = stage.GetEditTarget().GetLayer()
    layer.customLayerData = {**layer.customLayerData, STUDY_ROOT_KEY: str(root)}


def render_camera(stage, name):
    """Resolve suite cameras first, then the published standalone input."""
    for root in (Sdf.Path("/Renders/cctv"), Sdf.Path("/Renders")):
        prim = stage.GetPrimAtPath(root.AppendChild(name))
        if prim and prim.IsA(UsdGeom.Camera):
            return UsdGeom.Camera(prim)
    raise ValueError("missing CCTV render camera: " + name)
