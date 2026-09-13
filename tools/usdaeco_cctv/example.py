"""Pinned data-centre import, derivation and independently selected studies."""
import datetime
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import numpy as np
from pxr import Gf, Sdf, Usd, UsdGeom, Vt
from . import ROOT, __version__, iter_cameras, sensors_of
from .derive import SHELL_COLOURS
from .paths import author_study_root, render_camera, study_root, study_scope
from .study import Settings, enumerate_views, run_study


def _id(prim):
    return prim.GetAttribute('aeco:props:DC_Identity:Id').Get() or ''


def _target(prim, points, density=0., spacing=0.):
    prim.ApplyAPI('AecoCctvTargetAPI')
    for key, value in {'points': Vt.Vec3fArray([Gf.Vec3f(*map(float, p)) for p in points]),
                       'requiredDensity': density, 'passFraction': 1., 'gridSpacing': spacing}.items():
        prim.GetAttribute('aeco:cctvTarget:' + key).Set(value)
    return prim.GetPath()


def _door_colours(mesh):
    """Accent the existing leaf and perimeter frame without changing geometry."""
    points = np.asarray(mesh.GetPointsAttr().Get())
    low, high = points.min(axis=0), points.max(axis=0)
    indices = np.asarray(mesh.GetFaceVertexIndicesAttr().Get())
    colours, start = [], 0
    for count in mesh.GetFaceVertexCountsAttr().Get():
        centre = points[indices[start:start + count]].mean(axis=0)
        frame = centre[0] < low[0] + .06 or centre[0] > high[0] - .06 or centre[2] > high[2] - .06
        colours.append(Gf.Vec3f(.8, .65, .28) if frame else Gf.Vec3f(.38, .60, .8))
        start += count
    mesh.CreateDisplayColorPrimvar('uniform').Set(colours)


def _split_derived(path):
    """Keep the example's authored camera layers below the 2 MB text cap."""
    stage = Usd.Stage.Open(str(path))
    layer = stage.GetRootLayer()
    paths = sorted(p.GetPath() for p in iter_cameras(stage) if layer.GetPrimAtPath(p.GetPath()))
    parts = []
    for offset in range(0, len(paths), 8):
        target = path.with_name(f'derived-cameras-{len(parts) + 1:02d}.usda')
        part = Sdf.Layer.CreateNew(str(target))
        part.customLayerData = layer.customLayerData
        edits = Sdf.BatchNamespaceEdit()
        for prim_path in paths[offset:offset + 8]:
            Sdf.CreatePrimInLayer(part, prim_path.GetParentPath())
            if not Sdf.CopySpec(layer, prim_path, part, prim_path):
                raise ValueError('cannot archive derived camera opinions')
            edits.Add(prim_path, Sdf.Path.emptyPath)
        part.Save()
        if not layer.Apply(edits):
            raise ValueError('cannot split derived camera opinions')
        parts.append(target.name)
    layer.subLayerPaths = parts + list(layer.subLayerPaths)
    layer.Save()


def hook(stage, out):
    root = study_root(stage)
    version = '0.5.3' if root == Sdf.Path.absoluteRootPath else __version__
    targets_path, studies_path = (study_scope(root, name) for name in ('Targets', 'Studies'))
    source = Sdf.Layer.CreateNew(str(out / 'source.usda'))
    source.TransferContent(stage.GetRootLayer())
    # The harness supplies inputs/source and rebases it when archiving (S29).
    source.Save()
    before = {p.realPath: hashlib.sha256(Path(p.realPath).read_bytes()).hexdigest()
              for p in stage.GetUsedLayers() if p.realPath}
    print('== stage: import published camera properties', flush=True)
    result = subprocess.run([sys.executable, str(ROOT / 'tools/aeco-cctv'), 'import', str(out / 'source.usda'),
                             '-o', str(out / 'kind.usda')], capture_output=True, text=True, check=True)
    imported = json.loads(result.stdout)
    imported.pop('timingsSeconds')
    (out / 'import-warnings.log').write_text(result.stderr)
    # The default layout retains its published receipt; suite runs use this release.
    kind = Sdf.Layer.FindOrOpen(str(out / 'kind.usda'))
    kind.customLayerData = {**kind.customLayerData, 'aeco:version': version}
    kind.Save()
    print('== stage: derive cameras and sectors', flush=True)
    derive_code = """import json, sys
sys.path.insert(0, sys.argv.pop(1))
from usdaeco_cctv import register_plugins
register_plugins()
from usdaeco_cctv.derive import derive_file
print(json.dumps(derive_file(sys.argv[1], sys.argv[2], stamp='usdAecoCctv derive ' + sys.argv[3])))
"""
    result = subprocess.run([sys.executable, '-c', derive_code, str(ROOT / 'tools'), str(out / 'kind.usda'),
                             str(out / 'derived.usda'), version], capture_output=True, text=True, check=True)
    derived = json.loads(result.stdout)
    derived.pop('output', None)
    _split_derived(out / 'derived.usda')
    drivers = Sdf.Layer.CreateNew(str(out / 'studies.usda'))
    drivers.subLayerPaths = ['derived.usda']
    drivers.Save()
    stage.GetRootLayer().subLayerPaths = ['studies.usda']
    cameras = sorted(iter_cameras(stage), key=lambda p: str(p.GetPath()))
    by_id = {_id(p): p for p in stage.Traverse() if _id(p)}
    selected = [p for p in cameras if _id(p).startswith('sec.cam.door.')]
    if len(cameras) != imported['cameras'] or len(selected) != 11:
        raise ValueError('published camera census differs from import or the 11 door providers')
    bbox = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ['default', 'render', 'proxy', 'guide'])
    targets, exclusions = [], []
    with Usd.EditContext(stage, drivers):
        author_study_root(stage, root)
        if root != Sdf.Path.absoluteRootPath:
            for path in (targets_path, studies_path):
                UsdGeom.Scope.Define(stage, path)
        for camera in selected:
            door = by_id[_id(camera).removeprefix('sec.cam.')]
            n = {'+X': (1, 0, 0), '-X': (-1, 0, 0), '+Y': (0, 1, 0), '-Y': (0, -1, 0)}[
                door.GetAttribute('aeco:props:DC_DoorApproach:ApproachNormal').Get().upper()]
            n = np.asarray(n); across = np.array([-n[1], n[0], 0])
            bounds = bbox.ComputeWorldBound(door).ComputeAlignedRange()
            low, high = np.array(bounds.GetMin()), np.array(bounds.GetMax())
            centre = (low + high) / 2; centre[2] = low[2]
            width = float(np.dot(high - low, np.abs(across)))
            # Sample the approach side of the published door envelope at 0.25 m.
            face = float(np.dot((high - low) / 2, np.abs(n))) + .15
            points = [centre + n * face + across * x + np.array([0, 0, z])
                      for x in np.arange(-width / 2 + .1, width / 2, .25)
                      for z in np.arange(.3, min(high[2] - low[2], 2.1), .25)]
            inverse = UsdGeom.Xformable(door).ComputeLocalToWorldTransform(Usd.TimeCode.Default()).GetInverse()
            local = [inverse.Transform(Gf.Vec3d(*map(float, point))) for point in points]
            targets.append(_target(door, local, 250.))
        for space in list(stage.Traverse()):
            typ = space.GetAttribute('aeco:props:DC_Space:SpaceType').Get()
            if typ not in ('hall', 'battery', 'office', 'meeting', 'kitchen', 'wc') or _id(space) == 'sp.security':
                continue
            extent = next((p for p in space.GetChildren() if p.GetAttribute('aeco:derived:role').Get() == 'extent'), None)
            bounds = bbox.ComputeWorldBound(extent or space).ComputeAlignedRange()
            low, high = np.array(bounds.GetMin()), np.array(bounds.GetMax())
            tiles = {}
            for x in np.arange(low[0] + .8, high[0] - .3, 1.):
                for y in np.arange(low[1] + .8, high[1] - .3, 1.):
                    tiles.setdefault((math.floor(x/4), math.floor(y/4)), []).append([x, y, low[2] + 1.5])
            for i, points in enumerate(tiles.values()):
                target = stage.DefinePrim(targets_path.AppendChild(_id(space).replace('.', '_') + '_tile_' + str(i)), 'Xform')
                target.CreateAttribute('aeco:phase', Sdf.ValueTypeNames.Token).Set('proposed')
                exclusions.append(_target(target, points, spacing=1.))
        for name, providers, required, excluded, density in (
                ('CriticalDoors', selected, targets, [], 250.), ('Privacy', cameras, [], exclusions, 0.)):
            prim = stage.DefinePrim(studies_path.AppendChild(name), 'Scope')
            prim.ApplyAPI('AecoCctvStudyAPI')
            for key, value in {'densityModel': 'plane', 'levelSystem': 'dori2015', 'requiredDensity': density,
                               'ptzPolicy': 'presetsNotSole', 'phases': ['proposed', 'existing'],
                               'raySamples': Gf.Vec2i(16, 12), 'writeShells': True}.items():
                prim.GetAttribute('aeco:cctvStudy:' + key).Set(value)
            for name_, paths in [('cameras', [p.GetPath() for p in providers]), ('targets', required), ('exclusions', excluded)]:
                Usd.CollectionAPI(prim, name_).GetIncludesRel().SetTargets(paths)
    drivers.Save()
    findings = [{'name': 'Import', **imported}, {'name': 'Derive', **derived}]
    shell_counts = {}
    for name in ('CriticalDoors', 'Privacy'):
        print('== stage: study ' + name, flush=True)
        # This fixture has a fixed receipt time so its authored layers compare
        # byte for byte. Ordinary CLI studies continue to record their run time.
        with patch.multiple('usdaeco_cctv.study', __version__=version, TOOL='aeco-cctv study ' + version):
            report = run_study(stage, studies_path.AppendChild(name), out / (name + '.usda'), kernel='auto',
                               time=datetime.datetime(2026, 9, 11, tzinfo=datetime.timezone.utc))
        (out / (name + '.json')).write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
        findings.append({'name': name, 'results': report['results'], 'exclusionsCovered': report['exclusionsCovered']})
        views, skipped = enumerate_views(stage, Settings(stage.GetPrimAtPath(studies_path.AppendChild(name))))
        shells = [stage.GetPrimAtPath(v.sensor.GetPath().AppendChild(v.shell_name)) for v in views]
        if skipped or not views or not all(p and p.IsA(UsdGeom.Mesh) for p in shells):
            raise ValueError(name + ' must write a coverage shell for every selected view')
        shell_counts[name] = {'sensors': len({v.sensor.GetPath() for v in views}), 'views': len(views), 'shells': len(shells)}
    # Persist the study layers that the engine attached to the session layer.
    stage.GetRootLayer().subLayerPaths = list(stage.GetSessionLayer().subLayerPaths) + list(stage.GetRootLayer().subLayerPaths)
    stage.GetSessionLayer().subLayerPaths = []
    look = sensors_of(by_id['sec.cam.door.hall.a.s'])[0]
    views, _ = enumerate_views(stage, Settings(stage.GetPrimAtPath(studies_path.AppendChild('CriticalDoors'))))
    view = next(v for v in views if v.sensor == look)
    presentation = Sdf.Layer.CreateNew(str(out / 'presentation.usda'))
    stage.GetRootLayer().subLayerPaths.insert(0, 'presentation.usda')
    with Usd.EditContext(stage, presentation):
        for prim in stage.Traverse():
            role = prim.GetAttribute('aeco:derived:role').Get()
            if role == 'extent' or prim.GetName().startswith('Shell_') or prim.GetName() == 'Envelope':
                UsdGeom.Imageable(prim).MakeInvisible()
            if role == 'sector' and any(p.GetName().startswith('Coverage_') and p.IsA(UsdGeom.Mesh)
                                        for p in prim.GetParent().GetChildren()):
                UsdGeom.Imageable(prim).MakeInvisible()
            if prim.GetName().startswith('Coverage_') and prim.IsA(UsdGeom.Mesh):
                UsdGeom.Gprim(prim).CreateDisplayColorPrimvar('constant').Set([SHELL_COLOURS[0]])
                # Retain Privacy output for inspection; frame the overview on doors.
                if prim.GetName().startswith('Coverage_Privacy'):
                    UsdGeom.Imageable(prim).MakeInvisible()
            if prim.GetAttribute('aeco:class:ifc:code').Get() == 'IfcSlab.ROOF':
                UsdGeom.Imageable(prim).MakeInvisible()
            # Bounded scene colours retain contrast with the unlit camera light.
            if prim.IsA(UsdGeom.Gprim) and role not in ('sector', 'coverage', 'extent'):
                colour = UsdGeom.Gprim(prim).GetDisplayColorPrimvar()
                if colour.Get():
                    colour.Set([Gf.Vec3f(*map(float, np.asarray(c) * .9)) for c in colour.Get()])
                if _id(prim.GetParent()) == 'door.hall.a.s' and prim.IsA(UsdGeom.Mesh):
                    _door_colours(UsdGeom.Mesh(prim))
                elif _id(prim.GetParent()) == 'sec.iris.hall.a.s':
                    colour.Set([Gf.Vec3f(.85, .25, .95)])
        camera = render_camera(stage, 'lookthrough')
        camera.GetFocalLengthAttr().Set(view.focal)
        camera.GetHorizontalApertureAttr().Set(2 * view.focal * view.frustum.tan_x)
        camera.GetVerticalApertureAttr().Set(2 * view.focal * view.frustum.tan_y)
        camera.GetClippingRangeAttr().Set(Gf.Vec2f(view.frustum.near, view.range))
        world = Gf.Matrix4d(1)
        for i in range(3):
            world.SetRow(i, Gf.Vec4d(*map(float, view.basis[i]), 0))
        world.SetTranslateOnly(Gf.Vec3d(*map(float, view.origin)))
        UsdGeom.Xformable(camera).MakeMatrixXform().Set(world)
    presentation.Save()
    # Only the look-through suppresses guides, including its own coverage shell.
    look_layer = Sdf.Layer.CreateNew(str(out / 'lookthrough.usda'))
    stage.GetSessionLayer().subLayerPaths.insert(0, look_layer.identifier)
    with Usd.EditContext(stage, look_layer):
        for prim in stage.Traverse():
            if prim.IsA(UsdGeom.Gprim) and (UsdGeom.Imageable(prim).ComputePurpose() == 'guide'
                                          or prim.GetPath().HasPrefix(look.GetParent().GetPath())):
                UsdGeom.Imageable(prim).MakeInvisible()
    look_layer.Save()
    stage.GetSessionLayer().subLayerPaths.remove(look_layer.identifier)
    from .example_metrics import coverage_footprint, render_limits
    print('== stage: coverage footprint', flush=True)
    findings.append({**coverage_footprint(stage), 'studies': shell_counts})
    findings.append(render_limits())
    unchanged = all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == h for p, h in before.items())
    if not unchanged:
        raise ValueError('published source or committed inputs changed')
    # Flattening retains the root layer's metadata, not sublayer receipts.
    with Usd.EditContext(stage, stage.GetRootLayer()):
        author_study_root(stage, root)
    return findings
