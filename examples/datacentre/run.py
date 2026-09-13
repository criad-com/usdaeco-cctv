#!/usr/bin/env python3
"""Run the pinned example; --publish updates the standalone result and renders."""
import argparse
import json
import os
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
KIT = Path(os.environ.get('TOOLCHAIN_DIR', ROOT.parent / 'usdaeco-toolchain'))
sys.path[:0] = [str(ROOT / 'tools'), str(KIT / 'tools')]
from usdaeco_cctv import register_plugins
register_plugins()
from usdaeco_cctv.example import hook
from usdaeco_cctv.paths import render_camera
from usdaeco_check.example import run_example
import usdaeco_render

# Preserve the shared harness's comparison/publication contract while selecting
# purposes, the per-view presentation layer and the study camera aspect ratio.
_render = usdaeco_render.render
def render(*args, **kwargs):
    from pxr import Usd
    os.environ['HDEMBREE_USE_LIGHTING'] = '0'
    os.environ['HDEMBREE_CAMERA_LIGHT_INTENSITY'] = '100'
    stage = Usd.Stage.Open(str(args[0]))
    camera = render_camera(stage, 'lookthrough')
    ratio = camera.GetHorizontalApertureAttr().Get() / camera.GetVerticalApertureAttr().Get()
    width = kwargs['size'][0]
    options = {**kwargs, 'purposes': 'guide,proxy,render'}
    records = _render(*args, **{**options, 'views': ['overview']})
    records += _render(*args, **{**options, 'views': ['lookthrough'],
        'cameras': Path(args[0]).parent / 'lookthrough.usda', 'size': (width, round(width / ratio))})
    from usdaeco_cctv.example_metrics import measure_render
    out = Path(args[0]).parent
    limits = next(row for row in json.loads((out / 'findings.json').read_text()) if row['name'] == 'RenderMetrics')
    metrics = {view: measure_render(out / 'renders' / (view + '.png'), bounds)
               for view, bounds in limits['views'].items()}
    (out / 'render-metrics.json').write_text(json.dumps(metrics, indent=2, sort_keys=True) + '\n')
    for view, values in metrics.items():
        print(f"{view}: foreground {values['foregroundFraction']:.2%}; saturated white {values['saturatedWhiteFraction']:.2%}")
        if not values['passed']:
            raise ValueError(view + ' failed render metrics')
    return sorted(records, key=lambda r: r['path'])
usdaeco_render.render = render

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    os.environ.setdefault('AECO_DATACENTRE_ROOT', str(ROOT.parent / 'usdaeco-datacentre'))
    example = Path(__file__).parent
    manifest = run_example(example, hook, variant='base', publish=args.publish, keywords=[], size=(960, 600))
    manifest['renderMetrics'] = json.loads((example / 'out/render-metrics.json').read_text())
    encoded = json.dumps(manifest, indent=2, sort_keys=True) + '\n'
    (example / 'out/manifest.json').write_text(encoded)
    if args.publish:
        (example / 'manifest.json').write_text(encoded)
