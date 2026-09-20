# DESIGN.md — the screen (Arya owns this)

One screen, no scrolling. Map fills the viewport; a **340 px right panel**. All data
comes from the files described in [CONTRACT.md](CONTRACT.md) — start against
`web/mock/`, switch to `web/data/` by flipping the `DATA_DIR` constant in `app.js`.

## Chrome

- Panel background `#0f1115`, text `#e6e6e6`. Dark chrome throughout.
- Font: Inter via system stack fallback:
  `Inter, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif` (no webfont
  download — the demo must work offline).

## Map layers (bottom to top)

1. **Basemap** — satellite tiles (Esri World Imagery,
   `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}`)
   when online, else `web/<data>/basemap.png` placed with `meta.bounds`.
   Auto-detect: listen for a tile `tileerror` / load a probe tile with a **3 s
   timeout** — if tiles fail within 3 s, default to the offline basemap. The demo
   laptop has unreliable wifi; offline is the safe default, online is the upgrade.
2. **Fire** — a canvas layer (custom `L.Layer`): create a `<canvas>` in the overlay
   pane, position it from `meta.bounds` via `map.latLngToLayerPoint` on every
   `move`/`zoom`, redraw from the arrival grid. Cells with `arrival <= t` are drawn
   with an **age ramp** (age = t − arrival):
   just-ignited `#ffd166` → `#ff7a1a` → `#c1121f` → charcoal `#2b2b2b`,
   at **0.75 alpha**; unburned cells fully transparent. Value 65535 = never burns.
   Use `image-rendering: pixelated` so cells stay crisp.
3. **Buildings** — 3 px dots. White at 40 % opacity; a dot turns `#ff3b3b` the moment
   `arrival[row*cols+col] <= t` for the current grid.
4. **Breaks** — `#3ddc84` polygons with a 1 px white stroke (from the current
   solution's `breaks` FeatureCollection). Baseline ($0) has no breaks.

## Right panel, top to bottom

1. Title **"Firebreak"** + the one-line story from `meta.story`.
2. **Three big numbers**, updating live as t and budget change:
   *Buildings hit* · *Spent* · *Houses saved*.
3. **Budget slider** — snaps to `meta.budgets`, with **$0 = baseline** as the leftmost
   stop. Changing budget swaps the arrival grid + breaks + stats (from
   `solutions.json`; $0 uses `baseline.json`).
4. **Play/pause + scrubber** for t: range 0 … `meta.horizon_min`, **5-minute steps**,
   a full play-through takes **~20 s**.
5. **The curve** — inline SVG line chart of `curve.json`: cumulative cost (x) vs
   cumulative houses saved (y), with the current budget position marked.
6. Collapsible **"Real vs simplified"** list rendered from `meta.simplifications`.

## Constraints

- No frameworks, no build step, **≤ 500 lines of JS**.
- Leaflet is vendored at `web/vendor/leaflet/` — never reference a CDN.
- Everything must work from `python -m http.server -d web 8000` with wifi off
  (except the satellite tiles, which degrade to `basemap.png`).

## Checkpoint

A screenshot of the page running on mock data **within 60 minutes of starting**, into
the group chat. Then iterate.

## Switching to real data

One constant: `DATA_DIR = 'mock'` → `'data'` in `app.js`. The mock matches
CONTRACT.md exactly, so nothing else changes. Note: real budgets/stats will differ
from mock numbers — read everything from the files, hard-code nothing.

The current `app.js` is a throwaway skeleton proving the contract decodes and aligns —
replace it freely.
