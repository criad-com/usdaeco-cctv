# Release 0.5.0 migration acceptance

Core v0.9.1 and toolchain v0.2.1 are the reference inputs; data-centre v0.4.1 supplies its published base stage. IFC v0.1.0 supplies only the synthetic conversion tests. [Exact pins](../dependencies.json) and public flake refs agree.

## Contract evidence

Sdf property names, types and defaults compare against v0.4.8: **nine APIs, 71 properties, no changes**. Registered type names and schema identifiers are unchanged. The CameraType API retains unrestricted application to catalogue classes and explicitly records `aecoApplicability = unrestricted` with its rationale. The existing suite is retained in `testenv/`; its actual source floor contains 21 test files, exceeding the earlier 19-file inventory.

The new Python plugin wraps all 23 legacy callbacks. Wrapper tests compare names, severities, messages and sites through `UsdValidation.ValidationContext`; the original negative fixtures exercise their rules. Derivation and study calculations remain unchanged. Mesh guides now declare `approx = tessellated` instead of the old `exact` metadata, as required by core E15; topology and computed values are unchanged. The USD reader is the small additional adapter needed to import the published quarantined data without rerunning conversion.

## Deviations

- Core E15 newly rejects the released Mesh guides marked `exact`. The rebase changes only their representation metadata to `tessellated`, preserving geometry and the schema contract.

- **Raw structure lint:** toolchain v0.2.1 rejects the released per-API namespaces (S09) and deliberately unrestricted CameraType API (S10). The schema keeps both published contracts. No local lint wrapper or compatibility correction is installed. **toolchain IMP4 (v0.2.2) relaxes S09/S10; re-run after merge**. The raw result is **24/26**, with those two failures visible in `check.py`.

- Legacy lowercase error codes are retained by `wrap_legacy`; ProperCase validator names and test identifiers map to those existing codes. New rules must use ProperCase errors.
- The shared example harness does not expose rendering purposes. The runner forwards its render call to `usdaeco_render` with `guide,proxy,render`; all finding comparisons, hashes, publication and image checks remain in the shared harness.
- Published USD legacy mirrors have lost some IFC measure-type information. The unit-explicit tier A camera JSON remains authoritative; mirror conflicts are retained in the transient import log.
- The opt-in real-data gate is **NOT RUN**, never a skipped-as-pass row. Its historical numeric evidence remains in `baselines/`. Files use a neutral real-data label and `AECO_REAL_DATA_ROOT`; the original environment variable remains a supported compatibility alias.

- Render output uses 960 × 600 pixels to keep the facility views within the 400 KB per-image contract.

## Verification

The release check retains all 123 prior claims and adds schema comparison, 26 structure rules and the pinned example checks. Native execution is reported separately by the optional compute library.

### Single Nix attempt

One attempt, exit 1, 2.903 s; inputs resolved through local registry/source overrides. Offline mode disabled substituters and remote builders and used `--max-jobs 0`. CCTV evaluation stopped because the pinned Nixpkgs marks Python 3.14 IfcOpenShell 0.8.0 broken; the importer check was retained. No second attempt or package installation was made.

### Published example measurements

- Import: 45 cameras, 45 sensors, three catalogue types, seven presets, zero unmatched identities.
- Derivation: 45 sectors and three tours. The geometric calculations are unchanged; Mesh approximation metadata follows E15.
- CriticalDoors: **11/11 fixed-covered**, **640/640 samples**, minimum target density **342.7882274350903 px/m**, requirement 250 px/m, plane density, dori2015, 11 views.
- Privacy: **zero exclusion hits**, 49 views. The study uses the published space bounds; see the committed findings and example sampling contract.
- Two owned renders: **960 × 600**, look-through **212,535 bytes**, overview **145,417 bytes**. The userDoc overview uses the same image.
- Source mode **pinned**, data-centre v0.4.1 base; all input layer hashes remain unchanged during the hook.

### Final release checks

Before the raw-lint correction, the functional gate was green; the final raw gate is recorded below. Pytest: **322 passed, 5 skipped** (53.53 s). The skipped cases are one optional sync binding, three opt-in facility performance cases and one unavailable native-export fixture; none is counted as a pass. The real-data gate is separately **NOT RUN**. The composed facility example also opens in a fresh process without family plugins, with every typed prim resolved through a stock fallback. [Numeric acceptance](rebase-acceptance.json).

### Raw lint correction

The final gate includes unmodified toolchain v0.2.1 results. All 127 functional/example/hygiene claims pass; raw structure is 24 passed / 2 failed (S09/S10), so the combined gate reports **153 checks, 2 failed**. These known lint incompatibilities are not hidden or reclassified as passes. Toolchain IMP4 (v0.2.2) relaxes S09/S10; re-run after merge. No repeated Nix attempt was made.
