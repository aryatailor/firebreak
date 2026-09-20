"""ros.py - relative spread rates and directed edge travel times (plan section 3).

- Base rate per Scott & Burgan FBFM40 group: grass 1.00, grass-shrub 0.65, shrub
  0.45, slash 0.35, timber-understory 0.30, timber litter 0.12. Urban NB91 gets a
  tunable structure_rate (default 0.15) so fire can enter town the way the Camp
  Fire did; NB92/93/98/99 are impassable. One global scale (m/min for grass) is
  calibrated against the historical outcome, not claimed from theory.
- Slope per directed edge, straight from the DEM:
  f_slope = clamp(2^(theta_deg / 10), 0.5, 8), theta = uphill angle along travel.
- Wind, directional: f_wind = exp(k_w * (v_mph / 10) * cos(delta)), delta = angle
  between travel direction and downwind. At 35 mph, k_w 0.35: x3.4 downwind, x0.29
  upwind (~12:1 head-to-heel - the Camp Fire's defining behavior).
- Edge time i->j = ground_dist / (min(rate_i, rate_j) * f_slope * f_wind * scale);
  min() of the endpoints so a 2-cell-wide break cannot be leapfrogged diagonally.

Module used by simulate/calibrate/solve. Verify:
    python pipeline/ros.py --town paradise    # rate/factor diagnostics
"""
from __future__ import annotations

import argparse
import json
import math

import numpy as np

import common

DEFAULT_PARAMS = {"scale": 60.0, "k_w": 0.35, "structure_rate": 0.15,
                  "break_mult": 0.05}

GROUP_RATES = {
    "grass": 1.0, "grass_shrub": 0.65, "shrub": 0.45, "slash": 0.35,
    "timber_understory": 0.30, "timber_litter": 0.12,
}

# the 8 neighbor offsets (dr, dc); dr +1 = south, dc +1 = east
OFFSETS = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def base_rate(fuel: np.ndarray, params: dict) -> np.ndarray:
    """Relative spread rate per cell; 0 = impassable (NB except urban)."""
    rate = np.zeros(fuel.shape, dtype=np.float32)
    for group, rel in GROUP_RATES.items():
        rate[np.isin(fuel, common.FUEL_GROUPS[group])] = rel
    rate[fuel == 91] = params["structure_rate"]
    return rate


def _shift(a: np.ndarray, dr: int, dc: int, fill: float) -> np.ndarray:
    """Plane of neighbor values: out[r, c] = a[r + dr, c + dc], border-filled."""
    out = np.full(a.shape, fill, dtype=a.dtype)
    rows, cols = a.shape
    src_r = slice(max(dr, 0), rows + min(dr, 0))
    dst_r = slice(max(-dr, 0), rows + min(-dr, 0))
    src_c = slice(max(dc, 0), cols + min(dc, 0))
    dst_c = slice(max(-dc, 0), cols + min(-dc, 0))
    out[dst_r, dst_c] = a[src_r, src_c]
    return out


def slope_factors(elev: np.ndarray, cell_m: float) -> list[np.ndarray]:
    """f_slope per directed edge, one plane per offset. NaN at borders (no edge).
    Static across the calibration sweep - compute once."""
    elev = elev.astype(np.float32)
    planes = []
    for dr, dc in OFFSETS:
        dist = cell_m * math.hypot(dr, dc)
        dz = _shift(elev, dr, dc, np.nan) - elev
        theta = np.degrees(np.arctan(dz / dist))
        f = np.clip(2.0 ** (theta / 10.0), 0.5, 8.0)
        planes.append(f.astype(np.float32))  # NaN propagates through clip
    return planes


def wind_factors(wind: dict, k_w: float) -> list[float]:
    """Scalar f_wind per offset (wind field is uniform over the box)."""
    v = wind["speed_mph"]
    downwind = math.radians((wind["from_deg"] + 180.0) % 360.0)
    out = []
    for dr, dc in OFFSETS:
        bearing = math.atan2(dc, -dr)  # compass angle of travel: atan2(east, north)
        out.append(math.exp(k_w * (v / 10.0) * math.cos(bearing - downwind)))
    return out


def edge_times(rate: np.ndarray, slope_planes: list[np.ndarray],
               wind_f: list[float], cell_m: float, scale: float) -> list[np.ndarray]:
    """Minutes to traverse each directed edge; inf = impassable or border."""
    planes = []
    for k, (dr, dc) in enumerate(OFFSETS):
        dist = cell_m * math.hypot(dr, dc)
        r_eff = np.minimum(rate, _shift(rate, dr, dc, 0.0))
        speed = r_eff * slope_planes[k] * wind_f[k] * scale  # m/min
        with np.errstate(divide="ignore", invalid="ignore"):
            t = dist / speed
        t[~np.isfinite(t)] = np.inf
        planes.append(t.astype(np.float32))
    return planes


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="ros.py",
                                description="Spread-rate diagnostics for one town.")
    p.add_argument("--town", default="paradise")
    args = p.parse_args(argv)

    cfg = common.load_town(args.town)
    out = common.out_dir(args.town)
    fuel = np.load(out / "fuel.npy")
    elev = np.load(out / "elev.npy")
    gm = json.loads((out / "grid_meta.json").read_text(encoding="utf-8"))

    rate = base_rate(fuel, DEFAULT_PARAMS)
    print(f"ros diagnostics: {args.town}, grid {gm['rows']}x{gm['cols']}")
    print(f"  passable cells: {(rate > 0).mean():.1%}  "
          f"(impassable = water/barren/agriculture/snow)")
    for g, rel in GROUP_RATES.items():
        n = int(np.isin(fuel, common.FUEL_GROUPS[g]).sum())
        print(f"  {g:<18} rel {rel:>4.2f}  cells {n}")
    print(f"  urban (structure_rate {DEFAULT_PARAMS['structure_rate']}): "
          f"{int((fuel == 91).sum())} cells")

    sf = slope_factors(elev, gm["cell_m"])
    all_sf = np.concatenate([s[np.isfinite(s)].ravel() for s in sf])
    print(f"  f_slope: min {all_sf.min():.2f} median {np.median(all_sf):.2f} "
          f"max {all_sf.max():.2f} (cap 8)")
    wf = wind_factors(cfg["wind"], DEFAULT_PARAMS["k_w"])
    print(f"  f_wind at {cfg['wind']['speed_mph']} mph from {cfg['wind']['from_deg']}deg, "
          f"k_w {DEFAULT_PARAMS['k_w']}: downwind x{max(wf):.2f}, upwind x{min(wf):.2f}")


if __name__ == "__main__":
    main()
