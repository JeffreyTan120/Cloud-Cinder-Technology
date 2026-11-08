"""The scene passes (tools/ember_render.py) -> the page's images, and preview mixes to judge them.

    py tools/ember_webp.py <folder> preview <aspect> <moon weight> <ember weight> <out.png>
    py tools/ember_webp.py <folder> publish            (writes scene/*.webp + scene/scene.json into the site)

Each pass is stored with its own scale (its 99.8th percentile maps to 0.92, sRGB encoded), so the dark night keeps its
precision; the page undoes the scale, adds the passes with the scroll's weights and applies the same display curve
as display() here.
"""
import json
import os
import sys

import numpy as np
from PIL import Image

src = sys.argv[1]
site = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GAIN = {'moon': 0.9, 'ember': 1.0}       # the passes' balance at weight 1 (the page uses the same numbers)


def load(name):
    return np.load(os.path.join(src, name + '.npy')).astype(np.float32)


def srgb(x):
    x = np.clip(x, 0, 1)
    return np.where(x <= 0.0031308, 12.92 * x, 1.055 * x ** (1 / 2.4) - 0.055)


def display(L):
    """Exposure is in GAIN; a soft shoulder (no hard clip on the cinder) with a little lift in the blacks."""
    L = L / (1.0 + L / 4.0)                              # extended Reinhard, white at ~4
    L = L * (1.0 + L / 16.0) / (1.0 + L / 4.0) * 1.25
    return srgb(L)


mode = sys.argv[2]
if mode == 'preview':
    aspect, wm, we, outp = sys.argv[3], float(sys.argv[4]), float(sys.argv[5]), sys.argv[6]
    L = wm * GAIN['moon'] * load('p_moon' if aspect == 'wide' else 'p_moon_' + aspect) \
        + we * GAIN['ember'] * load('p_ember' if aspect == 'wide' else 'p_ember_' + aspect)
    rgb = display(L)
    Image.fromarray((rgb * 255 + 0.5).astype(np.uint8)).save(outp)
    lum = (L * [0.2126, 0.7152, 0.0722]).sum(-1)
    print(outp, 'lum p10/50/90/99.9', np.percentile(lum, [10, 50, 90, 99.9]).round(4))
else:
    os.makedirs(os.path.join(site, 'scene'), exist_ok=True)
    manifest = {}
    for aspect in (sys.argv[3:] or ['wide', 'tall']):
        info = json.load(open(os.path.join(src, 'final_moon_%s.json' % aspect)))
        entry = dict(horizon=info['horizon'], cloud=info['cloud'], unit=info['unit'], size=info['size'], zw=info['zw'], scale={})
        for p in ('moon', 'ember'):
            a = load('final_%s_%s' % (p, aspect))
            top = float(np.percentile(a.max(-1), 99.8)) / 0.92
            entry['scale'][p] = top
            rgb = srgb(a / top)
            o = os.path.join(site, 'scene', '%s_%s.webp' % (p, aspect))
            Image.fromarray((rgb * 255 + 0.5).astype(np.uint8)).save(o, 'WEBP', quality=90, method=6)
            print(o, os.path.getsize(o), 'scale', round(top, 4))
        m = np.load(os.path.join(src, 'final_mask_%s.npy' % aspect)).mean(-1)
        o = os.path.join(site, 'scene', 'water_%s.webp' % aspect)
        Image.fromarray((np.clip(m, 0, 1) * 255 + 0.5).astype(np.uint8)).convert('RGB').save(o, 'WEBP', quality=80, method=6)
        print(o, os.path.getsize(o))
        # a still for before the scene starts (and browsers without WebGL): the opening state
        L = 1.0 * GAIN['moon'] * load('final_moon_%s' % aspect) + 0.07 * GAIN['ember'] * load('final_ember_%s' % aspect)
        o = os.path.join(site, 'scene', 'still_%s.jpg' % aspect)
        Image.fromarray((display(L) * 255 + 0.5).astype(np.uint8)).save(o, 'JPEG', quality=86, optimize=True, progressive=True)
        manifest[aspect] = entry
    manifest['gain'] = GAIN
    with open(os.path.join(site, 'scene', 'scene.json'), 'w') as f:
        json.dump(manifest, f, indent=1)
    print(json.dumps(manifest, indent=1))
