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

import common
import numpy as np
from robust import candidate_saved, make_pool, scenario_hit
from simulate import Simulator
from tqdm import tqdm


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


def run(town: str, budget_cap: float | None = None,
        robust: bool = False) -> None:
    if robust:
        return run_robust(town, budget_cap)
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
        _neg_eff, i = heapq.heappop(heap)
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
              "generated": datetime.datetime.now().isoformat(  # noqa: DTZ005
                  timespec="seconds")}
    (out / "solve_result.json").write_text(json.dumps(result, indent=2),
                                           encoding="utf-8")
    solver_check_png(town, sim, [cells[a["id"]] for a in accepted])
    total_saved = accepted[-1]["cum_saved"]
    print(f"solve OK: {len(accepted)} breaks, ${cum_cost:,.0f} spent, "
          f"{total_saved} homes saved ({total_saved / base_hit:.1%} of baseline hit)")
    print(f"solve OK -> {out / 'solve_result.json'}")
    print(f"\nverify: open pipeline/out/{town}/solver_check.png - green breaks "
          f"should sit windward of town, not scattered in the void")


def _load_ensemble(town: str) -> tuple[dict, list[dict]]:
    path = common.out_dir(town) / "ensemble.json"
    if not path.exists():
        sys.exit(f"{path} missing - run ensemble first: "
                 f"python pipeline/ensemble.py --town {town}")
    data = json.loads(path.read_text(encoding="utf-8"))
    scenarios = data.get("scenarios", [])
    if not scenarios:
        sys.exit(f"{path} contains no scenarios")
    return data, scenarios


def _preserve_single_result(out) -> None:
    result_path = out / "solve_result.json"
    single_path = out / "solve_result_single.json"
    if not result_path.exists() or single_path.exists():
        return
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        result = {}
    if result.get("objective") != "ensemble":
        result_path.replace(single_path)


def _ensure_single_result(town: str, budget_cap: float | None) -> None:
    out = common.out_dir(town)
    single_path = out / "solve_result_single.json"
    if single_path.exists():
        return
    result_path = out / "solve_result.json"
    if not result_path.exists():
        run(town, budget_cap, robust=False)
    else:
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            result = {}
        if result.get("objective") == "ensemble":
            run(town, budget_cap, robust=False)
    _preserve_single_result(out)


def run_robust(town: str, budget_cap: float | None = None) -> None:
    _ensure_single_result(town, budget_cap)
    sim = Simulator(town)
    out = common.out_dir(town)
    _preserve_single_result(out)
    params = json.loads((out / "config.json").read_text(encoding="utf-8"))["params"]
    ensemble, scenarios = _load_ensemble(town)
    cells, cost, kinds = load_candidates(town)
    max_budget = budget_cap if budget_cap else max(sim.cfg["budgets"])
    shape = sim.fuel.shape
    flat_cells = [
        (rr.astype(np.int64) * sim.cols + cc.astype(np.int64)).astype(np.int64)
        for rr, cc in cells
    ]
    baseline = [int(s["baseline_homes_hit"]) for s in scenarios]
    weights = np.asarray([float(s["weight"]) for s in scenarios])
    base_mean = float(np.dot(weights, baseline))
    print(
        f"solve robust: {town}, {len(cells)} candidates, max budget "
        f"${max_budget:,.0f}, {len(scenarios)} scenarios, "
        f"baseline mean {base_mean:.1f}"
    )

    heap = []
    with make_pool(town, params, scenarios, flat_cells) as pool:
        bar = tqdm(total=len(cells), desc="  robust CELF init", unit="cand")
        for i, saved in pool.imap_unordered(candidate_saved, range(len(cells)),
                                             chunksize=1):
            if saved > 0:
                heapq.heappush(heap, (-saved / cost[i], i))
            bar.update(1)
        bar.close()
        print(f"  {len(heap)} candidates with positive robust solo marginal")

        accepted, curve = [], []
        mask = np.zeros(shape, dtype=bool)
        current_hits = np.asarray(baseline, dtype=float)
        cum_cost = 0.0
        bar = tqdm(desc="  robust CELF greedy", unit="eval")
        while heap:
            _neg_eff, i = heapq.heappop(heap)
            if cum_cost + cost[i] > max_budget:
                continue
            m2 = mask.copy()
            m2[cells[i][0], cells[i][1]] = True
            hit_pairs = pool.map(
                scenario_hit,
                [(scenario_id, m2) for scenario_id in range(len(scenarios))],
            )
            new_hits = np.zeros(len(scenarios), dtype=float)
            for scenario_id, hit in hit_pairs:
                new_hits[scenario_id] = hit
            saved = float(np.dot(weights, current_hits - new_hits))
            bar.update(1)
            if saved <= 0:
                continue
            eff = saved / cost[i]
            if heap and eff < -heap[0][0]:
                heapq.heappush(heap, (-eff, i))
                continue
            mask = m2
            cum_cost += float(cost[i])
            current_hits = new_hits
            cumulative = base_mean - float(np.dot(weights, current_hits))
            accepted.append(
                {
                    "id": int(i),
                    "kind": "strip" if kinds[i] == 0 else "arc",
                    "cells": int(cells[i][0].size),
                    "cost": float(cost[i]),
                    "marginal_saved": round(saved, 1),
                    "cum_cost": cum_cost,
                    "cum_saved": round(cumulative, 1),
                }
            )
            curve.append(
                {
                    "step": len(accepted),
                    "break_id": int(i),
                    "cumulative_cost": cum_cost,
                    "cumulative_saved": round(cumulative, 1),
                }
            )
            bar.set_postfix(spent=f"${cum_cost:,.0f}", saved=f"{cumulative:.1f}")
            (out / "solve_partial.json").write_text(
                json.dumps({"accepted": accepted, "curve": curve}),
                encoding="utf-8",
            )
        bar.close()

    if not accepted:
        sys.exit("robust solver accepted zero breaks - no positive marginal "
                 "within budget")

    historical_base = sim.stats(sim.arrival(params))["homes_hit"]
    historical_saved = []
    historical_mask = np.zeros(shape, dtype=bool)
    for item in accepted:
        rr, cc = cells[item["id"]]
        historical_mask[rr, cc] = True
        hit = sim.stats(sim.arrival(params, break_mask=historical_mask))["homes_hit"]
        historical_saved.append(int(historical_base - hit))

    per_scenario_final = [
        {
            "baseline_hit": int(baseline[i]),
            "hit_with_all_breaks": int(current_hits[i]),
        }
        for i in range(len(scenarios))
    ]
    result = {
        "town": town,
        "max_budget": max_budget,
        "baseline_homes_hit": round(base_mean, 1),
        "accepted": accepted,
        "curve": curve,
        "generated": datetime.datetime.now(
            datetime.timezone.utc).isoformat(timespec="seconds"),
        "objective": "ensemble",
        "ensemble": {
            "n_scenarios": len(scenarios),
            "weights": [float(s["weight"]) for s in scenarios],
            "winds": ensemble.get("winds", []),
            "ignitions": ensemble.get("ignitions", []),
        },
        "per_scenario_final": per_scenario_final,
        "historical": {
            "baseline_hit": int(historical_base),
            "cum_saved": historical_saved,
        },
    }
    (out / "solve_result.json").write_text(json.dumps(result, indent=2),
                                           encoding="utf-8")
    solver_check_png(town, sim, [cells[a["id"]] for a in accepted])
    total_saved = accepted[-1]["cum_saved"]
    print(
        f"solve robust OK: {len(accepted)} breaks, ${cum_cost:,.0f} spent, "
        f"{total_saved:.1f} mean homes saved"
    )
    print(f"solve OK -> {out / 'solve_result.json'}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="solve.py",
        description="CELF lazy-greedy fuel-break selection over the candidate set.")
    p.add_argument("--town", default="paradise",
                   help="name of towns/<town>.json (default: paradise)")
    p.add_argument("--budget", type=float, default=None, metavar="N",
                   help="cap the run at one budget (default: the town's max)")
    p.add_argument("--robust", action="store_true",
                   help="optimize the deterministic ensemble in ensemble.json")
    return p


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    common.run_stage("solve", args.town,
                     lambda: run(args.town, args.budget, args.robust))


if __name__ == "__main__":
    main()
