"""simulate.py - fire arrival times over the whole grid (plan section 4).

- Directed 8-neighbor graph (wind/slope are asymmetric), built vectorized as 8
  shifted edge-time planes -> scipy.sparse CSR -> csgraph.dijkstra from the
  ignition cell. Budget: < 0.5 s per evaluation on the full grid.
- Ignition comes from the town config, snapped to the nearest passable cell (the
  exact origin may rasterize onto barren canyon or water).
- Breaks are a boolean cell mask: cleared cells get base rate x break_mult (0.05),
  20x slower not zero - breaks delay rather than hard-block.
- Arrival minutes -> uint16 for export: 65535 = never reached within the horizon.

CLI writes pipeline/out/<town>/arrival_baseline.npy using the calibrated params
from config.json (falls back to ros.DEFAULT_PARAMS with a warning).

Verify:
    python pipeline/simulate.py --town paradise    # prints stats + sim seconds
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra

import common
import ros


class Simulator:
    """Loads a town's grid once; arrival() is the per-evaluation hot path
    (calibrate sweeps it, solve.py will call it hundreds of times)."""

    def __init__(self, town: str):
        self.town = town
        self.cfg = common.load_town(town)
        out = common.out_dir(town)
        gm_path = out / "grid_meta.json"
        if not gm_path.exists():
            sys.exit(f"{gm_path} missing - run build_grid first: "
                     f"python pipeline/build_grid.py --town {town}")
        self.gm = json.loads(gm_path.read_text(encoding="utf-8"))
        self.rows, self.cols = self.gm["rows"], self.gm["cols"]
        self.cell_m = self.gm["cell_m"]
        self.fuel = np.load(out / "fuel.npy")
        self.elev = np.load(out / "elev.npy")
        b = np.load(out / "buildings.npz")
        self.b_row, self.b_col = b["row"], b["col"]
        self.b_lat, self.b_lon = b["lat"], b["lon"]
        self.geom = {"west_m": self.gm["bounds_3857"]["west_m"],
                     "north_m": self.gm["bounds_3857"]["north_m"],
                     "cell_merc": self.gm["cell_merc"]}
        self.slope = ros.slope_factors(self.elev, self.cell_m)
        self._idx = np.arange(self.rows * self.cols).reshape(self.rows, self.cols)
        self.ignition = self._snap_to_passable(self.cfg["ignition"])
        self.town_center = self._snap_to_passable(self.cfg["town_center"])
        self.last_sim_s = 0.0

    def _snap_to_passable(self, pt: dict) -> tuple[int, int]:
        rate = ros.base_rate(self.fuel, ros.DEFAULT_PARAMS)
        r, c = common.lonlat_to_rowcol(self.geom, pt["lon"], pt["lat"])
        r = min(max(r, 0), self.rows - 1)
        c = min(max(c, 0), self.cols - 1)
        if rate[r, c] > 0:
            return r, c
        rr, cc = np.nonzero(rate > 0)
        k = int(np.argmin((rr - r) ** 2 + (cc - c) ** 2))
        print(f"  snapped ({pt.get('label', 'point')}) from cell {r},{c} to "
              f"nearest passable {rr[k]},{cc[k]}")
        return int(rr[k]), int(cc[k])

    def arrival(self, params: dict, break_mask: np.ndarray | None = None) -> np.ndarray:
        """Minutes from ignition to every cell (float, inf = unreachable)."""
        t0 = time.perf_counter()
        rate = ros.base_rate(self.fuel, params)
        if break_mask is not None:
            rate = rate * np.where(break_mask, params["break_mult"], 1.0).astype(np.float32)
        wf = ros.wind_factors(self.cfg["wind"], params["k_w"])
        planes = ros.edge_times(rate, self.slope, wf, self.cell_m, params["scale"])

        i_parts, j_parts, t_parts = [], [], []
        for k, (dr, dc) in enumerate(ros.OFFSETS):
            t = planes[k]
            valid = np.isfinite(t)
            i_parts.append(self._idx[valid])
            j_parts.append(self._idx[valid] + dr * self.cols + dc)
            t_parts.append(t[valid])
        n = self.rows * self.cols
        g = csr_matrix((np.concatenate(t_parts),
                        (np.concatenate(i_parts), np.concatenate(j_parts))),
                       shape=(n, n))
        src = self.ignition[0] * self.cols + self.ignition[1]
        dist = dijkstra(g, directed=True, indices=src)
        self.last_sim_s = time.perf_counter() - t0
        return dist.reshape(self.rows, self.cols)

    def stats(self, arrival: np.ndarray) -> dict:
        horizon = self.cfg["horizon_min"]
        at_homes = arrival[self.b_row, self.b_col]
        hit = at_homes <= horizon
        mtt = float(arrival[self.town_center])
        first = float(at_homes.min()) if np.isfinite(at_homes).any() else math.inf
        return {
            "homes_total": int(self.b_row.size),
            "homes_hit": int(hit.sum()),
            "frac_homes_hit": round(float(hit.mean()), 4),
            "minutes_to_town_center": round(mtt, 1) if np.isfinite(mtt) else None,
            "minutes_to_first_home": round(first, 1) if np.isfinite(first) else None,
            "sim_seconds": round(self.last_sim_s, 3),
        }


def to_uint16(arrival: np.ndarray, horizon_min: int) -> np.ndarray:
    """CONTRACT.md encoding: minutes, 65535 = not reached within the horizon."""
    out = np.where(np.isfinite(arrival) & (arrival <= horizon_min),
                   np.round(arrival), 65535)
    return out.astype(np.uint16)


def load_params(town: str) -> dict:
    cfg_path = common.out_dir(town) / "config.json"
    if cfg_path.exists():
        params = json.loads(cfg_path.read_text(encoding="utf-8"))["params"]
        print(f"  params from {cfg_path}: {params}")
        return params
    print(f"  WARNING: {cfg_path} missing (calibrate not run) - "
          f"using ros.DEFAULT_PARAMS {ros.DEFAULT_PARAMS}")
    return dict(ros.DEFAULT_PARAMS)


def run(town: str) -> None:
    sim = Simulator(town)
    params = load_params(town)
    arr = sim.arrival(params)
    s = sim.stats(arr)
    out = common.out_dir(town)
    np.save(out / "arrival_baseline.npy", arr.astype(np.float32))
    print(f"simulate: baseline arrival for {town}: {json.dumps(s)}")
    if sim.last_sim_s > 0.5:
        common.log_event("simulate", town,
                         f"sim took {sim.last_sim_s:.2f}s > 0.5s budget - "
                         f"downsample lever may be needed for the solver")
        print(f"  WARNING: {sim.last_sim_s:.2f}s > 0.5s per-evaluation budget")
    print(f"simulate OK -> {out / 'arrival_baseline.npy'}")
    print(f"\nverify: python pipeline/simulate.py --town {town}  "
          f"(same stats, sim_seconds < 0.5)")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="simulate.py",
        description="Compute baseline fire arrival times (dijkstra over the "
                    "directed 8-neighbor graph).")
    p.add_argument("--town", default="paradise",
                   help="name of towns/<town>.json (default: paradise)")
    return p


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    common.run_stage("simulate", args.town, lambda: run(args.town))


if __name__ == "__main__":
    main()
