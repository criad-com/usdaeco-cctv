# Release 0.5.2 publication acceptance

The committed [result](../examples/datacentre/result/README.md) composes the pinned data-centre v0.4.1 base stage. Open `examples/datacentre/result/example.usdc` in stock USD with no family plugins or sibling checkouts. The accompanying USDA layers preserve the example's own opinions; the vanilla PNG proves a fresh-process stock USD render with `proxy,render` purposes.

The two existing study renders and expected findings are byte-identical to v0.5.1. The camera derivation is partitioned into six USDA layers for the 2 MB per-layer cap; its complete composed USDA serialization is identical before and after splitting. The CriticalDoors and Privacy shell generation, camera poses and presentation are unchanged. This example supplies a fixed receipt time of `2026-09-11T00:00:00Z` to the study writer so archived layers reproduce byte-for-byte; normal study commands retain real receipt times. The lobby derivation is refreshed only for the new tool version stamp. A transient source-directory link keeps archived wrapper paths independent of dependency checkout locations and is excluded from publication.

Verification uses core v0.9.1, IFC v0.1.0 and data-centre v0.4.1 from exact tagged sources, plus the existing core v0.9.1 resource plugin. No dependency checkout is modified. Python imports the core validator module explicitly; the gate requires all eight core rules to load and proves E15 execution with seeded exact Mesh failures. Publication freshness and independent stock rendering run through the unmodified shared `check_example()` and S27/S28 checks.

## Verification

| Claim | Verified result |
|---|---|
| Acceptance gate | **158 checks, 0 failed**; all 156 preceding rows retained plus S27/S28 |
| Raw structure rules | **28/28 PASS**, including MIT (S01), term sweep (S25), relocated stock USD open (S27), independent stock render (S28) |
| Freshness | Shared `check_example()` PASS; normalized crate and all own layer bytes match a fresh run |
| Core validation | **8/8** rules loaded; seeded exact Mesh failures caught in both examples; no new source findings |
| Pytest | **332 passed, 5 skipped** in 60.60 s; no installed package or setuptools required |
| Repository term sweep | **133 text files, 0 matches**, including all archived USDA layers; required MIT attribution retained |
| Complete result | **18 files; 7,934,911 bytes / 10,000,000 cap** |
| Flattened crate | **1,930,455 bytes; 12,680 prims**, complete stock fallbacks, metres/Z-up, no composition errors or external assets |
| Archived own layers | **15 USDA files; 5,863,406 bytes** total; largest **790,098 / 2,000,000 bytes** |
| Vanilla PNG | **960 × 600; 140,036 / 400,000 bytes**, non-uniform; fresh process has no family plugins |
| Original study PNGs | Overview **165,751 bytes**; look-through **240,303 bytes**; both byte-identical to v0.5.1 |
| Schema | Source and generated schema byte-identical to v0.5.1; **9 APIs / 71 properties** unchanged |
| Findings and camera inputs | Byte-identical to v0.5.1 |
| Coverage | **11/11** fixed-covered doors; **640/640** samples; **0** privacy exclusion hits |
| Clipped shells | **11** CriticalDoors and **49** Privacy shells; displayed hall projection **0.1533 m²**, nominal sectors **723.4732 m²** |
| Camera layer partition | Full composed USDA serialization byte-identical before and after splitting |
| Lobby fixture | Only **13** tool-version stamps changed |
| Nix | **1 attempt**, exit **1** during input resolution; not proven |

## Deviations

- Toolchain v0.3.2 replaces the requested v0.3.1 pin. Version 0.3.1 requires Apache-2.0 in S01 and rejects the mandated MIT copyright notice in S25. Version 0.3.2 adds MIT support while retaining the publication contract; no local lint waiver is used.
- One `nix flake check` attempt exited 1 during source resolution: the public core v0.9.1 ref returned HTTP 404. Nix build/check results are **not proven**; no retry or installation was made.
- The inherited source contains two classification health warnings. The core gate compares their complete findings with the untouched source and rejects any introduced finding. Derived examples have no core errors.
- The stock render excludes guide-purpose geometry by S28. Both original study renders, including their clipped shells, are retained byte-for-byte; only the new standalone proof image is added.

The five optional pytest skips and opt-in real-data gate are not counted as passes. Geometry establishes sampled visibility and pixel density, not lighting quality, recognition performance or coverage between samples.
