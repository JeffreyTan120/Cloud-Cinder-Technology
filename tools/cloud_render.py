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
scene.cycles.volume_bounces = 8                           # light bouncing inside the cloud: the soft, luminous look
scene.cycles.volume_max_steps = 1024
scene.cycles.volume_step_rate = 0.5
scene.render.film_transparent = True
scene.render.resolution_x, scene.render.resolution_y = size
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = 'PNG'
scene.render.image_settings.color_mode = 'RGBA'
scene.render.filepath = out
scene.view_settings.view_transform = 'Standard'
scene.view_settings.exposure = -0.5


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


# the pile, after the reference: six round lobes along the base, six above, five, then three on top with the peak
# left of centre; four bulges toward the camera; a small trailing cloud back right. A little jitter.
def row(n, x0, x1, y, z, r, jz=0.04):
    for i in range(n):
        x = x0 + (x1 - x0) * i / (n - 1)
        lobes.append((x + (rnd() - 0.5) * 0.02, y + (rnd() - 0.5) * 0.02, z + (rnd() - 0.5) * jz * 2, r + rnd() * 0.015))


row(6, -0.34, 0.34, -0.06, 0.0, 0.13)
row(6, -0.31, 0.31, 0.04, 0.0, 0.12, 0.06)
row(5, -0.25, 0.25, 0.13, 0.0, 0.105)
row(3, -0.12, 0.12, 0.215, 0.0, 0.085)
lobes.append((-0.05, 0.255, 0.0, 0.07))
row(4, -0.22, 0.22, 0.03, -0.14, 0.1)
row(3, -0.16, 0.16, -0.04, -0.15, 0.1)
for x, y, z, r in ((0.46, -0.02, 0.12, 0.11), (0.54, 0.04, 0.08, 0.09), (0.6, -0.03, 0.14, 0.085), (0.5, -0.08, 0.18, 0.09)):
    lobes.append((x, y, z, r))

flat = 0.82 if state == 'rain' else 1.0
wide = 1.08 if state == 'rain' else 1.0
mb = bpy.data.metaballs.new('cloud')
mb.resolution = 0.045
mb.threshold = 0.78
mbo = link(bpy.data.objects.new('cloud', mb))
for x, y, z, r in lobes:
    el = mb.elements.new()
    el.co = (x * S * wide, z * S * wide, y * S * flat)
    el.radius = r * S * 1.12
bpy.context.view_layer.objects.active = mbo
mbo.select_set(True)
bpy.ops.object.convert(target='MESH')
mesh = bpy.context.view_layer.objects.active
mesh.hide_render = True

vol = link(bpy.data.objects.new('cloudvol', bpy.data.volumes.new('cloudvol')))
m2v = vol.modifiers.new('m2v', 'MESH_TO_VOLUME')
m2v.object = mesh
m2v.resolution_mode = 'VOXEL_AMOUNT'
m2v.voxel_amount = 320
if hasattr(m2v, 'use_fill_volume'):
    m2v.use_fill_volume = True
m2v.interior_band_width = 0.35
tex = bpy.data.textures.new('fluff', 'CLOUDS')
tex.noise_scale = 0.45
tex.noise_depth = 3
disp = vol.modifiers.new('fluff', 'VOLUME_DISPLACE')
disp.texture = tex
disp.strength = 0.13
disp.texture_sample_radius = 0.3
tex2 = bpy.data.textures.new('fine', 'CLOUDS')
tex2.noise_scale = 0.14
tex2.noise_depth = 4
disp2 = vol.modifiers.new('fine', 'VOLUME_DISPLACE')
disp2.texture = tex2
disp2.strength = 0.09
disp2.texture_sample_radius = 0.14

mat = bpy.data.materials.new('cloud')
mat.use_nodes = True
nodes = mat.node_tree.nodes
nodes.clear()
pv = nodes.new('ShaderNodeVolumePrincipled')
pv.inputs['Density'].default_value = {'calm': 70.0, 'rain': 85.0, 'sun': 62.0}[state]
pv.inputs['Anisotropy'].default_value = 0.3
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
tgt.location = (0.08 * S, 0, 0.08 * S)
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
    lamp('key', (0.8, -2.2, 9.0), 2600, (1.0, 1.0, 1.0), 2.6)
    lamp('fill', (0.0, -9.0, 2.0), 160, (0.9, 0.93, 1.0), 8.0)
elif state == 'rain':
    lamp('key', (0.8, -2.2, 9.0), 900, (0.72, 0.8, 0.95), 3.2)
    lamp('fill', (0.0, -9.0, 2.0), 80, (0.7, 0.78, 0.95), 8.0)
else:
    lamp('key', (0.8, -2.2, 9.0), 3800, (1.0, 0.86, 0.62), 2.6)
    lamp('rim', (-1.0, 4.0, 7.0), 2600, (1.0, 0.8, 0.5), 2.0)
    lamp('fill', (0.0, -9.0, 2.0), 140, (1.0, 0.9, 0.78), 8.0)

world = bpy.data.worlds.new('w')
scene.world = world
world.use_nodes = True
bg = world.node_tree.nodes['Background']
bg.inputs['Color'].default_value = (0.05, 0.055, 0.07, 1.0)
bg.inputs['Strength'].default_value = {'calm': 1.0, 'rain': 0.6, 'sun': 1.2}[state]

bpy.ops.render.render(write_still=True)
print('[cloud] rendered', state, size, samples, '->', out)
