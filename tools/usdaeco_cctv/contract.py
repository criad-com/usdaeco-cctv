"""Reference reader for usdaeco-cctv-ifc/1.x, with no USD dependency.

Entities become ordinary Python driver dictionaries in degrees (tilt down),
millimetres (focal lengths), metres (ranges/offsets), px/m and seconds.
``read(entity)`` reads tiers A/B; host dialects can use ``optics``/``pose``
and the fact-level interface used by the importer to track promoted facts.
Only ifcopenshell and the standard library are required. No schema registration
or file writes occur. A fact cache belongs to one opened IFC model/read pass.
"""
from collections import defaultdict
import copy
from dataclasses import dataclass
from functools import lru_cache
import json
import math
import re
import warnings

CONTRACT = "usdaeco-cctv-ifc/1.0"
PSET = "Pset_AecoCctv"
STANDARD = CAMERA_PSET = "Pset_AudioVisualApplianceTypeCamera"
SENSOR = "aeco:cctvSensor:"
HOUSING = {'aeco:cctv:mount', 'aeco:cctv:scenario', 'aeco:cctvType:outdoor',
           'aeco:cctvType:irRange', 'aeco:type:model', 'aeco:type:manufacturer'}
SENSOR_DRIVERS = {'projection', 'spectrum', 'focalRange', 'hfovRange', 'vfovRange', 'sensorSize',
                  'pixels', 'offset', 'panRange', 'tiltRange', 'motorised', 'pan', 'tilt', 'roll',
                  'focalLength', 'range', 'targetDensity', 'tour'}


def _norm(value):
    # Matches the reference converter's sanitized property names as well as
    # the original names in IFC and COBie (spaces, punctuation and case).
    return _normal_text(str(value))


@lru_cache(maxsize=8192)
def _normal_text(value):
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _present(value):
    return value is not None and str(value).strip().lower() not in ("", "n/a", "na", "none", "null")


def _number(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Camera parameters must be finite")
    return result


def _bool(value):
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "yes"):
        return True
    if text in ("0", "false", "no"):
        return False
    raise ValueError("Invalid camera boolean: " + repr(value))


@dataclass
class Fact:
    pset: str
    name: str
    value: object
    # Source lengths converted to metres; optical focal lengths stay mm.
    length_scale: float = 0.001


class Values:
    """Ordered facts and the property names actually given a typed home."""
    def __init__(self, facts):
        self.facts = facts
        self.used = set()
        self.by_name = defaultdict(list)
        for fact in facts:
            self.by_name[_norm(fact.name)].append(fact)

    def pick(self, *names, pset=None, convert=None):
        matches = [f for name in names for f in self.by_name.get(_norm(name), ())
                   if _present(f.value)
                   and (not f.name.startswith("_") or name.startswith("_"))
                   and (pset is None or _norm(f.pset) == _norm(pset))]
        if not matches:
            return None
        value = matches[0].value
        if convert == "length":
            value = _number(value) * matches[0].length_scale
        elif convert:
            value = convert(value)
        self.used.update((_norm(f.pset), _norm(f.name)) for f in matches)
        return value


def _ifc_psets(entity, cache):
    """Read own Psets once, retaining the utility's ordered same-name merge."""
    import ifcopenshell.util.element as element
    if entity.is_a("IfcTypeObject"):
        definitions = entity.HasPropertySets or ()
    elif entity.is_a("IfcElement"):
        definitions = [rel.RelatingPropertyDefinition for rel in entity.IsDefinedBy
                       if rel.is_a("IfcRelDefinesByProperties")]
    else:
        return element.get_psets(entity, should_inherit=False, verbose=True)
    result = {}
    for definition in definitions:
        key = definition.id()
        if key not in cache:
            cache[key] = (definition.Name, element.get_property_definition(definition, verbose=True))
        name, properties = cache[key]
        result.setdefault(name, {}).update(properties)
    return result


def _ifc_facts(model, entity, *, cache=None):
    import ifcopenshell
    import ifcopenshell.util.unit as unit
    facts = []
    if not entity:
        return facts
    # Scoped to one opened model: shared properties and unit conversions are
    # decoded once, with explicit property units still taking precedence.
    cache = {} if cache is None else cache
    entities = cache.setdefault("entities", {})
    if entity.id() in entities:
        return entities[entity.id()]
    decoded = cache.setdefault("properties", {})
    scales = cache.setdefault("lengthScales", {})
    for pset, properties in _ifc_psets(entity, cache.setdefault("psets", {})).items():
        for name, data in properties.items():
            if name == "id" or not isinstance(data, dict):
                continue
            key = (pset, name, data["id"])
            if key in decoded:
                facts.append(decoded[key])
                continue
            value = data.get("value")
            scale = 0.001  # number-valued project parameters conventionally mm
            cls = data.get("class")
            nominal_type = data.get("value_type") or ""
            prop = None
            if cls == "IfcPropertyBoundedValue":
                prop = model.by_id(data["id"])
                nominal = prop.SetPointValue
                if nominal is None:
                    bounds = [b for b in (prop.LowerBoundValue, prop.UpperBoundValue) if b is not None]
                    if bounds:
                        nominal = ifcopenshell.create_entity(bounds[0].is_a(), model.schema,
                            sum(_number(b.wrappedValue) for b in bounds) / len(bounds))
                value = nominal.wrappedValue if nominal else None
                nominal_type = nominal.is_a() if nominal else ""
            if "LengthMeasure" in nominal_type:
                prop = prop or model.by_id(data["id"])
                source = unit.get_property_unit(prop, model) or unit.get_project_unit(model, "LENGTHUNIT")
                source_id = source.id() if source else None
                if source_id not in scales:
                    if "metre" not in cache:
                        cache["metre"] = ifcopenshell.create_entity("IfcSIUnit", schema=model.schema,
                                                                  UnitType="LENGTHUNIT", Name="METRE")
                    scales[source_id] = (unit.convert_unit(1., source, cache["metre"])
                                         if source else unit.calculate_unit_scale(model))
                scale = scales[source_id]
            if cls == "IfcPropertyTableValue":
                prop = model.by_id(data["id"])
                value = [{"name": str(k.wrappedValue), "value": v.wrappedValue}
                         for k, v in zip(prop.DefiningValues or [], prop.DefinedValues or [])]
            # Revit plane-angle measures already arrive in the project's
            # degree unit; honor an explicit/project radian unit as well.
            if nominal_type == "IfcPlaneAngleMeasure" and name != "PanHorizontal":
                prop = prop or model.by_id(data["id"])
                source = unit.get_property_unit(prop, model) or unit.get_project_unit(model, "PLANEANGLEUNIT")
                if source and source.is_a("IfcSIUnit") and source.Name == "RADIAN":
                    value = math.degrees(_number(value))
                elif source and source.is_a("IfcConversionBasedUnit"):
                    factor = source.ConversionFactor.ValueComponent.wrappedValue
                    if not math.isclose(factor, math.pi / 180, rel_tol=1e-12):
                        value = math.degrees(_number(value) * factor)
            if pset == CAMERA_PSET and name == "Zoom" and "LengthMeasure" in nominal_type:
                value = _number(value) * scale * 1000
            decoded[key] = Fact(pset, name, value, scale)
            facts.append(decoded[key])
    entities[entity.id()] = facts
    return facts


def optics(values):
    result = {}
    for driver, first, second in (
        ("focalRange", "FOV Focal Length Minimum", "FOV Focal Length Maximum"),
        ("hfovRange", "FOV Horizontal Maximum", "FOV Horizontal Minimum"),
        ("vfovRange", "FOV Vertical Maximum", "FOV Vertical Minimum")):
        before = set(values.used)
        a = values.pick(first, first.replace("Minimum", "Min").replace("Maximum", "Max"), convert=_number)
        b = values.pick(second, second.replace("Minimum", "Min").replace("Maximum", "Max"), convert=_number)
        if a is not None and b is not None:
            result[driver] = [a, b]
        else:
            values.used = before
    before = set(values.used)
    pixels = []
    for standard, project in (("VideoResolutionWidth", "FOV Horizontal Resolution"),
                              ("VideoResolutionHeight", "FOV Vertical Resolution")):
        a = values.pick(standard, pset=CAMERA_PSET, convert=lambda v: int(_number(v)))
        b = values.pick(project, convert=lambda v: int(_number(v)))
        pixels.append(a if a is not None else b)
    if all(v is not None for v in pixels):
        result["pixels"] = pixels
    else:
        values.used = before
    x = values.pick("Origin Horizontal", convert="length")
    z = values.pick("Origin Vertical", convert="length")
    if x is not None or z is not None:
        result["offset"] = [x or 0., 0., z or 0.]
    ptz = values.pick("PTZ", convert=_bool)
    if ptz is not None or "focalRange" in result:
        lens = result.get("focalRange", (1., 1.))
        result["motorised"] = ptz if ptz is not None else bool(lens[0] > 0 and lens[1] / lens[0] > 4)
    return result


def pose(values, n=None):
    result = {}
    for driver, standard, aliases in (
        ("pan", "PanHorizontal", ("FOV Pan", "FOV Camera Rotation")),
        ("tilt", "TiltHorizontal", ("FOV Tilt", "FOV Camera Tilt")),
        ("focalLength", "Zoom", ("FOV Desired Focal Length", "FOV Actual Focal Length"))):
        a = values.pick(standard, pset=CAMERA_PSET, convert=_number) if n is None else None
        names = aliases if n is None else tuple(name.replace("FOV ", "FOV %d " % n) for name in aliases)
        b = values.pick(*names, convert=_number)
        value = (-a if driver == "tilt" else a) if a is not None else b
        if value is not None:
            result[driver] = value
    if n is None:
        corridor = values.pick("Corridor Format", convert=_bool)
        if corridor is not None:
            result["roll"] = 90. if corridor else 0.
        for name, driver, conv in (("FOV Distance to Object", "range", "length"),
                                   ("FOV Target Pixel Density", "targetDensity", _number)):
            value = values.pick(name, convert=conv)
            if value is not None:
                result[driver] = value
    return result


def preset_rows(value, sensor_index=0):
    """IFC table: name -> JSON object or comma-separated pan, tilt, zoom.

    JSON list-of-row objects is also accepted for COBie Attribute cells.
    IFC tilt is positive upward; table tilt has the same sign convention.
    """
    if isinstance(value, str):
        value = json.loads(value)
    if isinstance(value, dict):
        value = [{"name": k, "value": v} for k, v in value.items()]
    result = {}
    for i, row in enumerate(value):
        data = row.get("value", row)
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                data = [_number(v) for v in data.strip("()[] ").split(",")]
        if isinstance(data, (list, tuple)):
            data = dict(zip(("pan", "tilt", "focalLength"), data))
        if not isinstance(data, dict) or not all(k in data for k in ("pan", "tilt", "focalLength")):
            raise ValueError("Preset rows require pan, tilt and focalLength")
        name = str(row.get("name", "Preset_%d" % (i + 1)))
        if ":" in name:
            head, name = name.split(":", 1)
            if head != "Sensor_%d" % sensor_index:
                continue
        name = re.sub(r"[^A-Za-z0-9_]", "_", name)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", name):
            name = "Preset_" + name
        result[name] = {k: (-_number(v) if k == "tilt" else _number(v))
                        for k, v in data.items() if k in ("pan", "tilt", "focalLength", "dwell")}
        if "home" in data:
            result[name]["home"] = _bool(data["home"])
    return result


def decode(values, label):
    contract = values.pick('Contract', pset=PSET)
    if contract is None:
        return {}
    if not str(contract).startswith("usdaeco-cctv-ifc/1."):
        warnings.warn('%s: unknown camera contract %s; using tiers B/C' % (label, contract), stacklevel=2)
        return {}
    result = {}
    def finite(value):
        if isinstance(value, dict):
            return all(finite(v) for v in value.values())
        if isinstance(value, list):
            return all(finite(v) for v in value)
        return not isinstance(value, (int, float)) or math.isfinite(value)
    for name in ('Type', 'Drivers', 'Sensors'):
        raw = values.pick(name, pset=PSET)
        if raw is None:
            continue
        try:
            value = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise ValueError('%s: invalid %s JSON' % (label, name)) from exc
        if not isinstance(value, list if name == 'Sensors' else dict) or not finite(value):
            raise ValueError('%s: %s must have the contract shape and finite numbers' % (label, name))
        result[name] = value
    for i, sensor in enumerate(result.get('Sensors', [])):
        if not isinstance(sensor, dict) or sensor.get('name') != 'Sensor_%d' % i or not isinstance(sensor.get('drivers', {}), dict):
            raise ValueError('%s: Sensors must be ordered Sensor_0 ... Sensor_n driver objects' % label)
    return result


def drivers(sensor):
    return {k[len(SENSOR):]: v for k, v in sensor.get('drivers', {}).items()
            if k.startswith(SENSOR) and k[len(SENSOR):] in SENSOR_DRIVERS}


def conflict(label, name, authoritative, mirror):
    if isinstance(authoritative, (list, tuple)):
        same = len(authoritative) == len(mirror) and all(abs(a-b) <= 1e-6 for a, b in zip(authoritative, mirror))
    elif isinstance(authoritative, (int, float)) and not isinstance(authoritative, bool):
        same = abs(authoritative - mirror) <= 1e-6
    else:
        same = authoritative == mirror
    if not same:
        warnings.warn('%s: %s tier A %r conflicts with tier B %r; keeping A' %
                      (label, name, authoritative, mirror), stacklevel=2)


def stored(e):
    from ifcopenshell.util import element as uel
    return uel.get_psets(e, should_inherit=False).get(PSET, {})


def load(e, key, default=None):
    """Read a tier A member; missing/unsupported contracts use a fresh default."""
    values = Values([Fact(PSET, name, value) for name, value in stored(e).items() if name != "id"])
    return decode(values, e.GlobalId).get(key, copy.deepcopy(default))


def standard_values(e):
    result = {}
    from ifcopenshell.util import element as uel
    fields = uel.get_psets(e).get(STANDARD, {})
    if not fields:
        return result
    for prop in e.file.by_id(fields["id"]).HasProperties:
        if prop.is_a("IfcPropertySingleValue") and prop.NominalValue is not None:
            result[prop.Name] = prop.NominalValue.wrappedValue
        elif prop.is_a("IfcPropertyEnumeratedValue") and prop.EnumerationValues:
            vals = [v.wrappedValue for v in prop.EnumerationValues]
            result[prop.Name] = vals[0] if len(vals) == 1 else vals
        elif prop.is_a("IfcPropertyBoundedValue"):
            vals = [v.wrappedValue for v in (prop.LowerBoundValue, prop.UpperBoundValue) if v is not None]
            if prop.SetPointValue is not None:
                result[prop.Name] = prop.SetPointValue.wrappedValue
            elif vals:
                result[prop.Name] = sum(vals) / len(vals)
    return result


def prefer(authoritative, fallback, label):
    result = dict(fallback)
    for key, val in authoritative.items():
        if key in fallback:
            other = fallback[key]
            if isinstance(val, (int, float)) and isinstance(other, (int, float)):
                same = abs(val - other) <= 1e-6
            elif isinstance(val, list) and isinstance(other, list):
                same = len(val) == len(other) and all(abs(a-b) <= 1e-6 for a, b in zip(val, other))
            else:
                same = val == other
            if not same:
                warnings.warn(f"{label}: {key} tier A {val!r} conflicts with tier B {other!r}; keeping A", stacklevel=2)
        result[key] = val
    return result


def table_presets(e):
    from ifcopenshell.util import element as uel
    fields = uel.get_psets(e, should_inherit=False).get(STANDARD, {})
    result = {}
    if fields:
        for p in e.file.by_id(fields["id"]).HasProperties:
            if p.Name == "PanTiltZoomPreset" and p.is_a("IfcPropertyTableValue"):
                for key, val in zip(p.DefiningValues or [], p.DefinedValues or []):
                    name, preset = key.wrappedValue.split(":", 1)
                    pose = json.loads(val.wrappedValue)
                    result.setdefault(name, {})[preset] = {**pose, "tilt": -pose.get("tilt", 0)}
    return result


def mirror(e):
    """Head-zero tier B sensor drivers, with explicit/project IFC units."""
    from ifcopenshell.util import element as uel
    typ = None if e.is_a("IfcTypeObject") else uel.get_type(e)
    cache = {}
    values = Values(_ifc_facts(e.file, e, cache=cache) + _ifc_facts(e.file, typ, cache=cache))
    standard = Values([f for f in values.facts if f.pset == STANDARD])
    result = pose(standard)
    pixels = optics(standard).get("pixels")
    if pixels is not None:
        result["pixels"] = pixels
    return {SENSOR + key: value for key, value in result.items()}


def read(e):
    """Read an occurrence and its type into a JSON-compatible driver dictionary.

    Result keys: ``type`` (``drivers``, ``sensors``), effective housing ``drivers``
    and ``sensors``. Each head has ``name``, effective ``drivers``, ``presets``
    and ``tour``. Type values are also retained separately for sparse authors.
    Tier A wins over B; occurrence A overrides type A. No fallback optics are
    invented. Unknown driver keys are ignored; unknown major versions warn.
    This function reads the versioned tiers A/B, not vendor tier C dialects.
    """
    from ifcopenshell.util import element as uel
    typ = uel.get_type(e)
    type_a = load(typ, "Type", {}) if typ else {}
    type_heads = load(typ, "Sensors", []) if typ else []
    own_heads = load(e, "Sensors", [])
    type_b = mirror(typ) if typ else {}
    own_b = mirror(e)
    type_housing_b = standard_values(typ) if typ else {}
    type_drivers = prefer({k: v for k, v in type_a.items() if k in HOUSING},
        {"aeco:cctvType:outdoor": type_housing_b["IsOutdoors"]}
        if "IsOutdoors" in type_housing_b else {}, typ.GlobalId if typ else e.GlobalId)
    type_sensors = []
    if not type_heads and type_b:
        type_heads = [{"name": "Sensor_0", "drivers": {}}]
    for i, head in enumerate(type_heads):
        td = {SENSOR + k: v for k, v in drivers(head).items()}
        type_sensors.append(dict(name=head["name"], drivers=prefer(td,
            {k: v for k, v in type_b.items() if k == SENSOR + "pixels"} if i == 0 else {}, e.GlobalId)))
    entries = own_heads or [{"name": h["name"], "drivers": {}} for h in type_heads]
    tables = table_presets(e)
    if not entries and (own_b or tables):
        entries = [{"name": "Sensor_0", "drivers": {}}]
    sensors = []
    for i, entry in enumerate(entries):
        td = type_sensors[i]["drivers"] if i < len(type_sensors) else {}
        od = {SENSOR + k: v for k, v in drivers(entry).items()}
        resolved = prefer({**td, **od}, own_b if i == 0 else {}, e.GlobalId)
        type_head = type_heads[i] if i < len(type_heads) else {}
        presets = entry.get("presets", type_head.get("presets", tables.get(entry["name"], {})))
        tour = entry.get("tour", type_head.get("tour", resolved.pop(SENSOR + "tour", [])))
        sensors.append(dict(name=entry["name"], drivers=resolved, presets=presets, tour=tour))
    housing_b = standard_values(e)
    housing_a = {**{k: v for k, v in type_a.items() if k in HOUSING},
                 **{k: v for k, v in load(e, "Drivers", {}).items() if k in HOUSING}}
    housing_fallback = {**type_drivers, **({"aeco:cctvType:outdoor": housing_b["IsOutdoors"]}
                        if "IsOutdoors" in housing_b else {})}
    housing = prefer(housing_a, housing_fallback, e.GlobalId)
    return dict(type=dict(drivers=type_drivers, sensors=type_sensors), drivers=housing, sensors=sensors)
