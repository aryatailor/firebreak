# Implementation notes

Sections: **Plan** (Phase 1, written before any pipeline code), **Decisions** (choices
made along the way, one line of why each), **Deviations** (where reality contradicted
the plan — conservative option taken, reason logged).

## Plan

Status: **approved by Zach 2026-09-19**, with two amendments folded in below:
§2 ambiguity default (LF 2014, ≤ 10 min on the check) and the §0 story string.
Decisions most likely to change are first; boring mechanics at the bottom.

### 0. Town config (`--town`) — new requirement, 2026-09-19

- Everything town-specific lives in `towns/<town>.json`; pipeline code contains
  nothing about Paradise. `towns/paradise.json`:

  ```jsonc
  { "name": "Paradise, CA",
    "story": "What if Paradise had cut fuel breaks before November 8, 2018?",
    "bounds": {"west": -121.70, "south": 39.68, "east": -121.38, "north": 39.86},
    "ignition": {"lat": 39.794, "lon": -121.435, "label": "Pulga (Camp Fire origin)"},
    "wind": {"speed_mph": 35, "from_deg": 45},
    "town_center": {"lat": 39.7596, "lon": -121.6219},
    "horizon_min": 720,
    "budgets": [500000, 1000000, 2000000, 3000000, 5000000] }
  ```

- Every stage takes `--town paradise` (the default). Per-town paths: raw downloads
  `data_raw/<town>/`, intermediates + calibrated params `pipeline/out/<town>/`
  (including `config.json`); `run_log.md` entries are tagged with the town. Export
  writes `web/data/` from whichever town is named — a second real town Sunday
  morning is: write `towns/<newtown>.json`, `python pipeline/run_all.py --town
  <newtown>`, done.
- Grid rows × cols are derived from the config bbox at build_grid time (the
  334 × 456 in §1 is what the Paradise bbox produces, not a constant).

### 1. Box and grid (the numbers everything else inherits)

- Bounds (EPSG:4326): west −121.70, south 39.68, east −121.38, north 39.86 — Pulga in
  the NE corner, Paradise center-west, room to the SW for the fire to run past town.
- Working CRS **EPSG:3857** (Web Mercator). Why: it's the tile CRS, so the exported
  grid image aligns with Leaflet basemaps with zero client-side reprojection.
- Cell: **60 m ground distance**. Mercator meters are inflated by 1/cos(lat); at
  center lat 39.77° that means a cell edge of **60 / cos(39.77°) = 78.06 m in
  EPSG:3857 units**. Across the box the true ground size varies only 59.92–60.08 m —
  ignored.
- Grid anchored at the NW corner (−121.70, 39.86), snapped to whole cells:
  **334 rows × 456 cols = 152,304 cells** (≤ 350k target ✓). The snapped east/south
  edges move < 25 m from the nominal bounds; `meta.bounds` always reports the exact
  snapped extent (CONTRACT.md already tells the frontend to read it from the file).

### 2. Data vintage (verified against the landfire package's product table)

- The `landfire` package's `ProductSearch` (bundled LFPS product table, checked
  locally) offers FBFM40 at: LF 2014 (`140FBFM40`, ~2014 conditions), and LF 2016
  Remap (`200F40_19`, `200F40_20`). Elevation exists only as `ELEV2020` (terrain
  doesn't change; one vintage is fine).
- **Wrinkle:** LF 2016 Remap fuel layers are "capable" products updated with recent
  disturbances — `200F40_19` may already contain the Camp Fire burn scar (the fire we
  want to re-run would then see already-burned fuels). Plan: **fetch `200F40_19` and
  `140FBFM40` in one LFPS job** (both are small), then pick at build_grid time with a
  programmatic check — if the Remap layer shows a large fresh-disturbance/non-burn
  signature across the Pulga→Paradise corridor that LF 2014 lacks, use `140FBFM40`.
- Framing per outcome: Remap usable → "fuels as mapped just before the fire";
  LF 2014 → "fuels four years before the fire — the landscape the Camp Fire actually
  found, minus two years of growth". Either way the demo re-runs the 2018 event;
  `meta.data.fuel` records the product + version actually used.
- The LFPS ArcGIS endpoint the package calls was probed today and answers (HTTP 200).
- **Amendment (Zach, at approval)**: if the Remap-vs-LF 2014 check is ambiguous,
  default to `140FBFM40` and move on — spend no more than 10 minutes on it; the
  choice and the check's numbers are recorded in `meta.data`.

### 3. Spread-rate model (ros.py)

Relative base rate per Scott & Burgan FBFM40 **group** (within-group differences
matter for flame length, much less for a relative arrival-time story — group rates +
one calibrated global scale is the honest 24-hour version):

| group | codes | rel. rate | note |
|---|---|---|---|
| grass | GR1–9 | 1.00 | fastest |
| grass-shrub | GS1–4 | 0.65 | |
| shrub | SH1–9 | 0.45 | chaparral |
| slash | SB1–4 | 0.35 | |
| timber-understory | TU1–5 | 0.30 | |
| timber litter | TL1–9 | 0.12 | slowest |
| urban NB91 | | **0.15, tunable `structure_rate`** | LANDFIRE marks towns non-burnable; the Camp Fire burned Paradise structure-to-structure, so urban must be enterable |
| NB92/93/98/99 (snow, agriculture, water, barren) | | 0 | impassable |

- **Absolute scale**: one global `scale` (m/min for grass) chosen by calibrate.py —
  we're not claiming Rothermel fidelity, we calibrate one free parameter to the
  historical outcome.
- **Slope** (per edge, from the DEM — no separate slope layer needed):
  `f_slope = clamp(2^(θ_deg/10), 0.5, 8)` where θ is the uphill angle along the
  travel direction. "Fire doubles speed per 10° uphill" is the classic rule of thumb;
  the cap keeps canyon walls from going infinite, downhill mildly slows.
- **Wind** (directional): `f_wind = exp(k_w · (v_mph/10) · cosΔ)`, Δ = angle between
  travel direction and downwind. Default k_w 0.35 → at 35 mph: ×3.4 downwind, ×0.29
  upwind, ≈ 12:1 head-to-heel — the Camp Fire was wind-driven, downwind ≫ upwind is
  the defining behavior. k_w is swept in calibration.
- **Cleared (fuel-break) cells**: base-rate multiplier **0.05** (20× slower, not 0) —
  breaks delay rather than hard-block, which avoids the fake "one thin line stops
  everything" artifact; within a 12 h horizon, delayed ≈ stopped.
- **Edge weight** i→j = ground_dist / (min(base_i, base_j) · f_slope(i→j) ·
  f_wind(i→j) · scale). `min` of the endpoint fuels so a 2-cell-wide break can't be
  leapfrogged diagonally. dist = 60 m orthogonal, 60·√2 diagonal (ground meters).

### 4. Simulation (simulate.py)

- Directed 8-neighbor graph (directed because wind/slope are asymmetric), 152,304
  nodes / ~1.2M edges, built vectorized in numpy as 8 shifted weight planes →
  scipy.sparse CSR → `csgraph.dijkstra` from the ignition cell.
- Ignition: Pulga / Camp Fire origin ≈ (39.794, −121.435), snapped to the nearest
  burnable cell (the exact origin may rasterize onto barren canyon/water).
- Applying a set of breaks = multiply the affected cells' outgoing+incoming edge
  weights; the 8 base planes are precomputed once, so per-evaluation cost is
  CSR assembly + dijkstra. Budget: **< 0.5 s per run** on the full grid (dijkstra on
  1.2M edges is ~0.1–0.3 s; assembly vectorized) — else downsample 2×.
- Output: float minutes → uint16, 65535 for > horizon (720 min) or unreachable.

### 5. Calibration gate (calibrate.py)

- Sweep `scale` × `k_w`, print a table of (minutes to town center, % buildings hit at
  720 min). Auto-pick defaults hitting **60–90% of buildings within 2–4 h**; write
  them to `pipeline/out/<town>/config.json` (read by every later stage). (The real fire reached
  Paradise in ~90 min; we accept 2–4 h as "same story, softer physics".)
- **Gate**: place one hand-made ring break around the town's NE edge and assert
  buildings hit drops ≥ 30%. If it doesn't, STOP and tell Zach — the solver is
  pointless until the fire responds to breaks.

### 6. Candidates (candidates.py)

- 2-cell-wide strips: lengths {1.0, 1.5, 2.0 km}, 4 orientations (N–S, E–W, NE–SW,
  NW–SE), centers on a ~480 m lattice; plus arcs following the urban-cell boundary at
  offsets {250, 600, 1000 m}, split into ~1.2 km segments.
- Only **burnable wildland** cells (never NB urban/water/agriculture/barren); strips
  must be ≥ 60% inside the baseline burn corridor (cells with baseline arrival ≤
  horizon) dilated 500 m (scipy binary_dilation). Expected ~1–3k candidates.
- Cost = Σ cell cost; cell = 0.89 acre; per-acre from meta: grass-group $500,
  GS/SH $1500, TU/TL/SB $2500 (rough mechanical-treatment magnitudes — the demo's
  point is relative tradeoffs, not a bid sheet).

### 7. Solver (solve.py)

- Lazy greedy (CELF): max-heap of stale upper bounds on marginal
  buildings-saved-per-dollar; pop → re-evaluate (one dijkstra with current breaks +
  candidate) → re-push or accept. Standard practice for coverage-style objectives;
  the objective isn't provably submodular here, so CELF's laziness is a heuristic —
  fine for a hackathon solver.
- Checkpoint `solutions.partial.json` + curve after **every accepted break** (Ctrl-C
  always leaves usable output). tqdm progress bar with ETA.
- Stop at max budget (5M) or zero marginal. Budget solutions = greedy prefixes
  (matches CONTRACT.md). `--budget N` runs/exports one budget for live use.
- Writes `solver_check.png`: top solution's breaks over the basemap.
- Runtime estimate: worst case ~10–30 min full run; checkpoints make that safe.

### 8. Schemas

**Confirmed byte-for-byte as CONTRACT.md stands — no changes proposed.** The mock
already implements it and the skeleton page renders it; changing anything now costs
more than it buys.

### 9. Cost table & size budget (export.py)

- Cost table: as in meta (grass 500 / shrub 1500 / timber 2500 $/acre); FBFM40
  group → table key mapping: GR→grass, GS+SH→shrub, TU+TL+SB→timber.
- Size estimate for web/data: 6 arrival grids × 152,304 × 2 B → ~0.41 MB b64 each ≈
  2.4 MB; buildings.geojson (Paradise ≈ 10–15k OSM points, 5-decimal coords) ≈
  1–1.5 MB; basemap.png ≈ 0.3–0.6 MB; rest small → ≈ 4–4.5 MB. Enforced hard at
  5 MB; first lever if over: quantize building coords / drop to 4 decimals — never
  silently drop features.

### 10. Boring mechanics

- **fetch_data.py**: `Landfire(bbox=<from towns/<town>.json>)` — for Paradise
  `"-121.70 39.68 -121.38 39.86"` (native Albers
  output — server-side reprojection of categorical rasters is someone else's
  resampler; we do it ourselves), `request_data(layers=["200F40_19","140FBFM40",
  "ELEV2020"], output_path=data_raw/landfire.zip)`. Run inside a
  `concurrent.futures` worker with a **20-min wall-clock timeout** → on timeout or
  error, fallback: ESA WorldCover 2021 10 m (public AWS COG, windowed read by bbox,
  classes → {tree→timber, shrub→shrub, grass→grass, crop→NB93-like, built→urban,
  bare/water→NB}) + Copernicus DEM GLO-30 (public AWS COG) — switch recorded in
  meta.data. Everything cached in data_raw/, skipped on rerun, no API keys.
- **Buildings**: Overpass QL `building=*` with `out center` (centroids server-side —
  no polygon assembly), mirrors (overpass-api.de, overpass.kumi.systems), 3 retries
  with backoff, cached JSON. < 500 results → proxy points from built-up/urban cells,
  `buildings_proxy: true`. (OSM Paradise was heavily mapped during the 2018 response;
  expect ≥ 10k.)
- **build_grid.py**: rasterio `reproject` onto the 334×456 EPSG:3857 grid — nearest
  for FBFM40 (categorical), bilinear for elevation. Writes `alignment_check.png`
  (fuel colors + building dots — eyeball the road grid and canyon) and computes
  `basemap.png` (DEM hillshade × fuel-group colors).
- **run_all.py**: chain with per-stage try/except that prints stage name + input +
  exception and appends to run_log.md; `--quick` = cached downloads + 120 m grid.
- **Order tonight**: fetch → grid (+alignment check) → ros → simulate → calibrate
  (gate) → export baseline → commit/push. Morning: candidates → solve → export
  solutions → integrate.
- **Known risks + levers**: LFPS slow/down → WorldCover+CopDEM fallback already
  specified; Overpass flaky → mirrors/cache/proxy; sim too slow → downsample lever;
  data > 5 MB → coordinate quantization lever; Remap fuels post-fire → LF 2014
  fallback (§2).

### 11. Process discipline (every stage, once approved)

- Fail loud: a stage's top-level try/except exists only to print stage name + inputs
  + exception, append to `pipeline/run_log.md`, and re-raise. The only sanctioned
  fallbacks are the ones specified above (LANDFIRE→WorldCover+CopDEM, OSM→proxy
  buildings), and each records itself in `meta.data`.
- Every stage ends by printing a verification command Zach can run himself
  (open alignment_check.png / solver_check.png, a `--check` invocation, etc.).
- Commit + push after every completed stage. Files always written complete — never
  patched as diffs.
- Anything architecture-changing (schema, grid, model shape) → stop and ask first.
- Plan / Decisions / Deviations in this file stay current as the build runs.

**STOP — approval needed on: grid (§1), fuel vintage rule (§2), ROS numbers (§3),
solver shape (§7), schema freeze (§8). Everything else is mechanics.**

## Decisions

- Repo was created private; flipped **public** at approval time (2026-09-19),
  `gh repo edit --visibility public`.
- `pipeline/out/<town>/` is committed except bulky arrays (`*.npy`/`*.npz`
  gitignored) — check PNGs and grid_meta.json ride along with each stage commit;
  raw downloads stay out of git entirely (`pipeline/data_raw/` ignored).
- Leaflet 1.9.4 vendored from unpkg into `web/vendor/leaflet/` (js + css + images) —
  the demo laptop has unreliable wifi; no CDN anywhere.
- Mock generator is pure-stdlib Python (no numpy/PIL) so `web/mock/` can be
  regenerated before the geo stack is even installed.
- Mock grid is 150 rows × 200 cols on the **real** Paradise bounds — every value is
  fake, but Leaflet placement/alignment is real, so Arya's layer code carries over
  unchanged.
- Mock arrival field is straight-line anisotropic travel time (fast downwind toward
  the southwest, slow upwind) — cheap, and it produces the right visual: a wedge from
  Pulga (top-right) into Paradise. Tuned to 82% of mock buildings hit at baseline
  (real calibration targets 60–90%).
- Mock top budget tiers ($2M/$3M/$5M) are identical — the fake solver runs out of
  useful breaks; CONTRACT.md documents that adjacent budgets may legitimately tie.
- Elevation fetched as `ELEV2020` (only vintage LFPS offers) — terrain doesn't
  change; slope is computed per-edge from the DEM, so no slope/aspect layers needed.

## Deviations

- **landfire package vs Python 3.13**: every release of `landfire` pins
  Requires-Python < 3.12 (stale packaging; the machine runs 3.13), which aborted the
  original one-shot `pip install -r requirements.txt`. Installed it with
  `pip install landfire --ignore-requires-python --only-binary=:all:` and verified
  import + ProductSearch work (one benign pydantic-v2 config warning).
  requirements.txt documents the extra command; rasterio and the rest of the stack
  install and import cleanly on 3.13. Conservative fallback (WSL / a 3.11 venv) not
  needed unless `request_data` misbehaves at fetch time — Zach decides that.
