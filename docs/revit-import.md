# Revit camera helper import

Version 0.4.7 imports the demo data centre's IFC4X3 Revit export as **45
cameras, 45 sensors, 49 folded helpers, zero unmatched and three camera
types**. The [numeric evidence](../baselines/revit-import.json) includes
the source SHA-256, all 24 source-type mappings and all 49 helper bindings.
No native family or facility export is distributed with this library.
The [import performance report](import-performance.md) measures repeated
imports and verifies byte equality against the preceding algorithm.

## Diagnosis

The preceding importer produced 45 cameras, 45 sensors, seven presets,
24 types, one folded helper and 48 unmatched. All 49 helper records are
`IfcBuildingElementProxy` instances named `FOV-AXIS_v80:Default:<element-id>`,
of type `FOV-AXIS_v80:Default`. There are no separate 2D-symbol instances in
this export. All helpers are top-level spatial contents: 48 in `L00 Ground`,
one in `L01 Office`. None has IFC aggregate/nest ownership or `SuperComponent`.
They are nested family observations flattened by the export; the IFC file
itself does not prove native parent identities.

Their property sets are `Constraints`, `Data`, `Dimensions`, `Graphics`,
`IFC Parameters`, `Identity Data`, `Materials and Finishes`, `Other`, `Phasing`,
`Photometrics`, `Pset_BuildingElementProxyCommon` and
`Pset_EnvironmentalImpactIndicators`, with `Text` also present on some helpers.
Useful observations include:

| Set | Helper fields |
|---|---|
| Constraints | Level, Host, Tilt, Elevation from Level, FOV Distance to Object |
| Data | Focal Length Minimum/Maximum |
| Dimensions | FOV Horizontal/Vertical Max/Min, RG_Length_* |
| Graphics | Horizontal Res, Vertical Res, Focal Length, DORI display flags |

The legacy name matcher already recognized these records. The failure was
the distance cutoff: 41 pictures are approximately 0.77 m from their nearest
camera and seven approximately 0.861452 m away, beyond 0.5 m. The single
previously folded picture is 0.27 m away. Increasing distance alone would
assign the pictures to only 44 distinct cameras; checking matching optics
resolves all 45 owners. These are geometric associations, not recovered
native ownership evidence.

The type records have different IFC GlobalIds but identical names and
complete property data within each group:

| Shared type | Occurrences | Source types | Focal range, mm | HFOV, degrees | Pixels |
|---|---:|---:|---|---|---|
| dome_5mp | 31 | 12 | 3–8.5 | 104–34 | 2592 × 1944 |
| bullet_4mp_outdoor | 11 | 10 | 3.7–7.5 | 100–52 | 2688 × 1512 |
| ptz_4k | 3 | 2 | 6.64–225.5 | 60.8–2 | 3840 × 2160 |

The importer chooses the smallest source type UUID in each identical group.
The 45 occurrences retain their original identities and transforms; the
seven presets remain. Fewer repeated catalog opinions require blocking:
910 promoted copies versus 1,162 before consolidation. Original core catalogs
remain available in the input; three catalogs are assigned to the cameras.

## Ownership and equivalence rules

Ownership precedence is explicit `--subinstances`, IFC aggregate/nest ancestry
(including multiple hops), `SuperComponent`, then proximity. A missing explicit
owner remains unmatched. Multiple IFC camera ancestors or a cycle fail before
publication. Camera-class referents are never folded merely because they are
nested or named like helpers.

The proximity fallback requires a known, shared level and a unique nearest
camera, with a 1 micrometre tie tolerance. Ordinary helpers use 0.5 m.
Complete focal/HFOV/VFOV ranges and pixel counts restrict candidates to sensors
whose optical values agree within 1e-6, permitting 1 m. These observations are
used only for association, never promoted as camera drivers. Names only
identify helper candidates. An unnamed proxy requires complete helper optics
and a graphics formula observation such as `RG_Length_Id`.

Consolidation requires equal full legacy type records, type names, composed
core catalog attributes and head counts. Changed optics or unknown metadata
prevent a merge. Tier A JSON catalogs and catalogs with child geometry,
composition arcs, relationships or animation remain intact. This conservative
scope prevents losing data outside the duplicated-symbol export shape.

## Reproduce

First follow the [build instructions](../README.md#build-and-check). Set
`AECO_DEMO_REVIT_IFC` to an existing demo Revit export and run:

```sh
export AECO_DEMO_REVIT_IFC=../usdaeco-datacentre/out/revit/demo-datacentre-01-revit.ifc
env -u PYTHONPATH "$AECO_PYTHON" -m pytest -q testenv/test_revit_importer.py
```

The integration claim converts the supplied IFC afresh with the selected
core checkout, imports it, and checks the 45/45/49/0/3 census, mappings,
45 distinct owners, identities, transforms, input hashes and byte equality
against the pre-optimization importer. It explicitly skips if the export or
reference Git history is absent. The neutral fixture tests cover
both ownership relations, multiple hops, competing proximity, invalid owners,
level/radius/tie limits, unnamed helpers, type differences and sparse overrides.

To inspect the output yourself, use a new destination for each import:

```sh
mkdir -p artifacts/revit
env -u PYTHONPATH "$AECO_PYTHON" tools/usdaeco_cctv/gate_conversion.py \
  "$AECO_DEMO_REVIT_IFC" artifacts/revit/core.usda
env -u PYTHONPATH "$AECO_PYTHON" tools/aeco-cctv-import \
  artifacts/revit/core.usda "$AECO_DEMO_REVIT_IFC" -o artifacts/revit/kind.usda
env -u PYTHONPATH "$AECO_PYTHON" tools/aeco-cctv derive \
  artifacts/revit/kind.usda -o artifacts/revit/derived.usda
```

`aeco:cctv:typeMapping` and `aeco:cctv:foldMapping` in the kind layer's
`customLayerData` are JSON strings. The numeric baseline contains their decoded
values. On the measured export, derivation yields 45 sectors, 115 density
shells and zero skips. No tour or motorised envelope is inferred from those
helper pictures.

## Deviations

- The export's helper names were already recognized and IFC ownership was
  absent. Matching optics plus a bounded distance extension fixes the measured
  failure; nested ownership is separately proven on the synthetic fixture.
- The historical 0.4.6 import took 5.416 s in one measured call, exceeding
  the 3 s budget; conversion took 9.970 s. The repeated 0.4.7 measurement
  and its exact timing scope are in [import performance](import-performance.md).
- Type consolidation intentionally requires complete identical records;
  matching optics alone does not establish that two products are interchangeable.
- The historical private source model was not accessed. Existing lobby,
  importer and historical-gate synthetic fixtures pass unchanged. Three optional
  generated-facility performance probes were not run.
- Native Revit ownership, live sync, native tour parity and a Nix build remain
  not proven by this import gate.
