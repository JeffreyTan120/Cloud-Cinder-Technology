"""The site's cloud, rendered in Blender Cycles as a real volume, after the reference (a compact cumulus heap in a glass box).

Shape: a heap of core metaball lobes (a flat base row, two rows above, a peak left of centre) with ~40 small lobes
scattered over its upper surface for the cauliflower look, and a smaller, greyer cloud behind right. The metaball
becomes a mesh, the mesh a hard-edged voxel volume (a thin interior band, so the edge is crisp, not fog); three
Volume Displace layers tear the edge into lobes, cauliflower and fibre; the shader adds grain and cuts the base
flat. A dense Principled Volume with many bounces scatters one strong area lamp from above: a near-white top, a
grey base, shadowed creases. One render per weather state, transparent background.

    blender -b --factory-startup -P tools/cloud_render.py -- --state calm --out cloud_calm.png [--size 1400 1000] [--samples 512]

Framing: lens 55 at distance 9.6 covers 36/55*9.6 = 6.28 scene units = 2.09 page units across; the page draws
the image 2.1 box widths wide, centred on the camera target (the main cloud's centre).
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
samples = int(opt('--samples', '512'))
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
scene.cycles.denoiser = 'OPENIMAGEDENOISE'
scene.cycles.denoising_input_passes = 'RGB_ALBEDO_NORMAL'
scene.cycles.denoising_prefilter = 'ACCURATE'
scene.cycles.volume_bounces = 12                          # light bouncing inside the cloud: the white, luminous body
scene.cycles.volume_max_steps = 1024
scene.cycles.volume_step_rate = 0.4
scene.render.film_transparent = True
scene.render.resolution_x, scene.render.resolution_y = size
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = 'PNG'
scene.render.image_settings.color_mode = 'RGBA'
scene.render.filepath = out
scene.view_settings.view_transform = 'Standard'
scene.view_settings.exposure = float(opt('--exposure', {'calm': '0.0', 'rain': '-0.3', 'sun': '0.1'}[state]))


def link(o):
    scene.collection.objects.link(o)
    return o


seed = 11


def rnd():
    global seed
    seed = (seed * 1664525 + 1013904223) % 4294967296
    return seed / 4294967296


def gauss():
    return (rnd() + rnd() + rnd() - 1.5) * 1.6


# a cloud (page units, y up, z toward the camera): a dome, wider than tall. Big lobes fill the inside; even-sized
# lobes sit on the dome's surface at even spacing (a Fibonacci spread): the billows. ymin keeps the base a row of
# lobes (nothing dangles below it); the peak leans left.
def dome(cx, cy, cz, A, B, C, n, r0, rvar, ymin=-0.5, lean=-0.05, fill=0.16):
    out = []
    for x in (-0.6, -0.3, 0.0, 0.3, 0.6):
        for z in (-0.4, 0.4):
            for y in (-0.1, 0.35):
                if x * x + y * y + z * z < 0.6:
                    out.append((cx + x * A, cy + y * B, cz + z * C, fill * (A / 0.47)))
    golden = math.pi * (3 - math.sqrt(5))
    for i in range(n):
        y = 1 - 2 * (i + 0.5) / n
        if y < ymin:
            continue
        r = math.sqrt(1 - y * y)
        phi = i * golden
        x, z = math.cos(phi) * r, math.sin(phi) * r
        rr = r0 + rnd() * rvar + 0.15 * r0 * max(0.0, y)
        out.append((cx + x * A * 0.86 + lean * max(0.0, y) + (rnd() - 0.5) * 0.02, cy + y * B * 0.86 + (rnd() - 0.5) * 0.02,
                    cz + z * C * 0.86 + (rnd() - 0.5) * 0.02, rr))
    return out


lobes = dome(0.0, 0.1, 0.0, 0.46, 0.42, 0.3, 64, 0.1, 0.025)
# the second cloud, behind right, smaller, in the main cloud's shadow
lobes += dome(0.32, 0.1, -0.5, 0.3, 0.24, 0.2, 36, 0.08, 0.02, -0.4, -0.02, 0.12)

flat = 0.86 if state == 'rain' else 1.0
wide = 1.06 if state == 'rain' else 1.0
mb = bpy.data.metaballs.new('cloud')
mb.resolution = 0.04
mb.threshold = 0.8
mbo = link(bpy.data.objects.new('cloud', mb))
for x, y, z, r in lobes:
    el = mb.elements.new()
    el.co = (x * S * wide, -z * S * wide, y * S * flat)   # Blender: x right, y away from the camera, z up
    el.radius = r * S * 1.17
bpy.context.view_layer.objects.active = mbo
mbo.select_set(True)
bpy.ops.object.convert(target='MESH')
mesh = bpy.context.view_layer.objects.active
mesh.hide_render = True

vol = link(bpy.data.objects.new('cloudvol', bpy.data.volumes.new('cloudvol')))
m2v = vol.modifiers.new('m2v', 'MESH_TO_VOLUME')
m2v.object = mesh
m2v.resolution_mode = 'VOXEL_AMOUNT'
m2v.voxel_amount = 380
if hasattr(m2v, 'use_fill_volume'):
    m2v.use_fill_volume = True
m2v.interior_band_width = 0.23                            # a short fuzz at the edge, then full density


def displace(name, scale, depth, strength, radius):
    tex = bpy.data.textures.new(name, 'CLOUDS')
    tex.noise_scale = scale
    tex.noise_depth = depth
    tex.noise_basis = 'ORIGINAL_PERLIN'
    d = vol.modifiers.new(name, 'VOLUME_DISPLACE')
    d.texture = tex
    d.strength = strength
    d.texture_sample_radius = radius
    return d


displace('lobes', 0.8, 2, 0.2, 0.2)                       # bends the big lobes out of their metaball roundness
displace('cauli', 0.3, 3, 0.14, 0.08)                     # the cauliflower
displace('fibre', 0.12, 3, 0.08, 0.03)                    # the fibre at the edge

mat = bpy.data.materials.new('cloud')
mat.use_nodes = True
nt = mat.node_tree
nodes, links = nt.nodes, nt.links
nodes.clear()
attr = nodes.new('ShaderNodeAttribute')
attr.attribute_name = 'density'
# grain: a fine noise modulates the density (cotton texture inside the lobes)
tc = nodes.new('ShaderNodeTexCoord')
grain = nodes.new('ShaderNodeTexNoise')
grain.inputs['Scale'].default_value = 3.4
grain.inputs['Detail'].default_value = 10.0
grain.inputs['Roughness'].default_value = 0.62
grain.inputs['Lacunarity'].default_value = 2.3
links.new(tc.outputs['Object'], grain.inputs['Vector'])
gmap = nodes.new('ShaderNodeMapRange')
gmap.inputs['From Min'].default_value = 0.42
gmap.inputs['From Max'].default_value = 0.6
gmap.inputs['To Min'].default_value = 0.3
gmap.inputs['To Max'].default_value = 1.0
links.new(grain.outputs['Fac'], gmap.inputs['Value'])
# the flat base: the density fades out below z0 (scene units)
sep = nodes.new('ShaderNodeSeparateXYZ')
links.new(tc.outputs['Object'], sep.inputs['Vector'])
base = nodes.new('ShaderNodeMapRange')
z0 = -0.2 * S * flat
base.inputs['From Min'].default_value = z0
base.inputs['From Max'].default_value = z0 + 0.3
base.inputs['To Min'].default_value = 0.0
base.inputs['To Max'].default_value = 1.0
links.new(sep.outputs['Z'], base.inputs['Value'])
edge = nodes.new('ShaderNodeMath')                        # the fuzz band ramps up fast: dense just inside the edge
edge.operation = 'POWER'
edge.inputs[1].default_value = 0.6
links.new(attr.outputs['Fac'], edge.inputs[0])
m1 = nodes.new('ShaderNodeMath')
m1.operation = 'MULTIPLY'
links.new(edge.outputs['Value'], m1.inputs[0])
links.new(gmap.outputs['Result'], m1.inputs[1])
m2 = nodes.new('ShaderNodeMath')
m2.operation = 'MULTIPLY'
links.new(m1.outputs['Value'], m2.inputs[0])
links.new(base.outputs['Result'], m2.inputs[1])
pv = nodes.new('ShaderNodeVolumePrincipled')
pv.inputs['Anisotropy'].default_value = 0.35
pv.inputs['Color'].default_value = (1.0, 1.0, 1.0, 1.0)
m3 = nodes.new('ShaderNodeMath')                          # the density: attribute x grain x base cut x the state's density
m3.operation = 'MULTIPLY'
m3.inputs[1].default_value = {'calm': 160.0, 'rain': 190.0, 'sun': 150.0}[state]
links.new(m2.outputs['Value'], m3.inputs[0])
links.new(m3.outputs['Value'], pv.inputs['Density'])
outn = nodes.new('ShaderNodeOutputMaterial')
links.new(pv.outputs['Volume'], outn.inputs['Volume'])
vol.data.materials.append(mat)

# the camera: the box's view, 34 degrees round, 9 degrees down, far enough that the perspective is gentle
cam = bpy.data.cameras.new('cam')
cam.lens = 55
camo = link(bpy.data.objects.new('cam', cam))
yaw, pitch, dist = math.radians(34), math.radians(9), 9.6
target = (0.04 * S, 0.0, 0.17 * S)                        # the main cloud's visual centre
camo.location = (target[0] + math.sin(yaw) * math.cos(pitch) * dist, target[1] - math.cos(yaw) * math.cos(pitch) * dist,
                 target[2] + math.sin(pitch) * dist)
tgt = link(bpy.data.objects.new('tgt', None))
tgt.location = target
c = camo.constraints.new('TRACK_TO')
c.target = tgt
c.track_axis = 'TRACK_NEGATIVE_Z'
c.up_axis = 'UP_Y'
scene.camera = camo


def lamp(name, loc, energy, colour, sz, look=(0, 0, 0), spot=None):
    ld = bpy.data.lights.new(name, 'SPOT' if spot else 'AREA')
    ld.energy = energy
    ld.color = colour
    if spot:
        ld.spot_size = math.radians(spot)
        ld.spot_blend = 0.4
        ld.shadow_soft_size = sz
    else:
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


top, aim = (0.4, -0.9, 10.0), (0.1, 0.0, 0.5)             # the studio lamp: a spot above the main cloud; the back cloud in its penumbra
cone = 42.0
if state == 'calm':
    lamp('key', top, 7000, (1.0, 1.0, 1.0), 1.2, aim, cone)
    lamp('fill', (0.0, -10.0, 1.5), 60, (0.9, 0.93, 1.0), 9.0)
    lamp('bounce', (0.0, 0.0, -6.0), 200, (0.92, 0.94, 1.0), 6.0, (0, 0, 0))
elif state == 'rain':
    lamp('key', top, 2600, (0.74, 0.81, 0.95), 1.5, aim, cone + 4)
    lamp('fill', (0.0, -10.0, 1.5), 50, (0.7, 0.78, 0.95), 9.0)
    lamp('bounce', (0.0, 0.0, -6.0), 120, (0.8, 0.85, 1.0), 6.0, (0, 0, 0))
else:
    lamp('key', top, 9000, (1.0, 0.88, 0.66), 1.2, aim, cone)
    lamp('rim', (-1.5, 5.0, 7.5), 4000, (1.0, 0.8, 0.5), 2.4, (0, 0, 0.4))
    lamp('fill', (0.0, -10.0, 1.5), 80, (1.0, 0.9, 0.78), 9.0)
    lamp('bounce', (0.0, 0.0, -6.0), 300, (1.0, 0.9, 0.75), 6.0, (0, 0, 0))

world = bpy.data.worlds.new('w')
scene.world = world
world.use_nodes = True
bg = world.node_tree.nodes['Background']
bg.inputs['Color'].default_value = (0.05, 0.055, 0.07, 1.0)
bg.inputs['Strength'].default_value = {'calm': 1.0, 'rain': 0.6, 'sun': 1.2}[state]

bpy.ops.render.render(write_still=True)
print('[cloud] rendered', state, size, samples, 'lobes', len(lobes), '->', out)
