# The home page scene (2026-11-08)

A cloud floats over a mirror-still sea at night; a cinder smoulders in its base. Scrolling down, the cinder catches:
the cloud's belly and its reflection glow warmer, embers fall from the base and go out where they meet their own
reflection on the water (each leaves a faint ring). Stars twinkle in the sky and, fainter, in the water.

## How it is made

- `tools/ember_render.py` (Blender Cycles, the cloud grid `tools/cloud.vdb` from `cloud_render.py`) renders, from
  one fixed level camera, separate light passes as linear float: `moon` (a sun from the left and a little behind,
  35 deg up, plus a graded night sky), `ember` (eight point lights just inside the cloud's flat base, `COALS`, plus a
  thin mist around the cloud for a warm halo), `mask` (white where the water shows). Two framings: `wide`
  (3200 x 1334, cloud right of centre) and `tall` (1200 x 2600, cloud low, sky above for the words).
  ~1.5 min (moon) and ~4-5 min (ember) each at 256 samples on the RTX 3070.
- `tools/ember_webp.py <folder> publish` stores each pass with its own scale as sRGB WebP (`scene/*.webp`), writes
  `scene/scene.json` (camera: horizon, cloud centre, units, water height, the scales) and `scene/still_*.jpg` (the
  opening state, shown before WebGL starts and as the share image). `preview <aspect> <moon w> <ember w> <out>`
  mixes passes to judge them without the browser.
- `index.html` adds the passes in linear light in a WebGL shader (light adds, so relighting is exact), weights from
  the scroll (cinder 0.045 -> 0.42, moon 1 -> 0.85, a slow flicker), ripples the water below the horizon, draws
  stars, then the same display curve as `ember_webp.py display()`. The ember rain is a 2D canvas on top, projected
  with the camera numbers in scene.json. The shipped page has no comments (production rule); this file explains it.

## Lessons from building it

- Volume emission inside the dense cloud made dark round "craters" (and a face) wherever front billows sat in front
  of the glow; lights just inside the base look natural (a cloud lit from below), so the cinder is lights only.
- A moon from the front made the cloud a white cotton ball; from behind a dark rock; from the left it has form.
- Blender matches option prefixes even after `--` (`--cy` = `--cycles-*`): use distinct names (`--emit-*`).
- Headless Chrome screenshots: the top of the page works (`--virtual-time-budget`), scrolled ones come out blank,
  and only a few frames run; windows are at least ~500 px wide, so check phones in the browser pane's mobile view.
