"""The site's cloud: a cumulus modelled as a density grid in numpy, saved as OpenVDB, rendered in Blender Cycles.

Shape (after the reference: a cauliflower heap in a glass box): a hand-placed silhouette of big lobes (a tall peak
left of centre, stepped shoulders, a broad, flatter base), then two generations of billows grown on its surface
(medium lobes on the big ones, small ones on those; mostly on top and the sides, few underneath), then fine fbm on
the edge. The lobes are signed distance spheres joined with a smooth minimum, so the creases between billows stay
sharp but not cut. The density ramps up over a thin band inside the surface (a crisp, slightly fuzzy edge, not fog).
A second, flatter, softer cloud sits behind on the right. One grid serves all three weathers; only the light changes,
so the page can cross-fade the renders without ghosting.

    blender -b --factory-startup -P tools/cloud_render.py -- --state calm --out cloud_calm.png [--size 1600 1040] [--samples 512] [--rebuild]

Space: box units (the glass box is 1 wide, its centre at y 0.04), x right, y up, z toward the camera; the cloud's
origin is box (0, 0, 0). Camera: orthographic, pitched 9 degrees down, the frame FRAME box units wide and centred on
the origin, so the page draws the image FRAME box widths wide, centred on the projected origin.
"""
import math
import os
import sys

import bpy
import numpy as np
import openvdb as vdb

args = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []


def opt(name, default, n=1):
    if name not in args:
        return default
    i = args.index(name) + 1
    return args[i] if n == 1 else args[i:i + n]


state = opt('--state', 'calm')
out = opt('--out', 'cloud.png')
size = [int(v) for v in opt('--size', ['1800', '1170'], 2)]
samples = int(opt('--samples', '512'))
here = os.path.dirname(os.path.abspath(__file__))
grid_path = opt('--vdb', os.path.join(here, 'cloud.vdb'))
S = 3.0                       # scene units per box unit
FRAME = 1.5                   # frame width in box units
VS = float(opt('--voxel', '0.0032'))

# ---------------------------------------------------------------- the shape (view space: x right, y up, z to camera)
rng = np.random.default_rng(int(opt('--seed', '8')))

MAIN = [  # x, y, z, r (fitted to the reference's outline: peak right of centre, stepped shoulders, flat base)
    (0.05, 0.19, -0.02, 0.14),    # the peak
    (-0.08, 0.14, 0.03, 0.13),
    (-0.22, 0.06, 0.02, 0.135),   # the left shoulder, round, then nearly straight down
    (-0.32, -0.03, 0.03, 0.115),
    (-0.34, -0.10, 0.04, 0.11),
    (0.20, 0.08, -0.01, 0.13),    # the right shoulder, then down
    (0.31, 0.01, 0.02, 0.115),
    (0.36, -0.13, 0.02, 0.09),
    (-0.20, -0.19, 0.07, 0.12),   # the base row
    (0.00, -0.19, 0.09, 0.13),
    (0.20, -0.19, 0.06, 0.12),
    (0.00, -0.02, 0.00, 0.25),    # the body
    (-0.12, 0.02, -0.16, 0.17),
    (0.13, 0.00, -0.16, 0.16),
    (-0.08, 0.04, 0.14, 0.14),
    (0.12, -0.04, 0.14, 0.13),
]
BACK = [  # the second cloud: behind right, half hidden, greyer
    (0.33, 0.05, -0.34, 0.12),
    (0.46, 0.06, -0.36, 0.12),
    (0.58, 0.00, -0.33, 0.09),
    (0.40, -0.08, -0.30, 0.10),
    (0.54, -0.10, -0.31, 0.08),
    (0.26, -0.06, -0.38, 0.10),
]


def surface_points(spheres, n, up_min):
    """Points on the surface of a union of spheres (area-weighted), with the sphere normal there."""
    C = np.array([s[:3] for s in spheres])
    R = np.array([s[3] for s in spheres])
    w = R ** 2 / (R ** 2).sum()
    pts, tries = [], 0
    while len(pts) < n and tries < n * 300:
        tries += 1
        i = rng.choice(len(R), p=w)
        d = rng.normal(size=3)
        d /= np.linalg.norm(d)
        if d[1] < up_min:
            continue
        p = C[i] + R[i] * d
        dist = np.linalg.norm(C - p, axis=1)
        dist[i] = 1e9
        if (dist < R * 0.98).any():
            continue
        pts.append((p, d))
    return pts


def grow(base, n, rmin, rmax, up_min, sink, spacing):
    new = []
    for p, d in surface_points(base, n * 4, up_min):
        r = rng.uniform(rmin, rmax) * (1.0 + 0.25 * max(0.0, d[1]))   # bigger billows on top
        c = p - d * r * sink
        if any(np.linalg.norm(c - np.array(q[:3])) < (r + q[3]) * spacing for q in new):
            continue
        new.append((c[0], c[1], c[2], r))
        if len(new) >= n:
            break
    return new


L1 = grow(MAIN, 40, 0.06, 0.09, -0.4, 0.45, 0.7)
L2 = grow(MAIN + L1, 170, 0.026, 0.042, -0.3, 0.45, 0.75)
B1 = grow(BACK, 34, 0.035, 0.055, -0.3, 0.45, 0.7)
print('[cloud] lobes', len(MAIN), len(L1), len(L2), 'back', len(BACK), len(B1))

# ---------------------------------------------------------------- the grid (Blender axes: x, y away, z up)
X0, X1 = -0.62, 0.74
Y0, Y1 = -0.34, 0.56          # Blender y = -view z
Z0, Z1 = -0.36, 0.48          # Blender z = view y
nx, ny, nz = int((X1 - X0) / VS), int((Y1 - Y0) / VS), int((Z1 - Z0) / VS)


def smin_into(a, d, k):
    h = np.clip(0.5 + 0.5 * (d - a) / k, 0.0, 1.0)
    return d + (a - d) * h - k * h * (1.0 - h)


def sdf_of(spheres, kf):
    sdf = np.full((nx, ny, nz), 1.0, np.float32)
    for vx, vy, vz, r in spheres:
        bx, by, bz = vx, -vz, vy
        k = kf * r
        m = r + k + 3 * VS
        i0, i1 = max(0, int((bx - m - X0) / VS)), min(nx, int((bx + m - X0) / VS) + 2)
        j0, j1 = max(0, int((by - m - Y0) / VS)), min(ny, int((by + m - Y0) / VS) + 2)
        k0, k1 = max(0, int((bz - m - Z0) / VS)), min(nz, int((bz + m - Z0) / VS) + 2)
        xs = (X0 + np.arange(i0, i1, dtype=np.float32) * VS - bx)[:, None, None]
        ys = (Y0 + np.arange(j0, j1, dtype=np.float32) * VS - by)[None, :, None]
        zs = (Z0 + np.arange(k0, k1, dtype=np.float32) * VS - bz)[None, None, :]
        d = np.sqrt(xs * xs + ys * ys + zs * zs) - r
        sdf[i0:i1, j0:j1, k0:k1] = smin_into(sdf[i0:i1, j0:j1, k0:k1], d, k)
    return sdf


def vnoise(cell, seed):
    """Smooth value noise on the grid, lattice spacing `cell` box units, 0..1."""
    g = np.random.default_rng(seed).random([int(math.ceil(n * VS / cell)) + 3 for n in (nx, ny, nz)], dtype=np.float32)
    for ax, n in enumerate((nx, ny, nz)):
        t = np.arange(n, dtype=np.float32) * VS / cell
        i = np.floor(t).astype(np.int64)
        f = t - i
        f = f * f * (3 - 2 * f)
        shape = [1, 1, 1]
        shape[ax] = n
        f = f.reshape(shape)
        g = np.take(g, i, axis=ax) * (1 - f) + np.take(g, i + 1, axis=ax) * f
    return g


def fbm(cell, octaves, seed):
    tot, amp, norm = 0.0, 1.0, 0.0
    for o in range(octaves):
        tot = tot + amp * (vnoise(cell / 2 ** o, seed + o) - 0.5)
        norm += amp
        amp *= 0.5
    return tot / norm * 2.0           # about -1..1


def zcoord():
    return (Z0 + np.arange(nz, dtype=np.float32) * VS)[None, None, :]


def build():
    # the main cloud
    sdf = sdf_of(MAIN, 0.45)
    sdf = np.minimum(sdf, sdf_of(L1, 0.3))
    sdf = smin_into(sdf, sdf_of(L2, 0.45), 0.02)
    edge = fbm(0.05, 4, 11)
    sdf += 0.015 * edge                                        # roughen the lobes
    sdf += 0.004 * np.abs(fbm(0.016, 2, 31))                   # cauliflower grain
    sdf += float(opt('--fine', '0.005')) * fbm(0.007, 2, 71)   # cotton on the edge
    base = -0.31 + 0.025 * fbm(0.12, 2, 41)[:, :, :1]          # a flatter, lumpy base
    sdf = np.maximum(sdf, (base - zcoord()) * 0.8)
    band = float(opt('--band', '0.006'))                       # thin: a crisp edge (a thick band reads as blur)
    rho = np.clip(-sdf / band, 0.0, 1.0) ** 0.75
    fringe = np.clip(1.0 + sdf / (band * 1.5), 0.0, 1.0)       # 1 at the edge, 0 deeper in
    wisp = 0.5 + 0.5 * fbm(0.01, 2, 51)
    rho *= 1.0 - fringe * 0.55 * (1.0 - wisp)
    halo = float(opt('--halo', '0.12'))                        # a faint cotton fuzz just outside the edge
    rho += np.clip(1.0 - sdf / 0.012, 0.0, 1.0) * (sdf > 0) * halo * wisp
    rho *= 0.88 + 0.12 * (0.5 + 0.5 * edge)                    # a little variation inside
    # the second cloud: softer, thinner, its base higher
    sdb = smin_into(sdf_of(BACK, 0.5), sdf_of(B1, 0.3), 0.02)
    sdb += 0.018 * edge + 0.008 * np.abs(fbm(0.02, 2, 61))
    sdb = np.maximum(sdb, (-0.2 - zcoord()) * 0.8)
    bandb = 0.025
    rb = np.clip(-sdb / bandb, 0.0, 1.0) ** 0.9
    rb *= np.clip(sdf / 0.02, 0.0, 1.0)            # nothing where it overlaps the main cloud
    for arr, path in ((rho, grid_path), (rb, back_path)):
        g = vdb.FloatGrid()
        g.copyFromArray(arr.astype(np.float32), tolerance=0.002)
        g.name = 'density'
        g.transform = vdb.createLinearTransform(voxelSize=VS * S)
        vdb.write(path, grids=[g])
        print('[cloud] grid', (nx, ny, nz), 'active', g.activeVoxelCount(), '->', path)


back_path = grid_path.replace('.vdb', '_back.vdb')
if '--rebuild' in args or not os.path.exists(grid_path) or not os.path.exists(back_path):
    build()

# ---------------------------------------------------------------- the render
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
scene.cycles.volume_bounces = int(opt('--bounces', '32'))   # light bouncing inside the cloud: the white, luminous body
scene.cycles.max_bounces = 32
scene.cycles.volume_max_steps = 1024
scene.render.film_transparent = True
scene.render.resolution_x, scene.render.resolution_y = size
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = 'PNG'
scene.render.image_settings.color_mode = 'RGBA'
scene.render.filepath = out
scene.view_settings.view_transform = 'Standard'
scene.view_settings.exposure = float(opt('--exposure', {'calm': '0.25', 'rain': '-0.1', 'sun': '-0.25'}[state]))


def link(o):
    scene.collection.objects.link(o)
    return o


def volume(name, path, density, colour):
    vd = bpy.data.volumes.new(name)
    vd.filepath = path
    vol = link(bpy.data.objects.new(name, vd))
    vol.location = (X0 * S, Y0 * S, Z0 * S)
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    pv = nt.nodes.new('ShaderNodeVolumePrincipled')
    pv.inputs['Color'].default_value = colour
    pv.inputs['Density'].default_value = density
    pv.inputs['Anisotropy'].default_value = float(opt('--aniso', '0.45'))
    outn = nt.nodes.new('ShaderNodeOutputMaterial')
    nt.links.new(pv.outputs['Volume'], outn.inputs['Volume'])
    vd.materials.append(mat)


volume('cloud', grid_path, float(opt('--density', {'calm': '35', 'rain': '42', 'sun': '33'}[state])), (1.0, 1.0, 1.0, 1.0))
bc = float(opt('--backcol', '0.7'))
volume('back', back_path, float(opt('--bdensity', '18')), (bc, bc, bc * 1.02, 1.0))

cam = bpy.data.cameras.new('cam')
cam.type = 'ORTHO'
cam.ortho_scale = FRAME * S
camo = link(bpy.data.objects.new('cam', cam))
pitch, dist = math.radians(9), 14.0
camo.location = (0.0, -math.cos(pitch) * dist, math.sin(pitch) * dist)
camo.rotation_euler = (math.radians(90) - pitch, 0.0, 0.0)
scene.camera = camo


def lamp(name, loc, energy, colour, sz, look=(0, 0, 0), spot=None):
    ld = bpy.data.lights.new(name, 'SPOT' if spot else 'AREA')
    ld.energy = energy
    ld.color = colour
    if spot:
        ld.spot_size = math.radians(spot)
        ld.spot_blend = 0.5
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


key_e = float(opt('--key', '9000'))
fill_e = float(opt('--fill', '80'))
kx, ky = float(opt('--kx', '-1.0')), float(opt('--ky', '6.0'))  # above and well behind: a glowing outline, a grey core
top, aim = (kx, ky, 9.0), (0.0, 0.0, 0.3)
if state == 'calm':
    lamp('key', top, key_e, (1.0, 1.0, 1.0), 1.0, aim, 40)
    lamp('fill', (0.0, -9.0, 3.0), fill_e, (0.84, 0.9, 1.0), 8.0, (0, 0, 0.2))
    lamp('bounce', (0.0, -1.0, -5.0), 260, (0.86, 0.92, 1.0), 5.0, (0, 0, 0))
elif state == 'rain':
    lamp('key', top, key_e * 0.42, (0.74, 0.81, 0.95), 1.4, aim, 44)
    lamp('fill', (0.0, -9.0, 3.0), fill_e * 0.7, (0.7, 0.78, 0.95), 8.0, (0, 0, 0.2))
    lamp('bounce', (0.0, -1.0, -5.0), 140, (0.8, 0.85, 1.0), 5.0, (0, 0, 0))
else:
    lamp('key', top, key_e * 1.25, (1.0, 0.88, 0.66), 1.0, aim, 40)
    lamp('rim', (-1.5, 5.0, 7.5), 3500, (1.0, 0.8, 0.5), 2.4, (0, 0, 0.4))
    lamp('fill', (0.0, -9.0, 3.0), fill_e * 1.2, (1.0, 0.9, 0.78), 8.0, (0, 0, 0.2))
    lamp('bounce', (0.0, -1.0, -5.0), 320, (1.0, 0.9, 0.75), 5.0, (0, 0, 0))

world = bpy.data.worlds.new('w')
scene.world = world
world.use_nodes = True
bg = world.node_tree.nodes['Background']
bg.inputs['Color'].default_value = (0.05, 0.055, 0.07, 1.0)
bg.inputs['Strength'].default_value = {'calm': 1.0, 'rain': 0.6, 'sun': 1.2}[state]

bpy.ops.render.render(write_still=True)
print('[cloud] rendered', state, size, samples, '->', out)
