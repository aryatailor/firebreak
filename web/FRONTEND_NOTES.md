# FRONTEND_NOTES.md

Decisions made during the overnight frontend session (2026-09-19 into 2026-09-20).
Conservative choice taken whenever something was ambiguous; each one is logged here.

## Copy rules in force

No em dashes, no exclamation marks, no emoji, no "Let's". No UI sentence over 12
words. Numbers carry units. One idea per line. Labels that can be deleted are
deleted.

## Tier 1: typography and title

- **Font discovery.** `sedimentlabs.ai` loads `/assets/index-*.css`, which declares
  three `@font-face` rules for **Selawik** at weights 400, 600 and 700, plus a mono
  stack `ui-monospace, Cascadia Mono, Consolas, Menlo, monospace`.
- **Selawik is freely licensed** (Microsoft, SIL Open Font License 1.1 — it is their
  open metric-compatible replacement for Segoe UI), so it is used directly rather
  than substituted. The three woff2 files are vendored to `web/vendor/fonts/` and
  loaded from there. No CDN, works offline.
- The woff2 files are Sediment's Latin subsets (about 14 KB each). Glyph coverage is
  verified in the headless check; if a glyph were missing the check would catch it
  as a fallback-metrics mismatch.
- Mono stack copied from Sediment as well, replacing the previous JetBrains Mono
  request (which was never vendored and so never actually loaded).
- **Title card**: the word is rendered twice. The back copy is `#ff2a1a`, blurred
  12 px and passed through `feTurbulence` + `feDisplacementMap`; `seed` and
  `baseFrequency` drift under requestAnimationFrame so the red edge waves like heat.
  The white copy carries no filter at all, so it never flickers. The animation stops
  when the card is dismissed.
- **Logo**: one horizontal white line with a clean gap in the middle (the break),
  with the wordmark beside it in Selawik. Same mark as `web/favicon.svg`.
- Panel heading removed: the brand mark top-left is now the only place the product
  names itself.
- **Title card sits on solid black**, not over the map. Over satellite imagery the
  red heat was invisible and the screen was not "nothing else". Black also cuts
  straight into the opening, which starts black.

## Tier 2: cinematic opening

- Enter fades the card, the map starts at zoom 6 (state scale) and flies to the
  region over 10 s with `flyTo({duration: 10, easeLinearity: 0.25})`.
- `meta.crawl` is present for both towns (5 lines each). Lines hold 2.5 s with a
  0.7 s cross fade. If `crawl` were absent the opening falls back to the first four
  sentences of `meta.story`, as specified.
- The crawl runs **full bleed**: panel, brand, town control, zoom control, the
  ignition marker and the legend are all hidden, and the map takes the full width.
  When the fire starts the panel returns, `invalidateSize` runs and the view is
  reframed for the narrower map.
- Caption is two-stage at the burn: "This is what happened." for 2.6 s, then what
  the viewer is looking at.
- **Skip is always bottom right.** It reads "Skip intro" during the crawl (it jumps
  to the fire) and "Free play" afterwards. One label per meaning rather than one
  label with two behaviours.
- Fly target is the region centre at the zoom that frames the whole grid. Flying to
  the town centre instead would push part of the burn area off screen, and the
  viewer has to see the whole fire to read the story.

## Tier 3: panel and map controls

- Panel is now three blocks: **01 BUDGET**, **02 HOMES SAVED** (big number plus the
  waffle), and a footer line. "More" is gone entirely.
- The time scrubber moved out of the panel to a thin strip along the bottom edge of
  the map: play button, track, elapsed time. It is dimmed but live outside free play.
- The curve and the simplifications list moved behind one small **i** in the panel
  footer, which opens a plain modal. Escape or a click outside closes it.
- Region switching is the mono select at top left, under the brand. Selecting a town
  reloads against that town's `data_dir`, which replays its own crawl.
- Map: `minZoom 4`, `zoomSnap 0.25`, `wheelPxPerZoomLevel 45` and
  `wheelDebounceTime 12` for a fast, smooth wheel. Zoom control moved to the top
  right so it does not collide with the brand.
- **US** button under the zoom control flies to the continental bounds. Below zoom 9
  the fire, glow, ghost and ignition marker fade out and a pin appears for every town
  in `towns/index.json`, labelled with name and event; clicking one loads it.
  Pin centres come from each town's own `meta.bounds`, fetched once in the
  background, since the index carries no coordinates.

### Home drift, measured

Homes are pixels inside the fire's grid canvas, so their position is quantised to
the grid. Measured against Leaflet's own projection of the same lat/lon:

| zoom | offset | one grid pixel |
|---|---|---|
| 10 | 0.05 / 0.08 px | 0.26 px |
| 12 | 0.73 / 0.61 px | 1.02 px |
| 14 | 1.84 / 0.97 px | 4.09 px |

The offset never exceeds half a grid pixel, and a five-point continuous-projection
check (no rounding) returns errors inside 0.7 px at all three zooms with **no growth
across zoom**. There is no slide: what remains is the cost of drawing homes as grid
pixels, which is what the grid-canvas approach is for. Earlier readings of 6 px came
from probing a home on the grid edge, where the position is clamped so the saved
ring stays on canvas.
