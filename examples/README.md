# A lobby with three doors, three domes and a PTZ

Open [lobby.usda](lobby.usda) for the model and [lobby.derived.usda](lobby.derived.usda)
for the model plus its derived layer (it sublayers `./lobby.usda`). The lobby
is 12 × 8 × 3.5 m with seven wall pieces, three doors (`Door_1` north,
`Door_2` south, `Door_3` east), a desk, a column and a cable tray at
z 3.2–3.3 m crossing the room. Every element is an `Xform` with
`AecoElementAPI`, an IFC classification, a phase and a `Body` mesh marked
`AecoDerivedGeometryAPI`. All ids are `uuid5` of the prim path under
`urn:usdaeco:id:v1`.

```text
        Door_1 (6, 8)                 Cam_4  corner PTZ at (0.3, 7.7, 3.0)
  +-----------====---------------+    presets Home, Door_1, Door_3; tour 16 s
  |  Cam_1 (6, 5.5)   tray      |
  |                  Column     ||  Door_3 (12, 4)
  |  Cam_2 (3, 2.5)  Desk       ||     Cam_3 (9.5, 4)
  +----====----------------------+
     Door_2 (3, 0)
```

Three ceiling domes inherit `/_TypeCatalog/Dome_P3277` (focal range 3–8.5 mm,
104°–34° × 76°–26°, 2592 × 1944 px); each sits 2.5 m from its door at 3.3 m,
pan toward the door, tilt 40° down, 3 mm, design range 14 m. The corner PTZ
inherits `/_TypeCatalog/Ptz_Q6088` (6.64–225.5 mm, 60.8°–2° × 36.5°–1.1°,
3840 × 2160 px, motorised, pan ±180°, tilt 0–90°) with presets `Home`
(4 s), `Door_1` (12 mm, 6 s) and `Door_3` (16 mm, 6 s). `/CctvLobby/Cctv` is
the `AecoSystem` (retention 90 days, 45 cameras per recorder, four members,
serving the lobby space). `/CctvLobby/Analyses/DoorCoverage` is the study:
targets = the three doors, 125 px/m, `plane`, `presetsNotSole`,
`maxTargetDistance 3`; its `Results/` are produced by the coverage engine of a
later work package.

The derived layer gives every sensor its camera attributes (Cam_1: 3 mm,
aperture 7.68 mm, hfov 104°, `clippingRange (0.05, 14)`), one transform,
`targetRange` 8.1 m at 125 px/m, a cyan `Sector` and the teal
`Shell_identify` (4.05 m) and `Shell_recognise` (8.1 m); the PTZ adds an
`Envelope` and a tour of 384 time codes at 24 per second with samples at
0/96/240/384. `mountHeight` is 3.22 m for the domes and 2.85 m for the PTZ.
Turn on guide geometry in a viewer to see the sectors; look through
`/CctvLobby/Site/Building/L0/Lobby/Cam_1/Sensor_0` to see the door; play the
time range to watch the PTZ tour. The cctv, core and built-in validators
report **0 errors, 0 warnings** on both layers under both profiles.

To reproduce after following the [root setup](../README.md):

```sh
env -u PYTHONPATH "$AECO_PYTHON" tools/build_example.py
env -u PYTHONPATH "$AECO_PYTHON" tools/aeco-cctv derive examples/lobby.usda -o examples/lobby.derived.usda
env -u PYTHONPATH "$AECO_PYTHON" check.py
```

`check.py` edits anonymous copies: a wrong IFC code gives `cctvKindMismatch`;
deactivating a sensor gives `cctvSensorMissing`; 12 mm on a dome or a preset
tilt of −5° gives `cctvOutOfEnvelope`; a `focalLength` or xformOp authored
above the derived layer gives `cctvNativeCameraAuthored`; editing a driver
without re-deriving gives `cctvDerivedMismatch`; an upward dome tilt or a
`mountHeightRange` of 2.4–2.9 m gives `cctvMountFrame`; capacity 3 gives
`cctvSystemCapacity`. The committed example stays valid.
