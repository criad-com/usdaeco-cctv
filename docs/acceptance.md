# 0.4.8 acceptance

The reference IFC camera reader is now `usdaeco_cctv.contract`. It uses only
ifcopenshell and the standard library; the importer consumes its fact and
unit/driver helpers while preserving sparse USD authoring and promotion
bookkeeping. Sync can use the same reader when its optional companion is
registered. Schema metadata remains 0.2.1 and study algorithm revision 4.

| Acceptance | Measured result |
|---|---|
| check.py | 123 checks, 0 failed |
| Pytest with the demo export and reference history | 297 passed; 3 optional study-performance probes skipped |
| Independent reference-reader tests | 14 passed, including a process refusing USD imports |
| Shared/fallback parity in sync | 20 tests passed; exact dictionaries for 8 unit/tier variants and 45 generated cameras |
| Raw layer comparisons against 0.4.7 | 7/7 identical: IFC, COBie, Revit-shaped and gate fixtures, generated IFC, demo export, lobby derivation |
| Gate fixture importer counters | 3 cameras, 6 sensors, 2 presets, 3 types, 137 blocks, 2 folded, 0 unmatched |
| Generated model importer counters | 45 cameras/sensors, 7 presets, 3 types, 459 blocks, 0 folded, 0 unmatched |
| Demo export importer counters | 45 cameras/sensors, 7 presets, 3 types, 910 blocks, 49 folded, 0 unmatched |
| Lobby derivation | 4 sensors/sectors, 7 shells, 1 envelope, 1 tour, 0 skipped |
| Vanilla composition | 51 transforms identical without plugins |
| Sanitization | Family term sweep clean; no new source paths or facility identifiers |
| Nix | NOT PROVEN; one offline attempt |

[Numeric comparison evidence](../baselines/contract-import-parity.json) records
raw hashes, byte lengths and every counter. The comparison loads the pre-change
importer from revision `bef00469a14b942369dc7c558c2c4512330523aa` against the
same dependencies and current release stamp. It writes both outputs beside the
same core stage; no layer bytes are normalized. The demo export SHA-256 matches
the preceding release's evidence. The generated IFC comes from version 0.3.3,
revision `c3517940cd0f491112ee980d76024f6e7dcf66f1`, built in owned scratch output.

Follow the [root setup](../README.md) and the
[reference reader instructions](ifc-camera-contract.md#reference-python-reader).
Set `AECO_DEMO_REVIT_IFC` to the existing sanitized export to reproduce the
optional full-size regression, then run:

```sh
mkdir -p artifacts/contract
env -u PYTHONPATH "$AECO_PYTHON" check.py --report artifacts/contract/check.json
env -u PYTHONPATH "$AECO_PYTHON" -m pytest -q
```

For a byte comparison on another prepared source/core pair, the existing
benchmark worker supports both implementations without running timing trials:

```sh
env -u PYTHONPATH "$AECO_PYTHON" tools/bench_import.py --worker reference \
  --reference-ref bef00469a14b942369dc7c558c2c4512330523aa \
  --source "$SOURCE_IFC" --core "$CORE_STAGE" --out artifacts/contract/reference.usda
env -u PYTHONPATH "$AECO_PYTHON" tools/bench_import.py --worker optimized \
  --source "$SOURCE_IFC" --core "$CORE_STAGE" --out artifacts/contract/shared.usda
cmp artifacts/contract/reference.usda artifacts/contract/shared.usda
```

Both worker JSON results must also have identical `counts`. Use new output
filenames on each run, and retain Git history for the reference implementation.

## Deviations

- The required release stamp changes from 0.4.7 to 0.4.8. Both algorithms use
  the current stamp for the raw-byte comparison. Lobby semantic counters
  exclude the caller-selected `output` filename; all seven importer counters
  compare directly without exclusions.
- The private historical dataset was not accessed. Its generated gate fixture,
  the generated demo IFC and the existing sanitized demo export were checked.
- Three unrelated optional performance probes were skipped. No new study or
  importer performance budget, or live Revit execution, is claimed.
- One offline Nix attempt stopped at the unresolved nested
  `toolchain/aeco-toolchain` registry input; no second attempt was made.

# Historical 0.4.7 acceptance

The importer now indexes source properties and camera proximity while
preserving the prior matching and authoring results. Schema metadata stays
0.2.1, study algorithm revision 4; the plugin/tool release is 0.4.7. Verification
uses compatible core 0.8.4 and toolchain 0.1.0; the core source pin stays 0.8.3.

| Acceptance | Measured result |
|---|---|
| Library check.py | 123 checks, 0 failed |
| Pytest with the demo export and reference history | 283 passed, 3 optional study-performance probes skipped |
| Fresh-process import p95, 12 trials | 2.820268 s, against < 3.0 s; previous algorithm 5.578668 s |
| Slowest optimized import | 2.825724 s |
| Camera / sensor / folded helper / unmatched / type counts | 45 / 45 / 49 / 0 / 3 in every trial |
| Presets / promoted-property blocks | 7 / 910 in every trial |
| Before/after kind-layer bytes | 24/24 outputs identical at the same release stamp and relative core arc |
| IFC and core-layer preservation | Original IFC and every used input layer unchanged |
| Fresh core conversion | 9.563 s; 1,514 elements, 35 spatial prims, 30 extents, zero unparented |
| Demo vanilla composition | 0 family plugins, 0 composition errors, 45/45 camera transforms unchanged |
| Sanitization | 0 forbidden-term hits |
| Nix | NOT PROVEN; one offline attempt |

[Import performance](import-performance.md) documents the methodology,
reproduction commands and every stage. [Numeric evidence](../baselines/revit-import-performance.json)
retains all trials and hashes. The tests compare whole output files against
the preceding algorithm on IFC, COBie, a neutral Revit-shaped fixture and the
supplied demo export. Spatial tests cover negative cells, metres/millimetres,
multiple levels, head overrides and ties just outside the distance cutoff.

## Deviations

- The release metadata must change to 0.4.7. Before that change, optimized and
  unmodified 0.4.6 outputs had the same raw hash. The benchmark runs both
  algorithms with the current release stamp and identical relative paths;
  it performs no output normalization. Full hashes and scope are recorded.
- The seven-counter Python return contract remains intact; CLI JSON adds
  `timingsSeconds`, and Python callers opt into timing collection with a
  supplied dictionary. Timing data is excluded from the authored layer.
- Timing covers the complete import API, including teardown, but excludes
  process startup and core conversion. Each trial starts a fresh process;
  the operating system's filesystem cache is not flushed.
- Historical-output tests require Git and the reference revision. The demo
  integration claim also requires the external export; neither input is
  silently substituted. Three unrelated optional study-performance probes
  were not run. Native Revit execution and live sync are outside this package.
- The single offline Nix attempt could not resolve the nested
  `toolchain/aeco-toolchain` registry input. No Nix pass is claimed.

## Historical 0.4.6 acceptance

The importer folds exported Revit helper pictures and consolidates identical
legacy symbol catalogs. Schema metadata remains 0.2.1, algorithm revision 4;
the plugin/tool version is 0.4.6. This run uses core v0.8.4 and toolchain
v0.1.0; the reproducible core source pin remains v0.8.3.

| Acceptance | Measured result |
|---|---|
| Library check.py | 123 checks, 0 failed |
| Pytest with the supplied demo Revit IFC | 269 passed, 3 optional performance probes skipped |
| Demo source cameras / imported cameras / sensors | 45 / 45 / 45 |
| Demo helper pictures folded / unmatched | 49 / 0; all 45 camera owners represented |
| Assigned camera catalog types | 24 → 3; source groups of 12, 10 and 2 |
| Demo occurrence identities and world transforms | 45/45 unchanged |
| Demo vanilla composition | 0 family plugins, 0 composition errors; all 45 camera transforms unchanged |
| Demo derivation | 45 sectors, 115 density shells, zero skipped sensors |
| Original IFC/COBie fixture | 3 cameras, 6 sensors, 2 presets, 3 types, 137 blocks, 2 folded, 0 unmatched |
| Scenarios | 31/31 on numpy and Embree; original 16 lobby cases retained |
| Sanitization | 0 forbidden-term hits |
| Nix | NOT PROVEN; one offline attempt |

[Diagnosis and reproduction](revit-import.md) describe the export shape and
the synthetic IFC4X3 fixture. [Numeric evidence](../baselines/revit-import.json)
records the source hash, full type/helper mappings and input preservation.
The optional export test performs a fresh core conversion. Ordinary pytest
also skips that claim when `AECO_DEMO_REVIT_IFC` is unavailable.

### Historical 0.4.6 deviations

- All helper names were already recognized. The export has no IFC nesting or
  SuperComponent ownership; its picture origins exceed the legacy distance
  bound. Complete matching optics plus a bounded 1 m fallback resolves it.
  IFC ownership support is proven separately with synthetic aggregates/nests.
- Complete type records must match, including unknown properties and head
  counts. Tier A JSON and catalogs with additional composition remain intact;
  optics alone never discard different product data.
- A single measured import took 5.416 s, exceeding the 3 s plan budget.
  Conversion took 9.970 s. No new p95 study/performance claim is made.
- The three optional generated-facility performance probes were not run.
  Historical private input files were not accessed; their synthetic gate and
  the original importer fixtures retain their expectations.
- The single offline Nix attempt failed to resolve the nested
  `toolchain/aeco-toolchain` registry input. No second attempt was made.
  No live Revit or sync acceptance is claimed by this package.

## Historical 0.4.5 acceptance

Core schema compatibility is `>=0.8.1,<0.9`. Builds use core v0.8.3 at
`fd87fff09cce26ba132082e5dad721ec2210a7e2`, with toolchain v0.1.0.
CCTV schema metadata remains 0.2.1; the plugin/tool version is 0.4.5.

| Acceptance | Measured result |
|---|---|
| Library check.py against core 0.8.3 | 120 checks, 0 failed |
| Full pytest with freshly generated facility | 251 passed, 0 skipped |
| Requirement regressions | 9 passed; manifest and built plugin tested |
| Core accepted / rejected | 0.8.1, 0.8.3 / 0.8.0, 0.9.0 |
| Scenarios candidate revision and requirement audit | 15 checks, 0 failed |
| Unchanged scenarios audit regressions | 5 passed |
| Schema and generated schema changes | 0 |
| Sanitization | 0 forbidden-term hits |
| Nix | NOT PROVEN; one offline attempt |

The complete pytest run supplied the facility built from the pinned generator
v0.2.1 in an owned scratch checkout. The source generator and stable family
checkouts were read only. The library gate includes all 31 cases on numpy
and Embree, IFC/COBie import and the fresh vanilla composition probe.
[Dependency evidence](requirements-evidence.json) records the candidate
revisions and every scenarios audit row. Reproduce the ordinary checks with
the [root README](../README.md#build-and-check); the full facility test setup
is in the [performance guide](performance.md).

### Historical 0.4.5 deviations

- The single offline Nix check could not resolve the nested
  `toolchain/aeco-toolchain` registry input. No second attempt was made.
  The updated core lock hash comes from the exact local Git snapshot;
  dependency closure execution remains not proven.
- The scratch scenarios audit used exact candidate branch revisions because
  release tags and the scenarios re-pin are pending. It did not run the full
  family gate or claim acceptance of future release tags.
- No new performance budget or live-host result is claimed. The following
  performance and enclosure evidence belongs to 0.4.4.

## Historical 0.4.4 acceptance

The release adds opaque-body enclosure sampling, the default-true study toggle,
an explicit derived count, JSON sample accounting and the region warning.
Schema metadata is 0.2.1; plugin and tool version is 0.4.4; algorithm revision
is 4. Core v0.8.1, toolchain v0.1.0 and generator v0.2.1 revisions are in
[dependencies.json](../dependencies.json). All new models are synthetic.

| Acceptance | Measured result |
|---|---|
| Family gate | 119 checks, 0 failed |
| Pytest with generated facility | 242 passed, 0 skipped |
| Scenarios | 31/31 on numpy; 31/31 on Embree |
| Original lobby scenarios | All 16 retain their expectations |
| Synthetic yard | 64 original, 24 enclosed, 40 evaluated, fraction 1.0; both kernels |
| Yard with toggle false | 64 evaluated, 24 enclosed reported, fraction 0.625; no coverage pass |
| Lobby crates | 1/5, 3/5, 5/5 enclosed; partial and empty denominators; both kernels |
| Parity geometry | Shared triangle diagonals, surface bounds, open shells, cavities, overlapping bodies, distant coordinates; both kernels |
| Obstacle policy | Opaque target body included; transparent, ignored, filtered phase and extent geometry excluded |
| Warning | Strictly >50%, target/study sites and counts; no rays cast by validators |
| Result integrity | Missing, negative and excessive enclosure counts rejected |
| Vanilla composition | No-plugin composition and derived geometry gate passes |
| Sanitization | Zero forbidden terms or local source paths in release files and new commit messages |
| Nix | One offline attempt; unresolved nested toolchain input |

[Benchmark evidence](../baselines/facility-enclosed-study.json) records the
single invocation, 30 trials per mode and configuration (180 trials total),
stage timings, correctness counts and unchanged cached publications. The
strict `--enforce` command exited 0. The previous measurement remains in
[the historical baseline](../baselines/facility-study.json).

| Configuration | Cold p95, s | Cached p95, s | Hash-only p95, s | Peak RSS, MiB |
|---|---:|---:|---:|---:|
| 32 fixed views | 0.971236 | 0.027247 | 0.004663 | 367.266 |
| 29 generated cameras, 33 views | 1.065166 | 0.020816 | 0.004048 | 423.922 |
| Required fixed32 budget | <1.000 | <0.250 | <0.100 | <1024 |

Both configurations use all 41 doors and 87,136 triangles. Every result,
per-view sample, blocker, through record and flag matches the uncropped
oracle: 32/33 views and 115/116 view-target entries respectively. Each has
30/30 unchanged cached mtime/SHA-256 checks. The benchmark disables shell
meshes while retaining all depth casts, target rays and result publication;
ordinary studies default to writing shells. Setup conversion/import is
excluded. Reproduce with the [benchmark instructions](performance.md).

### Historical deviations

- The supplied branch was based on main with 0.4.2 release metadata, rather
  than the stated 0.4.3. The implementation uses that supplied branch and
  publishes 0.4.4 without inventing a 0.4.3 release history.
- Nix is not proven: the one offline attempt could not resolve the nested
  `toolchain/aeco-toolchain` registry input. No second attempt was made.
- Generated-camera cold p95 is 1.065166 s; the required fixed32 configuration
  meets the unchanged budget. This is measured performance on the reference
  machine, not a guarantee for another machine or system load.
- Open-shell classification uses three bidirectional axis tests and requires
  two agreeing axes. It is a sampling heuristic, not a watertightness or
  walkability certificate. Privacy exclusions keep visible-sample semantics.
- Fully enclosed targets have no coverage evidence (zero fraction/density,
  no providers) and raise the region warning. The older importer fixture has
  15 samples inside its three door leaves; its numeric summary now exposes
  three enclosed targets instead of claiming covered or uncovered doors.
- Native BVH disk persistence remains unavailable in embreex 4.4.0. The
  benchmark includes cold BVH rebuilding; in-process caches remain supported.
- Without `AECO_FACILITY_STAGE`, the portable pytest command skips three
  facility probes. The release run supplied the generated stage and skipped
  none. No live host, lighting, recognition or recorder commissioning is
  claimed, and the full demo security schedule was not rerun in this package.
