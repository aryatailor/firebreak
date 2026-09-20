# Morning briefing — Sunday, state as of overnight autonomous run

Both towns are live behind `web/towns/index.json`. Everything below is pushed;
the frontend session only needs the selector wired to the index.

## What worked

- **The second town took one config file and one command.** `towns/altadena.json`
  + `python pipeline/run_all.py --town altadena` = fetch (LF2024 fuels via live
  LFPS, 24 s) → grid → calibrate → solve → export, ~12 minutes end to end,
  fully unattended. The §0 town-config bet paid off exactly as designed.
- **The calibration gate machinery earned its keep on Altadena**: the plan's
  break_mult 0.05 only cut 17.5% there (fast chaparral leaks through a 20×
  break), so the gate auto-descended and chose 0.02 (50×) → 35.2% drop, PASS.
  Per-town physics knobs live in each town's config.json, as intended.
- Altadena's solver output is *clean*: a shield of breaks along the mountain
  front, densest at the Eaton Canyon mouth. Ring fallback exists
  (`pipeline/out/altadena/ring_fallback.json`) but is not needed.
- Paradise numbers unchanged and strong; both PNG check images eyeballed good
  (San Gabriels north / urban plain south / ignition on the canyon mouth).
- Two Claude sessions shared one working tree all night with zero collisions
  after switching to explicit-path staging.

## Altadena, CA — Eaton Fire re-run

Baseline: first home hit at **37 min**, town center at 193 min, **11,841 of
15,000 homes** hit within 12 h.

| Budget | Spent | Breaks | Homes saved | $ / home | Evac. min (town) | Evac. min (1st home) |
|---|---|---|---|---|---|---|
| $500k | $464,135 | 11 | 2,474 | $188 | +103 | +132 |
| $1M | $996,800 | 19 | 8,000 | **$125** | +351 | +317 |
| $2M | $1,967,790 | 35 | 10,349 | $190 | +454 | +494 |
| $3M | $2,947,680 | 51 | **11,838** | $249 | +739 | +627 |
| $5M | $2,947,680 | 51 | 11,838 | $249 | +739 | +627 |

The $1M point is the demo money shot: $125 per home saved.

## What didn't / judgment calls for Zach

1. **Altadena saturates at $2.95M — the solver saves every baseline-hit home**
   (3 left), and $3M/$5M are identical (CONTRACT allows ties, but the slider
   will have a dead zone at the top). At break_mult 0.02 the single mountain-front
   interface is sealable within a 12 h horizon. Options: (a) lean in — "Altadena
   was savable for $3M" is a strong headline; (b) leakier breaks (but the gate
   needs ≥ 30%); (c) shorter horizon for Altadena. I shipped (a) since the model
   chose it honestly.
2. **Altadena's 15,000 proxy homes spread over ALL 29,107 urban cells**, which
   includes a lot of Pasadena along the south of the box — Altadena proper is
   diluted. If you want density concentrated where the Eaton Fire actually hit,
   shrink the bbox south edge (config-only, ~15 min rerun).
3. Paradise's minutes-bought-to-town plateaus $3M→$5M (+418) — later breaks
   defend other lobes of town; homes saved keeps climbing. Expected, not a bug.
4. The exported "breaks slow fire Nx" simplification line is now driven by each
   town's calibrated break_mult (Paradise 20×, Altadena 50×) — was hardcoded 20×
   for a few minutes; Altadena re-exported with the fix.
5. Altadena's OSM fetch returned 103,950 buildings (Pasadena is well mapped) —
   cached in data_raw, unused per the proxy policy, available if ever wanted.

## Where everything is

- Selector: `web/towns/index.json` (2 towns). Paradise: `web/data/` (19.17 MB,
  71 steps). Altadena: `web/towns/altadena/` (9.46 MB, 52 steps).
- Judge-facing: `README.md` (results, model, solver, sources, simplifications,
  Planscape positioning). Submission: `PLUME.md` (12-line how-we-built-it).
- Per-town knobs: `towns/*.json`; calibrated params: `pipeline/out/<town>/config.json`;
  audit trail: `implementation-notes.md` + `pipeline/run_log.md`.
