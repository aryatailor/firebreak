# Firebreak

**Fuel-break optimizer on real terrain: where to cut to stop a wildfire, per dollar.**
Built at HackMIT 2026.

Pick a town that actually burned — Paradise, CA (the 2018 Camp Fire) — and press
play: the fire re-runs from its real ignition point, over real LANDFIRE fuels and
real terrain, under the historical wind. Then move the budget slider. A solver has
already worked out, for every budget, which fuel breaks save the most homes per
dollar; the breaks appear on the map, the fire re-runs against them, and the panel
shows homes hit, dollars spent, and **evacuation minutes bought** — live. The point
is prevention economics a fire-safe council can argue about in a meeting, not a
planning document.

## Results — Paradise, CA (Camp Fire re-run)

Baseline: the fire reaches the first homes in **59 minutes** (the real fire took
about 90); within 12 hours it hits **7,765 of 11,000 homes**.

| Budget | Spent | Breaks | Homes saved | $ / home | Evac. minutes bought |
|---|---|---|---|---|---|
| $500k | $430,760 | 5 | 2,515 | $171 | +153 |
| $1M | $983,005 | 14 | 3,610 | $272 | +206 |
| $2M | $1,990,930 | 27 | 4,663 | $427 | +282 |
| $3M | $2,948,125 | 43 | 5,519 | $534 | +418 |
| $5M | $4,997,350 | 70 | 6,173 | $810 | +418 |

The first half-million dollars saves a home for every $171 spent.

## How the simulation works

Fire arrival time = **minimum travel time** from the ignition point. The town is a
60 m grid; each cell connects to its 8 neighbors by a directed edge whose crossing
time is distance ÷ speed, where speed is the product of three factors:

- **Fuel** — Scott & Burgan FBFM40 fuel models, grouped: grass burns fastest,
  chaparral about half that, timber litter ~8× slower; town cells carry a slow
  structure-to-structure rate (the Camp Fire burned Paradise house to house).
- **Slope** — fire roughly doubles its speed per 10° uphill (the classic
  firefighter rule of thumb), capped, and slows downhill.
- **Wind** — exponential in the angle to downwind; at 45 mph the head-to-heel
  ratio is ~12:1, which is what makes a wind-driven fire a wind-driven fire.

One global speed parameter is **calibrated to the historical outcome** (we don't
claim fire physics — we claim the same fire). A single Dijkstra run gives the
arrival time of every cell in ~0.1 s, which is what makes optimization possible.
Before the solver is allowed to run, a hard gate: a hand-placed test ring around
town must cut homes hit by ≥ 30%, or the pipeline stops and says so.

## How the solver works

Plain English: we enumerate ~4,800 candidate breaks — 120 m-wide strips at several
angles and lengths, plus arcs hugging the town's edge — priced by what mechanical
fuel treatment roughly costs per acre in that fuel type. The solver repeatedly
asks "which single break saves the most homes per dollar right now?", re-running
the whole fire to answer, buys it, and repeats until the money runs out. The lazy
part (CELF): a break's value only shrinks as other breaks get bought, so stale
scores are re-checked only when a break reaches the top of the queue — thousands
of simulations avoided. Each budget's answer is a prefix of the same greedy
sequence, so bigger budgets visibly contain smaller ones on the slider.

## Data sources

- Fuels + elevation: [LANDFIRE](https://landfire.gov) via the
  [LFPS API](https://lfps.usgs.gov) — pre-fire vintages (LF2016 for Paradise),
  product codes validated against the live product table at fetch time.
- Buildings: [OpenStreetMap](https://www.openstreetmap.org) via Overpass
  (fetched + cached; displayed homes are a density proxy — see simplifications).
- Automatic fallback if LANDFIRE is down:
  [ESA WorldCover 2021](https://esa-worldcover.org) +
  [Copernicus DEM GLO-30](https://registry.opendata.aws/copernicus-dem/), both
  public AWS open data, no keys.
- Online basemap: Esri World Imagery tiles; offline, a hillshade basemap rendered
  from the DEM. The whole demo works with wifi off.

## Real vs simplified

1. Homes are estimated from developed-land cells at pre-fire density (11,000 for
   Paradise), not individual footprints — 2026 OSM maps the post-fire town.
2. Fire spread is a calibrated graph travel-time model, not fire physics — one
   free speed parameter fit to the historical outcome.
3. One uniform historical wind for all 12 hours; no ember spotting, no weather
   change. (The real Camp Fire threw embers over a river canyon.)
4. Fuel breaks slow fire 20× rather than stopping it; break costs are rough
   mechanical-treatment magnitudes ($500–$2,500/acre by fuel), not bids.
5. Fuels are the newest pre-fire LANDFIRE vintage, resampled to a 60 m grid;
   evacuation minutes are model arrival-time deltas, not traffic modeling.

## Run it

```
python -m http.server -d web 8000        # the demo (static, offline-capable)
```

Rebuild any town from raw data:

```
pip install -r requirements.txt --only-binary=:all:
python pipeline/run_all.py --town paradise
```

A new town is one config file — bounds, ignition, wind, homes estimate, fuel
vintage — then one command:

```
cp towns/paradise.json towns/yourtown.json   # edit it
python pipeline/run_all.py --town yourtown
```

Every stage is cached, idempotent, fails loud into `pipeline/run_log.md`, and
survives LANDFIRE being down (public-COG fallback recorded in the metadata).

## Closest existing tool

[Planscape](https://planscape.org) is the serious version of this idea: statewide
treatment prioritization for California land managers, running in hours. Firebreak
is the meeting-sized version — one town, one historical fire, answers in seconds,
with a budget slider a council member can drag themselves. Different question:
not "where should the state treat," but "what would $500k have bought *us*."

| Path | What | 
|---|---|
| `pipeline/` | fetch → grid → fire sim → calibrate → candidates → solver → export |
| `web/` | static page: vendored Leaflet + canvas fire + panel, no build step |
| `web/data/`, `web/towns/<id>/` | committed pipeline output per town |
| `towns/*.json` | per-town config (bounds, ignition, wind, fuel vintage) |
| `CONTRACT.md` | the data interface between pipeline and page |
| `implementation-notes.md` | plan / decisions / deviations, written as we went |
