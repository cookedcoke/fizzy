"""Red Coke / Black Coke member card concepts, credit-card size (85.6 x 54 mm), built from the
Cooked Coke faceted wallpaper (its 1168 triangles, recoloured per tier).

Run:  python3 make_cards.py   (needs Pillow; reads the wallpaper and logo from ~/Documents/brand)
Writes 1536 x 969 PNGs (the ISO ID-1 ratio) into renders/, and a contact sheet.
"""
import math
import random
import re
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
BRAND = Path.home() / "Documents/brand/wallet-cards/cookedcoke/src"
OUT = HERE / "renders"
OUT.mkdir(exist_ok=True)
W, H, SS = 1536, 969, 3
R = 58  # corner radius: 3.18 mm on a real card

FONTS = "/System/Library/Fonts"
SUP = FONTS + "/Supplemental"


def font(name, size, index=0):
    return ImageFont.truetype(name, size, index=index)


HEAVY = lambda s: font(SUP + "/Futura.ttc", s, 4)          # Futura Condensed ExtraBold
BOLD = lambda s: font(SUP + "/Futura.ttc", s, 2)           # Futura Bold
MED = lambda s: font(SUP + "/Futura.ttc", s, 0)            # Futura Medium
MONO = lambda s: font(FONTS + "/SFNSMono.ttf", s)

pat = r'points="([^"]+)" fill="(#[0-9A-Fa-f]{6})"'
TRIS = []
for pts, col in re.findall(pat, (BRAND / "cookedcoke-wallpaper-2560x1600.svg").read_text()):
    p = [tuple(map(float, q.split(","))) for q in pts.split()]
    TRIS.append((p, col))
s = max(W / 2560, H / 1600)
XO, YO = (2560 * s - W) / 2, (1600 * s - H) / 2
hexrgb = lambda h: tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def mix(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def facets(colour_of, seed=7, scale=1.0, ox=0.0, oy=0.0):
    """Draw the wallpaper's triangles, each coloured by colour_of(cx, cy, shade, rnd) where cx, cy
    are 0..1 across the card and shade is the facet's own lightness in the dark wallpaper (0..1)."""
    img = Image.new("RGB", (W * SS, H * SS), "#000")
    dr = ImageDraw.Draw(img)
    rnd = random.Random(seed)
    for p, col in TRIS:
        q = [((x * s - XO) * scale + ox, (y * s - YO) * scale + oy) for x, y in p]
        cx = sum(x for x, _ in q) / 3 / W
        cy = sum(y for _, y in q) / 3 / H
        r, g, b = hexrgb(col)
        shade = min(1, max(0, (min(r, g, b) - 5) / 40))   # greys run about #080808..#2C2C2C
        c = colour_of(cx, cy, shade, rnd)
        dr.polygon([(x * SS, y * SS) for x, y in q], fill=c, outline=c)
    return img.resize((W, H), Image.LANCZOS).convert("RGBA")


LOGO = Image.open(BRAND / "cookedcoke-full-logo-white-3000w.png").convert("RGBA")


def tier_logo(tier, width):
    return logo(width, (255, 255, 255, 255) if tier == "red" else None)


def logo(width, colour=None):
    im = LOGO.resize((width, round(LOGO.height * width / LOGO.width)), Image.LANCZOS)
    if colour:
        solid = Image.new("RGBA", im.size, colour)
        solid.putalpha(im.split()[3])
        im = solid
    return im


def shadowed(base, im, pos, blur=12, alpha=0.5, off=(3, 5)):
    pad = blur * 3
    sh = Image.new("RGBA", (im.width + 2 * pad, im.height + 2 * pad), (0, 0, 0, 0))
    a = im.split()[3].point(lambda v: int(v * alpha))
    blk = Image.new("RGBA", im.size, (0, 0, 0, 255))
    blk.putalpha(a)
    sh.paste(blk, (pad, pad))
    base.alpha_composite(sh.filter(ImageFilter.GaussianBlur(blur)), (pos[0] - pad + off[0], pos[1] - pad + off[1]))
    base.alpha_composite(im, pos)


def text(base, xy, s, f, fill, spacing=0, anchor="la", shadow=False):
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    if spacing:   # tracked caps, drawn a letter at a time
        x, y = xy
        if anchor[0] == "r":
            x -= tracked_width(s, f, spacing)
        for ch in s:
            d.text((x, y), ch, font=f, fill=fill, anchor="l" + anchor[1])
            x += f.getlength(ch) + spacing
    else:
        d.text(xy, s, font=f, fill=fill, anchor=anchor)
    if shadow:
        sh = Image.new("RGBA", base.size, (0, 0, 0, 0))
        blk = Image.new("RGBA", base.size, (0, 0, 0, 255))
        blk.putalpha(layer.split()[3].point(lambda v: int(v * 0.45)))
        sh.alpha_composite(blk, (3, 5))
        base.alpha_composite(sh.filter(ImageFilter.GaussianBlur(10)))
    base.alpha_composite(layer)


def tracked_width(s, f, spacing):
    return sum(f.getlength(c) for c in s) + spacing * (len(s) - 1)


def finish(card, name):
    """Round the corners, add a hairline edge, save, and return the card for the sheet."""
    mask = Image.new("L", (W * 2, H * 2), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, W * 2 - 1, H * 2 - 1], R * 2, fill=255)
    mask = mask.resize((W, H), Image.LANCZOS)
    edge = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(edge).rounded_rectangle([1, 1, W - 2, H - 2], R, outline=(255, 255, 255, 34), width=2)
    card.alpha_composite(edge)
    card.putalpha(ImageChops.multiply(card.split()[3], mask))
    card.save(OUT / f"{name}.png", optimize=True)
    return card


def gloss(card, strength=0.16, angle=-24):
    """A soft diagonal sheen, as on a laminated card."""
    g = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(g)
    for i in range(W + H):
        t = i / (W + H)
        v = int(255 * strength * max(0, 1 - abs(t - 0.32) / 0.22) ** 2)
        d.line([(i, 0), (i - H * math.tan(math.radians(-angle)), H)], fill=v, width=2)
    white = Image.new("RGBA", (W, H), (255, 255, 255, 0))
    white.putalpha(g.filter(ImageFilter.GaussianBlur(30)))
    card.alpha_composite(white)


def chip(card, x, y, tone):
    """A contact-chip outline, like a real card's (just a nod to it: there's no chip)."""
    w, h = 150, 116
    layer = Image.new("RGBA", card.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    base, line = tone
    d.rounded_rectangle([x, y, x + w, y + h], 20, fill=base)
    for yy in (y + h * 0.33, y + h * 0.66):
        d.line([(x, yy), (x + w * 0.32, yy)], fill=line, width=3)
        d.line([(x + w * 0.68, yy), (x + w, yy)], fill=line, width=3)
    d.line([(x + w * 0.32, y), (x + w * 0.32, y + h)], fill=line, width=3)
    d.line([(x + w * 0.68, y), (x + w * 0.68, y + h)], fill=line, width=3)
    d.rounded_rectangle([x + w * 0.32, y + h * 0.33, x + w * 0.68, y + h * 0.66], 8, outline=line, width=3)
    card.alpha_composite(layer)


TIER = {
    "black": dict(name="BLACK COKE", pct="20%", field=(17, 17, 17), deep=(6, 6, 6), lit=(46, 46, 46),
                  accent=(214, 18, 24), accent2=(140, 12, 14), ink=(255, 255, 255)),
    "red": dict(name="RED COKE", pct="10%", field=(196, 14, 22), deep=(110, 6, 10), lit=(238, 40, 44),
                accent=(20, 20, 20), accent2=(60, 6, 8), ink=(255, 255, 255)),
}
MEMBER, UNTIL, TERM = "AMELIA WILLIAMS", "11 DEC 2026", "TERM 4"


# ---- A. Wallpaper: the company card's composition, recoloured per tier --------------------------

def a_wallpaper(tier):
    t = TIER[tier]
    rx, ry = 0.36, 0.44   # bright core from the bottom-right corner

    def colour(cx, cy, shade, rnd):
        d = ((1 - cx) / rx) ** 2 + ((1 - cy) / ry) ** 2
        j = rnd.uniform(-0.12, 0.12)
        base = mix(t["deep"], t["lit"], shade)
        if d < 1 + j:                       # core
            return mix((235, 232, 228), (255, 255, 255), rnd.random()) if tier == "black" else \
                mix((20, 20, 20), (52, 52, 52), shade)
        if d < 1.9 + j:                     # ring
            k = (d - 1 - j) / 0.9
            return mix(mix(t["accent"], t["accent2"], rnd.random() * 0.6), base, k ** 1.5)
        return base

    card = facets(colour)
    shadowed(card, tier_logo(tier, 470), (78, 66))
    text(card, (80, 760), t["name"], HEAVY(118), t["ink"], spacing=4, anchor="ls", shadow=True)
    text(card, (84, 828), f"{t['pct']} OFF EVERYTHING  ·  {TERM}", BOLD(38), (255, 255, 255, 220), spacing=3, anchor="ls", shadow=True)
    text(card, (84, 905), MEMBER, MONO(36), (255, 255, 255, 200), spacing=2, anchor="ls")
    dark = (17, 17, 17) if tier == "black" else (255, 255, 255)
    text(card, (W - 84, 905), f"TO {UNTIL}", MONO(36), dark, spacing=2, anchor="rs")
    return finish(card, f"A-wallpaper-{tier}")


# ---- B. Big number: a calm field, the facets only in a diagonal band, the discount huge -----------

def b_big_number(tier):
    t = TIER[tier]

    def colour(cx, cy, shade, rnd):
        band = cx * 0.62 + (1 - cy) * 0.38          # 0 bottom-left .. 1 top-right
        k = max(0, 1 - abs(band - 0.66) / 0.17 + rnd.uniform(-0.15, 0.15))
        base = mix(t["field"], t["deep"], 0.35 + shade * 0.3)
        if k <= 0:
            return base
        hot = mix(t["accent2"], t["accent"], shade + rnd.random() * 0.5) if tier == "black" else \
            mix((40, 8, 10), (250, 70, 70), min(1, shade + rnd.random() * 0.6))
        return mix(base, hot, min(1, k))

    card = facets(colour, seed=11)
    shadowed(card, tier_logo(tier, 360), (78, 70))
    f = HEAVY(560)
    text(card, (W - 70, 470), t["pct"], f, (255, 255, 255, 235), anchor="rm", shadow=True)
    text(card, (82, 828), t["name"], HEAVY(92), t["ink"], spacing=6, anchor="ls")
    text(card, (84, 905), f"{MEMBER}  ·  {TERM}  ·  TO {UNTIL}", MONO(32), (255, 255, 255, 190), spacing=1, anchor="ls")
    return finish(card, f"B-big-number-{tier}")


# ---- C. Embossed: tone-on-tone facets like a pressed metal card, one bright edge -----------------

def c_embossed(tier):
    t = TIER[tier]
    lo, hi = (t["deep"], mix(t["field"], t["lit"], 0.5))

    def colour(cx, cy, shade, rnd):
        c = mix(lo, hi, 0.25 + shade * 0.75)
        # light falls from the top-left
        return mix(c, (255, 255, 255), max(0, 0.08 - (cx + cy) * 0.05))

    card = facets(colour, seed=3, scale=1.35, ox=-260, oy=-140)
    gloss(card, 0.12)
    # a single accent stripe down the right, like the edge of a can
    stripe = Image.new("RGBA", card.size, (0, 0, 0, 0))
    ImageDraw.Draw(stripe).rectangle([W - 150, 0, W - 118, H], fill=t["accent"] + (255,))
    card.alpha_composite(stripe)
    shadowed(card, tier_logo(tier, 400), (78, 70), alpha=0.6)
    chip(card, 84, 330, ((206, 178, 120, 255), (150, 120, 70, 255)) if tier == "black" else
         ((215, 215, 220, 255), (150, 150, 158, 255)))
    text(card, (84, 740), t["name"], BOLD(84), t["ink"], spacing=10, anchor="ls", shadow=True)
    text(card, (86, 806), f"{t['pct']} OFF EVERY SALE", MED(40), (255, 255, 255, 215), spacing=5, anchor="ls")
    text(card, (86, 905), MEMBER, MONO(34), (255, 255, 255, 190), spacing=3, anchor="ls")
    text(card, (W - 190, 905), f"{TERM} · TO {UNTIL}", MONO(34), (255, 255, 255, 190), spacing=1, anchor="rs")
    return finish(card, f"C-embossed-{tier}")


# ---- D. Shard: a solid colour block broken by a wedge of facets from the wallpaper ---------------

def d_shard(tier):
    t = TIER[tier]
    other = TIER["red" if tier == "black" else "black"]

    def colour(cx, cy, shade, rnd):
        # the wedge: inside a slanted line from top 58% to bottom 34%, jagged by the triangles
        edge = 0.58 - 0.24 * cy + rnd.uniform(-0.035, 0.035)
        if cx < edge:
            return mix(t["field"], t["deep"], 0.15 + shade * 0.1)   # nearly flat
        k = min(1, (cx - edge) / 0.25)
        lit = mix(other["deep"], other["lit"], shade) if tier == "red" else \
            mix((120, 8, 12), (236, 30, 36), min(1, shade * 0.8 + rnd.random() * 0.5))
        return mix(mix(t["field"], t["deep"], 0.2), lit, 0.35 + 0.65 * k)

    card = facets(colour, seed=21)
    gloss(card, 0.1)
    shadowed(card, tier_logo(tier, 380), (78, 70))
    text(card, (80, 560), t["name"].split()[0], HEAVY(220), t["ink"], spacing=2, anchor="ls", shadow=True)
    text(card, (86, 662), "COKE", HEAVY(96), (255, 255, 255, 170), spacing=30, anchor="ls")
    text(card, (86, 790), f"{t['pct']} off everything, all term", MED(44), (255, 255, 255, 230), anchor="ls")
    text(card, (86, 905), MEMBER, MONO(34), (255, 255, 255, 200), spacing=3, anchor="ls")
    text(card, (W - 84, 905), UNTIL, MONO(34), (255, 255, 255, 230), spacing=2, anchor="rs")
    return finish(card, f"D-shard-{tier}")


VARIANTS = [("A", "Wallpaper", a_wallpaper), ("B", "Big number", b_big_number),
            ("C", "Embossed", c_embossed), ("D", "Shard", d_shard)]

if __name__ == "__main__":
    cards = {(k, tier): fn(tier) for k, _, fn in VARIANTS for tier in ("black", "red")}
    # Contact sheet: each variant as a Black / Red pair
    cw, ch, gap, lab = 720, round(720 * H / W), 48, 70
    sheet = Image.new("RGB", (gap + 2 * (cw + gap), gap + len(VARIANTS) * (ch + lab + gap)), (236, 233, 228))
    d = ImageDraw.Draw(sheet)
    for i, (k, title, _) in enumerate(VARIANTS):
        y = gap + i * (ch + lab + gap)
        d.text((gap, y + 10), f"{k}  ·  {title}", font=BOLD(40), fill=(22, 19, 15))
        for j, tier in enumerate(("black", "red")):
            c = cards[(k, tier)].resize((cw, ch), Image.LANCZOS)
            x = gap + j * (cw + gap)
            sh = Image.new("RGBA", (cw + 80, ch + 80), (0, 0, 0, 0))
            ImageDraw.Draw(sh).rounded_rectangle([40, 48, cw + 40, ch + 48], 28, fill=(0, 0, 0, 90))
            sheet.paste(sh.filter(ImageFilter.GaussianBlur(16)), (x - 40, y + lab - 40), sh.filter(ImageFilter.GaussianBlur(16)))
            sheet.paste(c, (x, y + lab), c)
    sheet.save(OUT / "sheet.png", optimize=True)
    print("wrote", len(cards), "cards and renders/sheet.png")


# ---- Premium wallpaper takes (round 2): quiet tonal facets lit from the top left, foil type -------

def foil(base, xy, s, f, spacing, anchor="ls", stops=((250, 250, 250), (168, 168, 172), (236, 236, 238), (140, 140, 146))):
    """Text filled with a brushed-metal gradient, with a faint dark bevel under it."""
    mask = Image.new("L", base.size, 0)
    d = ImageDraw.Draw(mask)
    x, y = xy
    if anchor[0] == "r":
        x -= tracked_width(s, f, spacing)
    for ch in s:
        d.text((x, y), ch, font=f, fill=255, anchor="l" + anchor[1])
        x += f.getlength(ch) + spacing
    box = mask.getbbox()
    grad = Image.new("RGBA", base.size, (0, 0, 0, 0))
    if box:
        x0, y0, x1, y1 = box
        g = Image.new("RGB", (1, 256))
        for i in range(256):
            t = i / 255 * (len(stops) - 1)
            k = min(int(t), len(stops) - 2)
            g.putpixel((0, i), mix(stops[k], stops[k + 1], t - k))
        g = g.resize((x1 - x0, y1 - y0)).rotate(0)
        layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
        layer.paste(g, (x0, y0))
        grad = layer
    bevel = Image.new("RGBA", base.size, (0, 0, 0, 255))
    bevel.putalpha(mask.point(lambda v: v * 0.7))
    sh = Image.new("RGBA", base.size, (0, 0, 0, 0))
    sh.alpha_composite(bevel, (0, 3))
    base.alpha_composite(sh.filter(ImageFilter.GaussianBlur(2)))
    grad.putalpha(mask)
    base.alpha_composite(grad)


def inset_rule(card, alpha=30, inset=34):
    layer = Image.new("RGBA", card.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle([inset, inset, W - inset, H - inset], R - inset * 0.6,
                                            outline=(255, 255, 255, alpha), width=2)
    card.alpha_composite(layer)


def grain(card, amount=7):
    noise = Image.effect_noise((W, H), 40).point(lambda v: 128 + (v - 128) * amount // 40)
    n = Image.merge("RGBA", (noise, noise, noise, Image.new("L", (W, H), 18)))
    card.alpha_composite(n)


PREMIUM = {
    "black": dict(lo=(7, 7, 8), hi=(44, 44, 47), ember=((70, 6, 8), (150, 14, 18)), name="BLACK COKE", pct="20"),
    "red": dict(lo=(70, 3, 7), hi=(188, 16, 24), ember=((8, 8, 9), (34, 30, 31)), name="RED COKE", pct="10"),
}


def premium(tier, style):
    t = PREMIUM[tier]

    def colour(cx, cy, shade, rnd):
        light = max(0, 1 - math.hypot(cx * 0.9, cy * 1.1) / 1.25)      # lit from the top left
        c = mix(t["lo"], t["hi"], min(1, 0.12 + shade * 0.55 + light * 0.45))
        if style == "ember":       # a few embers glowing in the top right, as on the wallpaper
            d = math.hypot((1 - cx) / 0.42, cy / 0.7)
            k = max(0, 1 - d + rnd.uniform(-0.25, 0.25))
            if k > 0:
                c = mix(c, mix(*t["ember"], shade + rnd.random() * 0.4), min(1, k * 1.6) * 0.9)
        if style == "glow":        # light from under the bottom edge
            k = max(0, (cy - 0.62) / 0.38) ** 2 * (0.6 + rnd.random() * 0.4)
            c = mix(c, t["ember"][1] if tier == "black" else (232, 40, 46), k * 0.75)
        return c

    card = facets(colour, seed={"ember": 7, "foil": 5, "glow": 9}[style])
    gloss(card, 0.07)
    grain(card)
    inset_rule(card, 26 if tier == "black" else 34)
    shadowed(card, tier_logo(tier, 400), (86, 82), alpha=0.6)
    # tier mark top right: the discount, small and set like a card network logo
    foil(card, (W - 92, 168), f"{t['pct']}%", HEAVY(96), 2, anchor="rs")
    foil(card, (86, 760), t["name"], BOLD(80), 16)
    text(card, (90, 822), "MEMBER  ·  " + f"{t['pct']}% OFF EVERYTHING  ·  {TERM}", MED(30),
         (255, 255, 255, 170), spacing=5, anchor="ls")
    foil(card, (90, 900), MEMBER, MONO(38), 4,
         stops=((235, 235, 235), (175, 175, 178), (225, 225, 228)))
    text(card, (W - 92, 900), f"VALID TO  {UNTIL}", MONO(30), (255, 255, 255, 170), spacing=3, anchor="rs")
    return finish(card, f"P-{style}-{tier}")


if __name__ == "__main__":
    styles = [("ember", "Ember"), ("foil", "Foil"), ("glow", "Glow")]
    cards = {(st, tier): premium(tier, st) for st, _ in styles for tier in ("black", "red")}
    cw, ch, gap, lab = 720, round(720 * H / W), 48, 70
    sheet = Image.new("RGB", (gap + 2 * (cw + gap), gap + len(styles) * (ch + lab + gap)), (236, 233, 228))
    d = ImageDraw.Draw(sheet)
    for i, (st, title) in enumerate(styles):
        y = gap + i * (ch + lab + gap)
        d.text((gap, y + 10), f"{i + 1}  ·  {title}", font=BOLD(40), fill=(22, 19, 15))
        for j, tier in enumerate(("black", "red")):
            c = cards[(st, tier)].resize((cw, ch), Image.LANCZOS)
            x = gap + j * (cw + gap)
            sh = Image.new("RGBA", (cw + 80, ch + 80), (0, 0, 0, 0))
            ImageDraw.Draw(sh).rounded_rectangle([40, 48, cw + 40, ch + 48], 28, fill=(0, 0, 0, 90))
            sh = sh.filter(ImageFilter.GaussianBlur(16))
            sheet.paste(sh, (x - 40, y + lab - 40), sh)
            sheet.paste(c, (x, y + lab), c)
    sheet.save(OUT / "sheet-premium.png", optimize=True)
    print("wrote renders/sheet-premium.png")


# ---- Black Coke, round 3: a black metal card -------------------------------------------------------

MARK = LOGO.crop((60, 0, 880, LOGO.height))   # the C and its bubbles, without the words


def diag(size, stops, angle_deg=-35):
    """A linear gradient across the whole card at an angle, from colour stops [(pos, rgb)]."""
    w, h = size
    big = int(math.hypot(w, h)) + 4
    strip = Image.new("RGB", (big, 1))
    for i in range(big):
        u = i / (big - 1)
        for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
            if p0 <= u <= p1:
                strip.putpixel((i, 0), mix(c0, c1, (u - p0) / max(1e-6, p1 - p0)))
                break
    g = strip.resize((big, big)).rotate(angle_deg, resample=Image.BICUBIC)
    return g.crop(((big - w) // 2, (big - h) // 2, (big - w) // 2 + w, (big - h) // 2 + h))


def black_bg(app=False):
    """app=True leaves out the gloss band and the edge: the app draws those itself, moving with the phone."""
    # 1. anodised black: barely-there facets, lit softly from the top left
    def colour(cx, cy, shade, rnd):
        light = max(0, 1 - math.hypot(cx * 0.9, cy * 1.2) / 1.3)
        return mix((8, 8, 9), (26, 26, 28), min(1, 0.2 + shade * 0.45 + light * 0.4))
    card = facets(colour, seed=5)

    # 2. spot gloss: the same facets in a glossy grey, showing only where the light sweeps across
    def gloss_colour(cx, cy, shade, rnd):
        return mix((30, 30, 33), (92, 92, 98), shade ** 0.8 * rnd.uniform(0.7, 1.0))
    glossy = facets(gloss_colour, seed=5)
    band = diag((W, H), [(0, (0, 0, 0)), (0.30, (0, 0, 0)), (0.42, (150, 150, 150)), (0.47, (210, 210, 210)),
                         (0.56, (60, 60, 60)), (0.72, (0, 0, 0)), (1, (0, 0, 0))], -28).convert("L")
    glossy.putalpha(band.filter(ImageFilter.GaussianBlur(40)))
    if not app:
        card.alpha_composite(glossy)

    # 3. brushed metal: fine horizontal streaks
    n = Image.effect_noise((W // 2, H), 60).resize((W * 3, H)).filter(ImageFilter.BoxBlur(0))
    n = n.resize((W, H)).filter(ImageFilter.GaussianBlur((18, 0)) if False else ImageFilter.BoxBlur(0))
    streak = Image.effect_noise((W, H), 90).transform((W, H), Image.AFFINE, (0.04, 0, 0, 0, 1, 0)).point(lambda v: 128 + (v - 128) // 3)
    brush = Image.merge("RGBA", (streak, streak, streak, Image.new("L", (W, H), 18)))
    card.alpha_composite(brush)

    # 4. a big C pressed into the right half
    m = MARK.resize((round(MARK.width * 700 / MARK.height), 700), Image.LANCZOS).split()[3]
    pos = (W - round(m.width * 0.66), H - m.height + 90)
    full = Image.new("L", (W, H), 0)
    full.paste(m, pos)
    full = full.filter(ImageFilter.GaussianBlur(1.2))
    def tone(rgb, a, off):
        lay = Image.new("RGBA", (W, H), rgb + (0,))
        sh = Image.new("L", (W, H), 0)
        sh.paste(full.point(lambda v: int(v * a)), off)
        lay.putalpha(sh)
        return lay
    card.alpha_composite(tone((255, 255, 255), 0.10, (2, 2)))     # light catches the lower lip
    card.alpha_composite(tone((0, 0, 0), 0.40, (-2, -2)))         # shadow on the upper lip
    card.alpha_composite(tone((0, 0, 0), 0.16, (0, 0)))           # the pressed face, a shade darker

    grain(card, 5)

    if not app:
        # 5. polished chamfer round the edge: bright top-left, dark bottom-right
        ring = Image.new("L", (W, H), 0)
        ImageDraw.Draw(ring).rounded_rectangle([0, 0, W - 1, H - 1], R, outline=255, width=5)
        edge = diag((W, H), [(0, (235, 235, 240)), (0.35, (120, 120, 126)), (0.6, (40, 40, 44)), (1, (90, 90, 96))], -33).convert("RGBA")
        edge.putalpha(ring.point(lambda v: int(v * 0.8)))
        card.alpha_composite(edge)

    return card



# ---- Round 4: Red made simpler, and the type tried in different fonts ----------------------------

def red_bg(app=False):
    def colour(cx, cy, shade, rnd):
        light = max(0, 1 - math.hypot(cx * 0.9, cy * 1.1) / 1.25)
        return mix((128, 6, 12), (206, 20, 28), min(1, 0.25 + shade * 0.35 + light * 0.4))
    card = facets(colour, seed=5)
    if not app:
        gloss(card, 0.06)
    return card


S = "/System/Library/Fonts/"
F = lambda path, i=0: (lambda size: ImageFont.truetype(S + path, size, index=i))
FONTSETS = [
    # name, title face, title size, tracking, small face, member face, member size, % face
    ("Futura", F("Supplemental/Futura.ttc", 2), 78, 18, F("Supplemental/Futura.ttc", 0), F("SFNSMono.ttf"), 38, F("Supplemental/Futura.ttc", 4)),
    ("Didot", F("Supplemental/Didot.ttc", 2), 88, 14, F("Avenir Next.ttc", 5), F("Supplemental/Didot.ttc", 0), 42, F("Supplemental/Didot.ttc", 2)),
    ("Copperplate", F("Supplemental/Copperplate.ttc", 2), 72, 10, F("Supplemental/Copperplate.ttc", 0), F("Supplemental/Copperplate.ttc", 0), 40, F("Supplemental/Copperplate.ttc", 2)),
    ("Avenir Next", F("Avenir Next.ttc", 10), 96, 24, F("Avenir Next.ttc", 2), F("Avenir Next.ttc", 5), 36, F("Avenir Next.ttc", 10)),
    ("DIN", F("Supplemental/DIN Condensed Bold.ttf"), 128, 12, F("Supplemental/DIN Alternate Bold.ttf"), F("Supplemental/DIN Alternate Bold.ttf"), 38, F("Supplemental/DIN Condensed Bold.ttf")),
    ("Optima", F("Optima.ttc", 1), 82, 14, F("Optima.ttc", 0), F("Optima.ttc", 0), 42, F("Optima.ttc", 4)),
]
SILVER = ((252, 252, 252), (150, 150, 156), (226, 226, 230), (118, 118, 124))


def dress(bg, tier, fs):
    """Lay the type on a finished background in one font set."""
    name, title, tsize, track, small, member, msize, pctf = fs
    card = bg.copy()
    black = tier == "black"
    pct = "20%" if black else "10%"
    shadowed(card, tier_logo(tier, 380), (90, 86), alpha=0.7 if black else 0.35)
    if black:
        foil(card, (W - 96, 166), pct, pctf(96), 2, anchor="rs", stops=SILVER)
        foil(card, (90, 742), "BLACK COKE", title(tsize), track, stops=SILVER)
        rule = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(rule).rectangle([92, 768, 92 + 118, 771], fill=(214, 24, 30, 255))
        card.alpha_composite(rule)
        foil(card, (92, 902), MEMBER, member(msize), 5, stops=((230, 230, 232), (160, 160, 165), (220, 220, 224)))
    else:
        text(card, (W - 96, 166), pct, pctf(96), (255, 255, 255), spacing=2, anchor="rs")
        text(card, (90, 742), "RED COKE", title(tsize), (255, 255, 255), spacing=track, anchor="ls")
        text(card, (92, 902), MEMBER, member(msize), (255, 255, 255, 235), spacing=5, anchor="ls")
    dot = "·" if small(28).getmask("·").getbbox() and name != "DIN" else "|"
    text(card, (92, 822), f"MEMBER  {dot}  {pct} OFF EVERYTHING  {dot}  TERM 4", small(28), (255, 255, 255, 150 if black else 190), spacing=6, anchor="ls")
    text(card, (W - 96, 902), f"VALID TO  {UNTIL}", member(30), (255, 255, 255, 140 if black else 190), spacing=3, anchor="rs")
    return finish(card, f"F-{name.replace(' ', '')}-{tier}")


AVENIR = [   # thicker takes on Avenir Next for the name (and the %), small type in Demi Bold
    ("Avenir Next Regular", F("Avenir Next.ttc", 7), 92, 22, F("Avenir Next.ttc", 2), F("Avenir Next.ttc", 5), 36, F("Avenir Next.ttc", 7)),
    ("Avenir Next Medium", F("Avenir Next.ttc", 5), 90, 20, F("Avenir Next.ttc", 2), F("Avenir Next.ttc", 5), 36, F("Avenir Next.ttc", 5)),
    ("Avenir Next Demi Bold", F("Avenir Next.ttc", 2), 88, 18, F("Avenir Next.ttc", 2), F("Avenir Next.ttc", 5), 36, F("Avenir Next.ttc", 2)),
]

# The chosen look: Avenir Next Medium for the name and the %, SF Mono for everything under it.
FINAL = ("Final", F("Avenir Next.ttc", 5), 90, 20, F("SFNSMono.ttf"), F("SFNSMono.ttf"), 38, F("Avenir Next.ttc", 5))

def export_app(dest):
    """The card art for My CCC: each tier's base (with the logo) and its gloss layer, which the app
    reveals with a band of light that follows the phone's tilt. Type is drawn by the app."""
    size = (1200, round(1200 * H / W))
    for tier in ("black", "red"):
        base = black_bg(app=True) if tier == "black" else red_bg(app=True)
        shadowed(base, tier_logo(tier, 380), (90, 86), alpha=0.7 if tier == "black" else 0.35)
        lo, hi = ((30, 30, 33), (110, 110, 118)) if tier == "black" else ((170, 20, 28), (255, 120, 120))
        shine = facets(lambda cx, cy, shade, rnd: mix(lo, hi, shade ** 0.8 * rnd.uniform(0.7, 1.0)), seed=5)
        for name, im in ((f"Member{tier.title()}", base), (f"Member{tier.title()}Gloss", shine)):
            folder = dest / f"{name}.imageset"
            folder.mkdir(parents=True, exist_ok=True)
            im.convert("RGB").resize(size, Image.LANCZOS).save(folder / f"{name}.jpg", quality=88, optimize=True)
            (folder / "Contents.json").write_text('{"images":[{"filename":"%s.jpg","idiom":"universal","scale":"1x"}],'
                                                  '"info":{"author":"xcode","version":1}}' % f"{name}.jpg")
    print("wrote app art to", dest)


if __name__ == "__main__":
    import sys
    if "app" in sys.argv:
        export_app(Path(sys.argv[-1]))
        sys.exit()
    if "final" in sys.argv:
        bgs = {"black": black_bg(), "red": red_bg()}
        pair = [dress(bgs[t], t, FINAL) for t in ("black", "red")]
        cw, ch, gap = 1100, round(1100 * H / W), 70
        sheet = Image.new("RGB", (cw + 2 * gap, 2 * ch + 3 * gap + 40), (236, 233, 228))
        for i, c in enumerate(pair):
            c = c.resize((cw, ch), Image.LANCZOS)
            y = gap + i * (ch + gap + 20)
            sh = Image.new("RGBA", (cw + 120, ch + 120), (0, 0, 0, 0))
            ImageDraw.Draw(sh).rounded_rectangle([60, 74, cw + 60, ch + 74], 40, fill=(0, 0, 0, 110))
            sh = sh.filter(ImageFilter.GaussianBlur(24))
            sheet.paste(sh, (gap - 60, y - 60), sh)
            sheet.paste(c, (gap, y), c)
        sheet.save(OUT / "sheet-final.png", optimize=True)
        print("wrote renders/sheet-final.png")
        sys.exit()
    if "avenir" in sys.argv:
        FONTSETS, SHEET = AVENIR, "sheet-avenir.png"
    else:
        SHEET = "sheet-fonts.png"
    bgs = {"black": black_bg(), "red": red_bg()}
    cw, ch, gap, lab = 720, round(720 * H / W), 48, 70
    sheet = Image.new("RGB", (gap + 2 * (cw + gap), gap + len(FONTSETS) * (ch + lab + gap)), (236, 233, 228))
    d = ImageDraw.Draw(sheet)
    for i, fs in enumerate(FONTSETS):
        y = gap + i * (ch + lab + gap)
        d.text((gap, y + 10), f"{i + 1}  ·  {fs[0]}", font=BOLD(40), fill=(22, 19, 15))
        for j, tier in enumerate(("black", "red")):
            c = dress(bgs[tier], tier, fs).resize((cw, ch), Image.LANCZOS)
            x = gap + j * (cw + gap)
            sh = Image.new("RGBA", (cw + 80, ch + 80), (0, 0, 0, 0))
            ImageDraw.Draw(sh).rounded_rectangle([40, 48, cw + 40, ch + 48], 28, fill=(0, 0, 0, 90))
            sh = sh.filter(ImageFilter.GaussianBlur(16))
            sheet.paste(sh, (x - 40, y + lab - 40), sh)
            sheet.paste(c, (x, y + lab), c)
    sheet.save(OUT / SHEET, optimize=True)
    print("wrote renders/" + SHEET)
