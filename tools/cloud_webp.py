"""Turn the three Blender renders (tools/cloud_render.py) into the page's WebPs, with a tone curve per weather.

The gamma lifts the calm render's luminance to the reference's (p10/p50/p90 98/150/229 -> 130/175/236); rain stays
darker on purpose. Alpha is kept as rendered.

    py tools/cloud_webp.py <folder with final_calm.png, final_rain.png, final_sun.png>
"""
import os
import sys

import numpy as np
from PIL import Image

GAMMA = {'calm': 0.71, 'rain': 0.9, 'sun': 0.8}
src = sys.argv[1]
site = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for state, g in GAMMA.items():
    a = np.asarray(Image.open(os.path.join(src, 'final_%s.png' % state)).convert('RGBA')).astype(np.float32) / 255
    a[..., :3] = a[..., :3] ** g
    out = os.path.join(site, 'cloud_%s.webp' % state)
    Image.fromarray((a * 255 + 0.5).clip(0, 255).astype(np.uint8)).save(out, 'WEBP', quality=88, method=6)
    print(state, out, os.path.getsize(out))
