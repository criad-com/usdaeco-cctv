# Measured facility studies

The 0.4.4 run retains the unchanged budgets with enclosure classification
enabled: fixed32 p95 cold **0.971236 s**, cached **0.027247 s**, hash-only
**0.004663 s**, and peak RSS **367.266 MiB**. Both configurations have 30
trials per mode, exact uncropped parity and 30 unchanged cached publications.
Generated-camera cold p95 is **1.065166 s**, a separate reference.

The benchmark targets all 41 doors in `demo-datacentre-01`. It runs both the
29 generated cameras (33 views after expanding seven presets) and 32 fixed
views on an 8 by 4 grid, with 96 by 54 depth rays per view. The scene contains
2,938 obstacle owners and 87,136 triangles; space extents are target geometry.
These measurements do not claim that every door is covered.

Build the library using the [README](../README.md), then run:

```sh
export AECO_DATACENTRE_ROOT="$(cd ../usdaeco-datacentre && pwd)"
export PYTHONDONTWRITEBYTECODE=1
env -u PYTHONPATH "$AECO_PYTHON" tools/bench_facility.py \
  --work artifacts/facility --output artifacts/facility-benchmark.json \
  --trials 30 --enforce
```

Use the generator and core revisions in [dependencies.json](../dependencies.json).
If a sibling generator has advanced, create a separate checkout at the pinned
revision and set `AECO_DATACENTRE_ROOT` to it. The benchmark copies that source
into its work directory before building; the original remains read-only.
The work directory must be new for a build. Generation, core conversion and
camera import run in separate processes and are reported separately. They
are excluded from study timings and study peak RSS. No network is needed.
The optional generator dependencies are pydantic 2, PyYAML and IfcOpenShell.

An already converted, camera-imported stage can be reused:

```sh
env -u PYTHONPATH "$AECO_PYTHON" tools/bench_facility.py \
  --stage artifacts/facility/kind.usda --work artifacts/repeated-study \
  --output artifacts/repeated-study.json --trials 30 --enforce
export AECO_FACILITY_STAGE="$(pwd)/artifacts/facility/kind.usda"
env -u PYTHONPATH "$AECO_PYTHON" -m pytest -q testenv/test_performance.py
```

The facility tests include both camera configurations and additional tall-hall,
stair and exterior-yard views. Without `AECO_FACILITY_STAGE`, pytest explicitly
skips three facility tests. The portable family gate compares the lobby and a
synthetic cross-level fixture with both kernels. `--configuration fixed32` or
`generated` selects one measurement. `--trials 1` is only a smoke test.

The budgets remain **p95 < 1.0 s cold, < 0.25 s cached, < 0.10 s hash-only,
peak RSS < 1 GiB**, over at least 30 trials for 32 views and 41 doors against
at least 86,840 triangles. `--enforce` exits nonzero if the fixed-view run is
absent, ineligible or misses any budget. The [recorded evidence](../baselines/facility-enclosed-study.json)
retains every trial, p50/p95, software versions, correctness counts and peak
RSS. [Acceptance](acceptance.md) summarizes the measured release results.

## Measurement and output contract

Cold trials start with an already loaded USD stage, clear all process input,
geometry, topology, merged-buffer and BVH caches, and disable saved-view reuse.
They include preparation, computation, validation and atomic binary publication.
They do not flush operating-system file caches or include interpreter startup.
There is no persistent cache to warm. Each configuration has a separate worker.
Peak RSS is the worker high-water mark, including its uncropped correctness
oracle; it is not an allocation estimate or RSS delta.

Benchmarks set `aeco:cctvStudy:writeShells = false`. They still cast every depth
ray, collect its flags, compute every target result and publish the complete
numeric analysis. The property defaults to **true** for ordinary studies,
including the demo, and controls only shell mesh authoring. Changing it leaves
the numerical input hash and results unchanged. An unchanged shell is copied
as an Sdf spec when other views change; a completely unchanged publication is
reused in place. Result and shell authoring uses `Sdf.ChangeBlock` batches.

A study's default destination uses **usdc**. `--format usda` selects reviewable
text; an explicitly supplied `.usda` path also retains text for compatibility.
Use `.usd` or a matching suffix when selecting a format explicitly.
Both formats pass the same composition and completeness checks before atomic
replacement. Derivation keeps its existing output-format behavior.

Cached trials use the same stage and previous validated output. They bypass
scene gathering, geometry hashing and computation, and **do not rewrite the
output**. Every trial verifies its mtime and SHA-256 outside the measured call;
`publication.unchangedCachedRuns` must equal the trial count. The caller still
detaches the owned output before running the study again. Hash-only trials
write nothing. A new process can reuse saved per-view results, but must gather
and validate inputs first; that is not the measured in-process cached mode.

## Timing fields and caches

`run_study()` returns `seconds` for the complete public call and exclusive
`timings`, in seconds. Nested spans do not double count:

| JSON key | Work included |
|---|---|
| `usdTraversalGather` | USD traversal, filtering, camera enumeration, bounds and target sampling |
| `triangulation` | Topology validation, tessellation and world-space conversion |
| `geometryHashing` | Raw attribute/buffer hashing and study/view fingerprints |
| `bvhBuild` | Scene/kernel construction, Embree commit and owner-exclusion subset builds |
| `depthCasting` | Depth grids, casts and associated flags |
| `targetRays` | Opaque-body enclosure parity, finite target/exclusion queries and sample evaluation |
| `resultWriting` | Isolation, restoration, aggregation, authoring, validation and publication or reuse |

`input_hash(stage, study, timings={})` fills the same fields without casting.
The study and hash reader share gathered owners, views, target samples and
fingerprints. Four stage memos retain at most two prepared studies and four
validated reports each. File-backed layer identity includes identifier,
dirtiness and replacement-sensitive file metadata; session content is digested.
USD 26.8's Python binding has no `Sdf.Layer.GetModificationTime`, so notices
cover edits while a layer stays dirty. The stack includes referenced layers,
and muting, layer reordering and session edits invalidate relevant inputs.
External file changes invalidate reuse; reload the USD layer to compose those
new file contents, as with any USD client.

Changes at an obstacle, camera, target, exclusion or their ancestors invalidate
gathered inputs. New prims invalidate conservatively. Unrelated property edits
under non-obstacle prims retain them. Only outputs previously published by the
memo can be ignored during their attachment/removal. A changed or missing output
cannot use the no-write path. Registry/algorithm inputs are checked on lookup.
Result completeness is validated independently of input-hash memoization.

Raw geometry fingerprints are shared by content (4 MiB, 4,096 entries).
World-space gprim buffers are cached by raw content, resolved transform, units
and algorithm revision (96 MiB, 16,000 entries). Triangulated local mesh shapes
share topology across translations (32 MiB, 4,096 entries), followed by a batched
world-space degeneracy check. Merged buffers retain 64 MiB/four entries;
scene/BVH caches retain 128 MiB estimated payload/two entries. Embree owner-exclusion
subsets retain 48 MiB estimated payload/64 entries per scene. Prepared studies
also reference their buffers; native allocator overhead and retained references
are included in the process RSS measurement. `clear_caches()` discards all of them.

## Embree persistence deviation

The installed embreex 4.4.0 binding exposes scene construction and queries,
with no scene/BVH save or load operation. Its pickling hook raises `TypeError`
for the native scene pointer. Native BVH disk persistence and a load-versus-build
comparison are therefore **not proven**. No pointer dump or substitute geometry
file is presented as a persisted BVH. No disk-cache files are created.

The implemented alternative reuses one Embree depth BVH across frusta. Every
depth ray lies inside its frustum, and Embree already traverses the BVH spatially;
rebuilding that tree for each spatial crop was redundant. Views with self/transparent
owner exclusions retain cropped, bounded subsets. Numpy retains spatial cropping.
Packed triangle upload avoids a redundant index buffer and preserves triangle
order. Cold benchmarks still rebuild every native BVH and include its full cost.

## Why culling preserves the queries

Each gprim has an AABB computed from its world-space triangles. A box is rejected
only when wholly outside a frustum half-space or far sphere, with a 0.1 mm margin
that increases with coordinate magnitude. The near plane is not a culling plane:
nearby geometry can block farther targets. A sheared camera basis disables spatial
rejection. No level name or containment boundary clips geometry.

Partly included targets retain all sample segments, including outside-FOV samples,
through additional segment AABB tests. Sample membership is evaluated in batches.
Finite target rays use exact numpy intersections over nearby candidate triangles,
preserving source order for coincident blockers. Flagged Embree depth hits receive
the same exact tie resolution. Tests compare every aggregate and per-view target
sample, blocker, through record, exclusion and flag against `cull=False`.
