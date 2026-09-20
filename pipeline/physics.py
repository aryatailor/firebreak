"""physics.py - export the fire model itself, so the browser can run it.

Writes two files into a town's web data dir (both specified in CONTRACT.md):

- physics.json: fuel class + elevation rasters, the calibrated constants, the
  three formulas as strings, homes, ignition. Everything a JS Dijkstra needs.
- parity.json: this Python sim's arrival buckets with no breaks and with one
  hand-placed 2-cell-wide strip, computed FROM THE QUANTISED physics.json values
  (int16 elevation) so an exact reimplementation matches cell-for-cell rather
  than approximately. The frontend diffs against these.

The parity arrays are also checked here against the shipped float-elevation
baseline, and the agreement is printed - if int16 rounding ever moved a
meaningful number of cells, that shows up in this stage's output, not in the demo.

Verify: python pipeline/physics.py --town paradise
"""
from __future__ import annotations

import argparse
import base64
import json

import numpy as np

import common
import ros
from candidates import COST_PER_ACRE, ACRES_PER_CELL
from simulate import Simulator

BUCKET_MIN = 5


def fuel_class_array(fuel: np.ndarray) -> np.ndarray:
    out = np.zeros(fuel.shape, dtype=np.uint8)
    for code, group in common.CODE_TO_GROUP.items():
        out[fuel == code] = common.FUEL_CLASS[group]
    return out


def buckets(arrival: np.ndarray, horizon: int) -> np.ndarray:
    return np.where(np.isfinite(arrival) & (arrival <= horizon),
                    np.round(arrival / BUCKET_MIN), 255).astype(np.uint8)


def b64(a: np.ndarray) -> str:
    return base64.b64encode(a.tobytes()).decode("ascii")


def parity_break_cells(sim: Simulator) -> list[list[int]]:
    """A deterministic 2-cell-wide, 20-cell-long strip across the middle of the
    ignition->town line. Nothing special about it; it just has to be identical
    on both sides of the parity check."""
    mid_r = (sim.ignition[0] + sim.town_center[0]) // 2
    mid_c = (sim.ignition[1] + sim.town_center[1]) // 2
    cells = []
    for dr in (0, 1):
        for dc in range(-10, 10):
            r, c = mid_r + dr, mid_c + dc
            if 0 <= r < sim.rows and 0 <= c < sim.cols:
                cells.append([int(r), int(c)])
    return cells


def run(town: str) -> None:
    cfg = common.load_town(town)
    out = common.out_dir(town)
    web = common.web_dir(cfg)
    sim = Simulator(town)
    gm = json.loads((out / "grid_meta.json").read_text(encoding="utf-8"))
    params = json.loads((out / "config.json").read_text(encoding="utf-8"))["params"]
    horizon = cfg["horizon_min"]

    fuel_class = fuel_class_array(sim.fuel)
    elev_i16 = np.round(sim.elev).astype("<i2")

    rates = {str(common.FUEL_CLASS[g]): r for g, r in ros.GROUP_RATES.items()}
    rates["7"] = params["structure_rate"]

    physics = {
        "grid": {"rows": sim.rows, "cols": sim.cols, "cell_m": gm["cell_m"]},
        "fuel_class_b64": b64(fuel_class),
        "elev_m_b64": b64(elev_i16),
        "rates": {k: rates[k] for k in sorted(rates, key=int)},
        "scale_m_per_min": params["scale"],
        "k_w": params["k_w"],
        "wind": cfg["wind"],
        "slope_formula": "2^(theta_deg/10) clamped to [0.5, 8], theta = uphill "
                         "angle along travel direction",
        "wind_formula": "exp(k_w * (v_mph/10) * cos(delta)), delta = angle "
                        "between travel direction and downwind",
        "edge_weight": "dist_m / (min(base_i, base_j) * f_slope * f_wind * "
                       "scale_m_per_min); dist 60 orthogonal, 60*sqrt(2) diagonal",
        "break_mult": params["break_mult"],
        "horizon_min": horizon,
        "bucket_min": BUCKET_MIN,
        "cost_per_acre": {k: int(v) for k, v in COST_PER_ACRE.items()},
        "cell_acres": ACRES_PER_CELL,
        "homes_rc": [[int(r), int(c)] for r, c in zip(sim.b_row, sim.b_col)],
        "ignition_rc": [int(sim.ignition[0]), int(sim.ignition[1])],
    }
    (web / "physics.json").write_text(json.dumps(physics, separators=(",", ":")),
                                      encoding="utf-8")

    # --- parity: recompute from the EXPORTED (quantised) elevation --------------
    shipped_base = sim.arrival(params)
    sim.elev = elev_i16.astype(np.float32)
    sim.slope = ros.slope_factors(sim.elev, gm["cell_m"])

    base_arr = sim.arrival(params)
    cells = parity_break_cells(sim)
    mask = np.zeros(sim.fuel.shape, dtype=bool)
    for r, c in cells:
        mask[r, c] = True
    break_arr = sim.arrival(params, break_mask=mask)

    parity = {
        "bucket_min": BUCKET_MIN, "horizon_min": horizon,
        "baseline_b64": b64(buckets(base_arr, horizon)),
        "break_cells": cells,
        "break_b64": b64(buckets(break_arr, horizon)),
        "target": "at least 99% of cells within 1 bucket of these arrays",
    }
    (web / "parity.json").write_text(json.dumps(parity, separators=(",", ":")),
                                     encoding="utf-8")

    # how much did int16 elevation actually move things?
    a, b = buckets(shipped_base, horizon).astype(np.int16), \
        buckets(base_arr, horizon).astype(np.int16)
    within1 = float((np.abs(a - b) <= 1).mean())
    exact = float((a == b).mean())
    n_break = int((buckets(break_arr, horizon) != buckets(base_arr, horizon)).sum())
    print(f"physics: {town} {sim.rows}x{sim.cols}, classes present "
          f"{sorted(set(np.unique(fuel_class).tolist()))}, elev "
          f"{int(elev_i16.min())}-{int(elev_i16.max())} m")
    print(f"  quantised vs shipped baseline: {exact:.3%} identical bucket, "
          f"{within1:.3%} within 1")
    print(f"  parity break: {len(cells)} cells, moves {n_break} cells' buckets")
    sizes = {p.name: round(p.stat().st_size / 1e6, 3)
             for p in (web / "physics.json", web / "parity.json")}
    total = sum(f.stat().st_size for f in web.iterdir()) / 1e6
    print(f"  wrote {sizes} into {web.name} (dir total {total:.2f} MB)")
    if total > common.SIZE_CAP_MB:
        raise SystemExit(f"{web} is {total:.2f} MB > {common.SIZE_CAP_MB:.0f} MB "
                         f"cap - STOP")
    print(f"\nverify: python pipeline/physics.py --town {town}  (same numbers)")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="physics.py",
        description="Export physics.json + parity.json so the browser can run "
                    "the fire model itself.")
    p.add_argument("--town", default="paradise",
                   help="name of towns/<town>.json (default: paradise)")
    return p


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    common.run_stage("physics", args.town, lambda: run(args.town))


if __name__ == "__main__":
    main()
