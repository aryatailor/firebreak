"""calibrate.py - pick the free parameters, then prove breaks matter (plan section 5).

- Sweep scale (m/min for grass) x k_w (wind response) x structure_rate (urban
  spread), print a table of minutes to first home / town center and % homes hit
  within the horizon.
- Selection (amended after the first live sweep - see Deviations): the plan's
  joint target (town in 2-4 h AND 60-90% homes hit) is infeasible under this
  model - any config fast enough to reach town in 2-4 h burns the whole box well
  inside 12 h (fire flows around town through wildland and enters every lobe), so
  homes hit is ~100% and a break's delay is invisible at the horizon. The binding
  target is kept: **60-90% of homes hit at the horizon** (that is what makes
  breaks able to matter); arrival time is reported for the story, not filtered on.
- GATE: one ring break (2 cells wide, ~500 m off the town's windward edge,
  windward half only) must cut homes hit >= 30%. The gate searches break_mult
  downward from the plan's 0.05 and records the weakest multiplier that passes;
  if none passes, fail loud and stop.
- Chosen params (incl. the gate-passing break_mult) go to
  pipeline/out/<town>/config.json - every later stage reads them from there.

Verify:
    python pipeline/calibrate.py --town paradise   # table + gate PASS, twice = same
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys

import numpy as np
from scipy.ndimage import binary_dilation

import common
import ros
from simulate import Simulator

SCALES = [30.0, 45.0, 60.0, 90.0]
KWS = [0.25, 0.35, 0.45]
STRUCTURE_RATES = [0.15, 0.08]
BREAK_MULTS = [0.05, 0.02, 0.01]   # weakest first; gate takes the first that passes

HIT_MIN, HIT_MAX = 0.60, 0.90      # fraction of homes hit at horizon - the target
GATE_DROP = 0.30
MAX_GATE_TRIES = 4                 # candidates to try against the gate


def ring_break_mask(sim: Simulator) -> np.ndarray:
    """The hand-placed gate break: a 2-cell band ~500-600 m outside the urban
    edge, kept only on the windward half (the side the fire comes from) and only
    on burnable wildland."""
    urban = sim.fuel == 91
    inner = binary_dilation(urban, iterations=8)    # ~480 m off the urban edge
    outer = binary_dilation(urban, iterations=10)   # 2 cells further
    band = outer & ~inner

    rate = ros.base_rate(sim.fuel, ros.DEFAULT_PARAMS)
    wildland = (rate > 0) & ~urban

    # windward = within +/-90 deg of the direction the wind comes FROM, seen from
    # the town center
    tc_r, tc_c = sim.town_center
    rr, cc = np.meshgrid(np.arange(sim.rows), np.arange(sim.cols), indexing="ij")
    bearing = np.degrees(np.arctan2(cc - tc_c, tc_r - rr))  # atan2(east, north)
    delta = (bearing - sim.cfg["wind"]["from_deg"] + 180.0) % 360.0 - 180.0
    windward = np.abs(delta) <= 90.0

    ring = band & wildland & windward
    if ring.sum() < 20:
        sys.exit(f"gate ring came out with only {int(ring.sum())} cells - "
                 f"urban blob or wind config looks wrong")
    return ring


def run(town: str) -> None:
    sim = Simulator(town)
    print(f"calibrate: {town}, ignition cell {sim.ignition}, "
          f"town center cell {sim.town_center}")
    print(f"  sweep: scale {SCALES} x k_w {KWS} x structure_rate {STRUCTURE_RATES}")
    print(f"  target: {HIT_MIN:.0%}-{HIT_MAX:.0%} homes hit at horizon "
          f"(arrival time reported, not filtered - see Deviations)")

    table = []
    print(f"\n  {'scale':>6} {'k_w':>5} {'str_rate':>8} {'first_home':>10} "
          f"{'town_ctr':>9} {'homes_hit':>10} {'sim_s':>6}")
    for scale in SCALES:
        for k_w in KWS:
            for sr in STRUCTURE_RATES:
                params = {**ros.DEFAULT_PARAMS, "scale": scale, "k_w": k_w,
                          "structure_rate": sr}
                s = sim.stats(sim.arrival(params))
                row = {"scale": scale, "k_w": k_w, "structure_rate": sr,
                       "minutes_to_first_home": s["minutes_to_first_home"],
                       "minutes_to_town": s["minutes_to_town_center"],
                       "frac_homes_hit": s["frac_homes_hit"],
                       "sim_seconds": s["sim_seconds"]}
                table.append(row)
                fh = row["minutes_to_first_home"]
                mtt = row["minutes_to_town"]
                print(f"  {scale:>6.0f} {k_w:>5.2f} {sr:>8.2f} "
                      f"{fh if fh is not None else 'never':>10} "
                      f"{mtt if mtt is not None else 'never':>9} "
                      f"{row['frac_homes_hit']:>10.1%} {row['sim_seconds']:>6.3f}")

    in_band = [r for r in table
               if HIT_MIN <= r["frac_homes_hit"] <= HIT_MAX
               and r["minutes_to_first_home"] is not None]
    if not in_band:
        sys.exit(f"no sweep combo lands in the {HIT_MIN:.0%}-{HIT_MAX:.0%} homes-hit "
                 f"band - the model needs rethinking, STOP (plan section 5)")

    # prefer hit fraction near 75%, mildly prefer an earlier first-home time
    # (drama); the gate has the final say
    def score(r):
        return (abs(r["frac_homes_hit"] - 0.75) / 0.15
                + 0.3 * r["minutes_to_first_home"] / sim.cfg["horizon_min"])

    in_band.sort(key=score)
    ring = ring_break_mask(sim)
    print(f"\n  gate ring: {int(ring.sum())} cells (~{int(ring.sum()) * 0.89:.0f} "
          f"acres), 2 cells wide, windward half, >= 30% drop required")

    chosen, gate, attempts = None, None, []
    for r in in_band[:MAX_GATE_TRIES]:
        for bm in BREAK_MULTS:
            params = {**ros.DEFAULT_PARAMS, "scale": r["scale"], "k_w": r["k_w"],
                      "structure_rate": r["structure_rate"], "break_mult": bm}
            base = sim.stats(sim.arrival(params))
            ringed = sim.stats(sim.arrival(params, break_mask=ring))
            hit0, hit1 = base["homes_hit"], ringed["homes_hit"]
            drop = 1.0 - (hit1 / hit0) if hit0 else 0.0
            attempts.append({"scale": r["scale"], "k_w": r["k_w"],
                             "structure_rate": r["structure_rate"], "break_mult": bm,
                             "homes_hit": hit0, "ringed_homes_hit": hit1,
                             "drop_frac": round(drop, 4)})
            print(f"  gate try scale={r['scale']:.0f} k_w={r['k_w']} "
                  f"str={r['structure_rate']} bm={bm}: {hit0} -> {hit1} "
                  f"(drop {drop:.1%})")
            if drop >= GATE_DROP:
                chosen, gate = params, attempts[-1]
                break
        if chosen:
            break

    out = common.out_dir(town)
    config = {"town": town,
              "params": chosen,
              "target": {"frac_homes_hit": [HIT_MIN, HIT_MAX]},
              "calibration_table": table,
              "gate": {"ring_cells": int(ring.sum()), "required_drop": GATE_DROP,
                       "attempts": attempts, "passed": chosen is not None,
                       "chosen": gate},
              "ignition_cell": list(sim.ignition),
              "town_center_cell": list(sim.town_center),
              "generated": datetime.datetime.now().isoformat(timespec="seconds")}
    (out / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    print(f"  wrote {out / 'config.json'}")

    if chosen is None:
        sys.exit(f"GATE FAILED for every candidate x break_mult {BREAK_MULTS} - "
                 f"the fire does not respond to breaks, STOP, tell Zach "
                 f"(plan section 5). config.json holds all attempts.")
    print(f"\n  chosen: scale={chosen['scale']:.0f} k_w={chosen['k_w']} "
          f"structure_rate={chosen['structure_rate']} break_mult={chosen['break_mult']}")
    print(f"  GATE PASSED: drop {gate['drop_frac']:.1%} >= {GATE_DROP:.0%}")
    print(f"\nverify: python pipeline/calibrate.py --town {town}  "
          f"(same table, same gate result)")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="calibrate.py",
        description="Sweep scale x k_w x structure_rate, run the ring-break gate, "
                    "write config.json.")
    p.add_argument("--town", default="paradise",
                   help="name of towns/<town>.json (default: paradise)")
    return p


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    common.run_stage("calibrate", args.town, lambda: run(args.town))


if __name__ == "__main__":
    main()
