# Import performance, 0.4.7

The demo data centre's Revit IFC imports at **p95 2.820268 s**, below the
3.0 s budget, across 12 fresh-process trials. The preceding algorithm's
p95 is 5.578668 s on the same input. All 24 outputs have identical bytes
and counters when written with the same library release stamp and relative
core-layer reference. [Full numeric evidence](../baselines/revit-import-performance.json)
retains every trial, input hashes, stage timings and environment versions.

| Measurement | Previous algorithm | 0.4.7 |
|---|---:|---:|
| Complete import p50 | 5.451453 s | 2.722574 s |
| Complete import p95 | 5.578668 s | 2.820268 s |
| Slowest import | 5.630290 s | 2.825724 s |
| Cameras / sensors | 45 / 45 | 45 / 45 |
| Folded helpers / unmatched | 49 / 0 | 49 / 0 |
| Types / presets / blocked properties | 3 / 7 / 910 | 3 / 7 / 910 |

Measured on an Apple M2 Max, 32 GiB RAM, 12 logical CPUs, Darwin 24.3.0
arm64, Python 3.13.12, USD 26.8, IfcOpenShell 0.8.5, numpy 2.5.3 and
compatible core 0.8.4. Core conversion was performed once, separately:
9.563 s, 1,514 elements, 35 spatial prims, 30 extents and zero unparented
elements. The full converted geometry remained loaded during every import.

Each trial starts a new Python process and calls the complete import API.
Wall time includes IFC opening, registration, stage composition, all source
reads, matching, authoring, validation/publication and function teardown.
Python process startup and core conversion are excluded. There is no warmup
or retained IFC/USD cache between trials. The operating system's filesystem
cache is not flushed; reference/optimized order alternates. Percentiles use
numpy's linear interpolation, and even the maximum is below budget.

## Where the time went

An initial unmodified import took 5.255 s. Its cProfile run counted
35,334,405 calls, including 2,841,588 name normalizations, 1,843 entity/type
Pset reads and 19,094 property-unit lookups. Profiling overhead is excluded
from the budget measurements. Exclusive p50 stage timings from the repeated
benchmark are:

| Stage | Previous, s | Optimized, s |
|---|---:|---:|
| IFC opening and reader imports | 0.990828 | 0.981864 |
| Pset reads and value/unit conversion | 1.576822 | 0.604707 |
| IFC row assembly | 0.065507 | 0.063698 |
| Core stage opening | 0.152468 | 0.154911 |
| Prim identity traversal | 0.018580 | 0.018675 |
| Quarantine snapshot | 0.381851 | 0.461834 |
| Type matching | 0.470996 | 0.138775 |
| Camera classification and authoring | 1.085146 | 0.185607 |
| Helper ownership, matching and folding | 0.567769 | 0.051429 |
| Writing and publication | 0.029862 | 0.029732 |

The JSON also records setup, source cleanup and total timing. Pset time is
the sum of the per-entity read intervals; it is subtracted from row assembly
so stages do not overlap. Stage medians need not sum to the median total.
The CLI prints these under `timingsSeconds`; Python callers pass `timings={}`
while retaining the existing seven-counter return value.

The changes address repeated work: an ordered normalized-name index for
fact lookup; per-opened-model caches for entity facts, shared Psets and
properties, project units and conversion factors; one camera-class registry
read per import; and metre-sized spatial cells keyed by level. Sensor optics
are cached once and compared in numpy batches. Search includes the 1 micrometre
tie halo, retains exact Gf distance arithmetic and uses the previous stable
UUID ordering. Ownership, optical tolerances and radius rules are unchanged.

## Reproduce

Follow the [build instructions](../README.md#build-and-check), supply the
existing demo export and use new output destinations:

```sh
export AECO_DEMO_REVIT_IFC=../usdaeco-datacentre/out/revit/demo-datacentre-01-revit.ifc
mkdir -p artifacts/import-profile
env -u PYTHONPATH "$AECO_PYTHON" tools/usdaeco_cctv/gate_conversion.py \
  "$AECO_DEMO_REVIT_IFC" artifacts/import-profile/core.usda
env -u PYTHONPATH "$AECO_PYTHON" tools/bench_import.py \
  --core artifacts/import-profile/core.usda --source "$AECO_DEMO_REVIT_IFC" \
  --out artifacts/import-benchmark --trials 12 --enforce
env -u PYTHONPATH "$AECO_PYTHON" -m pytest -q \
  testenv/test_importer_performance.py testenv/test_revit_importer.py
```

The benchmark requires Git history containing the instrumentation-only
revision `b35622a9c18f69a2f22d3d54e7075f21caf4e4da`; fetch the full PR history
if using a shallow checkout. It refuses missing history, differing output
hashes/counters, modified inputs, fewer than ten trials and existing output
directories. `--enforce` exits nonzero on p95 >= 3.0 s. Historical comparisons
in pytest explicitly skip without Git/history; the demo integration test
also requires the supplied export. Spatial boundary tests remain portable.
The source IFC and intermediate stages are not distributed with the library.

## Deviations and limits

- The library metadata stamp must advance from 0.4.6 to 0.4.7. Before that
  bump, the optimized layer was byte-identical to the unmodified release
  (`033b8d98a36731478d99db901a91b7a157c1a33e703e258b2bc8c2e858bc7833`).
  The benchmark loads both algorithms against 0.4.7 and compares raw output
  bytes without normalization. Their shared hash, with the documented layout,
  is `49bd74a3bc99aee644e692b5d525953ec1bc68f5a3a5efe87fe30598682b9227`.
  Moving the core relative to the output changes the authored sublayer arc
  and therefore the whole-file hash.
- The 3 s result is a reference-machine import measurement, not a limit
  enforced by ordinary check.py or pytest. It does not include conversion,
  derivation, studies, native Revit execution or a cold filesystem cache.
- Scope remains conservative: helper proximity is geometric association;
  it does not recover native parent identities. Product catalog sharing
  still requires complete equivalent records.
