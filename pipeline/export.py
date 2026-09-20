"""export.py - write web/data/ exactly per CONTRACT.md (plan section 9).

- Files: meta.json, baseline.json, solutions.json, curve.json, buildings.geojson,
  basemap.png, fuel_legend.json. Total ENFORCED <= 5 MB; first lever if over is
  4-decimal building coordinates - features are never silently dropped.
- Grids: uint16 little-endian base64, 65535 = not reached within the horizon.
- Solutions: one entry per budget (greedy prefixes from solve_result.json), each
  re-simulated for its exact arrival grid.
- Stats (Zach's addition, 2026-09-19): baseline and every solution carry
  minutes_to_town (first arrival at the town center) and minutes_to_first_home;
  solutions add minutes_bought_town / minutes_bought_first_home = solution minus
  baseline. That is the evacuation-time number for the demo.
- Also writes pipeline/out/<town>/ring_fallback.json - the calibration ring break
  packaged as a shippable solution entry, in case the solver's breaks are ugly.

Verify:
    python pipeline/export.py --town paradise   # prints the per-budget table + sizes
"""
from __future__ import annotations

import argparse
import base64
import datetime
import json
import shutil
import sys

import common
import numpy as np
from calibrate import ring_break_mask
from candidates import cell_costs
from robust import make_pool, scenario_hit
from simulate import Simulator, to_uint16


def b64_grid(arrival: np.ndarray, horizon: int) -> str:
    grid = to_uint16(arrival, horizon)
    return base64.b64encode(grid.astype("<u2").tobytes()).decode("ascii")


def b64_grid_u8(arrival: np.ndarray, horizon: int) -> str:
    """steps.json encoding: uint8, 5-minute buckets, 255 = never within horizon."""
    grid = np.where(np.isfinite(arrival) & (arrival <= horizon),
                    np.round(arrival / 5.0), 255).astype(np.uint8)
    return base64.b64encode(grid.tobytes()).decode("ascii")


def cells_to_geojson_feature(sim: Simulator, rr, cc, props: dict,
                             decimals: int = 5) -> dict:
    """Union of cell squares (in Mercator, planar) -> (Multi)Polygon in 4326."""
    from shapely.geometry import box
    from shapely.ops import unary_union

    g = sim.geom
    cell = g["cell_merc"]
    boxes = [box(g["west_m"] + c * cell, g["north_m"] - (r + 1) * cell,
                 g["west_m"] + (c + 1) * cell, g["north_m"] - r * cell)
             for r, c in zip(rr.tolist(), cc.tolist())]
    merged = unary_union(boxes)

    def ring_to_lonlat(coords):
        return [[round(v, decimals) for v in common.merc_to_lonlat(x, y)]
                for x, y in coords]

    geoms = getattr(merged, "geoms", [merged])
    polys = [[ring_to_lonlat(p.exterior.coords)]
             + [ring_to_lonlat(i.coords) for i in p.interiors] for p in geoms]
    geometry = ({"type": "Polygon", "coordinates": polys[0]} if len(polys) == 1
                else {"type": "MultiPolygon", "coordinates": polys})
    return {"type": "Feature", "geometry": geometry, "properties": props}


def dominant_group(sim: Simulator, rr, cc) -> str:
    codes, counts = np.unique(sim.fuel[rr, cc], return_counts=True)
    code = int(codes[np.argmax(counts)])
    return common.CODE_TO_GROUP.get(code, "mixed")


def solution_entry(sim: Simulator, params: dict, base_stats: dict, mask, breaks_fc,
                   budget, cost: float, horizon: int) -> dict:
    arr = sim.arrival(params, break_mask=mask)
    s = sim.stats(arr)
    hit_ids = np.nonzero(arr[sim.b_row, sim.b_col] <= horizon)[0]
    saved = base_stats["buildings_hit"] - int(hit_ids.size)

    def bought(key):
        a, b = s[key], base_stats[key]
        return round(a - b, 1) if a is not None and b is not None else None

    return {
        "budget": budget, "cost": round(cost),
        "breaks": breaks_fc,
        "arrival_min_b64": b64_grid(arr, horizon),
        "buildings_hit": hit_ids.tolist(),
        "stats": {
            "buildings_hit": int(hit_ids.size),
            "houses_saved": saved,
            "cost_per_house_saved": round(cost / saved) if saved > 0 else None,
            "minutes_to_town": s["minutes_to_town_center"],
            "minutes_to_first_home": s["minutes_to_first_home"],
            "minutes_bought_town": bought("minutes_to_town_center"),
            "minutes_bought_first_home": bought("minutes_to_first_home"),
        },
    }


def prefix_step_entry(sim: Simulator, base_stats: dict, step: int,
                      break_ids: list[int], cum_cost: float, cum_saved,
                      arr: np.ndarray, horizon: int,
                      include_arrival: bool = True) -> dict:
    s = sim.stats(arr)
    bought = (round(s["minutes_to_town_center"] - base_stats["minutes_to_town"], 1)
              if s["minutes_to_town_center"] is not None
              and base_stats["minutes_to_town"] is not None else None)
    entry = {
        "step": step,
        "break_ids": break_ids,
        "cumulative_cost": round(cum_cost),
        "cumulative_saved": cum_saved,
        "minutes_to_first_home": s["minutes_to_first_home"],
        "minutes_to_town": s["minutes_to_town_center"],
        "minutes_bought": 0 if step == 0 else bought,
    }
    if include_arrival:
        entry["arrival_b64"] = b64_grid_u8(arr, horizon)
    return entry


def build_prefix_plan(sim: Simulator, params: dict, base_stats: dict,
                      base_arr: np.ndarray, accepted: list[dict],
                      cand_cells: dict[int, tuple[np.ndarray, np.ndarray]],
                      horizon: int, include_arrival: bool = True):
    steps = [prefix_step_entry(
        sim, base_stats, 0, [], 0.0, 0, base_arr, horizon, include_arrival
    )]
    break_features = []
    prefix_masks = [(0.0, np.zeros(sim.fuel.shape, dtype=bool))]
    mask = np.zeros(sim.fuel.shape, dtype=bool)
    for step, item in enumerate(accepted, start=1):
        rr, cc = cand_cells[item["id"]]
        mask[rr, cc] = True
        arr = sim.arrival(params, break_mask=mask)
        steps.append(prefix_step_entry(
            sim, base_stats, step, [item["id"]], item["cum_cost"],
            item["cum_saved"], arr, horizon, include_arrival
        ))
        properties = {
            "id": item["id"], "step": step, "cells": item["cells"],
            "cost": round(item["cost"]),
            "fuel_group": dominant_group(sim, rr, cc),
        }
        if not include_arrival:
            properties["cell_idx"] = sorted((rr * sim.cols + cc).tolist())
            properties["cells"] = len(properties["cell_idx"])
        break_features.append(cells_to_geojson_feature(
            sim, rr, cc, properties
        ))
        prefix_masks.append((float(item["cum_cost"]), mask.copy()))
    return steps, {"type": "FeatureCollection", "features": break_features}, prefix_masks


def score_prefixes(town: str, params: dict, scenarios: list[dict],
                   prefix_masks: list[tuple[float, np.ndarray]]) -> list[dict]:
    baseline_hits = np.asarray(
        [scenario["baseline_homes_hit"] for scenario in scenarios], dtype=float
    )
    weights = np.asarray([scenario["weight"] for scenario in scenarios])
    scored = []
    with make_pool(town, params, scenarios) as pool:
        for step, (cum_cost, prefix_mask) in enumerate(prefix_masks):
            hit_pairs = pool.map(
                scenario_hit,
                [(scenario_id, prefix_mask)
                 for scenario_id in range(len(scenarios))],
            )
            hits = np.zeros(len(scenarios), dtype=float)
            for scenario_id, hit in hit_pairs:
                hits[scenario_id] = hit
            saved = baseline_hits - hits
            scored.append(
                {
                    "step": step,
                    "cum_cost": round(cum_cost),
                    "mean_saved": round(float(np.dot(weights, saved)), 1),
                    "min_saved": int(saved.min()),
                    "max_saved": int(saved.max()),
                    "per_scenario_saved": [int(value) for value in saved],
                }
            )
    return scored


def _load_prefix_inputs(town: str):
    cfg = common.load_town(town)
    out = common.out_dir(town)
    sim = Simulator(town)
    horizon = cfg["horizon_min"]
    config = json.loads((out / "config.json").read_text(encoding="utf-8"))
    params = config["params"]
    gm = json.loads((out / "grid_meta.json").read_text(encoding="utf-8"))
    solve = json.loads((out / "solve_result.json").read_text(encoding="utf-8"))

    z = np.load(out / "candidates.npz")
    cand_cells = {int(i): (z["flat_r"][s:s + n], z["flat_c"][s:s + n])
                  for i, (s, n) in enumerate(zip(z["starts"], z["lengths"]))}
    base_arr = sim.arrival(params)
    bs = sim.stats(base_arr)
    base_stats = {
        "buildings_total": bs["homes_total"],
        "buildings_hit": int(np.count_nonzero(
            base_arr[sim.b_row, sim.b_col] <= horizon
        )),
        "minutes_to_town_center": bs["minutes_to_town_center"],
        "minutes_to_town": bs["minutes_to_town_center"],
        "minutes_to_first_home": bs["minutes_to_first_home"],
    }
    return (cfg, out, sim, horizon, config, params, gm, solve, cand_cells,
            base_arr, base_stats)


def run_historical_plan_only(town: str) -> None:
    (cfg, out, sim, horizon, _config, params, _gm, _solve, cand_cells,
     base_arr, base_stats) = _load_prefix_inputs(town)
    single_path = out / "solve_result_single.json"
    if not single_path.exists():
        raise FileNotFoundError(f"missing {single_path}")
    single = json.loads(single_path.read_text(encoding="utf-8"))
    historical_steps, historical_breaks, _ = build_prefix_plan(
        sim, params, base_stats, base_arr, single["accepted"], cand_cells,
        horizon, include_arrival=False
    )
    web = common.web_dir(cfg)
    plan_historical = {"steps": historical_steps, "breaks": historical_breaks}
    (web / "plan_historical.json").write_text(
        json.dumps(plan_historical, separators=(",", ":")), encoding="utf-8"
    )
    total = sum(f.stat().st_size for f in web.iterdir()) / 1e6
    size = (web / "plan_historical.json").stat().st_size / 1e6
    print(f"  plan_historical.json: {size:.3f} MB")
    if total > common.SIZE_CAP_MB:
        raise SystemExit(
            f"{web} is {total:.2f} MB > {common.SIZE_CAP_MB:.0f} MB"
        )
    print(f"historical plan OK: {web} total = {total:.2f} MB <= "
          f"{common.SIZE_CAP_MB:.0f} MB")


def run(town: str) -> None:
    (cfg, out, sim, horizon, config, params, gm, solve, cand_cells,
     base_arr, base_stats) = _load_prefix_inputs(town)

    web = common.web_dir(cfg)
    print(f"  export target: {web}")

    # --- baseline ---------------------------------------------------------------
    base_hit_ids = np.nonzero(base_arr[sim.b_row, sim.b_col] <= horizon)[0]
    baseline = {"arrival_min_b64": b64_grid(base_arr, horizon),
                "buildings_hit": base_hit_ids.tolist(), "stats": base_stats}

    # --- solutions: greedy prefixes per budget ----------------------------------
    accepted = solve["accepted"]
    solutions = []
    for budget in cfg["budgets"]:
        prefix = [a for a in accepted if a["cum_cost"] <= budget]
        mask = np.zeros(sim.fuel.shape, dtype=bool)
        features = []
        for a in prefix:
            rr, cc = cand_cells[a["id"]]
            mask[rr, cc] = True
            features.append(cells_to_geojson_feature(
                sim, rr, cc,
                {"id": a["id"], "cells": a["cells"], "cost": round(a["cost"]),
                 "fuel_group": dominant_group(sim, rr, cc)}))
        fc = {"type": "FeatureCollection", "features": features}
        cost = prefix[-1]["cum_cost"] if prefix else 0.0
        solutions.append(solution_entry(sim, params, base_stats, mask if prefix else None,
                                        fc, budget, cost, horizon))
        s = solutions[-1]["stats"]
        print(f"  budget ${budget:>9,}: cost ${solutions[-1]['cost']:>9,} "
              f"{len(prefix):>3} breaks, saved {s['houses_saved']:>5}, "
              f"bought {s['minutes_bought_town']} min (town)")

    # --- steps.json + breaks.geojson (continuous budget slider) -----------------
    steps, breaks_geojson, _prefix_masks = build_prefix_plan(
        sim, params, base_stats, base_arr, accepted, cand_cells, horizon
    )
    print(f"  steps.json: {len(steps)} entries (step 0 = baseline + "
          f"{len(accepted)} greedy steps)")

    robust_data = None
    if solve.get("objective") == "ensemble":
        ensemble_path = out / "ensemble.json"
        ensemble_data = json.loads(ensemble_path.read_text(encoding="utf-8"))
        scenarios = ensemble_data["scenarios"]
        robust_steps = score_prefixes(town, params, scenarios, _prefix_masks)
        robust_data = {
            "scenarios": scenarios,
            "steps": robust_steps,
        }
        single_path = out / "solve_result_single.json"
        if single_path.exists():
            single = json.loads(single_path.read_text(encoding="utf-8"))
            historical_steps, historical_breaks, historical_prefix_masks = (
                build_prefix_plan(
                    sim, params, base_stats, base_arr, single["accepted"],
                    cand_cells, horizon, include_arrival=False
                )
            )
            robust_data["historical_plan"] = {
                "steps": score_prefixes(
                    town, params, scenarios, historical_prefix_masks
                )
            }
            plan_historical = {
                "steps": historical_steps,
                "breaks": historical_breaks,
            }
        else:
            plan_historical = None
        print(f"  robust.json: {len(robust_steps)} prefix steps x "
              f"{len(scenarios)} scenarios")
    else:
        plan_historical = None

    # --- ring fallback (Zach: ship this if the solver's breaks are ugly) --------
    ring = ring_break_mask(sim)
    rr, cc = np.nonzero(ring)
    ring_cost = float(cell_costs(sim.fuel)[rr, cc].sum())
    ring_fc = {"type": "FeatureCollection",
               "features": [cells_to_geojson_feature(
                   sim, rr, cc, {"id": "ring", "cells": int(rr.size),
                                 "cost": round(ring_cost),
                                 "fuel_group": dominant_group(sim, rr, cc)})]}
    ring_entry = solution_entry(sim, params, base_stats, ring, ring_fc,
                                None, ring_cost, horizon)
    ring_entry["note"] = ("calibration ring fallback - swap into solutions.json "
                          "if the solver output is ugly; cost exceeds all budgets")
    (out / "ring_fallback.json").write_text(json.dumps(ring_entry),
                                            encoding="utf-8")
    print(f"  ring fallback: cost ${ring_cost:,.0f}, saved "
          f"{ring_entry['stats']['houses_saved']} -> {out / 'ring_fallback.json'}")

    # --- meta -------------------------------------------------------------------
    ign_r, ign_c = config["ignition_cell"]
    meta = {
        "town": cfg["name"], "story": cfg["story"],
        "bounds": {k: round(v, 6) for k, v in gm["bounds"].items()},
        "grid": {"rows": gm["rows"], "cols": gm["cols"], "cell_m": gm["cell_m"]},
        "wind": cfg["wind"],
        "ignition": {"row": ign_r, "col": ign_c,
                     "lat": cfg["ignition"]["lat"], "lon": cfg["ignition"]["lon"],
                     "label": cfg["ignition"]["label"]},
        "horizon_min": horizon,
        "budgets": cfg["budgets"],
        "cost_per_acre": {"grass": 500, "shrub": 1500, "timber": 2500},
        "data": {"fuel": gm["fuel"]["product"], "terrain": gm["terrain"],
                 "buildings": gm["buildings"]["source"],
                 "buildings_proxy": bool(gm["buildings"]["proxy"]),
                 "fetched": gm["fetched"]},
        "simplifications": [
            gm["buildings"].get("simplification",
                                "Homes are estimated from developed-land cells."),
            ("Fire spread is a calibrated graph travel-time model (Dijkstra), "
             "not fire physics - one free speed parameter fit to the historical "
             "outcome."),
            (f"One uniform historical wind ({cfg['wind']['speed_mph']:g} mph from "
             f"{cfg['wind']['from_deg']:g} degrees) for all "
             f"{horizon // 60} hours; no ember spotting, no weather change."),
            (f"Fuel breaks slow fire {round(1 / params['break_mult'])}x rather than "
             "stopping it; costs are rough mechanical-treatment magnitudes, not bids."),
            (f"Fuels are {gm['fuel']['product']}, terrain {gm['terrain']}, both "
             f"resampled to a {gm['cell_m']:g} m grid."),
        ],
    }

    # --- write + size gate --------------------------------------------------------
    def write_all(decimals: int) -> float:
        features = [{"type": "Feature",
                     "geometry": {"type": "Point",
                                  "coordinates": [round(float(lo), decimals),
                                                  round(float(la), decimals)]},
                     "properties": {"id": i, "row": int(r), "col": int(c)}}
                    for i, (la, lo, r, c) in enumerate(
                        zip(sim.b_lat, sim.b_lon, sim.b_row, sim.b_col))]
        files = {
            "meta.json": meta, "baseline.json": baseline,
            "solutions.json": solutions,
            "curve.json": {"points": solve["curve"]},
            "steps.json": steps,
            "breaks.geojson": breaks_geojson,
            "buildings.geojson": {"type": "FeatureCollection", "features": features},
            "fuel_legend.json": [{"group": g, "color": c}
                                 for g, c in common.GROUP_COLORS.items()],
        }
        if robust_data is not None:
            files["robust.json"] = robust_data
        if plan_historical is not None:
            files["plan_historical.json"] = plan_historical
        for name, obj in files.items():
            (web / name).write_text(json.dumps(obj, separators=(",", ":")),
                                    encoding="utf-8")
        shutil.copyfile(out / "basemap.png", web / "basemap.png")
        return sum(f.stat().st_size for f in web.iterdir()) / 1e6

    SIZE_CAP = common.SIZE_CAP_MB
    total = write_all(5)
    if total > SIZE_CAP:
        print(f"  {total:.2f} MB > {SIZE_CAP:.0f} MB - quantizing building coords "
              f"to 4 decimals")
        total = write_all(4)
    if total > SIZE_CAP:
        sys.exit(f"web/data is {total:.2f} MB > {SIZE_CAP:.0f} MB even after "
                 f"quantization - STOP (never silently drop features)")

    sizes = {f.name: round(f.stat().st_size / 1e6, 3) for f in sorted(web.iterdir())}
    print(f"  web/data sizes (MB): {json.dumps(sizes)}")
    for name in ("robust.json", "plan_historical.json"):
        if name in sizes:
            print(f"  {name}: {sizes[name]:.3f} MB")
    print(f"export OK: web/data = {total:.2f} MB <= {SIZE_CAP:.0f} MB, "
          f"{len(steps)} slider steps "
          f"({datetime.datetime.now(datetime.timezone.utc).date().isoformat()})")
    print("\nverify: python -m http.server -d web 8000, flip DATA_DIR to 'data' "
          "(frontend session does this)")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="export.py",
        description="Write web/data/ per CONTRACT.md; enforce the 5 MB budget.")
    p.add_argument("--town", default="paradise",
                   help="name of towns/<town>.json (default: paradise)")
    p.add_argument("--historical-plan-only", action="store_true",
                   help="rewrite only plan_historical.json from cached results")
    return p


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    fn = (run_historical_plan_only if args.historical_plan_only else run)
    common.run_stage("export", args.town, lambda: fn(args.town))


if __name__ == "__main__":
    main()
