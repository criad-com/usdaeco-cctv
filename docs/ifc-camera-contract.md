# usdAeco CCTV — IFC camera interchange contract, version `usdaeco-cctv-ifc/1.0`

Purpose: one encoding of a camera element, its catalog type, its sensors (heads), presets and pose in IFC4 or IFC4X3 such that
(a) the core converter + the `usdaeco-cctv` importer reconstruct the USD model from the IFC file alone (no USD stage, no cached receipt), and
(b) the `usdaeco-sync` IFC host writes and reads exactly the same thing. Vendor-neutral. This file is copied verbatim into
`usdaeco-cctv/docs/ifc-camera-contract.md` by W1 and referenced by sync (W4) and the generator (W3).

## 1. Entities
- Catalog type: `IfcAudioVisualApplianceType`, `PredefinedType = CAMERA`, `Name` = the catalog type's name (e.g. `Dome_5MP`). Optional body
  representation (`Body`, MappedRepresentation) reused by occurrences.
- Occurrence: `IfcAudioVisualAppliance`, `PredefinedType = CAMERA`, related to its type by `IfcRelDefinesByType`; `GlobalId` is the element's
  identity (`aeco:id` ↔ 22-char GlobalId, the family's usual conversion); `Name` = the prim name.
- Placement: `ObjectPlacement` is the **device frame** (local +X = the pan-0 reference direction, local +Z = up), placed relative to its
  container. Pan/tilt/roll are NOT baked into the placement. No FOV geometry is exported; host FOV sub-instances (Revit) are optional
  observations, never required.
- Containment: `IfcRelContainedInSpatialStructure` to the space it is mounted in, else the storey, else the site (exterior).
- System: membership of an `IfcDistributionSystem` `PredefinedType = SECURITY` via `IfcRelAssignsToGroup` (optional).
- Sensors (heads) carry no identity; they are the ordered list in tier A below and become `Sensor_0 … Sensor_n` children in USD.

## 2. Property sets — three tiers; readers apply precedence A > B > C
### Tier A (authoritative, unit-explicit): project Pset `Pset_AecoCctv`
Text properties holding JSON (`IfcText`). Units are fixed by the USD schema and independent of `IfcUnitAssignment`: angles in **degrees**
(tilt **positive down**), focal lengths in **millimetres**, ranges/offsets in **metres**, densities in **px/m**, dwell in **seconds**.
Keys are the USD attribute names. Readers ignore unknown keys; writers write every driver they know.
- On the TYPE: `Contract` = `"usdaeco-cctv-ifc/1.0"`; `Type` = JSON object of type drivers (`aeco:cctvType:outdoor` bool, `aeco:cctvType:irRange` m,
  `aeco:type:model` str, `aeco:type:manufacturer` str); `Sensors` = JSON list, one object per head in order:
  `{"name": "Sensor_0", "drivers": {"aeco:cctvSensor:projection": "rectilinear", "aeco:cctvSensor:spectrum": "visible",
  "aeco:cctvSensor:focalRange": [3.0, 8.5], "aeco:cctvSensor:hfovRange": [104.0, 34.0], "aeco:cctvSensor:vfovRange": [76.0, 26.0],
  "aeco:cctvSensor:sensorSize": [0, 0], "aeco:cctvSensor:pixels": [2592, 1944], "aeco:cctvSensor:offset": [0, 0, -0.08],
  "aeco:cctvSensor:panRange": [0, 0], "aeco:cctvSensor:tiltRange": [0, 0], "aeco:cctvSensor:motorised": false}}`.
- On the OCCURRENCE: `Contract`; `Drivers` = JSON `{"aeco:cctv:scenario": "door", "aeco:cctv:mount": "ceiling"}`; `Sensors` = JSON list with the
  same names and order as the type: `{"name": "Sensor_0", "drivers": {"aeco:cctvSensor:pan": 90.0, "aeco:cctvSensor:tilt": 45.0,
  "aeco:cctvSensor:roll": 0.0, "aeco:cctvSensor:focalLength": 3.0, "aeco:cctvSensor:range": 12.0, "aeco:cctvSensor:targetDensity": 0.0},
  "presets": {"Home": {"pan": -45.0, "tilt": 20.0, "focalLength": 7.0, "dwell": 4.0, "home": true}}, "tour": ["Home", "MainDoor"]}`.
  Occurrence-level datasheet overrides are allowed in the occurrence `Sensors.drivers` and win over the type (the USD override rule).
- Booleans/tokens/strings are JSON booleans/strings; vectors JSON arrays; all numbers finite.

### Tier B (interoperability mirror of head 0): standard `Pset_AudioVisualApplianceTypeCamera` (on the occurrence; type optional)
Template types (IFC4/IFC4X3) and the rule for each:
- `CameraType` (enum) = `VIDEO`; `IsOutdoors` (IfcBoolean) = type `outdoor`.
- `VideoResolutionWidth` / `VideoResolutionHeight` (IfcInteger) = head 0 `pixels`.
- `PanHorizontal` (template type IfcLengthMeasure — an upstream template defect): the numeric value is **degrees**; writers write the degrees
  number; readers must NOT apply the project length unit to it.
- `TiltHorizontal` (IfcPlaneAngleMeasure): IFC tilt is **positive up**: value = −tilt converted to the project's plane-angle unit
  (SI radian, or a conversion-based degree unit). Readers convert back using `IfcUnitAssignment`.
- `Zoom` (IfcPositiveLengthMeasure): head 0 focal length converted from mm to the project length unit; readers convert back.
- `PanTiltZoomPreset` (table): `DefiningValues` = `IfcIdentifier` `"Sensor_n:PresetName"`; `DefinedValues` = `IfcText` JSON
  `{"pan": deg, "tilt": deg (IFC sign, positive up), "focalLength": mm, "dwell": s, "home": bool}`. Writers rewrite the whole table on every
  change; an empty preset set writes an empty table (both value lists empty). Readers treat a missing or empty table as "no presets".
- Bounded-value templates may be written as single values (set point); readers accept `IfcPropertySingleValue` and
  `IfcPropertyBoundedValue` (set point, else the mean of the bounds).

### Tier C (read-only host dialects, unchanged): Revit family parameters exported as Psets (`FOV Focal Length Minimum/Maximum`,
`FOV Horizontal/Vertical Maximum/Minimum`, `FOV Horizontal/Vertical Resolution`, `FOV Pan/Tilt`, `FOV Camera Rotation/Tilt`, `FOV n …`,
`Preset n`, `FOV Desired/Actual Focal Length`, `FOV Distance to Object`, `FOV Target Pixel Density`, `Origin Horizontal/Vertical`,
`Corridor Format`, `Placement ID`, `Scenario`, `PTZ`) and COBie attributes, as the importer already reads them.

## 3. Precedence and conflicts
For one driver: tier A wins, then B, then C. When A and B both carry a value and disagree beyond 1e-6 (deg, mm, m) the reader keeps A and
reports a warning naming the property and both values. A camera with no optics in any tier is an **error at import** naming the sensor
and the missing drivers (never a silent default). Non-CAMERA `IfcAudioVisualAppliance` are not cameras.

## 4. Phase and status
`Pset_AudioVisualApplianceTypeCommon.Status` on the occurrence (`NEW` → proposed, `EXISTING` → existing, `DEMOLISH` → demolished,
`TEMPORARY` → temporary) is the phase, exactly as every other product's `Pset_*Common.Status` (core converter rule, decision D4). Absent → unauthored.

## 5. Round-trip acceptance (the test both libraries run)
Fresh IFC written by the sync host or the generator → core converter → cctv importer → derive reproduces every type and occurrence driver
within 1e-6 (deg, mm, m), exact pixels, tokens, booleans, every preset with dwell/home, the tour order, the scenario and mount, in four
project-unit combinations: (mm, degree), (m, degree), (mm, radian), (m, radian). Removing the last preset leaves none after reopen.

## 6. Versioning
`Contract` values `usdaeco-cctv-ifc/1.x` are accepted by 1.0 readers; an unknown major version yields a warning and tier B/C reading only.

## Reference Python reader

Since 0.4.8, `usdaeco_cctv.contract` is the reference implementation of tiers
A/B. Importing it requires only ifcopenshell and the standard library; it
does not load USD, register plugins or write files. In a source checkout, add
`tools` to Python's module search path:

```python
import sys
sys.path.insert(0, "tools")
import ifcopenshell
from usdaeco_cctv.contract import read

model = ifcopenshell.open("camera.ifc")
camera = model.by_type("IfcAudioVisualAppliance")[0]
data = read(camera)
head = data["sensors"][0]
print(head["drivers"]["aeco:cctvSensor:tilt"])
```

`read(entity)` returns a JSON-compatible dictionary with `type` (`drivers`
and ordered `sensors`), effective housing `drivers`, and ordered occurrence `sensors`.
Each occurrence head contains `name`, effective `drivers`, `presets` and
`tour`. Driver keys are the full USD attribute names; vectors are lists.
Angles are degrees with tilt positive down, focal lengths millimetres,
ranges/offsets metres, densities px/m and dwell seconds. Type data is retained
separately so consumers can author sparse occurrence overrides. It does not
invent missing optics or perform camera classification and stage validation.

The importer uses this module's fact interface (`Fact`, `Values`, `decode`,
`drivers`, `optics`, `pose`, `preset_rows`) to retain the set of promoted
properties for quarantine blocking. Its IFC fact reader retains explicit
property units and caches only within one model/read pass. `load`, `mirror`,
`prefer`, `standard_values` and `table_presets` expose the same tier A/B rules
to sync; sync retains its own writer and host-specific tier C mappings.
`exchange` handles USD attribute coercion only. Unknown driver keys are
ignored and unsupported contract majors emit a warning; installed-reader
errors are not silently replaced with fallback results.

Run the independent reader and importer round-trip tests from the checkout:

```sh
env -u PYTHONPATH "$AECO_PYTHON" -m pytest -q testenv/test_contract.py testenv/test_exchange.py
```

The tests cover four project-unit combinations, explicit property units,
head overrides, conflict warnings, removed presets and a subprocess that
refuses every USD import. The importer round trip additionally requires the
built core/CCTV schemas as described in the root README.
