"""candidates.py - enumerate candidate fuel breaks (plan section 6).

- 2-cell-wide strips: lengths {1.0, 1.5, 2.0 km}, 4 orientations (N-S, E-W,
  NE-SW, NW-SE), centers on a ~480 m lattice.
- Arcs following the urban-cell boundary at offsets ~{250, 600, 1000 m}, split
  into ~1.2 km segments by bearing around the town center.
- Cells: burnable wildland only (never urban/water/agriculture/barren/snow).
  A candidate must keep >= 60% of its nominal footprint after the wildland filter
  and have >= 60% of its kept cells inside the burn corridor (baseline arrival
  <= horizon, dilated ~500 m).
- Cost = sum of cell costs; cell = 0.89 acre; per-acre from the meta cost table:
  grass $500, grass-shrub/shrub $1500, timber-understory/litter/slash $2500.
- Output: pipeline/out/<town>/candidates.npz (ragged cell lists as flat arrays +
  offsets, cost, kind) + a printed census.

Verify:
    python pipeline/candidates.py --town paradise   # counts, costs, corridor stats
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

STRIP_LENGTHS_M = [1000.0, 2000.0]
LATTICE_STEP = 12           # ~720 m between strip centers
ARC_OFFSETS_ITER = [4, 10, 17]   # ~250 / 600 / 1000 m off the urban edge
ARC_SEG_CELLS = 40          # ~1.2 km of 2-wide band
DEFENSE_ITER = 50           # candidates stay within ~3 km of the urban edge -
                            # nearly the whole box burns by the horizon, so the
                            # corridor alone barely prunes (first live run: 19.7k
                            # candidates); breaks defend near town, not mid-canyon
MIN_KEEP_FRAC = 0.60
MIN_CORRIDOR_FRAC = 0.60
MIN_CELLS = 12

COST_KEY = {"grass": "grass", "grass_shrub": "shrub", "shrub": "shrub",
            "timber_understory": "timber", "timber_litter": "timber",
            "slash": "timber"}
COST_PER_ACRE = {"grass": 500.0, "shrub": 1500.0, "timber": 2500.0}
ACRES_PER_CELL = 0.89


def cell_costs(fuel: np.ndarray) -> np.ndarray:
    """$ to clear each cell (0 where not clearable - non-wildland)."""
    cost = np.zeros(fuel.shape, dtype=np.float64)
    for group, key in COST_KEY.items():
        cost[np.isin(fuel, common.FUEL_GROUPS[group])] = \
            COST_PER_ACRE[key] * ACRES_PER_CELL
    return cost


def burn_corridor(town: str, horizon: int) -> np.ndarray:
    arr = np.load(common.out_dir(town) / "arrival_baseline.npy")
    burned = np.isfinite(arr) & (arr <= horizon)
    return binary_dilation(burned, iterations=8)   # ~500 m


def _emit(cells_r, cells_c, wildland, corridor, nominal: int):
    """Apply the wildland + corridor filters; return kept (r, c) or None."""
    keep = wildland[cells_r, cells_c]
    r, c = cells_r[keep], cells_c[keep]
    if r.size < max(MIN_CELLS, MIN_KEEP_FRAC * nominal):
        return None
    if corridor[r, c].mean() < MIN_CORRIDOR_FRAC:
        return None
    return r, c


def strip_candidates(shape, wildland, corridor):
    rows, cols = shape
    out = []
    # (dr, dc, perpendicular thickening)
    dirs = [(0, 1, (1, 0)), (1, 0, (0, 1)), (1, 1, (1, 0)), (1, -1, (1, 0))]
    for r0 in range(0, rows, LATTICE_STEP):
        for c0 in range(0, cols, LATTICE_STEP):
            for dr, dc, (pr, pc) in dirs:
                step_m = 60.0 * (2 ** 0.5 if dr and dc else 1.0)
                for length_m in STRIP_LENGTHS_M:
                    n = max(2, round(length_m / step_m))
                    i = np.arange(n) - n // 2
                    rr = r0 + i * dr
                    cc = c0 + i * dc
                    rr = np.concatenate([rr, rr + pr])
                    cc = np.concatenate([cc, cc + pc])
                    ok = (rr >= 0) & (rr < rows) & (cc >= 0) & (cc < cols)
                    kept = _emit(rr[ok], cc[ok], wildland, corridor, 2 * n)
                    if kept:
                        out.append(("strip", kept[0], kept[1]))
    return out


def arc_candidates(shape, urban, wildland, corridor, town_center):
    out = []
    tc_r, tc_c = town_center
    for it in ARC_OFFSETS_ITER:
        band = binary_dilation(urban, iterations=it + 2) \
            & ~binary_dilation(urban, iterations=it) & wildland
        rr, cc = np.nonzero(band)
        if rr.size == 0:
            continue
        bearing = np.degrees(np.arctan2(cc - tc_c, tc_r - rr)) % 360.0
        order = np.argsort(bearing)
        rr, cc = rr[order], cc[order]
        n_seg = max(1, rr.size // ARC_SEG_CELLS)
        for seg in np.array_split(np.arange(rr.size), n_seg):
            kept = _emit(rr[seg], cc[seg], wildland, corridor, seg.size)
            if kept:
                out.append(("arc", kept[0], kept[1]))
    return out


def run(town: str) -> None:
    cfg = common.load_town(town)
    out = common.out_dir(town)
    fuel = np.load(out / "fuel.npy")
    if not (out / "arrival_baseline.npy").exists():
        sys.exit(f"arrival_baseline.npy missing - run simulate first: "
                 f"python pipeline/simulate.py --town {town}")

    rate = ros.base_rate(fuel, ros.DEFAULT_PARAMS)
    urban = fuel == 91
    wildland = (rate > 0) & ~urban
    corridor = burn_corridor(town, cfg["horizon_min"]) \
        & binary_dilation(urban, iterations=DEFENSE_ITER)
    costs = cell_costs(fuel)

    cj = json.loads((out / "config.json").read_text(encoding="utf-8"))
    tc = tuple(cj["town_center_cell"])

    cands = strip_candidates(fuel.shape, wildland, corridor)
    n_strips = len(cands)
    cands += arc_candidates(fuel.shape, urban, wildland, corridor, tc)
    if not cands:
        sys.exit("zero candidates survived the filters - corridor/wildland masks "
                 "look wrong")

    flat_r = np.concatenate([r for _, r, c in cands]).astype(np.int32)
    flat_c = np.concatenate([c for _, r, c in cands]).astype(np.int32)
    lengths = np.array([r.size for _, r, c in cands], dtype=np.int32)
    starts = np.concatenate([[0], np.cumsum(lengths)[:-1]]).astype(np.int64)
    kinds = np.array([0 if k == "strip" else 1 for k, _, _ in cands], dtype=np.int8)
    cost = np.array([float(costs[r, c].sum()) for _, r, c in cands])

    np.savez_compressed(out / "candidates.npz", flat_r=flat_r, flat_c=flat_c,
                        starts=starts, lengths=lengths, kinds=kinds, cost=cost)
    info = {"count": len(cands), "strips": n_strips, "arcs": len(cands) - n_strips,
            "cells_median": int(np.median(lengths)),
            "cost_min": round(float(cost.min())), "cost_median": round(float(np.median(cost))),
            "cost_max": round(float(cost.max())),
            "corridor_cells": int(corridor.sum()),
            "generated": datetime.datetime.now().isoformat(timespec="seconds")}
    (out / "candidates_meta.json").write_text(json.dumps(info, indent=2),
                                              encoding="utf-8")
    print(f"candidates: {info['count']} ({info['strips']} strips, {info['arcs']} arcs), "
          f"median {info['cells_median']} cells, cost "
          f"${info['cost_min']:,}-${info['cost_max']:,} (median ${info['cost_median']:,})")
    print(f"candidates OK -> {out / 'candidates.npz'}")
    print(f"\nverify: python pipeline/candidates.py --town {town}  (same census)")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="candidates.py",
        description="Enumerate candidate fuel breaks (strips + urban-boundary arcs).")
    p.add_argument("--town", default="paradise",
                   help="name of towns/<town>.json (default: paradise)")
    return p


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    common.run_stage("candidates", args.town, lambda: run(args.town))


if __name__ == "__main__":
    main()
