"""Validate isolated derived layers and publish with a same-directory rename."""
import os
from pathlib import Path
import tempfile

from pxr import Sdf, Usd


def refuse_input(stage, destination):
    layer = destination if isinstance(destination, Sdf.Layer) else None
    path = (layer.realPath if layer else str(destination)) or None
    resolved = Path(path).resolve() if path else None
    for source in stage.GetLayerStack():
        if source == layer or (resolved and source.realPath and
                               Path(source.realPath).resolve() == resolved):
            raise ValueError("output must not overwrite an input layer: " + source.GetDisplayName())


def isolated_stage(stage):
    session = Sdf.Layer.CreateAnonymous("cctv-session.usda")
    session.TransferContent(stage.GetSessionLayer())
    result = Usd.Stage.Open(stage.GetRootLayer(), session)
    for identifier in stage.GetMutedLayers():
        result.MuteLayer(identifier)
    return result


def validate_layer(layer):
    probe = Sdf.Layer.CreateAnonymous("cctv-validation.usda")
    if not probe.ImportFromString(layer.ExportToString()):
        raise ValueError("derived output does not parse")


def publish(layer, output, *, format=None):
    """The previous file and registered layer stay unchanged until validation succeeds."""
    output = Path(output).resolve()
    # Export once, then reopen and validate the exact bytes to be published.
    output.parent.mkdir(parents=True, exist_ok=True)
    if format not in (None, "usda", "usdc"):
        raise ValueError("format must be usda or usdc")
    fd, name = tempfile.mkstemp(prefix=".cctv-", suffix=".usd" if format else output.suffix, dir=output.parent)
    os.close(fd)
    try:
        if not layer.Export(name, args={"format": format} if format else {}):
            raise RuntimeError("failed to export derived output")
        probe = Sdf.Layer.FindOrOpen(name)
        composed = Usd.Stage.Open(probe)
        if not composed or composed.GetCompositionErrors():
            raise ValueError("derived output does not compose")
        os.replace(name, output)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    existing = Sdf.Layer.Find(str(output))
    if existing:
        existing.Reload(True)
    return Sdf.Layer.FindOrOpen(str(output))
