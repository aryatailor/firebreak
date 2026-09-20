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

## Results — Altadena, CA (Eaton Fire re-run)

Same pipeline, second town, one config file (`towns/altadena.json`, LANDFIRE
LF2024 fuels — the newest pre-fire vintage). Baseline: fire at the first homes
in **44 minutes**; 13,137 of 15,000 homes hit within 12 hours.

| Budget | Spent | Breaks | Homes saved | $ / home | Evac. minutes bought |
|---|---|---|---|---|---|
| $500k | $487,720 | 9 | 2,982 | $164 | +117 |
| $1M | $960,310 | 17 | 8,219 | **$117** | +332 |
| $2M | $1,956,220 | 32 | 11,822 | $165 | +437 |
| $3M | $2,984,615 | 49 | 12,911 | $231 | +549 |

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

## Robustness — "what if the wind was different?"

The shipped Paradise breaks (optimized for 35 mph from 45°) re-simulated under 9
wind variants; homes saved is against the same-variant baseline
(`pipeline/sensitivity.py`, full data in `web/data/sensitivity.json`):

| Wind | Baseline hit | $500k saved / +min | $1M saved / +min | $2M saved / +min |
|---|---|---|---|---|
| 30° @ 25 mph | 2,524 | 1,107 / +213 | 1,136 / +318 | 1,140 / +430 |
| 30° @ 35 mph | 5,095 | 2,822 / +199 | 2,951 / +298 | 2,996 / +406 |
| 30° @ 45 mph | 7,715 | 2,871 / +162 | 3,788 / +245 | 4,672 / +354 |
| 45° @ 25 mph | 4,124 | 2,030 / +187 | 2,162 / +243 | 2,170 / +330 |
| **45° @ 35 mph (calibrated)** | 7,765 | 2,515 / +153 | 3,610 / +206 | 4,663 / +282 |
| 45° @ 45 mph | 10,100 | 1,113 / +124 | 1,816 / +171 | 2,696 / +237 |
| 60° @ 25 mph | 5,278 | 2,185 / +167 | 2,998 / +198 | 3,072 / +269 |
| 60° @ 35 mph | 9,521 | 1,903 / +143 | 2,604 / +163 | 3,699 / +222 |
| 60° @ 45 mph | 10,536 | 61 / +119 | 97 / +136 | 305 / +183 |

Honestly: the breaks buy meaningful evacuation time under every wind we tested
(+2 to +7 hours), but homes-saved collapses in the worst case — a stronger wind
from a direction the plan wasn't optimized for (60° @ 45 mph) routes the fire
around the breaks, and only the time bought survives.

## Robust breaks — "what if it starts somewhere else?"

`python pipeline/run_all.py --town <t> --robust` builds an ensemble of 25 fires
(the historical ignition plus 11 upwind wildland ignitions, each under the
calibrated wind and ±10 mph / ±15° variants; `pipeline/ensemble.py`) and re-runs
the CELF solver with objective = **mean homes saved across the ensemble per
dollar** (`pipeline/robust.py`). The single-fire plan is kept
(`solve_result_single.json`) and both plans are scored on the same ensemble
(`web/<town>/robust.json`), so the page can toggle between "optimized for the
historical fire" and "optimized for any plausible fire".

The robust plan's *average* is far below the historical plan's headline on its one
fire — but the historical plan averaged over the same 25 fires is lower still
(Paradise: 218 / 326 / 467 / 687 mean saved at $500k / $1M / $2M / $3M, vs the
robust plan's 327 / 540 / 839 / 1,193). The page shows both numbers.

| Town | Budget | Robust plan, mean saved | Historical plan, saved on its fire |
|---|---|---|---|
| Paradise | $500k / $1M / $2M / $3M / $5M | 327 / 540 / 839 / 1,193 / 1,659 | 2,515 / 3,610 / 4,663 / 5,519 / 6,173 |
| Altadena | $500k / $1M / $2M / $3M | 2,214 / 3,247 / 5,455 / 6,768 | 2,982 / 8,219 / 11,822 / 12,911 |
| Santa Rosa (Tubbs, 2017) | $500k / $1M / $2M / $3M | 4,703 / 6,918 / 9,713 / 11,450 | 9,105 / 11,350 / 16,050 / 18,443 |

Santa Rosa (`towns/santarosa.json`, LF2016 fuels): baseline 21,942 of 30,000
homes hit, first home at 49 minutes; calibration ring cut homes hit by 57%.

Two more historical towns were attempted and **did not pass the calibration
gates**, so they are not shipped: Lahaina, HI (every parameter sweep hits 99.8%
of homes in the box — the model cannot reproduce a partial burn there) and
Superior–Louisville, CO (flat grass under 60 mph wind: the hand-placed test ring
cuts homes hit by <1%, far below the required 30%). Configs are in `towns/` for
whoever wants to try.

The browser also runs the fire itself now (`web/sim.js`, the CONTRACT.md
algorithm on `physics.json`; `node web/tools/parity.mjs` checks it against the
Python arrival grids — 100% exact on all shipped towns). That is what powers
"start a fire anywhere" and the wind sliders.

## Run it

```
python -m http.server -d web 8000        # the demo (static, offline-capable)
```

Rebuild any town from raw data:

```
pip install -r requirements.txt --only-binary=:all:
python pipeline/run_all.py --town paradise            # historical-fire plan only
python pipeline/run_all.py --town paradise --robust   # + ensemble and robust plan
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
