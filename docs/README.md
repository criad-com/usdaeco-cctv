# usdAecoCctv schema, computation and validator reference

Version **0.5.0**, schema metadata **0.2.1**, kind tier, requires `usdAeco >=0.9,<1.0`.
The build pin is core **0.9.1**; this release was checked against core **0.9.1**
and toolchain **v0.2.1**. The authoritative source is
[schema.usda](../usdAecoCctv/schema.usda). All nine schemas are
applied APIs (eight single-apply, one multiple-apply); they introduce no typed
prims, identity, kind vocabulary or geometry. Kind stays classification
(`IfcAudioVisualAppliance.CAMERA`, Uniclass product codes); the APIs carry data.

Units are SI: metres for distances and offsets, **millimetres for optics** (as
datasheets and `UsdGeomCamera` write them), degrees for angles, px/m for pixel
density, seconds for dwell. Geometry and transforms use the stage's
`metersPerUnit`. In the tables, D means driver and R means derived. Every R
property carries `aecoDerived = true` in the schema (23 in total), never in
`customData` or authored stage data; every property's doc string states its
units, D/R status and host mirror ("DRIVER (unit). ... Host mirror: ...").

## S6 build findings

The toolchain codeless build on USD 26.8 expands `AecoCctvStudyAPI` on a
`Scope` into `CollectionAPI:targets`, `CollectionAPI:exclusions` and `CollectionAPI:cameras`: both
`collection:*:includes` relationships resolve without explicit application, so
the study keeps its built-in collections and no `cctvStudyCollectionsMissing`
validator is needed. `apiSchemaCanOnlyApplyTo = ["Camera"]` accepts
`UsdGeomCamera` and refuses `Mesh` and `Xform`, so no `cctvSensorNotCamera`
validator is needed either. Neither fallback of the design was required.

Class names omit the repeated `Cctv` prefix so the registered type of a
codeless class is `UsdAecoCctvSensorAPI` and the stage identifier
`AecoCctvSensorAPI`. IFC4X3_ADD2, inspected with IfcOpenShell 0.8.5, contains
`IfcWindow`, `IfcCurtainWall` and `IfcPlateTypeEnum.CURTAIN_PANEL`;
`IfcCurtainWallPanel` is not an entity and is omitted from the transparency
registry.

## The camera element: `AecoCctvCameraAPI`

Applies to `Imageable`, beside core `AecoElementAPI` and a classification. The
element's transform is the **device frame** (the host's placement: point,
rotation about Z, mirror). Optics and pose live on `Camera`-typed child prims.

| Property | Type · fallback | D/R | Meaning and host mirror |
|---|---|---|---|
| `aeco:cctv:mount` | uniform token · `undefined` | D | `ceiling`, `pendant`, `recessed`, `wall`, `corner`, `pole`, `parapet`, `desk`, `other`, `undefined`; mounting type / placement id |
| `aeco:cctv:scenario` | uniform token · `""` | D | operational-requirement purpose from `registries/scenarios.json`; warn-only vocabulary |
| `aeco:cctv:mountHeight` | double · 0 | R | height (m) of the first sensor's pivot above the level datum (see derivation) |

## Catalog types: `AecoCctvCameraTypeAPI`

Author beside `AecoTypeAPI` on an abstract catalog class whose `Sensor_n`
children are `Camera` prims wearing `AecoCctvSensorAPI` with the optics.
Occurrences use native `inherits`; the sensor children and their optics are
inherited, and the occurrence's own `Sensor_n` override carries the pose.

| Property | Type · fallback | D/R | Meaning and host mirror |
|---|---|---|---|
| `aeco:cctvType:outdoor` | bool · false | D | `Pset_AudioVisualApplianceTypeCamera.IsOutdoors` |
| `aeco:cctvType:irRange` | double · 0 | D | built-in illumination reach (m); the night far limit of a study; 0 = none |
| `aeco:cctvType:sensorCount` | int · 0 | R | number of `Camera` + sensor-API children of the type |

## Sensors: `AecoCctvSensorAPI` (applies to `Camera` only)

Type level (inherited): the datasheet quartet and mechanical reach.
Occurrence level: pose, zoom, design range, requirement and tour.

| Property | Type · fallback | D/R | Meaning and host mirror |
|---|---|---|---|
| `aeco:cctvSensor:projection` | uniform token · `rectilinear` | D | `rectilinear`, `fisheye`, `cylindrical` |
| `aeco:cctvSensor:spectrum` | uniform token · `visible` | D | `visible`, `thermal`, `radar` (no pixels, detection sector only) |
| `aeco:cctvSensor:focalRange` | double2 · (2.8, 2.8) | D | min/max focal length (mm); FOV Focal Length Minimum/Maximum |
| `aeco:cctvSensor:hfovRange` | double2 · (0, 0) | D | horizontal FOV (deg) at the minimum and at the maximum focal length, i.e. (wide, telephoto); (0,0) = from `sensorSize` |
| `aeco:cctvSensor:vfovRange` | double2 · (0, 0) | D | vertical FOV likewise; (0,0) = from hfov and the pixel aspect |
| `aeco:cctvSensor:sensorSize` | double2 · (0, 0) | D | physical sensor width/height (mm) when no FOV ranges are given |
| `aeco:cctvSensor:pixels` | int2 · (1920, 1080) | D | design stream resolution; (0, 0) for radar |
| `aeco:cctvSensor:offset` | double3 · (0, 0, 0) | D | lens pivot in the device frame (m); Origin Horizontal/Vertical |
| `aeco:cctvSensor:panRange` | double2 · (0, 0) | D | mechanical/motorised pan reach (deg); (0,0) = fixed |
| `aeco:cctvSensor:tiltRange` | double2 · (0, 0) | D | tilt reach (deg, positive down); (0,0) = fixed |
| `aeco:cctvSensor:motorised` | bool · false | D | PTZ head: presets, tours and the study's `ptzPolicy` apply |
| `aeco:cctvSensor:pan` | double · 0 | D | FOV Pan / Camera Rotation; ONVIF pan |
| `aeco:cctvSensor:tilt` | double · 0 | D | FOV Tilt / Camera Tilt, **positive down**; IFC `TiltHorizontal` negated |
| `aeco:cctvSensor:roll` | double · 0 | D | about the optical axis; 90 = corridor format |
| `aeco:cctvSensor:focalLength` | double · 0 | D | zoom state (mm), clamped to `focalRange`; 0 = widest |
| `aeco:cctvSensor:range` | double · 0 | D | design far limit (m), FOV Distance to Object; 0 = the range at which the requirement is met |
| `aeco:cctvSensor:targetDensity` | double · 0 | D | this view's requirement (px/m); 0 = the study's |
| `aeco:cctvSensor:tour` | uniform token[] · [] | D | ordered preset instance names of the guard tour |
| `aeco:cctvSensor:hfov` | double · 0 | R | horizontal FOV (deg) at the current focal length |
| `aeco:cctvSensor:vfov` | double · 0 | R | vertical FOV (deg) |
| `aeco:cctvSensor:effectiveWidth` | double · 0 | R | the sensor width (mm) reproducing `hfovRange` at the current focal length; authored into `horizontalAperture` |
| `aeco:cctvSensor:targetRange` | double · 0 | R | distance (m) at which `targetDensity` (or the study's requirement) is met under the density model |

The stock camera attributes (`focalLength`, `horizontalAperture`,
`verticalAperture`, `clippingRange`) and the sensor's xformOps are **derived**
and only ever written by the derivation (`cctvNativeCameraAuthored`).

## Presets: `AecoCctvPresetAPI` (multiple-apply, `Camera` only)

`apiSchemas = ["AecoCctvPresetAPI:Door_1"]` yields `aeco:cctvPreset:Door_1:pan`,
`:tilt`, `:focalLength` (0 = widest), `:dwell` (s, fallback 5) and `:home`
(bool) — the ONVIF preset list, bijectively. All drivers.

## Studies: `AecoCctvStudyAPI` (applies to `Scope`)

Built-in `CollectionAPI:targets`, `CollectionAPI:exclusions` and `CollectionAPI:cameras`.

| Property | Type · fallback | D/R | Meaning |
|---|---|---|---|
| `aeco:cctvStudy:phases` | uniform token[] · [proposed, existing] | D | which `aeco:phase` values count as obstacles and targets |
| `aeco:cctvStudy:includeUnphased` | bool · true | D | count elements without an authored phase, and non-element gprims, as obstacles |
| `aeco:cctvStudy:requiredDensity` | double · 125 | D | default requirement (px/m) |
| `aeco:cctvStudy:levelSystem` | uniform token · `dori2015` | D | ladder used to name densities (`registries/density_levels.json`) |
| `aeco:cctvStudy:densityModel` | uniform token · `plane` | D | `plane` or `arc` |
| `aeco:cctvStudy:ptzPolicy` | uniform token · `presetsNotSole` | D | `ignore`, `presets`, `presetsNotSole`, `envelope` |
| `aeco:cctvStudy:night` | bool · false | D | limit sectors to the type's `irRange` |
| `aeco:cctvStudy:glazingTransparent` | bool · true | D | registry glazing classes are transparent unless a sightline API says otherwise |
| `aeco:cctvStudy:excludeEnclosedSamples` | bool · `true` | D | exclude samples inside opaque bodies from target evaluation; counts remain visible with either setting |
| `aeco:cctvStudy:writeShells` | bool · `true` | D | author coverage shell meshes; false keeps numeric analysis and depth flags |
| `aeco:cctvStudy:raySamples` | int2 · (96, 54) | D | depth-map resolution per view |
| `aeco:cctvStudy:maxTargetDistance` | double · 0 | D | a target needs a covering view within this distance (m); 0 = off |
| `aeco:cctvStudy:mountHeightRange` | double2 · (0, 0) | D | lens pivots must lie in this band (m); (0,0) = off |
| `aeco:cctvStudy:inputHash` | string · `""` | R | hash of every input the last run read |
| `aeco:cctvStudy:stamp` | string · `""` | R | tool and version of the last run |
| `aeco:cctvStudy:unphasedInView` | rel | R | elements with no authored phase that a view reached |
| `aeco:cctvStudy:unclassifiedInView` | rel | R | non-element gprims that a view reached |
| `aeco:cctvStudy:exclusionsCovered` | rel | R | exclusions with a visible sample |

## Results, targets, sightlines, systems

`AecoCctvCoverageAPI` (on a `Scope` under the study's `Results/`, all R):
`target` (rel), `requiredDensity`, `density`, `level`, `fraction`,
`fixedCoverage`, `dutyFraction`, `nearestViewDistance` (m), `views` (rel),
`viewNotes`, `blockers` (rel), `enclosedSamples` (int). The study writes all
results in its own layer.

`AecoCctvTargetAPI` (Imageable, D): `requiredDensity` (unauthored = the study's; explicitly authored 0 = presence),
`points` (double3[], local metres; empty = the default five-point sampling).

`AecoCctvSightlineAPI` (Imageable, D): `transmittance` (0 opaque … 1
transparent), `ignore` (never an obstacle).

`AecoCctvSystemAPI` (`AecoSystem` only, D): `retentionDays`,
`recorderCapacity` (cameras per recorder). Membership is the core
`collection:members`; `aeco:serves` names the zones and spaces protected.

## Registries

`registries/density_levels.json`: `dori2015` (detect 25, observe 62.5,
recognise 125, identify 250), `oodpcvs2025` (overview 20, outline 40, discern
80, perceive 125, characterise 250, validate 500, scrutinise 1500) and an
example `project` ladder with a 180 px/m step. `registries/scenarios.json`
(twelve tokens), `registries/camera_classes.json`
(`IfcAudioVisualAppliance.CAMERA`) and
`registries/sightline_transparent_classes.json` (`IfcWindow`, `IfcCurtainWall`,
`IfcPlate.CURTAIN_PANEL`). Registries are data cadence and warn-only.

## Frames

```
world  --(element xformOps: host placement)-->  device frame
device --(T(offset) . Rz(pan-90) . Rx(-tilt) . Ry(roll) . Rx(+90))-->  sensor frame
sensor: USD camera convention, looks down -Z, +Y up
```

Pan 0 looks along device +X and pan rotates about device Z; tilt is positive
**down** from the horizontal, so the right-hand rotation about X is by −tilt;
roll is about the optical axis; the boresight `Rx(+90)` takes the camera's −Z
look to device +Y and its +Y up to device +Z. [`frames.sensor_matrix`](../tools/usdaeco_cctv/frames.py)
composes the chain as one `xformOp:transform` (Gf row vectors);
`frames.decompose` reads pan, tilt and roll back within 1e-9 degrees.

## Density

For a rectilinear head with `pixels = (H, V)`, focal length `f` and effective
width `w(f)` interpolated between `2 f_min tan(HFOV_wide/2)` and
`2 f_max tan(HFOV_tele/2)` (so datasheet FOV ranges are honoured), or taken
from `sensorSize`:

```
hfov = 2 atan(w / 2f)                      vfov = 2 atan(h / 2f)
plane:  rho(d) = H / (2 d tan(hfov/2))      d(rho) = H / (2 rho tan(hfov/2))
arc:    rho(d) = H / (hfov_rad d)           d(rho) = H / (hfov_rad rho)
```

`plane` is exact for a flat target facing the camera and is the default; `arc`
is the convention of vendor design tools and reproduces datasheet DORI
distances at the wide end. Fisheye and cylindrical heads take their angles
from the datasheet ranges and use the equidistant image (`w = f hfov_rad`),
which is the arc model. A head with `pixels = (0, 0)` (radar) has no density
and no level shells; its sector is a detection dome of the stated angles.
Level names come from the ladder: the highest threshold attained, else `none`.
[`density.py`](../tools/usdaeco_cctv/density.py) exposes `optics`,
`head_optics`, `effective_width`, `plane_range`/`arc_range`,
`plane_density`/`arc_density`, `ladder` and `level_name`.

## Derivation (Tier A)

`aeco-cctv derive <stage> [-o <layer>] [--model plane|arc] [--bare]` writes,
per sensor: `focalLength` (clamped zoom), `horizontalAperture` = effective
width, `verticalAperture`, `clippingRange = (0.05, range)`, one
`xformOp:transform`, the four derived attributes, and guide gprims under the
sensor — `Sector` (apex + 25 × 15 spherical-cap grid fanned to the apex,
closed), `Shell_<level>` for every ladder level whose range is inside the
sector (innermost first), `Envelope` for motorised heads (the pan × tilt
reach in the device frame, placed with the inverse of the sensor's rotation so
it stays put while the head moves). Guides are `purpose = guide`,
`subdivisionScheme = none`, double-sided, marked `AecoDerivedGeometryAPI`
(`source` = the camera's `aeco:id`, `role = sector` (density shells use
`coverage`), `approx = tessellated`, `stamp`), and bound to a `UsdPreviewSurface`
under `/AecoCctvLooks` with `opacity = 0.3` (cyan sector, teal shells, grey
envelope); `displayColor`/`displayOpacity` primvars carry the same colours.

The sensor's `range` driver is the sector radius; when it is 0 the radius is
`targetRange`, the distance at which `targetDensity` (else the explicitly selected study's
`requiredDensity`, else 125 px/m) is met under the density model (the
`--model` flag, else the explicitly selected study's `densityModel`, else `plane`). The
ladder is the explicitly selected study's `levelSystem`, else `dori2015`.

`aeco:cctv:mountHeight` is the first sensor's pivot height above the datum of
the `AecoLevel` the camera sits under (the level's placed origin, or its
`aeco:elevation` when it carries no xformOps); without a level ancestor, the
highest level datum at or below the pivot. `aeco:cctvType:sensorCount` is
authored as an `over` on each catalog type and reaches occurrences through
`inherits`.

A guard tour (`tour` on a motorised sensor) becomes time samples on the
transform, the camera attributes, the derived attributes, the sector and
shell points and the envelope transform: each preset is held for its `dwell`
(a sample at the boundary and one half a second before the next boundary), the
tour closes on its first preset, and the layer states `timeCodesPerSecond =
24`, `startTimeCode = 0` and `endTimeCode` = the tour length. Default-time
values are the sensor's own drivers. The tour clock is animation, not the
record tier's work time (E12).

The output layer sublayers the input stage by relative path and copies the
stage metadata a root layer needs (`defaultPrim`, `upAxis`, `metersPerUnit`,
`fallbackPrimTypes`), so it opens standalone in a plugin-free viewer; when the
input stack already composes the output layer, publication is refused.
Detach that layer first; `--bare` skips the input sublayer arc. The layer is marked `customLayerData
aeco:cctv:layer = "derived"` with the stamp, density model and ladder; it
carries no time of computation, so two runs are byte-equal. Drivers are read
from the stage as composed; properties flagged `aecoDerived` are read through
a view with `intent.usda` muted, so a derived opinion in an intent layer never
feeds the derivation. Muting the derived layer restores the stage.

## Analysis (Tier B)

See [facility performance](performance.md) for budgets, caches, timing fields
and the reproducible 30-trial benchmark.

[`study.py`](../tools/usdaeco_cctv/study.py) reads default-time drivers directly,
so coverage works before Tier A is derived. All ray coordinates and reported
distances are metres; mesh geometry and world translations are converted from
`metersPerUnit`. Sensor offsets and explicit target points are local SI metres.
The study does not use animated native camera transforms as pose drivers.

Obstacle gathering traverses default/render gprims, including instance proxies.
An element's authored phase must be selected. With `includeUnphased`, elements
with no authored phase and gprims outside elements count and are recorded when
rays reach them. The schema's fallback phase does not hide a missing authored
phase. Guide/proxy and space-extent geometry is excluded. Simple planar mesh
polygons use deterministic ear clipping; validated convex quads use a vectorized
path with the same winding and diagonal. Hole faces are omitted and unsupported
topology is refused. Cube geometry is exact, round solids are tessellated, and
other boundable gprims conservatively use their extent.

Sightline `ignore` removes an obstacle. Positive `transmittance` passes rays and
records `through` in view notes. The glazing registry supplies the default;
an explicit sightline API overrides it. A gprim override can distinguish glass
from its enclosing element's opaque frame. Through objects beyond the target
or behind the first blocker are not reported.

| PTZ policy | Views evaluated |
|---|---|
| `ignore` | fixed heads only |
| `presets` | fixed heads and each preset at its own pan/tilt/focal pose |
| `presetsNotSole` | same views; report targets whose covering views are all motorised |
| `envelope` | fixed heads and the motorised reach sampled every 15 degrees |

Motorised heads without presets contribute no view under the preset policies.
Night mode caps visible heads at their resolved occurrence/type `irRange`; zero IR range contributes
no night view. Thermal/radar heads retain their explicit range. Radar contributes
presence only where the requirement is zero. An explicitly authored target zero
is distinguished from the unauthored zero fallback so a presence target can sit
in a study with a positive default; this clarifies the original zero sentinel.

Each view gets a `raySamples` depth grid. With `writeShells = true`, it also gets
a closed `Coverage_<studyName>` guide mesh marked
`AecoDerivedGeometryAPI`, source = camera identity, role = `coverage`. Preset and
envelope sample names suffix the mesh; envelope mode additionally writes
`Coverage_<studyName>_Envelope`. Shells reset the inherited transform stack because they
record a world-space study snapshot, including for PTZ tours. The core currently
accepts the `coverage` role without a new core schema release.

Targets are collection members (an element and its expanded Body are one target).
The default samples are the bbox centre at 1.5 m and four inset corners; dimensions
are clamped for small targets. Each point moves 0.1 m horizontally toward the
containing space's bbox centre. Explicit points use the same offset. Coverage
requires a visible primary sample at the requested density. Results retain best
visible primary density even when it falls below the requirement, its ladder
name, the union of qualifying sample points, covering sensors/preset notes and
blocking elements. Result names disambiguate identical target basenames.

`fixedCoverage` records a qualifying fixed head. `dutyFraction` is 1 for fixed
coverage, otherwise the total dwell of covering presets divided by that sensor's
tour duration (including repeated tour entries). With multiple independent PTZ
heads it reports the best individual tour duty, without assuming synchronisation.
Envelope reach has no dwell guarantee. `nearestViewDistance` and mount-height
notes are measured per target. Any visible exclusion sample is recorded on
`aeco:cctvStudy:exclusionsCovered` and produces an error.

The layer has `aeco:cctv:study`, `aeco:cctv:tool`, `aeco:cctv:time` and
`aeco:cctv:inputHash` in `customLayerData`; internal view cache entries include
candidate sets and per-view hashes. The full SHA-256 covers resolved camera and
sensor drivers (including skipped heads), presets, type optics, study drivers,
registries, units, selected targets/exclusions and points, geometry/topology,
world transforms and phase/sightline decisions. Derived cameras, coverage,
result values and timestamps are excluded. Geometry and transforms are hashed
without rounding. Validators recompute only this hash, never coverage.

Candidate gprims pass vectorized world-bound tests against the far sphere and
frustum side/apex planes with a 0.1 mm margin enlarged for floating-point scale.
The near clip cannot reject blockers. Level labels never restrict candidates.
All rays to a partly included target remain candidates, even its outside samples.
Sheared camera bases conservatively retain every gprim.
A view hashes only its candidate obstacles and relevant target samples. An object
entering, leaving or moving inside that candidate set changes the view hash;
unchanged views reuse their serialized results and mesh. Kernel changes and
`--recompute` invalidate cached computation. Both kernels share `build`, `cast`
and `occluded`; numpy bounds ray/triangle chunks and Embree uses BVHs for depth
maps. Finite target/exclusion segments use a small exact numpy kernel after a
conservative segment-bound test. This avoids a new BVH per excluded target and
orders coincident blockers by source triangle order. Depth hits near flagged
bounds are also resolved exactly so BVH layout cannot change findings.
Candidate masks and owner exclusions have the same meaning in both kernels.

Python `run_study(stage, studyPath, output, kernel="numpy")` composes the output in
the session layer and preserves the caller's edit target. The CLI saves only the
bare analysis layer; compose it above the source in a normal stack. Existing
inputs and unrelated output files are protected. A failed run restores the
previous in-memory analysis. Muting the saved layer restores the input stage
bit-identically, including after a cached rerun.

Two implementation-plan assumptions require explicit deviations:

- **New prims need definitions.** Existing prims receive only `over` opinions;
  new result Scopes and Meshes use `def`. All-over new prims are undefined in
  USD, so normal traversal and imaging would omit the requested results and
  shells. Definitions introduce no new identity and disappear when muted.
- **The supplied tray is 11 m long.** Lowering it by 0.65 m intersects five view
  candidate sets and changes four sampled depth maps. Five views recompute and
  one reuses its cache. A local 0.8 m segment edit recomputes two and reuses four.
  Capping the full tray at two would leave stale coverage. Its PTZ-only Door_1
  is also farther than the example's 3 m limit, so V-tray correctly reports both
  `cctvPtzSoleCoverage` and `cctvTargetTooFar`.

Measured on the synthetic scene and lobby (Python 3.13, USD 26.8, embreex 4.4.0):

| Probe | Measured result |
|---|---|
| Full benchmark | 20,000 boxes, 240,000 triangles, 457 × 5,184 = 2,369,088 rays |
| Embree | build 0.233 s; cast 0.675 s; combined 0.908 s |
| Numpy benchmark subset | 20,736 rays × 24,000 triangles; build 0.001 s, cast 20.649 s |
| Numpy lobby study | about 0.2 s; six views, 31,104 depth rays |
| Lobby kernel parity | 31,104 rays; zero owner mismatches |
| Benchmark subset parity | 20,736 rays; zero owner mismatches |
| Full tray / local segment rerun | 5 / 2 views recomputed |
| Door_1 PTZ duty after full tray move | 6/16 = 0.375 |

The benchmark's full-scene numpy time is an explicitly labelled throughput
projection; only its subset is timed. See [the stored benchmark](../scenarios/benchmark.json).
`check.py` has 115 claims, including 30 scenarios; the release pytest run
contains 158 tests with both kernels installed. Nix was attempted once and could not resolve the nested
`aeco-toolchain` registry input. PyPI has no embreex sdist: the optional flake
derivation is configured to build the pinned [4.4.0 upstream source](https://github.com/trimesh/embreex/tree/4.4.0)
against `pkgs.embree`, with Cython and numpy. The default flake check uses numpy.

## Validators

[`validators.register()`](../tools/usdaeco_cctv/validators.py) registers
twenty-three rules under keyword **`UsdAecoCctvValidators`** with names
`usdAecoCctvValidators:Cctv<Rule>Checker`. `validate_stage(stage, include_core, include_builtin,
profile)` returns `UsdValidation.ValidationError` objects graded by a profile.

| Rule | Default | Condition |
|---|---|---|
| `cctvKindMismatch` | warn | camera API on a prim whose IFC entity is not `IfcAudioVisualAppliance` or a registry camera class |
| `cctvSensorMissing` | error | camera element without an active `Camera` child wearing the sensor API |
| `cctvSensorOrphan` | warn | sensor API on a prim whose parent is neither a camera element nor a catalog type |
| `cctvOutOfEnvelope` | error | pan/tilt outside a nonzero pan/tilt range, or a nonzero focal length outside the focal range; presets likewise (the message names the preset) |
| `cctvNativeCameraAuthored` | warn | stock camera attributes or xformOps on a sensor authored in a layer that is not a derived layer (by `GetPropertyStack` layer identity) |
| `cctvDerivedMismatch` | warn | authored `hfov`/`vfov`/`effectiveWidth` differ from a recomputation from the drivers by more than 1e-6 |
| `cctvMountFrame` | warn | ceiling/pendant/recessed mount with a sensor tilted upward; or a derived mount height outside a study's `mountHeightRange` |
| `cctvSystemCapacity` | warn | camera members of a system exceed `recorderCapacity` |
| `cctvStudyMissingResults` | info (error in `security`) | study has no authored result hash |
| `cctvStudyStale` | warn (error in `security`) | current input hash differs from the stored hash |
| `cctvTargetUncovered` | error | no view meets the primary-point requirement; message names blockers |
| `cctvTargetMostlyEnclosed` | warn | strictly more than half the samples lie inside opaque bodies; redefine the target region |
| `cctvPtzSoleCoverage` | warn (error in `security`) | only motorised views cover under presetsNotSole; message includes duty fraction |
| `cctvTargetTooFar` | warn (error in `security`) | nearest covering view exceeds maxTargetDistance |
| `cctvExclusionCovered` | error | an exclusion has a visible sample |
| `cctvUnphasedInView` | warn | a ray reaches an element without an authored phase |
| `cctvUnclassifiedInView` | warn | a ray reaches a gprim outside any element |

The wall-direction half of `cctvMountFrame` (a wall or corner mount whose
device X points into its wall) remains deferred: no relationship identifies
the mounting wall. The study records out-of-band lens heights in each covered
target's viewNotes. The rules evaluate default-time data. Profiles:
[`cctv.json`](../conformance/profiles/cctv.json) (the defaults above) and
[`security.json`](../conformance/profiles/security.json).

## Queries

| Query | Result |
|---|---|
| `iter_cameras(stage)` | active, defined occurrences with `AecoCctvCameraAPI` (catalog classes excluded) |
| `iter_studies(stage)` | study scopes in path order |
| `sensors_of(camera)` | active `Camera` children wearing the sensor API, inherited children included |
| `camera_type_of(prim)` | first inherited catalog class with the type API, or `None` |
| `presets_of(sensor)` | ordered `{name: {pan, tilt, focalLength, dwell, home}}` |
| `registry(name)` | a shipped registry as parsed JSON |

Tolerances: `ANGLE_TOLERANCE` 1e-6 deg, `LENGTH_TOLERANCE` 1e-6 m,
`DENSITY_TOLERANCE` 1e-6 px/m.

## Limits

Sync operations live in the optional sync library. Fisheye and cylindrical sectors are drawn as domes and
cylinders of the stated angles with the arc density; dewarped-view density is
deferred. A perspective `UsdGeomCamera` cannot show a 180-degree head; its
apertures are set from the equidistant width, an approximation. Thermal heads
use their `range` driver; there is no Johnson-criteria ladder. A study's
`night` mode and `irRange` are read by the coverage engine, not by the
derivation.

## IFC and COBie importer

[`import_cctv(core_stage, source_file, output="kind.usda")`](../tools/usdaeco_cctv/importer.py)
promotes camera facts above a core stage from `ifc2usdaeco`. The output
sublayers the core by relative path, opens on its own, and contains drivers,
catalog sensor children, quarantine value blocks and sub-instance
deactivations. Existing outputs and aliases of either input are refused.
All input files remain byte-identical. Compose `[kind, core]` in a review
root to mute the kind layer and recover the core composition exactly.

Only CAMERA audiovisual appliances qualify, including a CAMERA type inherited
by the IFC occurrence.
`IfcBuildingElementProxy` qualifies only with a camera optical parameter;
an ordinary equipment proxy is left alone. IFC classification remains the
exporter's class. A supplied `Classification.Uniclass.Pr.Number` is
promoted to `AecoClassificationAPI:uniclass`, giving the product its
dictionary classification alongside the export class.

The mapping matches original parameter names across **any Pset**, and the
reference converter's sanitized spelling (`FOV Pan` → `FOV_Pan`). The JSON contract wins, followed by the standard camera Pset; project aliases
then fill missing values. Within the project dialect, the first name below
wins. Raw source facts precede quarantined copies. Underscore-prefixed
internal formulas such as `_FOV Desired Focal Length` are not drivers.

| Source | Typed destination | Conversion / ownership |
|---|---|---|
| `Pset_AudioVisualApplianceTypeCamera.IsOutdoors` | `aeco:cctvType:outdoor` | bool; catalog housing, occurrence override when distinct |
| `VideoResolutionWidth/Height` | sensor `pixels` | pixel counts; standard Pset first |
| `TiltHorizontal` | sensor `tilt` | negate; IFC upward-positive to downward-positive |
| `PanHorizontal` | sensor `pan` | degrees, despite IFC's length declaration |
| `Zoom` | sensor `focalLength` | optical mm under this exchange convention |
| `PanTiltZoomPreset` | `AecoCctvPresetAPI:<name>` | IFC table: defining values = names, defined values = JSON row or `pan,tilt,focalLength`; tilt negated |
| `FOV Pan` / `FOV Camera Rotation` | sensor `pan` | degrees |
| `FOV Tilt` / `FOV Camera Tilt` | sensor `tilt` | degrees, positive downward |
| `Corridor Format` | sensor `roll` | true → 90°, false → 0° |
| `FOV Desired Focal Length` / `FOV Actual Focal Length` | sensor `focalLength` | optical mm; desired wins, derivation clamps |
| `FOV Distance to Object` | sensor `range` | source length → m |
| `FOV Target Pixel Density` | sensor `targetDensity` | px/m |
| `FOV n Pan/Tilt/Desired Focal Length` | occurrence `Sensor_<n-1>` | one sensor per head; inherited type optics |
| `Preset n` true + `FOV n …` | `AecoCctvPresetAPI:Preset_n` on `Sensor_0` | one motorised head; false presets are not applied |
| `Scenario` | `aeco:cctv:scenario` | lower case; checked against the scenario registry; unknown values stay quarantined |
| `FOV Focal Length Minimum/Maximum` | sensor `focalRange` | optical mm, `(wide, tele)` |
| `FOV Horizontal Maximum/Minimum` | sensor `hfovRange` | degrees, FOV at `(wide, tele)` focal settings |
| `FOV Vertical Maximum/Minimum` | sensor `vfovRange` | same ordering; abbreviated `Min`/`Max` also accepted |
| `FOV Horizontal/Vertical Resolution` | sensor `pixels` | catalog optics unless an occurrence differs |
| `Origin Horizontal/Vertical` | sensor `offset` | metres, `(horizontal, 0, vertical)` in the device frame |
| `Placement ID` | `aeco:cctv:mount` | 17160 wall, 17161 ceiling, 17162 pole, 17165 corner |
| `Mounting Type` | `aeco:cctv:mount` | mount word in the name; used only when the id is absent |
| Explicit `PTZ`, else focal ratio greater than 4 | sensor `motorised` | explicit false stays fixed; ratio is fallback evidence only |

IFC lengths honor explicit property units before project units; number-valued
project distances and origins conventionally use mm. Focal lengths remain
optical mm. Plane-angle measures honor radian/degree units; the anomalous
standard `PanHorizontal` remains a degree number. COBie `Unit` controls
source distances and origins, defaulting to mm.

Existing catalog class prims are found through `inherits`. If absent, the
importer creates `/<defaultPrim>/_TypeCatalog/Camera_<source-id>` with
`AecoTypeAPI` and `AecoCctvCameraTypeAPI`. Its `Sensor_n` Camera children
carry the sensor API and type optics. Occurrences inherit them and author
only differing drivers. Sensors receive no `aeco:id`, native camera
attributes or geometry; derivation supplies the latter two in its own layer.

Every promoted quarantine copy is value-blocked, including aliases shadowed
by a higher-priority source. Unknown names and incomplete optical pairs
survive. The schema has no shell-visibility driver for the DORI display
booleans; `Detect/Observe/Recognize/Identify/User-defined Pixel Density`
therefore remain quarantined. They do not change the target-density number.
Per-preset distance/density, per-head orbit, pendant/telescopic formulas,
radar-head inference and host formula-driven clipping are not promoted.

FOV and symbol proxies are recognized by their own name, type/family name,
Pset name, or `SuperComponent`; a camera's **choice of a symbol family**
does not make it a sub-instance. Generic helper labels also qualify when
they carry complete helper optics and `RG_Length_*` formula observations.
An explicit `IfcAudioVisualAppliance.CAMERA` remains a camera even when nested.
Ownership comes from `--subinstances` (JSON camera GUID → child GUID list),
then IFC `IfcRelAggregates`/`IfcRelNests` ancestry, then `SuperComponent`, then
a unique nearest camera within 0.5 m on the same known level. A complete
helper observation (focal/HFOV/VFOV ranges and resolution) restricts candidates
to matching sensor optics and permits a 1 m radius. Names never select owners.
Ties within 1 micrometre, different/unknown levels, distant pictures and missing
explicit owners stay unmatched. Cycles and multiple IFC camera owners refuse
publication. Resolved pictures are deactivated and their handles
move to the sensor. With the sync schema already registered the first
handle is `aeco:host:revit:ref`; without it, handles are stored in
`aeco:props:cctv:subInstances` string arrays. The sync schema has a scalar
`ref`, so additional preset/symbol handles also use that array. Register
sync before the first USD schema-registry access to enable its binding API.

The importer indexes camera positions in metre-sized cells per level, with a
1 micrometre tie halo beyond each search radius. Sensor optics are read once
and compared in numpy batches; distance arithmetic and UUID ordering remain
unchanged. Each IFC entity and shared Pset/property is read once per import,
and project units and conversion factors are cached within that opened model.
Ordered property-name indexes preserve alias precedence and quarantine blocks.
There is no retained IFC or USD cache between imports.

Identical legacy catalog records are consolidated deterministically onto the
smallest source type UUID. The complete type facts, core catalog properties,
type name and sensor layout must agree, including optics and unknown metadata.
Only occurrence `inherits` arcs change; source catalogs remain in the core
layer, and camera identities, placement and occurrence overrides remain intact.
Catalogs with children, composition arcs, relationships, animation or tier A
JSON are left intact. This pass addresses duplicated Revit symbols; it does
not infer product equivalence from optics alone.

The kind layer's `customLayerData` stores sorted JSON under
`aeco:cctv:typeMapping` (source type UUID → canonical UUID/catalog paths) and
`aeco:cctv:foldMapping` (helper UUID → owner, method, match status and distance
or refusal reason). The counters retain their existing seven-key interface.
See [the Revit import diagnosis](revit-import.md) for the measured demo export.

The COBie route reads `Component`, `Type`, and `Attribute` by header name,
with no fixed column order. Component `ExtIdentifier` or `IfcGUID` joins
the canonical `aeco:id`; `IfcGUID` may also be an Attribute. `TypeName`
selects type attributes, and Attribute `SheetName`/`RowName` identifies its
owner. Attribute `Category` may preserve a source Pset. A JSON table cell
represents presets. Duplicate core identities, duplicate camera identities,
ambiguous camera Component names, invalid numbers and malformed booleans
fail before publishing the layer. Standard camera attribute names also work
when `Category` is absent. The supplied workbook has two duplicated camera
names (four components), whose Attribute rows cannot be unambiguously joined;
the IFC export is the authoritative import route for this baseline.

The returned dictionary has exactly `cameras`, `sensors`, `presets`,
`types`, `propsBlocked`, `subInstancesFolded`, and `unmatched`. The synthetic
fixture asserts **3, 6, 2, 3, 137, 2, 0** respectively, in both IFC unit
variants and the matching COBie workbook.

The CLI adds `timingsSeconds` to those JSON counters. Python callers can pass
an empty `timings={}` dictionary to collect the same profile while retaining
the seven-counter return value. Exclusive wall-clock stages cover setup,
IFC opening, Pset reading/conversion, row assembly, source cleanup, stage
opening, identity traversal, quarantine reads, type matching, camera
authoring, helper matching/folding and output publication. `total` covers
the import through publication; core conversion is separate. Timing metadata
never enters the kind layer.

## Opt-in data-centre gate

[`tools/real_data_gate.py`](../tools/real_data_gate.py) runs against a data-centre
security model of **457 cameras**. It requires an explicit `AECO_REAL_DATA_ROOT`
holding `security.ifc`, `architecture.ifc`, `mapping.txt`, and `cobie.xlsx`;
it refuses the real-data route in CI. An optional `subinstances.json`
records the exported FOV ownership. No source model is committed.

The gate invokes the reference converter in a fresh process for each IFC,
aligns shared level names, composes the architecture as a sublayer of the
security model, promotes and derives cameras, then studies doors on the
security model's levels. The interior study selects `Scenario = door`
cameras, requires 125 px/m, uses `arc` and `presetsNotSole`, sets the door
distance rule to 3 m and the lens-height band to 2.3–3.0 m. A separate
study input layer removes other camera APIs while retaining their housing
geometry as obstacles. The original model and kind layers remain intact.

Core v0.8 handles IFC4 buildings and blank UI headings natively. The isolated
converter wrapper retains the geometry-before-USD process boundary and applies
no patches to the converter or IFC entities.

Parity compares independent host `Horizontal Angle` half-angles and clipped
`RG_Length_*` radii to optics computed from imported drivers. Tolerances are
1e-3 rad and 1 mm. The workbook does not carry those FOV observations, so
the supplied IFC FOV sub-instances provide the explicitly counted fallback.
The host formula ladder uses **26/62/125/250** px/m; the standard ladder is
**25/62.5/125/250**. Both radius comparisons are reported; the standard
registry is not changed to hide the difference.

The output `baselines/real-data-door-study.json` contains numeric summaries
only: population, input-preservation flags, conversion and study timings,
coverage counts per anonymized level, and parity minima/maxima. Intermediate
stages remain in a temporary directory; `--work /private/work/directory`
keeps them for local review and caches conversions against input SHA256s.
Each subsequent attempt gets a new directory for its derived artifacts.
`--fixture` runs the same pipeline on generated IFC4X3 models and COBie
observations, including three covered doors and independent angle/radius
oracles, without accessing the private model.

Recorded real-data acceptance: **457 cameras, 469 sensors, 8 enabled
presets, 108 catalog types, 10,792 promoted copies blocked, 469 sub-instances
folded, 0 unmatched, 0 derivation skips**. Nine levels matched by name;
the 176 door cameras assess 364 doors on five occupied levels. **67 doors
meet the density requirement; 297 do not.** The Embree run casts 912,384
rays against 3,274,934 triangles in **110.663 s**, exceeding the 60 s target.
The summary sets `studyWithin60Seconds = 0`; green import and parity claims
do not assert that the performance target passed.

All 469 half-angles agree within **3.721e-16 rad** and all 2,345 radii agree
within **6.395e-14 m** under the host's formula ladder. The standard ladder
differs on 26 radii, at most **0.406746 m**. There are zero COBie FOV
observations and 469 IFC fallback observations. All five private input files
are SHA256-unchanged. The [numeric baseline](../baselines/real-data-door-study.json)
contains the actual measurements.

To make the real model tractable, the coverage engine avoids full triangle
scans for distant transparent owners, avoids tracing self hits whose
through results are discarded, hashes numeric geometry buffers directly,
and reuses already-triangulated IFC face indices. Candidate selection,
hole handling, input invalidation, kernel parity and the reported door
results remain tested. These changes reduce unnecessary work without
approximating the model; the complete scene still exceeds the timing budget.

## Output publication

Derivation and studies compute in anonymous layers, validate them, then export
through a temporary file beside the destination and publish by atomic rename.
An output must be owned by the matching derivation/study and must be outside
the input layer stack, including symlink aliases. Failed runs preserve the
previous output and the caller's edit target. For a repeated Python study,
remove its previous output from the session sublayers before calling
`run_study` again; the successful call composes its output there. Unchanged
validated outputs retain their bytes and mtime. Studies default to binary usdc;
`--format usda` or an explicit `.usda` destination retains text. Shell authoring
is controlled by `aeco:cctvStudy:writeShells` (default true); the numerical study
and depth flags still run when it is false. See [performance](performance.md)
for cache invalidation and the measured publication contract.

## Phase policy

Cameras, sensor children, targets, exclusions and obstacles use the same
phase predicate. An authored phase must occur in the study's `phases`;
an unauthored phase is included only by `includeUnphased`. Sensor/body
children use their owning element's phase unless they author one themselves.
An admitted unphased camera or target reports `cctvUnphasedProvider` (warn).

## Complete results and security grading

A study has results only when its hash is present and every phase-eligible
member of `targets` has exactly one active result with a resolving target
relationship. Density, coverage fraction, duty and distance must be finite;
fractions lie in [0, 1], and level/fixed flags must agree with the ladder and
covering sensors. `cctvStudyIncomplete` warns by default and errors in the
security profile. A never-run study remains informational by default and
is an error in the security profile. Validators perform no ray casting.

## Study scopes

The built-in `collection:cameras` selects the providers for a study; an empty
includes list selects all cameras. Providers and mount-height bands respect
this collection and phase policy. Coverage shells use
`<sensor>/Coverage_<studyName>[_<preset>]`. Repeated study basenames receive a
stable path digest suffix. Day and night layers can therefore compose in
either order. Tier A defaults to plane/dori2015/125; `derive --model arc` or
`derive --study /Model/Analyses/Study` explicitly selects other settings.

## Mesh topology

Simple planar polygons use deterministic ear clipping with the source winding.
Hole faces are omitted, including meshes whose every face is a hole.
Malformed indices/counts, self intersections, degenerate faces and nonplanar
polygons stop a study with `cctvUnsupportedTopology` (error), naming the mesh.
The validation and triangulation happen before either numpy or Embree casts rays.

## Input fingerprints

The complete input hash and each view cache include `algorithmRevision = 4`
and library version `0.4.8`. Resolved occurrence optics and illumination
reach override their catalog values; occurrence IR therefore affects both
the hash and night coverage. Target/exclusion phase decisions are hashed
as well as geometry, collections and drivers. Reauthoring the same effective
driver value preserves the hash. Derived outputs and timestamps remain excluded.

## IFC camera interchange

The [versioned camera contract](ifc-camera-contract.md) defines the complete
reader interface. `Pset_AecoCctv` JSON (tier A) overrides the standard camera
Pset (tier B), which overrides the existing family/COBie dialect (tier C).
Occurrence JSON drivers override type JSON drivers. Unknown keys are ignored;
unknown contract major versions warn and fall back to B/C. Conflicting A/B
values warn with the property and both values while retaining A.

`PanHorizontal` remains degrees despite its template length type.
`TiltHorizontal` is converted from project angles and negated; length-valued
`Zoom` is converted to optical millimetres. Bounded values use the set point
or the mean of available bounds. Preset tables preserve sensor names, dwell,
home and the IFC tilt sign. JSON preserves every head and tour order; an
explicit empty preset set removes all presets.

Only CAMERA audiovisual appliances are promoted (including a CAMERA inherited
from their IFC type). Proxies need optical evidence. Explicit `PTZ=false`
keeps a varifocal head fixed. Missing source focal range, FOV/sensor size or
pixels raises `cctvMissingOptics` at the sensor during import; schema fallback
optics do not count as evidence. The importer publishes only after all cameras
have passed, and leaves its inputs unchanged.

## Explicit sampling

`AecoCctvTargetAPI` adds two drivers: `gridSpacing` (metres, zero retains
five-point sampling) and `passFraction` (0–1, zero retains the primary-point
rule). Explicit local points take precedence over a grid. A positive spacing
samples horizontal grid cell centres over the target bound, at the existing
1.5 m height above its base, clamped to its height. Use explicit points when
another height or approach-side schedule is required.

A positive pass fraction requires that proportion of points to meet the
resolved density across the union of views; fixed coverage requires the same
fraction across fixed views alone. Reported density is the density attained
at the requested fraction. Zero preserves all existing lobby semantics.
Exclusion errors carry target, study and responsible sensor sites and identify
preset views in the message. Uncovered findings also expose blocker sites.
Sampling is discrete: gaps smaller than the spacing are not certified clear.
Schema metadata is 0.2.1; the library is 0.4.8.

## Enclosed target samples

Before casting views, the study classifies every original door/area target
sample, including explicit points and five-point defaults, against its full
phase-filtered opaque obstacle set. AABB tests select possible bodies, then
ray parity tests three world axes using the selected numpy or Embree kernel.
An axis votes inside only when both directions have odd surface crossings;
at least two axes must agree. Coincident triangle hits at a face diagonal
count once. Separate gprim bodies are unioned, including bodies of one owner.
Hollow meshes retain their cavities. Extents, ignored objects and transparent
geometry do not enclose samples; the target's own opaque door leaf does.

`aeco:cctvStudy:excludeEnclosedSamples` defaults to true. Enclosed samples
are removed before visibility, density, pass-fraction, fixed/PTZ duty and
nearest-distance calculations. The remaining sample order is preserved;
with `passFraction = 0`, the first remaining point becomes the primary point.
False restores the original sample evaluation and denominator. Both settings
publish the derived integer `aeco:cctvCoverage:enclosedSamples` (fallback 0)
and JSON `enclosedSamples`, `sampleCount` (original) and `evaluatedSamples`.
Thus fraction is covered/evaluated, never covered/original when exclusion
is enabled. The toggle and classification participate in cache invalidation.

`cctvTargetMostlyEnclosed` warns, with target/study sites and enclosed/total
counts, when strictly more than 50% are enclosed, under either setting. It
asks for the region to be redefined. With no remaining samples the fraction,
density, duty and distance are zero, the level is `none`, and no covering
views or fixed coverage are claimed. Only the region warning applies to an
entirely enclosed target when exclusion is enabled; `cctvTargetUncovered`
still applies to retained uncovered samples. Validators read results and
sample counts without casting. Old result layers need a study rerun to
supply the new count and algorithm revision.

This is a conservative sampling heuristic for imperfect meshes. An opening
on one axis is tolerated; a shell without two agreeing axes remains eligible.
It does not certify watertightness or traversable floor area. Points on body bounds
are retained; coincident-surface tolerance is at least 4 micrometres and scales
with body size for Embree precision. Privacy exclusion collections keep their
existing visible-sample semantics. Finite grids still cannot certify gaps
between sample points. Scenario `V-enclosed-samples` covers lobby crates,
equipment pads, a cylindrical pipe, open shells, cavities and both kernels.

## Space extent geometry

A gprim with `AecoDerivedGeometryAPI` and `aeco:derived:role = extent` is never
an obstacle, independent of purpose or phase. When a Space/Scope target owns
extent gprims, their bound supplies its default samples; unrelated contents
are excluded. Grids are clipped to the projected extent footprint, preserving
concave boundaries. The same source selection applies to exclusions and to
the containing-space offset. Extent topology is included in the target hash.
Unowned gprims with other roles retain the unclassified-obstacle rule.

## Study projections

Coverage studies refuse included sensors whose projection is not `rectilinear`
with `cctvUnsupportedProjection` (error) naming the sensor and study. Tier A
can still draw the documented approximate fisheye/cylindrical guides; those
guides are not evidence of scene coverage.

## Regression gate

The family gate automatically runs all 31 entries in
`scenarios/cctv_cases.json`. The 15 added integrity cases use the same
executable assertions as pytest, with isolated inputs for every probe.
`check.py` also asserts version metadata, never-run grading, grid spacing,
fraction acceptance, projection refusal and exclusion attribution.

## Core 0.8 integration

Core `>=0.9,<1.0` is required; v0.9.2 is the pinned build source. The synthetic gate uses the IFC repository converter for IFC4 spatial
conversion and blank-heading handling; both local monkeypatches have been
removed. Tier-A sectors/envelopes use the core `sector` role, and density
shells and study coverage use `coverage`. Space extents may have `purpose =
guide`, as the converter writes them; they remain valid sample sources.


## Release limits and evidence boundaries

The [IFC contract](ifc-camera-contract.md) defines tier A > B > C and fixed
units. Studies accept rectilinear views; an unsupported projection is refused.
Explicit PTZ=false remains fixed even for a varifocal lens. Revit's current
export emits tier C parameters, not an authoritative tier A Pset. Its spaces
have no Common.Status. PTZ base poses and numbered preset poses are separate;
native tour/dwell/home parity is **not enforced** by the ordinary library gate.

The [performance baseline](../baselines/facility-enclosed-study.json) records 30 trials
per mode for 32 fixed views, 41 doors and 87,136 triangles: p95 cold 0.971236 s,
cached 0.027247 s and hash-only 0.004663 s; peak RSS 367.266 MiB. The strict
benchmark enforces 1.0/0.25/0.10 s and 1 GiB. These timing budgets are
**not enforced** by ordinary check.py or a Nix importer check. Generated-camera
cold p95 is 1.065166 s, a separate reference that misses the fixed32 budget.
The recorded benchmark disables shell meshes; ordinary studies write them.

Caches are process-local and invalidate on effective USD/registry changes.
No native BVH disk persistence is implemented. Unchanged cached publication
preserves output bytes and mtime. Detach the study output before rerunning;
input/output aliases and unowned output destinations are refused.

Finite door/grid samples do not certify unsampled areas. Lighting, compression,
recognition quality and recorder operation are **not enforced**. The demo's
critical door, corridor, yard/night and fixed lobby placement gaps remain
security findings; a passing implementation suite does not resolve them.
See [packaging acceptance](packaging.md) for current pins and Nix outcome.
