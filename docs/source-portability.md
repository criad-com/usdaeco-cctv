# Source portability — 0.5.4

The archived source now uses `../../../inputs/source/dist/base/dc.usda`.
The harness refreshes the ignored `inputs/source` link from
`AECO_DATACENTRE_ROOT`; S29 accepts it even with the link absent.
[Measured evidence](source-portability.json) records both independent layouts.

| Acceptance | Original layout | Relocated layout |
|---|---:|---:|
| check.py | 159 checks, 0 failed | 159 checks, 0 failed |
| Structure, including S29 | 29 passed, 0 failed | 29 passed, 0 failed |
| ResultStale comparison | PASS | PASS |
| pytest | 332 passed, 5 skipped | 332 passed, 5 skipped |
| Core validators loaded | 8/8 | 8/8 |
| Published prims, plugin-free | 12680 | 12680 |
| Door coverage / privacy hits | 11/11 / 0 | 11/11 / 0 |

The second checkout used a renamed repository and copied pinned releases in
separate directories. Its stale source link initially pointed at the first
layout; run.py retargeted it to the selected second-layout data-centre release.
The archived source then composed with 12269 prims and zero composition errors.
The same full check.py and pytest commands in the README ran in both layouts,
with explicit dependency roots and core first on the plugin path. The core
checkout and current repo were importable through the documented PYTHONPATH.
No test evidence was reused.

Publication retains all five binary artifacts: one crate and four PNGs,
including the user-documentation image. SHA256 values equal v0.5.3 exactly.
Within result/, only one source-reference line changed; the manifest updates
its hash/size and the toolchain pin. The result has 18 files, 7935090 bytes.
Fresh runs compare every authored layer byte and canonical crate content;
new images are independently checked, rather than promised byte-identical.

## Deviations

- The data-centre fixture explicitly preserves its published 0.5.3 import,
  derivation and study receipts, including its existing fixed timestamp and
  input hashes. This prevents a packaging version bump changing non-reference
  opinions. Normal CLI operations stamp 0.5.4; the separate lobby example was
  regenerated and its 13 stamp lines updated. Geometry is unchanged.
- The one offline Nix attempt with local source overrides stopped before
  evaluation: Nix rejected their temporary directory's symlinked ancestor.
  Nix packaging remains NOT PROVEN; no retry or network fetch was made.
- The analysis interpreter uses usd-core 26.8. Stock rendering uses the existing
  OpenUSD 0.26.11 Embree runtime through a temporary launcher. No package was
  installed. README setup now selects the renderer explicitly.
- Five existing optional tests remain skipped; the opt-in real-data gate was
  not run. The two inherited classification warnings remain unchanged and are
  checked against the source. No core validation errors were introduced.
