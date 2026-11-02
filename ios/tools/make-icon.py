"""Draws the app icon: the brand's plain white mark (media.cookedcoke.co,
branding-assets/cookedcoke-brand-final/cookedcoke-small-logo-1024-white.png) on a full-bleed
Fizzy red gradient. iOS rounds the corners itself.

Run:  python3 make-icon.py   (needs Pillow and numpy). Writes the 1024px icon into the asset catalog.
      python3 make-icon.py --student   The student app's (My CCC): the mark in black on the student site's sun yellow."""
import sys

import numpy as np
from pathlib import Path
from PIL import Image

HERE = Path(__file__).resolve().parent
STUDENT = "--student" in sys.argv
OUT = HERE.parent / ("Student" if STUDENT else "Fizzy") / "Assets.xcassets/AppIcon.appiconset/icon-1024.png"
N = 1024
MARK = 0.62          # how much of the icon the mark (C and bubbles) fills

# background: radial gradient, bright red at the upper left to deep red in the far corner
yy, xx = np.mgrid[0:N, 0:N].astype(np.float32)
d = np.clip(np.hypot(xx - 330, yy - 210) / np.hypot(1024 - 330, 1024 - 210), 0, 1) ** 0.9
c0, c1 = np.array([255, 0, 2], np.float32), np.array([176, 0, 0], np.float32)
red = (c0 * (1 - d[..., None]) + c1 * d[..., None]).astype(np.uint8)
img = Image.fromarray(red).convert("RGBA")
if STUDENT:
    # sun-yellow glow at the upper left (the site's --sun) to warm orange in the far corner
    sun0, sun1 = np.array([255, 214, 92], np.float32), np.array([255, 150, 50], np.float32)
    img = Image.fromarray((sun0 * (1 - d[..., None]) + sun1 * d[..., None]).astype(np.uint8)).convert("RGBA")
    # the same mark as the staff app, in the student site's ink black
    mark = Image.open(HERE / "cookedcoke-mark-white-1024.png").convert("RGBA")
    mark = mark.crop(mark.getchannel("A").getbbox())
    scale = N * MARK / max(mark.size)
    mark = mark.resize((round(mark.width * scale), round(mark.height * scale)), Image.LANCZOS)
    fill = Image.new("RGBA", mark.size, (27, 26, 31, 255))
    fill.putalpha(mark.getchannel("A"))
    img.alpha_composite(fill, ((N - mark.width) // 2, (N - mark.height) // 2))
else:
    # the white file is the mark filling its canvas: crop to the mark, then centre it at MARK size
    mark = Image.open(HERE / "cookedcoke-mark-white-1024.png").convert("RGBA")
    mark = mark.crop(mark.getchannel("A").getbbox())
    scale = N * MARK / max(mark.size)
    mark = mark.resize((round(mark.width * scale), round(mark.height * scale)), Image.LANCZOS)
    img.alpha_composite(mark, ((N - mark.width) // 2, (N - mark.height) // 2))

img.convert("RGB").save(OUT)   # app icons have no transparency
print("wrote", OUT)
