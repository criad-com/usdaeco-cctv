# usdAecoCctv — camera coverage over the built thing

## Use case

Camera coverage is a derivation over the built thing: camera drivers, built geometry and explicit sampling requirements produce inspectable findings. The core retains identity, placement and classification. See the [use case](docs/usecase.md) and [complete reference](docs/README.md).

## The schema on an index card

| APIs | Data |
|---|---|
| Camera, CameraType | Mount, purpose, housing and inherited type facts |
| Sensor, Preset | Optics, pose, range and discrete motorised positions |
| Study, Coverage | Provider/target/exclusion collections and derived results |
| Target, Sightline, System | Sampling, obstacle policy and recorder constraints |

Nine applied APIs, 71 properties; property names, types and defaults equal v0.4.8. Schema identifiers and registered types remain unchanged. [Schema](usdAecoCctv/schema.usda) · [manifest](library.json).

## The example

The published data-centre v0.4.8 base stage supplies 45 quarantined camera records. Import → derive → CriticalDoors (11 doors, 250 px/m, plane, dori2015) → Privacy → two renders. [Inputs, findings and reproduction](examples/datacentre/README.md).

Cyan = nominal field of view from drivers; light teal = what the study saw after walls and obstacles. The overview shows eleven occlusion-clipped CriticalDoors shells, with their nominal sectors hidden. Privacy also writes its 49 shells, retained in the output layers for inspection. Sensors without a study shell retain their nominal sectors.

Inside the L00 hall footprints, the displayed shells' projected union is **0.1533 m²**, versus **723.4732 m²** for the same eleven nominal sectors (1 cm scanline estimate). Door coverage stays **11/11** and privacy exclusion hits stay **0**. The look-through uses the study's sensor pose and frustum, with contrasting door, frame and reader colours. Both study images require ≥20% foreground and ≤40% saturated white; [numeric limits](examples/datacentre/expected/findings.json) and [measured pixels](examples/datacentre/manifest.json) accompany the renders.

Open [result/example.usdc](examples/datacentre/result/example.usdc) with stock `usdview`; it contains the flattened example, complete fallbacks and no external assets. [Editable layers](examples/datacentre/result/layers/) and a [plugin-free render](examples/datacentre/result/vanilla.png) accompany it. Regenerate with `examples/datacentre/run.py --publish` using the environment below.

![Coverage overview](examples/datacentre/renders/overview.png)

## Build and check

Use sibling core v0.9.5, toolchain v0.3.10, IFC v0.2.2 and data-centre v0.4.8 checkouts. The IFC converter also loads its pinned axis v0.1.1 plugin; set `AECO_AXIS_ROOT` and `AXIS_PLUGIN_DIR` when that checkout is elsewhere. The data-centre checkout must be at the exact pinned release; use a separate checkout when an existing sibling has advanced. Set `AECO_PYTHON` to Python with USD 26.8, NumPy, pytest, Jinja2, packaging, IfcOpenShell and openpyxl. Embree is optional; the NumPy kernel is the reference. Tests import directly from `tools/` and need no installed package or setuptools. Set `USDRECORD` to a stock OpenUSD `usdrecord` executable with Embree support and its matching imaging runtime.

```sh
export PYTHON="$AECO_PYTHON"
export TOOLCHAIN_DIR="$PWD/../usdaeco-toolchain"
export AECO_CORE_ROOT="$PWD/../usdaeco-core"
export AECO_IFC_ROOT="$PWD/../usdaeco-ifc"
export AECO_DATACENTRE_ROOT="$PWD/../usdaeco-datacentre"
export CORE_PLUGIN_DIR="$AECO_CORE_ROOT/usdAeco"
export PXR_PLUGINPATH_NAME="$CORE_PLUGIN_DIR:$PWD/usdAecoCctv:$PWD/usdAecoCctvValidators"
export PATH="$(dirname "$USDRECORD"):$(dirname "$AECO_PYTHON"):$PATH"
env -u PYTHONPATH bash build.sh --generate-only
env -u PYTHONPATH PYTHONPATH="$AECO_CORE_ROOT:$PWD" "$AECO_PYTHON" check.py
env -u PYTHONPATH PYTHONPATH="$AECO_CORE_ROOT:$PWD" "$AECO_PYTHON" -m pytest -q
env -u PYTHONPATH PYTHONPATH="$AECO_CORE_ROOT:$PWD" "$AECO_PYTHON" examples/datacentre/run.py
```

The recipe loads core's committed resource plugin so its metadata matches the selected release even if an earlier install remains under `out/`. The core repository root must be on `PYTHONPATH` so Plug can import `usdAecoValidators` by name; the resource plugin path alone is insufficient. Validation fails if the Python plugin or a selected rule cannot load. The gate runs all core rules on both derived examples and verifies that a seeded exact Mesh triggers both E15 findings.

The check ends with `N checks, M failed`. `build.sh --install-root out` creates the separate install layout. `tools/check_structure.py` runs all S01–S29 checks. Toolchain v0.3.10 verifies MIT licensing and the standalone result, including freshness (S27) an independent stock USD render (S28), and portable source references (S29); the earlier raw lint results remain in the [migration record](docs/migration.md).

```sh
nix flake check
nix flake check --offline --no-write-lock-file \
  --override-input toolchain path:../usdaeco-toolchain \
  --override-input core path:../usdaeco-core \
  --override-input ifc path:../usdaeco-ifc \
  --override-input datacentre path:../usdaeco-datacentre
```

Public flake refs match [exact dependency pins](dependencies.json). Local registry/source overrides are described by the [toolchain conventions](https://github.com/criad-com/usdaeco-toolchain/blob/v0.3.10/docs/repo-conventions.md); deployment lockfiles remain uncommitted. `nix run .#example` and `nix run .#render` use the same example harness.

## Family

Requires `usdAeco >=0.9,<1.0`; CCTV has no axis schema dependency. The IFC dependency is test tooling, and the data centre supplies published example data. [Family map](https://github.com/criad-com/usdaeco-core/blob/v0.9.5/docs/10-family.md) · [board](https://github.com/criad-com/usdaeco-board).

## Layout

`usdAecoCctv/` contains the flat schema, generated resources, user documentation and minimal stage. `usdAecoCctvValidators/` is the Python validation plugin. `tools/usdaeco_cctv/` contains the importer, contract reader, derivation and study engine. `testenv/`, `conformance/`, `docs/` and `examples/datacentre/` carry executable evidence and documentation.

## Status

Version **0.5.6**: **159 checks, 0 failed**; **332 tests passed, 5 skipped**. Re-pins all four family inputs to published tags and records their checked revisions; see [measured acceptance and deviations](docs/public-repin.md). The rebuilt crate and all fifteen editable layers match v0.5.5 byte for byte. Fresh renders pass; all four committed images retain their bytes. The result notice and manifest update dependency provenance only.

All eight core rules load and execute; derived outputs introduce no core validation errors. Two inherited classification warnings match the pinned data-centre source exactly. The single offline Nix attempt resolved local inputs and evaluated two package derivations, then stopped because the pinned nixpkgs marks `python3.14-ifcopenshell-0.8.0` broken. Nix builds and online public resolution remain **NOT PROVEN**. The opt-in real-data gate is **NOT RUN** unless its explicit input is supplied. Geometry establishes sampled visibility and pixel density; it does not establish lighting quality, recognition performance or coverage between samples.

## Licence

[MIT](LICENSE).

Runtime dependencies retain their own licences: OpenUSD — Apache-2.0-style TOST; NumPy and Jinja2 — BSD; packaging — Apache-2.0 or BSD; openpyxl — MIT; optional Pillow — HPND. Optional Embree acceleration uses embreex — Apache-2.0. IFC import uses IfcOpenShell — LGPL-3.0, imported only; its OCCT dependency — LGPL-2.1, dynamically linked only. The family toolchain is MIT. No third-party code is vendored.
