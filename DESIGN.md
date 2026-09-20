# DESIGN.md — the screen (matches what web/ ships)

One screen, no scrolling. Map fills the viewport; a **340 px right panel**. All data
comes from the files in [CONTRACT.md](CONTRACT.md) — served from `web/mock/` until
the pipeline lands `web/data/`, switched by the `DATA_DIR` constant in `app.js`.
When `steps.json`/`breaks.geojson` are absent, the page degrades to the five snap
budgets in `solutions.json` (per CONTRACT.md).

URL flags: `?offline=1` forces the offline basemap path; `?intro=0` skips the title
card (testing).

## Intro

Full-bleed title card over the **undimmed, still** satellite map: `FIREBREAK` in
white mono caps (clamp 64–150 px, .18em tracking), the one-line story from
`meta.story`, one button **"Run the fire →"**. Click anywhere skips. On click: the
map dims to the app treatment (.8 s filter transition), the panel appears, Pulga's
pulse marker shows, the fire starts, and audio starts (the click is the user
gesture autoplay needs). Time to interactive ≤ 5 s.

## Map layers (bottom to top)

1. **Basemap** — Esri World Imagery tiles
   (`https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}`),
   dimmed `brightness(0.72) saturate(0.8)` so the fire is the only saturated thing
   on screen. Offline fallback: `<data>/basemap.png` with its edges feathered by a
   40 px alpha ramp, placed with `meta.bounds` — never blended over satellite.
   Offline-first: the PNG shows until a probe tile loads AND a tile batch completes
   cleanly (Leaflet fires `load` even for all-error batches, so a clean-batch flag
   gates the upgrade); any `tileerror` brings the PNG back. A slow first handshake
   upgrades late rather than never.
2. **Fire** — custom canvas layer anchored to `meta.bounds`, **2 px per cell**,
   `image-rendering: pixelated` + `filter: blur(1.2px)`. Age ramp
   `#ffd166 → #ff7a1a → #c1121f → #1a1a1a` over 180 min. Alpha: fresh front
   (age ≤ 15 min) **0.55** with a seeded ±0.1 per-frame flicker; body **0.85**;
   burned-out charcoal **0.70** (terrain shows through). 65535 = never burns.
   A second smooth-scaled canvas carries a blurred amber **glow** on cells ignited
   within the last 30 min (screen blend). Audio level tracks the fresh-front count.
   **Breaks render inside this canvas as cleared ground**: `#d9c9a3` at 0.85 with a
   lighter edge at 0.35 — strips of bare earth, no outlines. Fire that crosses a
   break paints over it. Hovered break turns `#3ddc84` with a mono tooltip:
   `BREAK id · STEP n · $cost · N HOMES PROTECTED` (marginal saved from curve.json).
3. **Ghost** (state C only) — the baseline burn perimeter as a dashed white outline
   (merged boundary runs of `baseline arrival ≤ horizon`; domain-border edges are
   skipped where the fire runs off-grid).
4. **Homes** — canvas anchored to `meta.bounds` through the same positioning path
   as the fire canvas (so homes can never drift against the grid), backing store
   resized to screen resolution on zoomend (capped 4096 px; sprites compensate the
   stretch past the cap). Drawn at true sub-cell positions (mercator-correct).
   Below zoom 14: 2×2 px squares, `#d8d8d3` at 0.8 with a 1 px `#0a0b0d` edge.
   Zoom ≥ 14: a ~6 px house pictogram (square body + roof triangle), same colors.
   Burned = `#ff3b3b`, flips the frame fire arrives and stays red. Saved by breaks
   (baseline reached it, current grid never does) = same grey with a 1 px `#3ddc84`
   ring, visible from the moment a budget is picked.
5. **Ignition** — small amber dot with a 2 s pulse ring, mono label
   `Pulga · 6:30 AM` (time hard-coded until meta grows an ignition time field).

## Audio

Synthesized fire crackle, no files: filtered brown-noise bed (lowpass ~420 Hz) +
random short bandpassed impulses (0.02–0.07 s, 0.9–4.1 kHz). Master volume tracks
the active-front size (quiet at ignition, loud at full burn, fades when the fire
stalls). `SOUND ON/OFF` toggle in the footer. Everything is try/catch-wrapped —
audio can never throw or block the page.

## Guided flow — a game level, not a dashboard

Three states; one primary action each; controls not for the current state are
**dimmed, not hidden** (budget in A and C; explore in A). Play/pause + the time
scrubber exist in every state as secondary controls.

- **A** — guide line "Nov 8, 2018. No fuel breaks." Baseline auto-plays after the
  intro click. HOMES HIT is the live number; the other two are dimmed. When the run
  ends: "Now give Paradise a budget." and the slider lights up (→ B).
- **B** — budget slider active. Dragging updates breaks (cleared ground), saved
  rings, and numbers immediately, in place. Primary button **"Run it again →"**.
- **C** — t resets, the with-breaks fire plays over the ghosted baseline perimeter.
  Big numbers: **HOMES SAVED · EVACUATION TIME BOUGHT · SPENT** (evac =
  `minutes_bought` of the active step). End: "$993k saved 241 homes." + buttons
  **"Try another budget"** (→ B) and **"Explore budgets"** (opens the curve).

## Right panel, top to bottom

1. `FIREBREAK` (mono, tracked) + `meta.story`.
2. Guide line + flow buttons.
3. **Three big numbers** — mono, 40 px, light weight, unit label beneath
   (uppercase 11 px, .12em), tweened over 150 ms, hairline rows, no cards. Slots
   per flow state as above.
4. **01 — Homes**: waffle grid, one square = N homes (unit chosen so the grid is
   ~20×15; shown as `1 SQ = N HOMES`). Grey standing, red burned (fills from
   top-left as homes burn), green-ringed saved (fills from bottom-right as the
   baseline front passes protected homes). Legend row beneath.
5. **02 — Budget**: continuous slider $0…max(meta.budgets), mapped to the last
   step with `cumulative_cost ≤ budget` (steps are dense, so it feels continuous).
   Tick marks + labels at `meta.budgets`. Live mono readout underneath:
   `$1.37M · 12 breaks · 241 homes saved · 41 min bought`.
6. **03 — Timeline**: play/pause + scrubber, 0…`horizon_min` in 5-min steps, full
   sweep ≈ 20 s. Spacebar toggles.
7. **04 — Budget → homes saved** (collapsible): step curve of curve.json, 1 px
   green line, dotted grid at 0.06 alpha, dashed marker + dot at the current spend,
   sentence callout ("$993k saves 241 homes"), hover readout.
8. **05 — Real vs simplified** (collapsible): `meta.simplifications`.
9. Footer, mono: `SATELLITE · DATA: MOCK · 60 M CELLS · WIND NE 35 MPH` (basemap
   mode / data dir / `meta.grid.cell_m` / `meta.wind`) + sound toggle.

A fuel-color legend chip row (from `fuel_legend.json`) sits bottom-left on the map
only while the offline PNG is the basemap.

## Look

- Surfaces: page `#0a0b0d`, panel `#0f1115`; hairline dividers
  `rgba(255,255,255,0.08)` only — no cards, no boxes, corners ≤ 3 px, no shadows or
  gradients on UI, no decorative motion. The fire is the motion.
- Text: primary `#e8e8e6`, secondary `#8a8f98`. Body sans
  (`Inter, system-ui, …`, no webfont download). Every number, label, tick, and
  status line in mono (`JetBrains Mono → SF Mono → Consolas`), `tabular-nums`.
  Labels uppercase 11 px, letter-spacing 0.12em. Panel sections numbered 01–05.
- Sliders: 1 px track, small square thumb (green for budget, amber for time),
  native look removed.
- Accents: `#3ddc84` (breaks/saved — the only UI accent), `#ff3b3b` (burned),
  fire ramp on the map only.

## Constraints

- No frameworks, no build step, no CDN; Leaflet 1.9.4 vendored at
  `web/vendor/leaflet/`.
- Everything works from `python -m http.server -d web 8000` with wifi off (force
  with `?offline=1`).
- JS ≈ 900 lines (grew past the original 500 with the intro, audio, guided flow,
  and canvas layers — still one file, readable top to bottom).

## Switching to real data

One constant: `DATA_DIR = 'mock'` → `'data'` in `app.js`. Read everything from the
files; the only hard-coded story fact is the `6:30 AM` ignition time. Real numbers
will differ from the mock's everywhere.
