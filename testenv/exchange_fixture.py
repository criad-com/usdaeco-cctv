"""Generated IFC contract fixture with independent unit conversion at the writer."""
import json
import math

from ifcopenshell.api import run
import ifcopenshell.util.element as element
from fixtures import build_baseline

TYPE = {'aeco:cctvType:outdoor': True, 'aeco:cctvType:irRange': 40.,
        'aeco:type:model': 'OpticalHead', 'aeco:type:manufacturer': 'Example Optics'}
OPTICS = {'projection': 'rectilinear', 'spectrum': 'visible', 'focalRange': [3., 18.],
          'hfovRange': [104., 18.], 'vfovRange': [76., 12.], 'sensorSize': [0., 0.],
          'pixels': [2592, 1944], 'offset': [.1, .02, -.08], 'panRange': [-180., 180.],
          'tiltRange': [0., 90.], 'motorised': True}
POSE = {'pan': 15., 'tilt': 30., 'roll': 10., 'focalLength': 4., 'range': 12., 'targetDensity': 125.}
PRESETS = {'Home': {'pan': -45., 'tilt': 20., 'focalLength': 7., 'dwell': 4., 'home': True},
           'MainDoor': {'pan': 10., 'tilt': 30., 'focalLength': 5., 'dwell': 6., 'home': False}}


def write_contract(output, millimetres=False, radians=False, tier_a=True, bounded=False, empty_presets=False):
    model = build_baseline(output, millimetres=millimetres)
    camera = next(c for c in model.by_type('IfcAudioVisualAppliance') if c.Name == 'Fixed')
    for product in list(model.by_type('IfcElement')):
        if product != camera:
            run('root.remove_product', model, product=product)
    typ = element.get_type(camera)
    for product in (camera, typ):
        for values in element.get_psets(product, should_inherit=False).values():
            run('pset.remove_pset', model, product=product, pset=model.by_id(values['id']))
    project = model.by_type('IfcProject')[0]
    if radians:
        project.UnitsInContext.Units = [u for u in project.UnitsInContext.Units if u.UnitType != 'PLANEANGLEUNIT'] + [
            model.create_entity('IfcSIUnit', UnitType='PLANEANGLEUNIT', Name='RADIAN')]
    def pset(product, name, values):
        ps = run('pset.add_pset', model, product=product, name=name)
        ps.HasProperties = [model.create_entity('IfcPropertySingleValue', Name=k,
                             NominalValue=model.create_entity(t, v)) for k, (t, v) in values.items()]
        return ps
    factor = math.pi / 180 if radians else 1.
    length = .001 if millimetres else 1.
    standard = pset(camera, 'Pset_AudioVisualApplianceTypeCamera', {
        'CameraType': ('IfcLabel', 'VIDEO'), 'IsOutdoors': ('IfcBoolean', True),
        'VideoResolutionWidth': ('IfcInteger', 2592), 'VideoResolutionHeight': ('IfcInteger', 1944),
        'PanHorizontal': ('IfcLengthMeasure', 15.), 'TiltHorizontal': ('IfcPlaneAngleMeasure', -30. * factor),
        'Zoom': ('IfcPositiveLengthMeasure', .004 / length)})
    if bounded:
        props = []
        for prop in standard.HasProperties:
            if prop.Name in ('PanHorizontal', 'TiltHorizontal', 'Zoom'):
                nominal = prop.NominalValue
                props.append(model.create_entity('IfcPropertyBoundedValue', Name=prop.Name,
                    LowerBoundValue=model.create_entity(nominal.is_a(), nominal.wrappedValue * .9),
                    UpperBoundValue=model.create_entity(nominal.is_a(), nominal.wrappedValue * 1.1)))
            else:
                props.append(prop)
        standard.HasProperties = props
    presets = {} if empty_presets else PRESETS
    table = model.create_entity('IfcPropertyTableValue', Name='PanTiltZoomPreset',
        DefiningValues=[model.create_entity('IfcIdentifier', 'Sensor_0:' + n) for n in presets],
        DefinedValues=[model.create_entity('IfcText', json.dumps(dict(d, tilt=-d['tilt']))) for d in presets.values()])
    standard.HasProperties = [*standard.HasProperties, table]
    if tier_a:
        pset(typ, 'Pset_AecoCctv', {'Contract': ('IfcText', 'usdaeco-cctv-ifc/1.0'),
            'Type': ('IfcText', json.dumps(TYPE)), 'Sensors': ('IfcText', json.dumps([
                {'name': 'Sensor_0', 'drivers': {'aeco:cctvSensor:' + k: v for k, v in OPTICS.items()}},
                {'name': 'Sensor_1', 'drivers': {'aeco:cctvSensor:' + k: v for k, v in dict(OPTICS, motorised=False).items()}}]))})
        pset(camera, 'Pset_AecoCctv', {'Contract': ('IfcText', 'usdaeco-cctv-ifc/1.0'),
            'Drivers': ('IfcText', json.dumps({'aeco:cctv:scenario': 'door', 'aeco:cctv:mount': 'ceiling'})),
            'Sensors': ('IfcText', json.dumps([
                {'name': 'Sensor_0', 'drivers': {'aeco:cctvSensor:' + k: v for k, v in POSE.items()},
                 'presets': presets, 'tour': list(presets)},
                {'name': 'Sensor_1', 'drivers': {'aeco:cctvSensor:' + k: v for k, v in dict(POSE, focalRange=[3.,20.]).items()},
                 'presets': {}, 'tour': []}]))})
    else:
        pset(typ, 'Optical data', {'FOV Focal Length Minimum': ('IfcReal', 3.),
            'FOV Focal Length Maximum': ('IfcReal', 18.),
            'FOV Horizontal Maximum': ('IfcPlaneAngleMeasure', 104. * factor),
            'FOV Horizontal Minimum': ('IfcPlaneAngleMeasure', 18. * factor),
            'FOV Vertical Maximum': ('IfcPlaneAngleMeasure', 76. * factor),
            'FOV Vertical Minimum': ('IfcPlaneAngleMeasure', 12. * factor),
            'PTZ': ('IfcBoolean', False)})
    model.write(str(output))
    return model
