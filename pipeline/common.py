"""common.py - shared plumbing for every pipeline stage.

- Town config loading (towns/<town>.json) with validation: everything town-specific
  (bounds, ignition, wind, story, budgets) lives there, never in code.
- The one EPSG:3857 grid every stage shares: grid_geometry() computes it from the
  town bounds, all other stages read the result from pipeline/out/<town>/grid_meta.json.
- FBFM40 fuel-model groups + display colors (used by build_grid, ros, candidates,
  export - one table, no drift).
- run_stage(): the fail-loud stage boundary - prints stage + input + exception,
  appends to pipeline/run_log.md, re-raises. The only sanctioned silent-ish paths
  are the fallbacks named in implementation-notes.md, and those log too.
"""
from __future__ import annotations

import datetime
import json
import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PIPELINE = REPO / "pipeline"
RUN_LOG = PIPELINE / "run_log.md"
TOWNS = REPO / "towns"
WEB_DATA = REPO / "web" / "data"

# --- paths ---------------------------------------------------------------------

def raw_dir(town: str) -> Path:
    p = PIPELINE / "data_raw" / town
    p.mkdir(parents=True, exist_ok=True)
    return p


def out_dir(town: str) -> Path:
    p = PIPELINE / "out" / town
    p.mkdir(parents=True, exist_ok=True)
    return p


# --- town config ----------------------------------------------------------------

def load_town(town: str) -> dict:
    path = TOWNS / f"{town}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"no town config at {path} - write one (towns/paradise.json is the template)")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    required = ["name", "story", "bounds", "ignition", "wind", "town_center",
                "horizon_min", "budgets"]
    missing = [k for k in required if k not in cfg]
    if missing:
        raise KeyError(f"{path} is missing keys: {missing}")
    b = cfg["bounds"]
    if not (b["west"] < b["east"] and b["south"] < b["north"]):
        raise ValueError(f"{path}: bounds are inverted: {b}")
    for key in ("ignition", "town_center"):
        pt = cfg[key]
        if not (b["west"] <= pt["lon"] <= b["east"] and b["south"] <= pt["lat"] <= b["north"]):
            raise ValueError(f"{path}: {key} {pt['lat']},{pt['lon']} is outside bounds {b}")
    if sorted(cfg["budgets"]) != cfg["budgets"]:
        raise ValueError(f"{path}: budgets must be ascending (CONTRACT.md)")
    cfg["town"] = town
    return cfg


# --- run log / stage boundary ---------------------------------------------------

def log_event(stage: str, town: str, msg: str) -> None:
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    with RUN_LOG.open("a", encoding="utf-8") as f:
        f.write(f"- {stamp} [{stage}/{town}] {msg}\n")


def run_stage(stage: str, town: str, fn) -> None:
    """Fail loud: any exception is printed with stage+input, appended to run_log.md,
    and re-raised. Never swallowed."""
    try:
        fn()
    except SystemExit as e:
        # in-stage validation failures use sys.exit(msg) - those must hit the run
        # log too; only a clean exit (code 0/None) passes through silently
        if e.code not in (0, None):
            log_event(stage, town, f"FAILED: SystemExit: {e.code}")
        raise
    except BaseException as e:
        log_event(stage, town, f"FAILED: {type(e).__name__}: {e}")
        print(f"\n*** stage '{stage}' FAILED for --town {town}: {type(e).__name__}: {e}",
              file=sys.stderr)
        raise


# --- EPSG:3857 (spherical web mercator - exact formulas, no pyproj needed) ------

R_MERC = 6378137.0


def lonlat_to_merc(lon: float, lat: float) -> tuple[float, float]:
    x = math.radians(lon) * R_MERC
    y = R_MERC * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
    return x, y


def merc_to_lonlat(x: float, y: float) -> tuple[float, float]:
    lon = math.degrees(x / R_MERC)
    lat = math.degrees(2 * math.atan(math.exp(y / R_MERC)) - math.pi / 2)
    return lon, lat


def grid_geometry(bounds: dict, cell_m: float) -> dict:
    """The single grid all stages share. Anchored at the NW corner of the town
    bounds, snapped to whole cells; cell edge is cell_m ground meters, inflated to
    EPSG:3857 units by 1/cos(center lat). Returns snapped bounds in both CRSs."""
    west_m, north_m = lonlat_to_merc(bounds["west"], bounds["north"])
    east_m, south_m = lonlat_to_merc(bounds["east"], bounds["south"])
    lat_c = (bounds["north"] + bounds["south"]) / 2
    cell = cell_m / math.cos(math.radians(lat_c))
    cols = max(1, round((east_m - west_m) / cell))
    rows = max(1, round((north_m - south_m) / cell))
    east_snap = west_m + cols * cell
    south_snap = north_m - rows * cell
    east4, _ = merc_to_lonlat(east_snap, north_m)
    _, south4 = merc_to_lonlat(west_m, south_snap)
    return {
        "rows": rows, "cols": cols,
        "cell_m": cell_m, "cell_merc": cell, "lat_center": lat_c,
        "west_m": west_m, "north_m": north_m,
        "east_m": east_snap, "south_m": south_snap,
        "bounds": {"west": bounds["west"], "north": bounds["north"],
                   "east": east4, "south": south4},
    }


def lonlat_to_rowcol(geom: dict, lon: float, lat: float) -> tuple[int, int]:
    """Grid cell containing a point. May be out of range - caller bounds-checks."""
    x, y = lonlat_to_merc(lon, lat)
    col = int((x - geom["west_m"]) // geom["cell_merc"])
    row = int((geom["north_m"] - y) // geom["cell_merc"])
    return row, col


def rowcol_to_lonlat(geom: dict, row: float, col: float) -> tuple[float, float]:
    """Center of cell (row, col) - fractional indices allowed."""
    x = geom["west_m"] + (col + 0.5) * geom["cell_merc"]
    y = geom["north_m"] - (row + 0.5) * geom["cell_merc"]
    return merc_to_lonlat(x, y)


# --- FBFM40 fuel model tables ---------------------------------------------------

FUEL_GROUPS: dict[str, list[int]] = {
    "grass": list(range(101, 110)),             # GR1-9
    "grass_shrub": list(range(121, 125)),       # GS1-4
    "shrub": list(range(141, 150)),             # SH1-9
    "timber_understory": list(range(161, 166)), # TU1-5
    "timber_litter": list(range(181, 190)),     # TL1-9
    "slash": list(range(201, 205)),             # SB1-4
}

NB_CODES: dict[int, str] = {
    91: "urban", 92: "snow", 93: "agriculture", 98: "water", 99: "barren",
}

CODE_TO_GROUP: dict[int, str] = {
    **{c: g for g, codes in FUEL_GROUPS.items() for c in codes},
    **{c: g for c, g in NB_CODES.items()},
}

BURNABLE_CODES = sorted(c for codes in FUEL_GROUPS.values() for c in codes)

GROUP_COLORS: dict[str, str] = {
    "grass": "#d4d06e",
    "grass_shrub": "#c2b45c",
    "shrub": "#a08c50",
    "timber_understory": "#5a7d4a",
    "timber_litter": "#3f5f3a",
    "slash": "#8a6a42",
    "urban": "#9aa0a6",
    "snow": "#ffffff",
    "agriculture": "#e5d9a8",
    "water": "#4a90d9",
    "barren": "#cfc4b0",
}


def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
