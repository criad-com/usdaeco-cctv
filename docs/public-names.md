# Public names — 0.5.5

Public family URLs now use `github.com/criad-com`. The only dependency change
is toolchain v0.3.8; core v0.9.2, IFC v0.2.0, data-centre v0.4.5 and all
historical fixture pins remain unchanged. Release metadata agrees at 0.5.5.

| Acceptance | Measured result |
|---|---|
| Full check.py gate | 159 checks, 0 failed |
| Structure under toolchain v0.3.8 | 29 checks, 0 failed, including S05 and S25 |
| pytest | 332 passed, 5 skipped in 58.61 s |
| Core Python validators | 8/8 loaded and executed; seeded E15 defects rejected |
| Obsolete public org references | 0 in tracked repository text |
| Rewritten public URLs | 7 |
| Published result retention | 18 files byte-identical to v0.5.4 |
| PNG retention | All 4 PNGs byte-identical to v0.5.4 |
| Regenerated example comparison | Findings, authored layers and canonical crate contents match |
| Plugin-free result | 12680 prims, 0 composition errors |
| Door coverage / privacy hits | 11/11 / 0 |
| Generated schema | Byte-identical to v0.5.4 |

The README commands ran with source copies of the exact tagged dependencies,
including the IFC converter's transitive axis v0.1.1 plugin. The core checkout
was importable and missing validators remained a hard failure. Stock Embree
rendering passed independently; fresh renders were not published.

## Deviations

- S22 requires the example manifest's toolchain pin to match dependencies.json,
  so that single manifest field was updated. No result or render was republished.
- The lobby fixture's 13 derivation stamp lines follow the patch version so
  the existing deterministic CLI comparison remains green. Its geometry is
  unchanged; the data-centre fixture retains its published receipts.
- One `nix flake check --offline --no-write-lock-file` attempt with local
  overrides stopped before evaluation: pinned core's transitive data-centre
  source resolution reported HTTP 404. Nix is **NOT PROVEN**; no retry or
  additional dependency re-pin was made.
- Five optional pytest cases were skipped: one absent sync plugin, three
  facility-stage performance cases without their explicit input, and one
  unavailable demo IFC export. The opt-in real-data gate (three assertions)
  was **NOT RUN** because its input was absent and CI was enabled. These are
  separate from the 159 passing checks; the gate summary reports zero registered
  not-run rows because the optional gate is conditional.
- Two inherited classification warnings remain unchanged and match the pinned
  data-centre source. The derived examples introduce no core validation errors.
