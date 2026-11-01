"""Draws the app icon: the brand's plain white mark (media.cookedcoke.co,
branding-assets/cookedcoke-brand-final/cookedcoke-small-logo-1024-white.png) on a full-bleed
Fizzy red gradient. iOS rounds the corners itself.

Run:  python3 make-icon.py   (needs Pillow and numpy). Writes the 1024px icon into the asset catalog."""
import numpy as np
from pathlib import Path
from PIL import Image

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "Fizzy/Assets.xcassets/AppIcon.appiconset/icon-1024.png"
N = 1024
MARK = 0.62          # how much of the icon the mark (C and bubbles) fills

# background: radial gradient, bright red at the upper left to deep red in the far corner
yy, xx = np.mgrid[0:N, 0:N].astype(np.float32)
d = np.clip(np.hypot(xx - 330, yy - 210) / np.hypot(1024 - 330, 1024 - 210), 0, 1) ** 0.9
c0, c1 = np.array([255, 0, 2], np.float32), np.array([176, 0, 0], np.float32)
img = Image.fromarray((c0 * (1 - d[..., None]) + c1 * d[..., None]).astype(np.uint8)).convert("RGBA")

# the white file is the mark filling its canvas: crop to the mark, then centre it at MARK size
mark = Image.open(HERE / "cookedcoke-mark-white-1024.png").convert("RGBA")
mark = mark.crop(mark.getchannel("A").getbbox())
scale = N * MARK / max(mark.size)
mark = mark.resize((round(mark.width * scale), round(mark.height * scale)), Image.LANCZOS)
img.alpha_composite(mark, ((N - mark.width) // 2, (N - mark.height) // 2))

img.convert("RGB").save(OUT)   # app icons have no transparency
print("wrote", OUT)
