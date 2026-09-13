# Configurable CCTV study root — 0.5.7

Set `AECO_STUDY_ROOT=/Studies/cctv` when adding CCTV analysis to a suite
stage. The hook writes the following hierarchy:

```text
/<defaultPrim>/_TypeCatalog/…
/Studies                         Scope
  /cctv                          Scope
    /Looks                       Scope
    /Targets                     Scope
    /Studies                     Scope
      /CriticalDoors             Scope + AecoCctvStudyAPI
        /Results/…
      /Privacy                   Scope + AecoCctvStudyAPI
/Renders/cctv/<camera>
```

The suite supplies `overview` and `lookthrough` render cameras. The hook
resolves these beneath `/Renders/cctv`, with `/Renders` retained for the
committed standalone inputs. Physical camera elements, their sensor children,
door sampling opinions and sensor-local derived guides stay in the building.
Camera types are reused or created under `/<defaultPrim>/_TypeCatalog`.
No second catalog is added at the stage root.

With the setting unset or `/`, the legacy roots remain `/AecoCctvLooks`,
`/SecurityTargets` and `/SecurityStudies`. These names are retained only for
publication compatibility; the schema does not require them. Every configured
non-root layout uses the short child names above. The nine APIs, 71 properties,
schema metadata revision 0.2.1 and study algorithm revision 4 are unchanged.

The root must be an absolute USD prim path or `/`, without properties or
variant selections. Existing ancestors must be untyped or plain Scopes;
an Xform is rejected instead of being retyped. The setting is read per call.
Configured derivation, study-driver and hook root layers record `aeco:cctv:studyRoot`
in `customLayerData`; the strongest composed receipt takes precedence over
the environment when reopening data, including a flattened crate. No new metadata is written for the
default standalone layout.

Materials, shader connections, target collections, result relationships and
`aeco:cctv:study` receipts use the authored paths. `iter_studies(stage)`
discovers the study APIs; validators use those prims and result receipts.
The existing CLI accepts the resulting study path explicitly, including when
`AECO_STUDY_ROOT` is no longer set:

```sh
aeco-cctv derive suite.usda --study /Studies/cctv/Studies/CriticalDoors -o design.usda
aeco-cctv study suite.usda /Studies/cctv/Studies/CriticalDoors -o coverage.usda
```

## Reproduction

Use the [repository environment](../README.md#build-and-check). The retained
publication gate uses data-centre v0.4.8 base. The additional integration
tests use the separate **v0.5.2** checkout, revision
`16b80298097bc0d5ab7ee567f49e50566e3f6597`, and its `dist/full/dc.usda`.
`AECO_CCTV_DATACENTRE_ROOT` selects this test input; the default is the sibling
`usdaeco-datacentre-0.5.2`. Tests write only to temporary directories and check
all input USD layer hashes before and after the hook.

```sh
env -u PYTHONPATH PYTHONPATH="$AECO_CORE_ROOT:$PWD" "$AECO_PYTHON" -m pytest -q testenv/test_study_root.py
env -u PYTHONPATH PYTHONPATH="$AECO_CORE_ROOT:$PWD" "$AECO_PYTHON" check.py
env -u PYTHONPATH PYTHONPATH="$AECO_CORE_ROOT:$PWD" "$AECO_PYTHON" -m pytest -q
```

## Acceptance

| Check | Result |
|---|---|
| Full `check.py` gate | 159 checks, 0 failed |
| Structure | 29 rules, 0 failed |
| Nested-root regressions | 11 passed |
| Full pytest | 343 passed, 5 optional skips |
| v0.5.2 stage roots | Project, Studies, Renders only |
| Project catalog | All 47 cameras use project-local types; a missing type is created there |
| Scope hierarchy | Studies, cctv, Looks, Targets and Studies are plain Scopes |
| Relationships and shader connections | All CCTV targets resolve under the configured layout or building |
| CLI and validator reopening | Complete, current results; layered and flattened stages work without the setting |
| v0.5.2 coverage | 11/11 critical doors; zero privacy exclusion hits |
| Source preservation | Every input USD layer retains its SHA256 |
| Default publication | Existing crate, editable layers, findings, manifest and images unchanged |
| Fresh default renders | Both study views and stock vanilla proof pass |
| Schema | Generated property contract unchanged |
| Byte retention | 25 committed USD, JSON, image and generated-schema assets unchanged |

## Deviations

- The full v0.5.2 delivery contains 47 cameras, compared with the older base
  fixture's 45. The hook verifies its census against the importer and still
  requires eleven door providers. No source cameras are removed.
- The default fixture retains its 0.5.3 receipts and the lobby retains its
  0.5.6 derivation stamps so published assets stay byte-identical. Ordinary
  CLI operations and configured hook runs stamp 0.5.7. The lobby gate compares
  fresh CLI/API bytes exactly and permits only tool-version receipt changes
  against the retained fixture.
- The additional integration cases skip if the separately pinned full
  delivery is absent. They execute in the recorded acceptance run.
- The full-delivery import reports 94 mirror-value conflicts in the seeded
  missing-type test; authoritative Tier A values are retained. Five existing
  optional tests skip. The opt-in real-data gate is not run.
- One offline Nix check with four direct local tag overrides resolved inputs
  and evaluated both Darwin package derivations, then refused the pinned
  `python3.14-ifcopenshell-0.8.0` package because nixpkgs marks it broken.
  Builds and Linux evaluation remain unproven; no retry or lockfile was written.
