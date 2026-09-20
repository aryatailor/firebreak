# CONTRACT.md — the data interface

The pipeline (Zach) writes `web/data/`. The page (Arya) reads it. `web/mock/` is fake
data that matches this contract **exactly** — build against mock, then switch one
constant (`DATA_DIR` in `web/app.js`) from `'mock'` to `'data'`.

**Changing anything in this file = message the other person first, then commit.**

## Conventions

- All grid arrays are **row-major, top row = north**. Index of cell (row, col) is
  `row * cols + col`. Row 0 is the northernmost row; col 0 is the westernmost column.
- Grid arrays are **base64 of little-endian uint16**, exactly `rows * cols` values.
  Value `65535` = never reached within the horizon. Any other value = fire arrival
  time in minutes after ignition.
- All coordinates in `web/data/` (and `web/mock/`) are **EPSG:4326** — plain lat/lon.
- `bounds` is the **exact geographic extent of the grid image**. Place the grid canvas
  with Leaflet bounds — `L.imageOverlay(url, [[south, west], [north, east]])` or a
  custom canvas layer positioned from the same corners — and it aligns with the map
  tiles. No client-side reprojection is needed.

Decoding a grid in JS:

```js
const bin = atob(meta_or_solution.arrival_min_b64);
const bytes = Uint8Array.from(bin, ch => ch.charCodeAt(0));
const dv = new DataView(bytes.buffer);
const grid = new Uint16Array(bytes.length / 2);
for (let i = 0; i < grid.length; i++) grid[i] = dv.getUint16(i * 2, true); // little-endian
```

## meta.json

```jsonc
{ "town": "Paradise, CA", "story": "<one line>",
  "bounds": {"south":39.68,"west":-121.70,"north":39.86,"east":-121.38},
  "grid": {"rows":R,"cols":C,"cell_m":60},
  "wind": {"speed_mph":35,"from_deg":45},
  "ignition": {"row":..,"col":..,"lat":..,"lon":..,"label":"Pulga (Camp Fire origin)"},
  "horizon_min": 720,
  "budgets": [500000,1000000,2000000,3000000,5000000],
  "cost_per_acre": {"grass":500,"shrub":1500,"timber":2500},
  "data": {"fuel":"<product + version>","terrain":"<source>","buildings":"<source>",
           "buildings_proxy":false,"fetched":"YYYY-MM-DD"},
  "simplifications": ["<one line each>"] }
```

Notes: `budgets` is ascending. `from_deg` is the direction the wind blows FROM
(45 = from the northeast, i.e. blowing toward the southwest). `buildings_proxy` is true
only if OSM had too few buildings and land-cover cells were used as stand-ins.
`bounds` in `web/data/` may differ from the numbers above by less than one cell —
always read it from the file, never hard-code.

## baseline.json

```jsonc
{ "arrival_min_b64": "...",
  "buildings_hit": [ids],
  "stats": {"buildings_total":N,"buildings_hit":N,"minutes_to_town_center":N,
            "minutes_to_town":N,"minutes_to_first_home":N} }
```

The fire with $0 spent. `buildings_hit` are ids of buildings whose cell is reached
within `horizon_min`. `minutes_to_town` = first arrival at the town center (same
value as `minutes_to_town_center`, kept for compatibility); `minutes_to_first_home`
= earliest arrival at any building cell. These may exceed `horizon_min` (the grid
caps at 65535 but stats report the true model time).

## solutions.json

One entry per budget in `meta.budgets`, same ascending order:

```jsonc
[ { "budget":N, "cost":N,
    "breaks": <GeoJSON FeatureCollection of polygons,
               feature properties {id, cells, cost, fuel_group}>,
    "arrival_min_b64":"...",
    "buildings_hit":[ids],
    "stats": {"buildings_hit":N, "houses_saved":N, "cost_per_house_saved":N,
              "minutes_to_town":N, "minutes_to_first_home":N,
              "minutes_bought_town":N, "minutes_bought_first_home":N} } ]
```

`minutes_bought_town` / `minutes_bought_first_home` = this solution's arrival time
minus baseline's — the evacuation minutes the money buys. `cost_per_house_saved`
is null when houses_saved is 0.

`cost` ≤ `budget` (the solver spends what's worth spending). `houses_saved` =
baseline `buildings_hit` − this entry's `buildings_hit`. Each budget's solution is the
greedy prefix, so bigger budgets contain the smaller budgets' breaks. Two adjacent
budgets may be identical if the solver found nothing more worth buying.

## curve.json

Every greedy step, in acceptance order:

```jsonc
{ "points": [ {"step":i, "break_id":..., "cumulative_cost":N, "cumulative_saved":N} ] }
```

## steps.json

For the continuous budget slider: one entry per accepted greedy step, in
acceptance order, plus step 0 = baseline ($0, no breaks):

```jsonc
[ { "step": 0, "break_ids": [], "cumulative_cost": 0, "cumulative_saved": 0,
    "minutes_to_first_home": N, "minutes_to_town": N, "minutes_bought": 0,
    "arrival_b64": "<uint8 grid, see below>" },
  { "step": k, "break_ids": [ids added at this step], "cumulative_cost": N,
    "cumulative_saved": N, "minutes_to_first_home": N, "minutes_to_town": N,
    "minutes_bought": N, "arrival_b64": "..." } ]
```

- `arrival_b64` here is **uint8 in 5-minute buckets** (not uint16): minutes ≈
  value × 5; **255 = never reached within the horizon**. Exactly rows × cols
  values, row-major, top row north — same orientation as the uint16 grids, one
  byte per cell (`bytes[i]`, no DataView needed).
- `minutes_bought` = this step's `minutes_to_town` − baseline's (0 at step 0).
- The fire at cumulative_cost k = draw breaks of steps 1..k together and use
  step k's arrival grid.

## breaks.geojson

FeatureCollection of **every** break polygon the solver ever accepted, properties
`{id, step, cells, cost, fuel_group}`. At slider step k, draw features with
`step <= k`. (solutions.json still embeds per-budget FeatureCollections for the
five snap budgets — unchanged.)

## buildings.geojson

FeatureCollection of **Point centroids** (not footprints, to keep it small).
Properties: `{id, row, col}` — `row`/`col` is the grid cell containing the building, so
"is this building burning at time t" is one array lookup: `grid[row*cols+col] <= t`.

## basemap.png

RGBA image of the full grid extent: hillshade blended with fuel-group colors. This is
the offline base layer — place it with `bounds`, same as the fire canvas. Its pixel
aspect matches the grid; it may be rendered at a higher resolution than one pixel per
cell.

## fuel_legend.json

```jsonc
[ {"group":"grass","color":"#d4d06e"}, ... ]
```

Colors used in `basemap.png` fuel tinting, for the legend UI.

## physics.json — everything needed to run the fire in the browser

Present in every town data dir. This is the complete model: with these numbers and
the algorithm below, a JS implementation reproduces the Python simulation.

```jsonc
{ "grid": {"rows":R,"cols":C,"cell_m":60},
  "fuel_class_b64": "<uint8 per cell, rows*cols values, row-major, top row north>",
  "elev_m_b64": "<int16 little-endian per cell, metres, same order>",
  "rates": {"1":1.0,"2":0.65,"3":0.45,"4":0.35,"5":0.30,"6":0.12,"7":0.08},
  "scale_m_per_min": 30.0, "k_w": 0.45,
  "wind": {"speed_mph":35,"from_deg":45},
  "slope_formula": "2^(theta_deg/10) clamped to [0.5, 8], theta = uphill angle along travel direction",
  "wind_formula": "exp(k_w * (v_mph/10) * cos(delta)), delta = angle between travel direction and downwind",
  "edge_weight": "dist_m / (min(base_i, base_j) * f_slope * f_wind * scale_m_per_min); dist 60 orthogonal, 60*sqrt(2) diagonal",
  "break_mult": 0.05, "horizon_min": 720, "bucket_min": 5,
  "cost_per_acre": {"grass":500,"shrub":1500,"timber":2500}, "cell_acres": 0.89,
  "homes_rc": [[row,col], ...],
  "ignition_rc": [row,col] }
```

**Fuel classes** (`fuel_class_b64`): `0` non-burnable (water, snow, agriculture,
barren — fire never enters), `1` grass, `2` grass-shrub, `3` shrub, `4` slash,
`5` timber-understory, `6` timber-litter, `7` urban/developed. Classes 1–7 all
have an entry in `rates`; class 0 has none and is impassable.

`homes_rc` is one `[row, col]` per home, aligned index-for-index with
`buildings.geojson` features. `cost_per_acre` keys map from class:
1 → `grass`, 2 and 3 → `shrub`, 4, 5 and 6 → `timber`; class 7 is never clearable.

### The algorithm, exactly

Fire arrival time is the **minimum travel time** from the ignition cell over a
directed 8-neighbour graph. Dijkstra, one source, no heuristic.

1. **Neighbours**, in this order (`dr`, `dc`; `dr +1` = south, `dc +1` = east):
   `(-1,-1) (-1,0) (-1,1) (0,-1) (0,1) (1,-1) (1,0) (1,1)`.
   Neighbours outside the grid have no edge. Order does not affect the result;
   it is fixed only so implementations can be diffed.
2. **Distance** for that edge: `dist_m = cell_m * hypot(dr, dc)` — exactly
   `cell_m` orthogonally, `cell_m * sqrt(2)` diagonally. This is **ground**
   distance; never use the EPSG:3857 cell size here.
3. **Base rate** of a cell: `rates[fuel_class]`, or `0` for class 0. If the cell
   is cleared (a fuel break) multiply it by `break_mult`.
4. **Slope factor** for edge i→j:
   `dz = elev[j] - elev[i]` (metres, positive = uphill),
   `theta_deg = degrees(atan(dz / dist_m))`,
   `f_slope = min(max(2 ** (theta_deg / 10), 0.5), 8)`.
5. **Wind factor** for edge i→j — depends only on direction, so it is one of 8
   constants: `bearing = atan2(dc, -dr)` (radians, compass sense),
   `downwind = radians((wind.from_deg + 180) mod 360)`,
   `f_wind = exp(k_w * (wind.speed_mph / 10) * cos(bearing - downwind))`.
6. **Edge weight** (minutes):
   `dist_m / (min(base_i, base_j) * f_slope * f_wind * scale_m_per_min)`.
   If `min(base_i, base_j)` is 0 the edge does not exist.
7. **Dijkstra** from `ignition_rc`; the result is arrival minutes per cell.
   Unreached cells are infinite. Ties need no tie-breaking rule — equal-distance
   paths give equal arrival times, which is all that is observable.

Consequences worth stating, because they are easy to get wrong:

- **Cleared cells slow fire in both directions.** Because the edge uses
  `min(base_i, base_j)`, multiplying a cell's base rate by `break_mult` slows
  every edge **into** it and every edge **out of** it. There is no separate
  incoming/outgoing handling, and a 2-cell-wide break cannot be crossed
  diagonally in one step at full speed.
- **Urban cells burn.** Class 7 is burnable at `rates["7"]` (the calibrated
  structure-to-structure rate), not a wall. Only class 0 blocks fire.
- **A home is "reached"** when `arrival[row * cols + col] <= horizon_min`, using
  that home's `homes_rc` entry. Homes in the same cell share a fate.
- **Bucket encoding** (`arrival_b64` in steps.json, and parity.json):
  `round(minutes / bucket_min)`, and exactly `255` when the cell is unreachable
  or arrives after `horizon_min`.

## parity.json — proof the JS sim matches Python

```jsonc
{ "bucket_min": 5, "horizon_min": 720,
  "baseline_b64": "<uint8 buckets, no breaks>",
  "break_cells": [[row,col], ...],
  "break_b64": "<uint8 buckets, with exactly those cells cleared>",
  "target": "at least 99% of cells within 1 bucket of these arrays" }
```

Both arrays are computed by Python **from the quantised values in physics.json**
(int16 elevation, not the internal float grid), so an exact reimplementation
should match nearly cell-for-cell rather than merely closely. `break_cells` is a
2-cell-wide strip; apply it exactly as step 3 above and compare against
`break_b64`.

## sensitivity.json (optional)

May be absent — the page must not require it. Wind sensitivity of the shipped
solutions: the exact break sets re-simulated under wind variants, homes_saved
measured against the **same-variant** baseline (baselines differ per wind):

```jsonc
[ {"budget":500000, "wind_from_deg":30, "wind_mph":25,
   "homes_saved":N, "minutes_bought":N}, ... ]
```

The calibrated wind appears as one of the entries. `minutes_bought` is the
town-center arrival delta vs the same-variant baseline.

## web/towns/index.json

The town selector. An array, one entry per available town, in display order:

```jsonc
[ {"id":"paradise","name":"Paradise, CA","event":"Camp Fire · Nov 8, 2018",
   "data_dir":"data/"} ]
```

`data_dir` is relative to `web/` and contains the full file set described in this
document (Paradise lives at the legacy `data/`; later towns at
`towns/<id>/`). The pipeline appends an entry when a new town exports
successfully.

## File inventory

`web/data/` (and `web/mock/`) contains exactly: `meta.json`, `baseline.json`,
`solutions.json`, `curve.json`, `steps.json`, `breaks.geojson`,
`buildings.geojson`, `basemap.png`, `fuel_legend.json`. Total for `web/data/`
stays ≤ 20 MB (enforced by the exporter; served from localhost — the old 5 MB cap
was for cloning). `web/mock/` predates `steps.json`/`breaks.geojson`; the page
must degrade to the five snap budgets when they are absent.
