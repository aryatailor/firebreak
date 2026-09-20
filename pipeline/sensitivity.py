"""sensitivity.py - wind sensitivity of the shipped solutions.

The judge question: "what if the wind was different?" This re-simulates the
town's baseline AND its exact shipped break sets (greedy prefixes from
solve_result.json at the requested budgets) under a grid of wind variants -
direction x speed - holding the calibrated speed/structure params fixed. Homes
saved and evacuation minutes bought are computed against the SAME-variant
baseline, so each row is a fair like-for-like comparison. The breaks are never
re-optimized: this measures how the plan bought for the historical wind holds
up when the weather disobeys the forecast.

Outputs:
- pipeline/out/<town>/sensitivity.md   (full table + worst/best range per budget)
- <web dir>/sensitivity.json           (optional file per CONTRACT.md):
  [{budget, wind_from_deg, wind_mph, homes_saved, minutes_bought}]

Verify: python pipeline/sensitivity.py --town paradise   (deterministic)
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys

import numpy as np

import common
from simulate import Simulator

DIRECTIONS = [30.0, 45.0, 60.0]     # wind FROM, degrees
SPEEDS = [25.0, 35.0, 45.0]         # mph
BUDGETS = [500_000, 1_000_000, 2_000_000]


def budget_masks(town: str, shape, budgets) -> dict:
    """The exact shipped break sets: greedy prefixes of solve_result.json."""
    out = common.out_dir(town)
    solve = json.loads((out / "solve_result.json").read_text(encoding="utf-8"))
    z = np.load(out / "candidates.npz")
    cells = {int(i): (z["flat_r"][s:s + n], z["flat_c"][s:s + n])
             for i, (s, n) in enumerate(zip(z["starts"], z["lengths"]))}
    masks = {}
    for budget in budgets:
        mask = np.zeros(shape, dtype=bool)
        for a in solve["accepted"]:
            if a["cum_cost"] <= budget:
                rr, cc = cells[a["id"]]
                mask[rr, cc] = True
        masks[budget] = mask
    return masks


def run(town: str) -> None:
    cfg = common.load_town(town)
    sim = Simulator(town)
    out = common.out_dir(town)
    params = json.loads((out / "config.json").read_text(encoding="utf-8"))["params"]
    horizon = cfg["horizon_min"]
    masks = budget_masks(town, sim.fuel.shape, BUDGETS)
    cal = cfg["wind"]
    print(f"sensitivity: {town}, calibrated wind {cal['speed_mph']:g} mph from "
          f"{cal['from_deg']:g} deg; variants {DIRECTIONS} deg x {SPEEDS} mph, "
          f"budgets {BUDGETS}")

    records, table_rows = [], []
    for d in DIRECTIONS:
        for v in SPEEDS:
            wind = {"speed_mph": v, "from_deg": d}
            base = sim.arrival(params, wind=wind)
            base_hit = int((base[sim.b_row, sim.b_col] <= horizon).sum())
            base_mtt = float(base[sim.town_center])
            row = {"wind": wind, "baseline_hit": base_hit}
            for budget in BUDGETS:
                arr = sim.arrival(params, break_mask=masks[budget], wind=wind)
                hit = int((arr[sim.b_row, sim.b_col] <= horizon).sum())
                mtt = float(arr[sim.town_center])
                saved = base_hit - hit
                bought = (round(mtt - base_mtt, 1)
                          if np.isfinite(mtt) and np.isfinite(base_mtt) else None)
                records.append({"budget": budget, "wind_from_deg": d,
                                "wind_mph": v, "homes_saved": saved,
                                "minutes_bought": bought})
                row[budget] = {"saved": saved, "bought": bought}
            table_rows.append(row)
            print(f"  {d:>3.0f} deg @ {v:>2.0f} mph: baseline {base_hit:>5} hit | "
                  + " | ".join(f"${b // 1000}k: {row[b]['saved']:>5} saved, "
                               f"+{row[b]['bought']} min" for b in BUDGETS))

    # --- worst/best range per budget --------------------------------------------
    ranges = {}
    for budget in BUDGETS:
        entries = [r for r in records if r["budget"] == budget]
        lo = min(entries, key=lambda r: r["homes_saved"])
        hi = max(entries, key=lambda r: r["homes_saved"])
        ranges[budget] = {"worst": lo, "best": hi}

    # --- sensitivity.md ---------------------------------------------------------
    lines = [f"# Wind sensitivity - {cfg['name']}", "",
             f"Shipped breaks (optimized for {cal['speed_mph']:g} mph from "
             f"{cal['from_deg']:g} deg) re-simulated under 9 wind variants; homes "
             f"saved is against the same-variant baseline. Generated "
             f"{datetime.date.today().isoformat()}.", "",
             "| Wind | Baseline hit | " + " | ".join(
                 f"${b // 1000}k saved | +min" for b in BUDGETS) + " |",
             "|---|---|" + "---|---|" * len(BUDGETS)]
    for row in table_rows:
        w = row["wind"]
        star = " **(calibrated)**" if (w["from_deg"] == cal["from_deg"]
                                       and w["speed_mph"] == cal["speed_mph"]) else ""
        lines.append(f"| {w['from_deg']:g} deg @ {w['speed_mph']:g} mph{star} | "
                     f"{row['baseline_hit']:,} | " + " | ".join(
                         f"{row[b]['saved']:,} | +{row[b]['bought']}"
                         for b in BUDGETS) + " |")
    lines += ["", "## Range per budget", ""]
    for budget in BUDGETS:
        lo, hi = ranges[budget]["worst"], ranges[budget]["best"]
        lines.append(
            f"- **${budget:,}**: worst {lo['homes_saved']:,} saved "
            f"({lo['wind_from_deg']:g} deg @ {lo['wind_mph']:g} mph) - best "
            f"{hi['homes_saved']:,} saved ({hi['wind_from_deg']:g} deg @ "
            f"{hi['wind_mph']:g} mph).")
    md_path = out / "sensitivity.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  wrote {md_path}")

    # --- web export (optional file per CONTRACT.md) -----------------------------
    web = common.web_dir(cfg)
    (web / "sensitivity.json").write_text(json.dumps(records, separators=(",", ":")),
                                          encoding="utf-8")
    total = sum(f.stat().st_size for f in web.iterdir()) / 1e6
    if total > 20.0:
        sys.exit(f"{web} is {total:.2f} MB > 20 MB after sensitivity.json - STOP")
    print(f"  wrote {web / 'sensitivity.json'} ({web.name} total {total:.2f} MB)")
    print(f"\nverify: python pipeline/sensitivity.py --town {town}  (same table)")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sensitivity.py",
        description="Re-simulate shipped solutions under wind variants.")
    p.add_argument("--town", default="paradise",
                   help="name of towns/<town>.json (default: paradise)")
    return p


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    common.run_stage("sensitivity", args.town, lambda: run(args.town))


if __name__ == "__main__":
    main()
