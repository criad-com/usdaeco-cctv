# Coverage scenarios

Run from the repository root after the [build instructions](../README.md#build-and-check):

```sh
env -u PYTHONPATH "$AECO_PYTHON" scenarios/run.py
env -u PYTHONPATH "$AECO_PYTHON" scenarios/run.py --case V-unclassified
env -u PYTHONPATH "$AECO_PYTHON" scenarios/run.py --kernel embree --output artifacts/cases.json
env -u PYTHONPATH "$AECO_PYTHON" scenarios/run.py --bench
```

Every case edits an anonymous copy of `examples/lobby.usda`. Source files stay
unchanged. Setup operations, exact findings/severities, required message text,
result values and shell checks are data in [cctv_cases.json](cctv_cases.json).
A selected case automatically runs any baseline comparison it depends on. An
unknown case id fails. The command prints `N cases, M failed` and exits nonzero
on failure. The library's `check.py` also turns each case into a family claim.

| Cases | Contract |
|---|---|
| V-derive, V-missing, V-baseline | Tier A outputs; missing-results info; all three doors fixed and identify |
| V-temp-ignored, V-temp-counted | phase filter; opted-in hoarding blocks Door_2 and is named |
| V-unclassified, V-unphased | missing provenance counts as an obstacle, warns and clips the shell |
| V-tray, V-stale | Door_1 PTZ duty 0.375 and distance warning; later edit reports staleness |
| V-exclusion, V-too-far | explicit exclusion sample is seen; distance threshold is enforced |
| V-night, V-policy | night reach; presets policy removes the PTZ warning |
| V-radar, V-envelope | radar-only presence in a separate yard; sampled PTZ reach and shells |
| V-enclosed-samples | lobby crates and yard pads/pipe; both kernels; counted enclosure, old toggle and warning |
| V-local-edit | local tray segment recomputes two views and reuses four |

The inherited lobby differs from a few assumptions in the initial plan:
its 11 m tray affects five candidate views, its PTZ is more than 3 m from
Door_1, and its column blocks the PTZ-to-Door_3 line even in envelope mode.
The night case uses 2.5 m dome IR and 5 m PTZ IR because the lobby doors are
closer than 10 m. The exclusion case authors a point inside the excluded
space, avoiding a bbox inferred solely from furniture. The radar case uses
a separate yard so additional geometry does not change the lobby's bbox-based
sampling. All differences and measured timings are in the
[analysis reference](../docs/README.md#analysis-tier-b).

The family gate can import the runner without executing its CLI:

```python
import importlib.util
from pathlib import Path
import tempfile

path = Path("scenarios/run.py").resolve()
spec = importlib.util.spec_from_file_location("cctv_scenarios", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with tempfile.TemporaryDirectory() as directory:
    report = module.run_suite(Path(directory), kernel="numpy", verbose=False)
assert report["failed"] == 0
```

`run_suite(directory, selected=(), kernel="numpy", cases_path=CASES,
verbose=True)` returns `{cases, passed, failed, kernel}`. Each case includes
`id`, `passed`, `failures`, findings, messages and applicable result/statistics
fields. `load_cases` and `run_case` are available for gate adapters.

`--bench` builds 20,000 boxes and 457 sets of 96 × 54 rays. It reports full
Embree build/cast timings, or explicitly says `embree unavailable`. Numpy
build/cast times use a 2,000-box, four-sensor subset; the full-scene estimate
is a projection. [benchmark.json](benchmark.json) records measured counts and
timings, including zero owner mismatches on the subset. Lobby parity is a
separate full-grid acceptance claim.

## Integrity regressions

The suite contains the original 16 lobby cases plus 15 integrity cases:
provider/target phases, incomplete results, two studies, output aliases,
holes, concavity, malformed meshes, stale inputs, missing optics,
non-camera appliances, fixed varifocal heads, exchange units and extent targets.
`integrity.py` runs the same assertion probes used by pytest on fresh fixture
copies; no external model or host is needed. Both `numpy` and `embree` select
the corresponding topology and study kernel. The four import cases require
IfcOpenShell and OpenPyXL; the explicitly selected USD-only gate omits them.
