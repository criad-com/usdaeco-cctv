# Public re-pin — 0.5.6

All four direct family inputs now name published release tags. The exact
checked revisions are recorded in [dependencies.json](../dependencies.json)
and the [example manifest](../examples/datacentre/manifest.json).
[Machine-readable acceptance](public-repin.json) includes the fresh render measurements.

| Input | Release | Checked revision |
|---|---|---|
| usdaeco-toolchain | v0.3.10 | 59d3da5ff5114089b54efaaae38efdd7fe1b8e73 |
| usdaeco-core | v0.9.5 | 683adc3280254bc71b396a9c64a359cc708788f4 |
| usdaeco-datacentre | v0.4.8 | 9e22b14c4909e50806805e735f93b1b425ac48cb |
| usdaeco-ifc | v0.2.2 | 8eb72e89af15e8642d914948384fe73038cc7817 |

Requirement ranges remain unchanged. Historical evidence under
`dependencies.json.fixtures` remains historical; none is a flake input here.
The recursive build kit pin is `aeco-toolchain` v0.4.0 through toolchain v0.3.10.
Package, library and resource-plugin versions agree at 0.5.6; the additive
schema revision remains 0.2.1.

| Acceptance | Measured result |
|---|---|
| Full check.py gate | 159 checks, 0 failed |
| Structure under toolchain v0.3.10 | 29 checks, 0 failed, including S05 |
| pytest | 332 passed, 5 skipped in 56.76 s |
| Core Python validators | 8/8 loaded and executed; seeded E15 defects rejected |
| Documented schema build and pinned publication | PASS |
| Generated schema contract | Byte-identical; 9 APIs, 71 properties |
| Published result compared with v0.5.5 | 17/18 files byte-identical; only the notice's source tag changes |
| Flattened crate / editable layers | Crate and all 15 layers byte-identical |
| Published source evidence / findings | All 3 source layers, source manifest and findings byte-identical |
| Result size / plugin-free composition | 7,935,090 bytes; 12,680 prims; 0 composition errors |
| Committed PNG retention | All 4 PNGs byte-identical |
| Fresh rendering | Stock vanilla and both study renders pass |
| Fresh study foreground / saturated white | Overview 23.66% / 0%; look-through 98.55% / 0% |
| Door coverage / privacy hits | 11/11 / 0 |
| Lobby regeneration | Only 13 version-stamp lines change |
| Pin and hygiene checks | 4 family tag refs; 0 family hash refs; 0 obsolete org refs; S25 and diff check pass |
| Offline Nix attempt | Exit 1 in 3.349 s after evaluating 2 Darwin package derivations; builds NOT PROVEN |

The README commands use the tagged dependency source trees, core's committed
v0.9.5 resource plugin, and the IFC converter's existing axis v0.1.1 plugin.
The core repository remains importable, and missing Python validators are a
hard failure. No dependency checkout was modified or built.

The single `nix flake check --offline --no-write-lock-file` attempt used eight
local overrides: `toolchain`, `core`, `datacentre`, `ifc`, `core/datacentre`,
`toolchain/core` (the kit's v0.9.2 fixture), `toolchain/aeco-toolchain` (v0.4.0),
and `toolchain/aeco-toolchain/openusd` (the unchanged upstream revision).
Tagged Git overrides used `ref=refs/tags/<tag>`; the build kit used a separate
tagged source copy. No lockfile was written.

## Deviations

- Nix resolved the local inputs and evaluated both Darwin package derivations,
  then refused `checks.aarch64-darwin.library`: the pinned nixpkgs marks
  `python3.14-ifcopenshell-0.8.0` broken. No build or Linux evaluation is proven;
  there was no retry, dependency substitution or broken-package override.
  Online public GitHub resolution remains unverified for the release review.
- Fresh PNG bytes vary with renderer sampling. After running
  `examples/datacentre/run.py --publish`, retain the three committed example
  PNGs and their existing manifest metrics; the documentation PNG is unchanged.
  Fresh measurements remain separate from retained-image receipts. This follows
  the same measured limitation recorded in the
  [core v0.9.5 changelog](https://github.com/criad-com/usdaeco-core/blob/v0.9.5/CHANGELOG.md).
  The resulting publication diff contains only pin refs, resolved revisions,
  the source-tag notice and its hash; geometry and layers do not change.
- Preserve the fixture's explicit 0.5.3 import, derivation and study provenance
  and fixed receipt time. Ordinary CLI operations stamp 0.5.6; the separate
  lobby fixture was regenerated through the CLI.
- The installed core resource directory still reported 0.9.4. The build and
  gates use the tagged checkout's committed v0.9.5 resources instead; the
  README now selects this source directory explicitly.
- Five existing optional pytest cases skip: the unavailable sync plugin,
  three facility-stage performance cases, and the unavailable demo IFC export.
  The opt-in real-data gate is NOT RUN because its explicit input is absent
  and CI is enabled. These cases do not contribute passing checks; the gate's
  zero registered not-run rows reflect conditional registration.
- Two inherited classification warnings match the pinned data-centre source
  exactly. The derived examples introduce no core validation errors.
