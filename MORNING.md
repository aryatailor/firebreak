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

## Altadena, CA — Eaton Fire re-run (v2, after the bbox + budget fixes below)

Baseline: first home hit at **44 min**, town center at 284 min, **13,137 of
15,000 homes** hit within 12 h. Calibration re-chose k_w=0.25 for the tighter
box; gate passed at 33.2% with break_mult 0.02.

| Budget | Spent | Breaks | Homes saved | $ / home | Evac. min (town) | Evac. min (1st home) |
|---|---|---|---|---|---|---|
| $500k | $487,720 | 9 | 2,982 | $164 | +117 | +35 |
| $1M | $960,310 | 17 | 8,219 | **$117** | +332 | +200 |
| $2M | $1,956,220 | 32 | 11,822 | $165 | +437 | +200 |
| $3M | $2,984,615 | 49 | 12,911 | $231 | +549 | +200 |

The $1M point is the demo money shot: $117 per home saved.

## What didn't / judgment calls for Zach

1. ~~Altadena saturates / $3M–$5M dead zone~~ **RESOLVED**: budgets capped at
   $3M (Zach's call) and the tighter box leaves 226 homes unsaved at the top —
   no tie, no perfect-solution artifact, slider is live across its whole range.
2. ~~15,000 proxy homes diluted over Pasadena~~ **RESOLVED**: south edge moved
   34.13 → 34.16 (Zach's call; verified against the alignment PNG — the
   Altadena–Pasadena line is ~34.165). Urban cells 29,107 → 15,204, so homes sit
   ~1 per developed cell in Altadena proper. Full chain re-run, re-exported
   (7.54 MB, 50 steps); `web/data/` untouched.
3. Paradise's minutes-bought-to-town plateaus $3M→$5M (+418) — later breaks
   defend other lobes of town; homes saved keeps climbing. Expected, not a bug.
4. The exported "breaks slow fire Nx" simplification line is now driven by each
   town's calibrated break_mult (Paradise 20×, Altadena 50×) — was hardcoded 20×
   for a few minutes; Altadena re-exported with the fix.
5. Altadena's OSM fetch returned 103,950 buildings (Pasadena is well mapped) —
   cached in data_raw, unused per the proxy policy, available if ever wanted.

## Where everything is

- Selector: `web/towns/index.json` (2 towns). Paradise: `web/data/` (19.18 MB,
  71 steps, + optional `sensitivity.json` — wind robustness, see README).
  Altadena: `web/towns/altadena/` (7.54 MB, 50 steps).
- Judge-facing: `README.md` (results, model, solver, sources, simplifications,
  Planscape positioning). Submission: `PLUME.md` (12-line how-we-built-it).
- Per-town knobs: `towns/*.json`; calibrated params: `pipeline/out/<town>/config.json`;
  audit trail: `implementation-notes.md` + `pipeline/run_log.md`.
