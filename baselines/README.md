# Door-study baselines

[real-data-door-study.json](real-data-door-study.json) records the opt-in gate on a
data-centre security model of 457 cameras. It contains numeric summaries
only; no names, identifiers, coordinates or source paths are retained.

The gate promoted 457 cameras / 469 sensors, folded 469 FOV pictures and
reported zero unmatched objects. The 176 door cameras assessed 364 doors:
67 covered and 297 uncovered at 125 px/m. The complete Embree study took
110.663 s, so the 60 s performance target is **not met**.

Host-formula parity passes for all 469 half-angles and 2,345 radii. The
workbook has no FOV observations, so the exported IFC FOV properties supply
that evidence. The standard and host detect/observe thresholds differ;
26 standard-ladder radii exceed the 1 mm tolerance. Both comparisons remain
in the baseline.

Reproduce with the [opt-in gate](../docs/README.md#opt-in-data-centre-gate).
Intermediate model layers must remain outside version control. A fixture
run defaults to `artifacts/fixture-door-study.json`, preserving this baseline.

[facility-study.json](facility-study.json) records the generated demo data
centre with all 41 doors, 32 fixed views and the 29 generated cameras.
It retains every measured trial and strict acceptance flags. Reproduce it
with the [facility benchmark](../docs/performance.md); source stages and
IFC artifacts remain outside version control.

[facility-enclosed-study.json](facility-enclosed-study.json) records the 0.4.4
benchmark with enclosure classification enabled. Its single invocation retains
180 trials, exact uncropped parity and all 60 unchanged cached publications.
Fixed32 cold p95 is 0.971236 s; the older facility baseline remains historical.

[revit-import-performance.json](revit-import-performance.json) records 12
fresh-process imports for each of the previous and optimized algorithms.
All 24 kind layers match byte-for-byte; optimized p95 is 2.820268 s, with
45 cameras, 49 folded helpers, zero unmatched and three types. See the
[import benchmark](../docs/import-performance.md) for timing and stamp scope.

[contract-import-parity.json](contract-import-parity.json) records the 0.4.8
reader extraction: seven raw layer comparisons against the 0.4.7 algorithms
at the same release stamp, plus all importer and lobby semantic counters.
