"""The home page's scene: the cloud (tools/cloud.vdb) floating over a mirror-still sea at night, a cinder burning inside.

The page mixes light passes rendered from one fixed camera, so the scroll can relight the scene exactly (light adds):
  moon   the night: a cool moon from the left, a little behind, a dark sky graded to the horizon
  ember  only the cinder inside the cloud (two warm point lights), the sky black
  mask   white where the water is visible, black elsewhere (the page ripples only the water)
Each pass is saved as raw float (.npy, linear) plus a JSON with the camera, for tools/ember_webp.py.

    blender -b --factory-startup -P tools/ember_render.py -- --pass moon --aspect wide --out C:/tmp/moon_wide [--size 960 400] [--samples 64]
"""
import json
import math
import os
import sys

import bpy
import numpy as np
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

args = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []


def opt(name, default, n=1):
    if name not in args:
        return default
    i = args.index(name) + 1
    return args[i] if n == 1 else args[i:i + n]


PASS = opt('--pass', 'moon')
ASPECT = opt('--aspect', 'wide')
out = opt('--out', 'ember_' + PASS)
size = [int(v) for v in opt('--size', ['3200', '1334'] if ASPECT == 'wide' else ['1200', '2600'], 2)]
samples = int(opt('--samples', '256'))
here = os.path.dirname(os.path.abspath(__file__))
grid = opt('--vdb', os.path.join(here, 'cloud.vdb'))
S = 3.0
X0, Y0, Z0 = -0.62, -0.34, -0.36          # the grid's corner in box units (tools/cloud_render.py)
ZW = float(opt('--zw', '-1.6'))           # the water's height; the cloud's base is at about -0.93
CAM = {   # per aspect: distance, eye height above the water, lens, shift x / y
    'wide': dict(d=float(opt('--d', '13')), he=float(opt('--he', '0.22')), lens=32.0, sx=float(opt('--sx', '-0.14')), sy=float(opt('--sy', '0.0'))),
    'tall': dict(d=float(opt('--d', '8.4')), he=float(opt('--he', '0.22')), lens=30.0, sx=0.0, sy=float(opt('--sy', '0.14'))),
}[ASPECT]

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.engine = 'CYCLES'
prefs = bpy.context.preferences.addons['cycles'].preferences
prefs.compute_device_type = 'OPTIX'
prefs.refresh_devices()
for dv in prefs.devices:
    dv.use = dv.type != 'CPU'
scene.cycles.device = 'GPU'
scene.cycles.samples = samples if PASS != 'mask' else 16
scene.cycles.use_denoising = PASS != 'mask'
scene.cycles.denoiser = 'OPENIMAGEDENOISE'
scene.cycles.volume_bounces = int(opt('--bounces', '32'))
scene.cycles.max_bounces = 24
scene.cycles.glossy_bounces = 8
scene.cycles.volume_max_steps = 1024
scene.render.resolution_x, scene.render.resolution_y = size
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = 'OPEN_EXR'
scene.render.image_settings.color_depth = '32'
scene.render.image_settings.exr_codec = 'ZIP'
scene.render.filepath = out + '.exr'
if '--border' in args:      # a full-resolution crop to judge detail: x0 x1 y0 y1 (fractions, y from the bottom)
    b = [float(v) for v in opt('--border', None, 4)]
    scene.render.use_border, scene.render.use_crop_to_border = True, True
    scene.render.border_min_x, scene.render.border_max_x, scene.render.border_min_y, scene.render.border_max_y = b
scene.view_settings.view_transform = 'Standard'
scene.view_settings.look = 'None'
scene.view_settings.exposure = 0.0


def link(o):
    scene.collection.objects.link(o)
    return o


def mat_nodes(name):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    m.node_tree.nodes.clear()
    return m, m.node_tree


# ---------------------------------------------------------------- the cloud
vd = bpy.data.volumes.new('cloud')
vd.filepath = grid
cloud = link(bpy.data.objects.new('cloud', vd))
cloud.location = (X0 * S, Y0 * S, Z0 * S)
m, nt = mat_nodes('cloud')
o = nt.nodes.new('ShaderNodeOutputMaterial')
if PASS == 'mask':
    va = nt.nodes.new('ShaderNodeVolumeAbsorption')
    va.inputs['Density'].default_value = 400.0
    va.inputs['Color'].default_value = (0, 0, 0, 1)
    nt.links.new(va.outputs[0], o.inputs['Volume'])
else:
    pv = nt.nodes.new('ShaderNodeVolumePrincipled')
    pv.inputs['Color'].default_value = (1, 1, 1, 1)
    pv.inputs['Density'].default_value = float(opt('--density', '35'))
    pv.inputs['Anisotropy'].default_value = 0.45
    nt.links.new(pv.outputs['Volume'], o.inputs['Volume'])
    if PASS == 'ember' and float(opt('--glow', '0')) > 0:
        # the cinder: the cloud itself glows from a hot lower core outward; light emitted near the surface escapes,
        # so the glow shows where the cloud is thin, in its creases and on its underside (which the mirror shows)
        tc = nt.nodes.new('ShaderNodeTexCoord')
        core = nt.nodes.new('ShaderNodeVectorMath')
        core.operation = 'DISTANCE'
        core.inputs[1].default_value = (float(opt('--emit-x', '0.0')), float(opt('--emit-oy', '-0.12')), float(opt('--emit-z', '-0.55')))
        nt.links.new(tc.outputs['Object'], core.inputs[0])
        # Object coords of a volume are the grid's: subtract the object's offset so the core is in world units
        off = nt.nodes.new('ShaderNodeVectorMath')
        off.operation = 'ADD'
        off.inputs[1].default_value = (X0 * S, Y0 * S, Z0 * S)
        nt.links.new(tc.outputs['Object'], off.inputs[0])
        sq = nt.nodes.new('ShaderNodeVectorMath')              # an ellipsoid core, deep toward the camera, so the
        sq.operation = 'MULTIPLY'                               # front billows glow too instead of standing dark
        sq.inputs[1].default_value = (1.0, float(opt('--emit-sy', '0.45')), 1.0)
        nt.links.new(off.outputs[0], sq.inputs[0])
        nt.links.new(sq.outputs[0], core.inputs[0])
        fall = nt.nodes.new('ShaderNodeMapRange')
        fall.inputs['From Min'].default_value = 0.0
        fall.inputs['From Max'].default_value = float(opt('--emit-r', '1.08'))
        fall.inputs['To Min'].default_value = 1.0
        fall.inputs['To Max'].default_value = 0.0
        fall.interpolation_type = 'SMOOTHSTEP'
        nt.links.new(core.outputs['Value'], fall.inputs['Value'])
        hue = nt.nodes.new('ShaderNodeValToRGB')
        hr = hue.color_ramp
        hr.elements[0].position, hr.elements[0].color = 0.0, (1.0, 0.22, 0.035, 1)    # ~1400 K at the rim of the core
        hr.elements[1].position, hr.elements[1].color = 1.0, (1.0, 0.62, 0.27, 1)     # ~2600 K in the heart
        nt.links.new(fall.outputs['Result'], hue.inputs['Fac'])
        nt.links.new(hue.outputs['Color'], pv.inputs['Emission Color'])
        nz = nt.nodes.new('ShaderNodeTexNoise')
        nz.inputs['Scale'].default_value = 1.1
        nz.inputs['Detail'].default_value = 3.0
        nt.links.new(off.outputs[0], nz.inputs['Vector'])
        flick = nt.nodes.new('ShaderNodeMapRange')
        flick.inputs['From Min'].default_value = 0.35
        flick.inputs['From Max'].default_value = 0.7
        flick.inputs['To Min'].default_value = 0.7
        flick.inputs['To Max'].default_value = 1.3
        nt.links.new(nz.outputs['Fac'], flick.inputs['Value'])
        at = nt.nodes.new('ShaderNodeAttribute')
        at.attribute_type = 'GEOMETRY'
        at.attribute_name = 'density'
        m1 = nt.nodes.new('ShaderNodeMath')
        m1.operation = 'MULTIPLY'
        nt.links.new(fall.outputs['Result'], m1.inputs[0])
        nt.links.new(flick.outputs['Result'], m1.inputs[1])
        m2 = nt.nodes.new('ShaderNodeMath')
        m2.operation = 'MULTIPLY'
        nt.links.new(m1.outputs[0], m2.inputs[0])
        nt.links.new(at.outputs['Fac'], m2.inputs[1])
        m3 = nt.nodes.new('ShaderNodeMath')
        m3.operation = 'MULTIPLY'
        m3.inputs[1].default_value = float(opt('--glow', '0'))
        nt.links.new(m2.outputs[0], m3.inputs[0])
        nt.links.new(m3.outputs[0], pv.inputs['Emission Strength'])
vd.materials.append(m)

# ---------------------------------------------------------------- the sea: a still mirror to the horizon
bpy.ops.mesh.primitive_plane_add(size=600, location=(0, 200, ZW))
sea = bpy.context.active_object
m, nt = mat_nodes('sea')
o = nt.nodes.new('ShaderNodeOutputMaterial')
if PASS == 'mask':
    em = nt.nodes.new('ShaderNodeEmission')
    em.inputs['Strength'].default_value = 1.0
    nt.links.new(em.outputs[0], o.inputs['Surface'])
else:
    bs = nt.nodes.new('ShaderNodeBsdfPrincipled')
    bs.inputs['Base Color'].default_value = (0.52, 0.55, 0.6, 1)
    bs.inputs['Metallic'].default_value = 1.0
    bs.inputs['Roughness'].default_value = float(opt('--rough', '0.015'))
    tc = nt.nodes.new('ShaderNodeTexCoord')
    nz = nt.nodes.new('ShaderNodeTexNoise')
    nz.inputs['Scale'].default_value = 3.0
    nz.inputs['Detail'].default_value = 4.0
    mp = nt.nodes.new('ShaderNodeMapping')
    mp.inputs['Scale'].default_value = (1.0, 1.0, 1.0)
    nt.links.new(tc.outputs['Object'], mp.inputs['Vector'])
    nt.links.new(mp.outputs['Vector'], nz.inputs['Vector'])
    bp = nt.nodes.new('ShaderNodeBump')
    bp.inputs['Strength'].default_value = float(opt('--bump', '0.008'))
    nt.links.new(nz.outputs['Fac'], bp.inputs['Height'])
    nt.links.new(bp.outputs['Normal'], bs.inputs['Normal'])
    nt.links.new(bs.outputs[0], o.inputs['Surface'])
sea.data.materials.append(m)

# ---------------------------------------------------------------- a thin mist over the sea, to carry the cinder's rays
if PASS == 'ember' and float(opt('--mist', '0.05')) > 0:   # only the cinder lights the mist (the moon's share is negligible)
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 10, ZW + 3.0))
    mist = bpy.context.active_object
    mist.scale = (60, 60, 6)
    m, nt = mat_nodes('mist')
    o = nt.nodes.new('ShaderNodeOutputMaterial')
    sc = nt.nodes.new('ShaderNodeVolumeScatter')
    sc.inputs['Anisotropy'].default_value = 0.6
    tc = nt.nodes.new('ShaderNodeTexCoord')
    ds = nt.nodes.new('ShaderNodeVectorMath')
    ds.operation = 'DISTANCE'
    ds.inputs[1].default_value = (0.0, 0.0, -0.3)
    nt.links.new(tc.outputs['Object'], ds.inputs[0])
    # Object coords of the unit cube scaled by (60,60,6): use world position instead
    gp = nt.nodes.new('ShaderNodeNewGeometry')
    nt.links.new(gp.outputs['Position'], ds.inputs[0])
    fr = nt.nodes.new('ShaderNodeMapRange')
    fr.inputs['From Min'].default_value = 0.0
    fr.inputs['From Max'].default_value = float(opt('--mr', '4.5'))
    fr.inputs['To Min'].default_value = float(opt('--mist', '0.05'))
    fr.inputs['To Max'].default_value = 0.0
    fr.interpolation_type = 'SMOOTHSTEP'
    nt.links.new(ds.outputs['Value'], fr.inputs['Value'])
    nt.links.new(fr.outputs['Result'], sc.inputs['Density'])
    nt.links.new(sc.outputs[0], o.inputs['Volume'])
    mist.data.materials.append(m)
    mist.visible_shadow = True

# ---------------------------------------------------------------- the camera: level (the horizon is the image of infinity at eye height), framed by shift
cd = bpy.data.cameras.new('cam')
cd.lens = CAM['lens']
cd.sensor_width = 36.0
cd.sensor_fit = 'AUTO'
cd.shift_x, cd.shift_y = CAM['sx'], CAM['sy']
cd.clip_end = 2000
ZE = ZW + CAM['he']
cam = link(bpy.data.objects.new('cam', cd))
cam.location = (0.0, -CAM['d'], ZE)
cam.rotation_euler = (math.radians(90), 0, 0)
scene.camera = cam

# ---------------------------------------------------------------- the lights of this pass
# the cinder: coals just inside the cloud's flat base (it sits at z -0.93), a few deeper; x, y, z, share, colour
COALS = [(0.05, 0.0, -0.80, 1.0, (1.0, 0.45, 0.13)), (-0.42, 0.05, -0.78, 0.6, (1.0, 0.36, 0.08)),
         (0.48, -0.05, -0.78, 0.55, (1.0, 0.4, 0.1)), (-0.05, -0.38, -0.76, 0.6, (1.0, 0.5, 0.18)),
         (-0.62, -0.2, -0.74, 0.35, (1.0, 0.33, 0.07)), (0.68, 0.1, -0.76, 0.3, (1.0, 0.33, 0.07)),
         (0.25, -0.3, -0.62, 0.45, (1.0, 0.52, 0.2)), (-0.3, -0.25, -0.6, 0.45, (1.0, 0.48, 0.17))]
world = bpy.data.worlds.new('w')
scene.world = world
world.use_nodes = True
wn = world.node_tree
bg = wn.nodes['Background']


def point(name, loc, energy, colour, radius):
    ld = bpy.data.lights.new(name, 'POINT')
    ld.energy = energy
    ld.color = colour
    ld.shadow_soft_size = radius
    lo = link(bpy.data.objects.new(name, ld))
    lo.location = loc
    return lo


if PASS == 'moon':
    sd = bpy.data.lights.new('moon', 'SUN')
    sd.energy = float(opt('--moon', '3.0'))
    sd.color = (0.78, 0.86, 1.0)
    sd.angle = math.radians(0.6)
    so = link(bpy.data.objects.new('moon', sd))
    el, az = math.radians(float(opt('--mel', '35'))), math.radians(float(opt('--maz', '-75')))   # behind-left, high
    # a sun shines along its -Z; point -Z from the moon toward the scene (the moon sits behind: +y)
    dvec = Vector((math.sin(az) * math.cos(el), math.cos(az) * math.cos(el), math.sin(el)))      # toward the moon
    so.rotation_euler = (-dvec).to_track_quat('-Z', 'Y').to_euler()
    # the night sky: near black overhead, a deep blue band at the horizon
    tc = wn.nodes.new('ShaderNodeTexCoord')
    sep = wn.nodes.new('ShaderNodeSeparateXYZ')
    wn.links.new(tc.outputs['Generated'], sep.inputs[0])
    ramp = wn.nodes.new('ShaderNodeValToRGB')
    cr = ramp.color_ramp
    cr.interpolation = 'EASE'
    cr.elements[0].position, cr.elements[0].color = 0.0, (0.034, 0.046, 0.080, 1)
    cr.elements[1].position, cr.elements[1].color = 0.35, (0.0018, 0.0028, 0.0065, 1)
    e = cr.elements.new(0.08)
    e.color = (0.012, 0.018, 0.036, 1)
    absn = wn.nodes.new('ShaderNodeMath')
    absn.operation = 'ABSOLUTE'
    wn.links.new(sep.outputs['Z'], absn.inputs[0])
    wn.links.new(absn.outputs[0], ramp.inputs['Fac'])
    wn.links.new(ramp.outputs['Color'], bg.inputs['Color'])
    bg.inputs['Strength'].default_value = 1.0
elif PASS == 'ember':
    bg.inputs['Color'].default_value = (0, 0, 0, 1)
    E = float(opt('--ember', '1500'))
    if E > 0:   # coals just inside the base: they light the underside (which the mirror shows) and the cloud from within
        for (x, y, z, k, c) in COALS:
            lo = point('coal', (x, y, float(opt('--lz', str(z)))), E * k, c, 0.06)
            lo.visible_camera = False
            lo.visible_glossy = False
else:
    bg.inputs['Color'].default_value = (0, 0, 0, 1)

bpy.ops.render.render(write_still=True)

# raw linear float out, so the page tool can set each pass's exposure
img = bpy.data.images.load(out + '.exr')
w, h = img.size
px = np.empty(w * h * 4, np.float32)
img.pixels.foreach_get(px)
px = px.reshape(h, w, 4)[::-1, :, :3]                 # Blender stores bottom-up
np.save(out + '.npy', px.astype(np.float16 if PASS != 'mask' else np.float32))


def proj(p):
    v = world_to_camera_view(scene, cam, Vector(p))
    return [v.x, 1.0 - v.y]                             # fractions, from the top-left


info = dict(pass_=PASS, aspect=ASPECT, size=[w, h], zw=ZW, ze=ZE, d=CAM['d'],
            horizon=proj((0, 1e6, ZE))[1], cloud=proj((0, 0, 0)), unit=[proj((1, 0, 0))[0] - proj((0, 0, 0))[0], proj((0, 0, 0))[1] - proj((0, 0, 1))[1]])
with open(out + '.json', 'w') as f:
    json.dump(info, f)
print('[ember]', PASS, ASPECT, size, samples, info)
