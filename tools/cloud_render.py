"""The site's cloud, rendered in Blender Cycles as a real volume.

A pile of metaball lobes (five big along the base, five in the middle, four small on top) becomes a mesh, the mesh
a voxel volume, the volume is displaced by a noise texture for the cauliflower surface, and a Principled Volume
scatters one area lamp from above. One render per weather state, transparent background.

    blender -b --factory-startup -P tools/cloud_render.py -- --state calm --out cloud_calm.png [--size 1400 1000] [--samples 256]
"""
import math
import sys

import bpy

args = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []


def opt(name, default, n=1):
    if name not in args:
        return default
    i = args.index(name) + 1
    return args[i] if n == 1 else args[i:i + n]


state = opt('--state', 'calm')
out = opt('--out', 'cloud.png')
size = [int(v) for v in opt('--size', ['1400', '1000'], 2)]
samples = int(opt('--samples', '256'))
S = 3.0                                                   # scene units per page unit

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.engine = 'CYCLES'
prefs = bpy.context.preferences.addons['cycles'].preferences
prefs.compute_device_type = 'OPTIX'
prefs.refresh_devices()
for d in prefs.devices:
    d.use = d.type != 'CPU'
scene.cycles.device = 'GPU'
scene.cycles.samples = samples
scene.cycles.use_denoising = True
scene.cycles.volume_bounces = 6                           # light bouncing inside the cloud: the soft, luminous look
scene.cycles.volume_max_steps = 1024
scene.cycles.volume_step_rate = 0.5
scene.render.film_transparent = True
scene.render.resolution_x, scene.render.resolution_y = size
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = 'PNG'
scene.render.image_settings.color_mode = 'RGBA'
scene.render.filepath = out
scene.view_settings.view_transform = 'Standard'
scene.view_settings.exposure = -0.45


def link(o):
    scene.collection.objects.link(o)
    return o


# the lobes (page units, y up), the same pile the page used
lobes = []
seed = 11


def rnd():
    global seed
    seed = (seed * 1664525 + 1013904223) % 4294967296
    return seed / 4294967296


# the pile: five big lobes along the base, five in the middle, five small on top (the middle one highest), a little jitter
for i in range(5):
    a = -0.28 + i * 0.14
    lobes.append((a + (rnd() - 0.5) * 0.02, -0.06, (rnd() - 0.5) * 0.06, 0.16 + rnd() * 0.02))
for i in range(5):
    a = -0.24 + i * 0.12
    lobes.append((a + (rnd() - 0.5) * 0.03, 0.05 + rnd() * 0.02, (rnd() - 0.5) * 0.08, 0.13 + rnd() * 0.02))
for i in range(8):
    a = -0.245 + i * 0.07
    lobes.append((a + (rnd() - 0.5) * 0.02, 0.13 + 0.06 * math.cos((i - 3.5) * 0.55) + rnd() * 0.02, (rnd() - 0.5) * 0.1, 0.08 + rnd() * 0.02))

flat = 0.82 if state == 'rain' else 1.0
wide = 1.08 if state == 'rain' else 1.0
mb = bpy.data.metaballs.new('cloud')
mb.resolution = 0.06
mb.threshold = 0.7
mbo = link(bpy.data.objects.new('cloud', mb))
for x, y, z, r in lobes:
    el = mb.elements.new()
    el.co = (x * S * wide, z * S * wide, y * S * flat)
    el.radius = r * S * 1.2
bpy.context.view_layer.objects.active = mbo
mbo.select_set(True)
bpy.ops.object.convert(target='MESH')
mesh = bpy.context.view_layer.objects.active
mesh.hide_render = True

vol = link(bpy.data.objects.new('cloudvol', bpy.data.volumes.new('cloudvol')))
m2v = vol.modifiers.new('m2v', 'MESH_TO_VOLUME')
m2v.object = mesh
m2v.resolution_mode = 'VOXEL_AMOUNT'
m2v.voxel_amount = 260
if hasattr(m2v, 'use_fill_volume'):
    m2v.use_fill_volume = True
m2v.interior_band_width = 0.35
tex = bpy.data.textures.new('fluff', 'CLOUDS')
tex.noise_scale = 0.6
tex.noise_depth = 5
disp = vol.modifiers.new('fluff', 'VOLUME_DISPLACE')
disp.texture = tex
disp.strength = 0.38
disp.texture_sample_radius = 0.3
tex2 = bpy.data.textures.new('fine', 'CLOUDS')
tex2.noise_scale = 0.18
tex2.noise_depth = 4
disp2 = vol.modifiers.new('fine', 'VOLUME_DISPLACE')
disp2.texture = tex2
disp2.strength = 0.1
disp2.texture_sample_radius = 0.2

mat = bpy.data.materials.new('cloud')
mat.use_nodes = True
nodes = mat.node_tree.nodes
nodes.clear()
pv = nodes.new('ShaderNodeVolumePrincipled')
pv.inputs['Density'].default_value = {'calm': 50.0, 'rain': 65.0, 'sun': 42.0}[state]
pv.inputs['Anisotropy'].default_value = 0.5
pv.inputs['Color'].default_value = (1.0, 1.0, 1.0, 1.0)
outn = nodes.new('ShaderNodeOutputMaterial')
mat.node_tree.links.new(pv.outputs['Volume'], outn.inputs['Volume'])
vol.data.materials.append(mat)

# the camera: the box's view, 34 degrees round, 11 degrees down
cam = bpy.data.cameras.new('cam')
cam.lens = 60
camo = link(bpy.data.objects.new('cam', cam))
yaw, pitch, dist = math.radians(34), math.radians(9), 7.0
camo.location = (math.sin(yaw) * math.cos(pitch) * dist, -math.cos(yaw) * math.cos(pitch) * dist, math.sin(pitch) * dist + 0.15 * S)
tgt = link(bpy.data.objects.new('tgt', None))
tgt.location = (0, 0, 0.1 * S)
c = camo.constraints.new('TRACK_TO')
c.target = tgt
c.track_axis = 'TRACK_NEGATIVE_Z'
c.up_axis = 'UP_Y'
scene.camera = camo


def lamp(name, loc, energy, colour, sz, look=(0, 0, 0)):
    ld = bpy.data.lights.new(name, 'AREA')
    ld.energy = energy
    ld.color = colour
    ld.size = sz
    lo = link(bpy.data.objects.new(name, ld))
    lo.location = loc
    t = link(bpy.data.objects.new(name + '_t', None))
    t.location = look
    k = lo.constraints.new('TRACK_TO')
    k.target = t
    k.track_axis = 'TRACK_NEGATIVE_Z'
    k.up_axis = 'UP_Y'
    return lo


if state == 'calm':
    lamp('key', (0.6, -1.5, 9.0), 2800, (1.0, 1.0, 1.0), 2.5)
    lamp('fill', (-2.0, -9.0, 1.0), 260, (0.85, 0.9, 1.0), 6.0)
elif state == 'rain':
    lamp('key', (0.6, -1.5, 9.0), 900, (0.72, 0.8, 0.95), 3.5)
    lamp('fill', (-2.0, -9.0, 1.0), 120, (0.7, 0.78, 0.95), 6.0)
else:
    lamp('key', (0.6, -1.5, 9.0), 4200, (1.0, 0.86, 0.62), 2.6)
    lamp('rim', (-1.0, 4.0, 7.0), 2600, (1.0, 0.8, 0.5), 2.0)
    lamp('fill', (-2.0, -9.0, 1.0), 220, (1.0, 0.9, 0.78), 6.0)

world = bpy.data.worlds.new('w')
scene.world = world
world.use_nodes = True
bg = world.node_tree.nodes['Background']
bg.inputs['Color'].default_value = (0.05, 0.055, 0.07, 1.0)
bg.inputs['Strength'].default_value = {'calm': 1.0, 'rain': 0.6, 'sun': 1.2}[state]

bpy.ops.render.render(write_still=True)
print('[cloud] rendered', state, size, samples, '->', out)
