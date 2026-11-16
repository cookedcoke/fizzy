# Bottle cap logo (on hold)

A replacement for the current logo, which is a recoloured CachyOS logo. Made in November 2026 and
parked until the owner decides to switch. **Nothing here is used by the sites, the apps or the Docker
image**: the files sit next to the live ones, ready to copy over.

The mark is a red crown cap, seen from 52° above, with metallic paint and a clear coat, a liner ring,
and a white enamel C (an even, rounded stroke drawn for this, not a font). From 72° above it stays
readable down to 16px, so that view is used for favicons and the small logo.

## What's here

| Folder | What it is | Replaces |
| --- | --- | --- |
| `master/` | Transparent renders: 52° with and without its shadow, 52° with fizz bubbles, 72° | - |
| `web/` | `logo.svg`, favicons, `apple-touch-icon.png`, `icon-192/512.png`, the two maskable icons | `fizzy/static/` (same names) |
| `ios/Fizzy`, `ios/Student`, `ios/Clip` | 1024px app icons (staff on dark cola, My CCC and its clip on sun yellow) | `ios/*/Assets.xcassets/AppIcon.appiconset/icon-1024.png` |
| `ios/Logo.imageset/` | The sign-in logo as PNGs at 1x/2x/3x | `ios/*/Assets.xcassets/Logo.imageset/` (remove its `logo.svg`) |
| `mac/` | `icon.icns` for the counter app, and the 1024px it's made from | `mac/assets/icon.icns` (and `mac/assets/logo.svg` from `web/`) |
| `render/` | The 3D scene the renders come from | - |

Colours: cap red `#A80904` (painted metal, so it reads lighter in the light), C white, staff icon
background cola `#2A1712`, My CCC background sun `#FFC23D`.

## Switching to it

Copy each file over the one it replaces, then bump the app builds so the new icons ship. `logo.svg`
wraps a PNG, which browsers show fine; the iOS apps get PNGs instead because Xcode's SVG support is patchy.

## Rendering again

The cap is a Three.js scene (`render/cap.html`), so the angle, paint and C can be changed and
re-rendered without other software:

```bash
cd brand/bottle-cap/render && python3 serve.py
```

Open http://127.0.0.1:8766/cap.html, then in the browser console:

```js
letter("mono", "white");                                   // the C used here
await save("cap-52deg", { bubbles: false });               // 52°, the app icons
await save("cap-72deg", { bubbles: false, shadow: false, elev: 72, yaw: -10, env: 0.3 });  // favicons
```

Each `save` writes a 1024px transparent PNG next to `serve.py`. Options: `elev` (camera height in
degrees), `yaw`, `shadow`, `bubbles`, `env` (how much the paint reflects). The other C shapes are in
`letters` (soda, ball, pop, retro, nib, mono), and `letter(name)` without `"white"` gives gold. Trim the
renders (`magick in.png -trim +repage out.png`) before laying them on icon backgrounds.
