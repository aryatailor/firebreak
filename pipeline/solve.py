"""solve.py - lazy greedy (CELF) fuel-break selection (plan section 7).

- Max-heap of stale upper bounds on marginal homes-saved-per-dollar. Pop, re-run
  one dijkstra with (current breaks + candidate), re-push or accept. The objective
  is not provably submodular; CELF's laziness is a heuristic - fine here.
- Accept while total cost fits the max budget (largest entry in the town's budget
  list) and the marginal is positive. Budget solutions are greedy prefixes
  (CONTRACT.md: bigger budgets contain the smaller budgets' breaks).
- Checkpoint after EVERY accepted break: solve_partial.json - Ctrl-C always
  leaves usable output. tqdm progress bar over heap work.
- Writes solve_result.json (acceptance sequence + curve) and solver_check.png
  (top solution's breaks over the basemap).
- --budget N caps the run at one budget for live use.

Verify:
    python pipeline/solve.py --town paradise    # deterministic given config.json
"""
from __future__ import annotations

import argparse
import datetime
import heapq
import json
import sys

import numpy as np
from tqdm import tqdm

import common
from simulate import Simulator


def load_candidates(town: str):
    out = common.out_dir(town)
    path = out / "candidates.npz"
    if not path.exists():
        sys.exit(f"{path} missing - run candidates first: "
                 f"python pipeline/candidates.py --town {town}")
    z = np.load(path)
    cells = [(z["flat_r"][s:s + n], z["flat_c"][s:s + n])
             for s, n in zip(z["starts"], z["lengths"])]
    return cells, z["cost"], z["kinds"]


def solver_check_png(town: str, sim: Simulator, accepted_cells) -> None:
    from PIL import Image, ImageDraw
    out = common.out_dir(town)
    img = Image.open(out / "basemap.png").convert("RGBA")
    scale = img.width // sim.cols
    draw = ImageDraw.Draw(img)
    for rr, cc in accepted_cells:
        for r, c in zip(rr.tolist(), cc.tolist()):
            draw.rectangle([c * scale, r * scale,
                            (c + 1) * scale - 1, (r + 1) * scale - 1],
                           fill=(61, 220, 132, 255))
    for cell, color in ((sim.ignition, (255, 0, 0)), (sim.town_center, (0, 60, 255))):
        x, y = cell[1] * scale, cell[0] * scale
        draw.ellipse([x - 10, y - 10, x + 10, y + 10], outline=color, width=3)
    img.save(out / "solver_check.png")
    print(f"  wrote {out / 'solver_check.png'}")


def run(town: str, budget_cap: float | None = None) -> None:
    sim = Simulator(town)
    out = common.out_dir(town)
    params = json.loads((out / "config.json").read_text(encoding="utf-8"))["params"]
    cells, cost, kinds = load_candidates(town)
    max_budget = budget_cap if budget_cap else max(sim.cfg["budgets"])
    horizon = sim.cfg["horizon_min"]

    base_arr = sim.arrival(params)
    base_hit = sim.stats(base_arr)["homes_hit"]
    print(f"solve: {town}, {len(cells)} candidates, max budget ${max_budget:,.0f}, "
          f"baseline {base_hit} homes hit, {sim.last_sim_s:.2f}s/eval")

    def homes_hit(mask):
        arr = sim.arrival(params, break_mask=mask)
        return int((arr[sim.b_row, sim.b_col] <= horizon).sum())

    # CELF round 1: every candidate alone. Round 2+: lazy re-evaluation.
    shape = sim.fuel.shape
    heap = []
    bar = tqdm(total=len(cells), desc="  CELF init", unit="cand")
    for i, (rr, cc) in enumerate(cells):
        m = np.zeros(shape, dtype=bool)
        m[rr, cc] = True
        saved = base_hit - homes_hit(m)
        if saved > 0:
            heapq.heappush(heap, (-saved / cost[i], i))
        bar.update(1)
    bar.close()
    print(f"  {len(heap)} candidates with positive solo marginal")

    accepted, curve = [], []
    mask = np.zeros(shape, dtype=bool)
    cum_cost, cur_hit = 0.0, base_hit
    bar = tqdm(desc="  CELF greedy", unit="eval")
    while heap:
        neg_eff, i = heapq.heappop(heap)
        if cum_cost + cost[i] > max_budget:
            continue                                   # can never afford it later
        m2 = mask.copy()
        m2[cells[i][0], cells[i][1]] = True
        saved = cur_hit - homes_hit(m2)
        bar.update(1)
        if saved <= 0:
            continue
        eff = saved / cost[i]
        if heap and eff < -heap[0][0]:                 # stale - someone else looks better
            heapq.heappush(heap, (-eff, i))
            continue
        mask = m2
        cum_cost += float(cost[i])
        cur_hit -= saved
        accepted.append({"id": int(i), "kind": "strip" if kinds[i] == 0 else "arc",
                         "cells": int(cells[i][0].size), "cost": float(cost[i]),
                         "marginal_saved": int(saved),
                         "cum_cost": cum_cost, "cum_saved": int(base_hit - cur_hit)})
        curve.append({"step": len(accepted), "break_id": int(i),
                      "cumulative_cost": cum_cost,
                      "cumulative_saved": int(base_hit - cur_hit)})
        bar.set_postfix(spent=f"${cum_cost:,.0f}", saved=base_hit - cur_hit)
        (out / "solve_partial.json").write_text(
            json.dumps({"accepted": accepted, "curve": curve}), encoding="utf-8")
    bar.close()

    if not accepted:
        sys.exit("solver accepted zero breaks - nothing had positive marginal "
                 "within budget; STOP, the ring fallback is the demo")

    result = {"town": town, "max_budget": max_budget,
              "baseline_homes_hit": base_hit,
              "accepted": accepted, "curve": curve,
              "generated": datetime.datetime.now().isoformat(timespec="seconds")}
    (out / "solve_result.json").write_text(json.dumps(result, indent=2),
                                           encoding="utf-8")
    solver_check_png(town, sim, [cells[a["id"]] for a in accepted])
    total_saved = accepted[-1]["cum_saved"]
    print(f"solve OK: {len(accepted)} breaks, ${cum_cost:,.0f} spent, "
          f"{total_saved} homes saved ({total_saved / base_hit:.1%} of baseline hit)")
    print(f"solve OK -> {out / 'solve_result.json'}")
    print(f"\nverify: open pipeline/out/{town}/solver_check.png - green breaks "
          f"should sit windward of town, not scattered in the void")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="solve.py",
        description="CELF lazy-greedy fuel-break selection over the candidate set.")
    p.add_argument("--town", default="paradise",
                   help="name of towns/<town>.json (default: paradise)")
    p.add_argument("--budget", type=float, default=None, metavar="N",
                   help="cap the run at one budget (default: the town's max)")
    return p


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    common.run_stage("solve", args.town, lambda: run(args.town, args.budget))


if __name__ == "__main__":
    main()
