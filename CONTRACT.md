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
