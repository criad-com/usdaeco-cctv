"""Compare the published property contract without changing structure rules."""
import json
from pathlib import Path


def schema_snapshot(root):
    from pxr import Sdf
    layer = Sdf.Layer.FindOrOpen(str(Path(root) / "usdAecoCctv/schema.usda"))
    return {p.name: {a.name: {"type": str(a.typeName) if isinstance(a, Sdf.AttributeSpec) else "relationship",
                             "default": str(a.default) if isinstance(a, Sdf.AttributeSpec) else None}
                     for a in p.properties} for p in layer.rootPrims if p.name != "GLOBAL"}


def unchanged(root):
    baseline = json.loads((Path(root) / "testenv/baseline/schema-v0.4.8.json").read_text())
    if schema_snapshot(root) != baseline:
        raise ValueError("CCTV property contract differs from v0.4.8")
    return baseline

