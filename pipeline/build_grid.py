"""build_grid.py - reproject every input onto the one EPSG:3857 grid.

Stage 2 of the Firebreak pipeline (see implementation-notes.md "## Plan").

- Single target grid in EPSG:3857 (matches Leaflet tiles), cell edge 60 m ground
  distance (Mercator size inflated by 1/cos(center lat)), anchored NW, snapped to
  whole cells. Geometry comes from common.grid_geometry(town bounds).
- Nearest-neighbor resampling for the categorical fuel layer, bilinear for elevation.
- Fuel vintage (plan section 2, amended by Deviation): the live LFPS product table
  offers no LF2014 layer, so the two-vintage comparison is impossible. The fetched
  LF2016_FBFM40 is the LF 2016 Remap base (~2016 conditions, pre-Camp-Fire); a
  corridor sanity check (woody fraction along ignition->town) confirms it is not a
  burn-scarred layer. Numbers recorded in grid_meta.json and later meta.data.
- Homes are the urban-cell PROXY scaled to the town's pre-fire count (config
  homes_estimate), buildings_proxy=true - Zach's call 2026-09-19: the story is
  2018, so the numbers are 2018's (2026 OSM reflects post-fire Paradise and
  undercounts ~6x). The Overpass fetch stays cached in data_raw but unused.
- Writes: fuel.npy, elev.npy, buildings.npz, grid_meta.json, alignment_check.png
  (fuel colors + building dots + ignition marker - eyeball the town blob and canyon),
  basemap.png (hillshade x fuel-group colors, the offline base layer per CONTRACT.md).

Verify: open pipeline/out/<town>/alignment_check.png and eyeball it against a real
map (e.g. for Paradise: gray urban blob center-west with the building dots on it,
the Feather River canyon running to the NE).
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

import numpy as np

import common

WC_TO_FBFM = {  # ESA WorldCover class -> representative FBFM40 code (fallback only)
    10: 183,  # tree cover        -> TL3
    20: 145,  # shrubland         -> SH5
    30: 102,  # grassland         -> GR2
    40: 93,   # cropland          -> NB agriculture
    50: 91,   # built-up          -> NB urban
    60: 99,   # bare/sparse       -> NB barren
    70: 92,   # snow/ice          -> NB snow
    80: 98,   # water             -> NB water
    90: 102,  # herbaceous wetland-> GR2
    95: 145,  # mangroves         -> SH5
    100: 102, # moss/lichen       -> GR2
}


# --- reprojection ---------------------------------------------------------------

def _dst_transform(geom):
    from affine import Affine
    c = geom["cell_merc"]
    return Affine(c, 0.0, geom["west_m"], 0.0, -c, geom["north_m"])


def reproject_band(src_path: str, band: int, geom: dict, categorical: bool,
                   dst_nodata: float) -> np.ndarray:
    import rasterio
    from rasterio.warp import reproject, Resampling

    dst = np.full((geom["rows"], geom["cols"]), dst_nodata,
                  dtype=np.int32 if categorical else np.float32)
    with rasterio.open(src_path) as src:
        reproject(
            source=rasterio.band(src, band),
            destination=dst,
            dst_transform=_dst_transform(geom),
            dst_crs="EPSG:3857",
            src_nodata=src.nodata,
            dst_nodata=dst_nodata,
            resampling=Resampling.nearest if categorical else Resampling.bilinear,
        )
    return dst


# --- fuel vintage rule (plan section 2 + amendment) -----------------------------

def _corridor_mask(geom: dict, cfg: dict, width_m: float = 3000.0) -> np.ndarray:
    ax, ay = common.lonlat_to_merc(cfg["ignition"]["lon"], cfg["ignition"]["lat"])
    bx, by = common.lonlat_to_merc(cfg["town_center"]["lon"], cfg["town_center"]["lat"])
    cell = geom["cell_merc"]
    cols_x = geom["west_m"] + (np.arange(geom["cols"]) + 0.5) * cell
    rows_y = geom["north_m"] - (np.arange(geom["rows"]) + 0.5) * cell
    px, py = np.meshgrid(cols_x, rows_y)
    abx, aby = bx - ax, by - ay
    t = np.clip(((px - ax) * abx + (py - ay) * aby) / (abx * abx + aby * aby), 0.0, 1.0)
    dist = np.hypot(px - (ax + t * abx), py - (ay + t * aby))
    return dist <= width_m * (geom["cell_merc"] / geom["cell_m"])  # ground -> merc


def check_fuel_prefire(fuel: np.ndarray, geom: dict, cfg: dict, town: str) -> dict:
    """The live LFPS table offers no second vintage to compare against, so this is
    an absolute check: the ignition->town corridor should be dominated by woody
    fuels (shrub/timber) if the layer is pre-fire; a fresh burn scar would read as
    grass/barren instead. Low woody fraction is logged loudly, not fatal - the
    alignment_check eyeball is the final gate."""
    corridor = _corridor_mask(geom, cfg)
    woody_codes = (common.FUEL_GROUPS["shrub"] + common.FUEL_GROUPS["timber_understory"]
                   + common.FUEL_GROUPS["timber_litter"])
    n = int(corridor.sum())
    woody_frac = float(np.isin(fuel, woody_codes)[corridor].mean()) if n else 0.0
    scar_frac = float(np.isin(fuel, common.FUEL_GROUPS["grass"] + [99])[corridor].mean()) if n else 1.0
    looks_prefire = woody_frac >= 0.30
    check = {"woody_frac": round(woody_frac, 4), "grass_barren_frac": round(scar_frac, 4),
             "corridor_cells": n, "looks_prefire": looks_prefire,
             "note": "absolute check - no LF2014 layer on the live LFPS to compare"}
    print(f"  pre-fire check: corridor woody={woody_frac:.3f} grass/barren="
          f"{scar_frac:.3f} (n={n}) -> {'looks pre-fire' if looks_prefire else 'SUSPICIOUS'}")
    if not looks_prefire:
        common.log_event("grid", town,
                         f"fuel layer corridor is only {woody_frac:.0%} woody - possible "
                         f"burn scar in LF2016_FBFM40; eyeball alignment_check.png")
    return check


# --- buildings ------------------------------------------------------------------

def build_proxy_buildings(geom: dict, fuel: np.ndarray, cfg: dict) -> tuple[dict, dict]:
    """Homes = developed-land (urban NB91) cells scaled to the town's pre-fire home
    count (config homes_estimate; falls back to one per cell). Points are spread
    deterministically (seeded) with sub-cell jitter for display; row/col stays the
    containing cell so 'is this home burning' is one grid lookup (CONTRACT.md)."""
    rr, cc = np.nonzero(fuel == 91)
    n_urban = int(rr.size)
    if n_urban == 0:
        sys.exit("no urban (NB91) cells - nothing to protect; check the town bbox")
    # A curated town states its pre-fire home count; a free-play region has no
    # such number, so it gets one home per developed cell, capped.
    target = int(cfg.get("homes_estimate")
                 or min(n_urban, int(cfg.get("homes_cap", 20000))))
    rng = np.random.default_rng(0)
    counts = np.full(n_urban, target // n_urban, dtype=np.int32)
    counts[rng.permutation(n_urban)[: target - int(counts.sum())]] += 1
    row = np.repeat(rr, counts).astype(np.int32)
    col = np.repeat(cc, counts).astype(np.int32)
    rowf = (row + rng.uniform(-0.38, 0.38, row.size)).astype(np.float64)
    colf = (col + rng.uniform(-0.38, 0.38, col.size)).astype(np.float64)
    x = geom["west_m"] + (colf + 0.5) * geom["cell_merc"]
    y = geom["north_m"] - (rowf + 0.5) * geom["cell_merc"]
    lon = np.degrees(x / common.R_MERC)
    lat = np.degrees(2.0 * np.arctan(np.exp(y / common.R_MERC)) - np.pi / 2.0)
    order = np.lexsort((col, row))
    arr = {"row": row[order], "col": col[order],
           "rowf": rowf[order].astype(np.float32), "colf": colf[order].astype(np.float32),
           "lat": lat[order], "lon": lon[order]}
    info = {"source": "proxy: developed-land cells scaled to pre-fire home count",
            "count": target, "proxy": True, "urban_cells": n_urban,
            "simplification": cfg.get(
                "homes_note", "Homes are estimated from developed-land cells at "
                              "pre-fire density, not individual footprints.")}
    print(f"  homes: {target} proxy points over {n_urban} urban cells "
          f"(homes_estimate; OSM stays cached but unused)")
    return arr, info


# --- rendering ------------------------------------------------------------------

def fuel_color_image(fuel: np.ndarray) -> np.ndarray:
    rgb = np.zeros((*fuel.shape, 3), dtype=np.uint8)
    rgb[:] = (16, 16, 16)  # anything unmapped stays near-black and visible
    for code, group in common.CODE_TO_GROUP.items():
        rgb[fuel == code] = common.hex_to_rgb(common.GROUP_COLORS[group])
    return rgb


def hillshade(elev: np.ndarray, cell_ground_m: float) -> np.ndarray:
    gy, gx = np.gradient(elev.astype(np.float64), cell_ground_m)
    # ESRI's aspect formula wants raster-down (southward-positive) dz/dy, which is
    # exactly np.gradient's axis-0 output on this NW-anchored grid.
    dzdx, dzdy = gx, gy
    slope = np.arctan(np.hypot(dzdx, dzdy))
    aspect = np.arctan2(dzdy, -dzdx)
    zen = np.radians(45.0)
    az = np.radians(360.0 - 315.0 + 90.0)
    hs = np.cos(zen) * np.cos(slope) + np.sin(zen) * np.sin(slope) * np.cos(az - aspect)
    return np.clip(hs, 0.0, 1.0)


def write_pngs(out: Path, geom: dict, cfg: dict, fuel: np.ndarray, elev: np.ndarray,
               bld: dict, bld_info: dict, fuel_product: str) -> None:
    from PIL import Image, ImageDraw

    scale = 2
    rows, cols = geom["rows"], geom["cols"]

    # basemap.png: hillshade x fuel colors, RGBA (CONTRACT.md offline base layer)
    hs = 0.45 + 0.55 * hillshade(elev, geom["cell_m"])
    base = (fuel_color_image(fuel).astype(np.float64) * hs[..., None]).astype(np.uint8)
    rgba = np.dstack([base, np.full((rows, cols), 255, dtype=np.uint8)])
    Image.fromarray(rgba, "RGBA").resize((cols * scale, rows * scale),
                                         Image.NEAREST).save(out / "basemap.png")

    # alignment_check.png: flat fuel colors + buildings + ignition/town markers
    img = Image.fromarray(fuel_color_image(fuel), "RGB").resize(
        (cols * scale, rows * scale), Image.NEAREST)
    draw = ImageDraw.Draw(img)
    for rf, cf in zip(bld["rowf"].tolist(), bld["colf"].tolist()):
        draw.point((int((cf + 0.5) * scale), int((rf + 0.5) * scale)), fill=(176, 0, 32))
    for key, color in (("ignition", (255, 0, 0)), ("town_center", (0, 60, 255))):
        r, c = common.lonlat_to_rowcol(geom, cfg[key]["lon"], cfg[key]["lat"])
        x, y = c * scale, r * scale
        draw.ellipse([x - 10, y - 10, x + 10, y + 10], outline=color, width=3)
        draw.line([x - 14, y, x + 14, y], fill=color, width=1)
        draw.line([x, y - 14, x, y + 14], fill=color, width=1)
    lines = [f"{cfg['name']}  {rows}x{cols} @ {geom['cell_m']:g} m",
             f"fuel: {fuel_product}",
             f"homes: {bld_info['count']}"
             + (" (proxy @ pre-fire density)" if bld_info["proxy"] else " (OSM)"),
             "red circle = ignition, blue = town center"]
    draw.multiline_text((7, 7), "\n".join(lines), fill=(255, 255, 255))
    draw.multiline_text((6, 6), "\n".join(lines), fill=(0, 0, 0))
    img.save(out / "alignment_check.png")
    print(f"  wrote {out / 'alignment_check.png'} and {out / 'basemap.png'}")


# --- stage entry ----------------------------------------------------------------

def run(town: str, cell_m: float = 60.0, quick: bool = False) -> None:
    cfg = common.load_town(town)
    raw = common.raw_dir(town)
    out = common.out_dir(town)
    if quick:
        cell_m *= 2

    geom = common.grid_geometry(cfg["bounds"], cell_m)
    n_cells = geom["rows"] * geom["cols"]
    print(f"build_grid: town={town} grid {geom['rows']} rows x {geom['cols']} cols "
          f"= {n_cells} cells @ {cell_m:g} m ({geom['cell_merc']:.2f} m in EPSG:3857)")
    if n_cells > 350_000:
        sys.exit(f"{n_cells} cells > 350k budget - shrink the bbox or grow the cell")

    status_path = raw / "fetch_status.json"
    if not status_path.exists():
        sys.exit(f"{status_path} missing - run fetch_data first: "
                 f"python pipeline/fetch_data.py --town {town}")
    status = json.loads(status_path.read_text(encoding="utf-8"))

    fuel_check = None
    if status["fuel_terrain"]["source"] == "landfire-lfps":
        import fetch_data
        fuel_layer, elev_layer = fetch_data.landfire_layers(cfg)
        bands = json.loads((raw / "landfire_bands.json").read_text(encoding="utf-8"))
        print(f"  reprojecting LANDFIRE bands {fuel_layer}+{elev_layer} "
              f"(local Albers -> EPSG:3857) ...")
        fuel = reproject_band(bands[fuel_layer]["path"], bands[fuel_layer]["band"],
                              geom, categorical=True, dst_nodata=-1)
        elev = reproject_band(bands[elev_layer]["path"], bands[elev_layer]["band"],
                              geom, categorical=False, dst_nodata=-9999.0)
        fuel_check = check_fuel_prefire(fuel, geom, cfg, town)
        fuel_product = f"LANDFIRE {fuel_layer} (pre-fire vintage)"
        terrain_source = f"LANDFIRE {elev_layer}"
    else:
        print("  reprojecting WorldCover + Copernicus DEM (fallback source) ...")
        wc = reproject_band(str(raw / "worldcover.tif"), 1, geom,
                            categorical=True, dst_nodata=-1)
        elev = reproject_band(str(raw / "copdem.tif"), 1, geom,
                              categorical=False, dst_nodata=-9999.0)
        fuel = np.full_like(wc, -1)
        for wc_class, code in WC_TO_FBFM.items():
            fuel[wc == wc_class] = code
        fuel_product = "ESA WorldCover 2021 mapped to FBFM40 groups (fallback)"
        terrain_source = "Copernicus DEM GLO-30 (fallback)"

    # unknown fuel codes -> barren (impassable); a large fraction means something
    # is wrong with the source, not the mapping - fail loud.
    known = np.isin(fuel, list(common.CODE_TO_GROUP.keys()))
    unknown_frac = float(1.0 - known.mean())
    if unknown_frac > 0.20:
        sys.exit(f"{unknown_frac:.0%} of fuel cells have unknown codes "
                 f"(sample: {np.unique(fuel[~known])[:12].tolist()}) - bad source?")
    fuel = np.where(known, fuel, 99).astype(np.int16)

    bad_elev = ~np.isfinite(elev) | (elev < -1000)
    bad_frac = float(bad_elev.mean())
    if bad_frac > 0.20:
        sys.exit(f"{bad_frac:.0%} of elevation cells are nodata - bad source?")
    if bad_elev.any():
        elev = np.where(bad_elev, np.median(elev[~bad_elev]), elev)
    elev = elev.astype(np.float32)

    counts = {g: int(np.isin(fuel, codes).sum())
              for g, codes in common.FUEL_GROUPS.items()}
    counts.update({name: int((fuel == code).sum())
                   for code, name in common.NB_CODES.items()})
    print(f"  fuel-group cell counts: {counts}")
    if counts["urban"] == 0:
        common.log_event("grid", town, "zero urban cells - town blob missing from fuel "
                                       "layer; alignment_check.png needs a hard look")
        print("  WARNING: zero urban (NB91) cells - check alignment_check.png closely")

    bld, bld_info = build_proxy_buildings(geom, fuel, cfg)

    np.save(out / "fuel.npy", fuel)
    np.save(out / "elev.npy", elev)
    np.savez_compressed(out / "buildings.npz", **bld)

    grid_meta = {
        "town": town, "name": cfg["name"],
        "rows": geom["rows"], "cols": geom["cols"],
        "cell_m": cell_m, "cell_merc": geom["cell_merc"],
        "bounds": geom["bounds"],
        "bounds_3857": {k: geom[k] for k in ("west_m", "north_m", "east_m", "south_m")},
        "fuel": {"product": fuel_product, "check": fuel_check},
        "terrain": terrain_source,
        "buildings": bld_info,
        "fuel_group_cells": counts,
        "unknown_fuel_frac": round(unknown_frac, 4),
        "elev_nodata_frac": round(bad_frac, 4),
        "fetched": status["fetched"],
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    (out / "grid_meta.json").write_text(json.dumps(grid_meta, indent=2),
                                        encoding="utf-8")
    write_pngs(out, geom, cfg, fuel, elev, bld, bld_info, fuel_product)
    print(f"build_grid OK -> {out / 'grid_meta.json'}")
    print(f"\nverify: open pipeline/out/{town}/alignment_check.png - building dots "
          f"must sit on the urban blob of {cfg['name']}, red circle on "
          f"{cfg['ignition']['label']}, blue cross on the town center")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="build_grid.py",
        description="Reproject fuels/terrain/buildings onto one EPSG:3857 grid; "
                    "write alignment_check.png.")
    p.add_argument("--town", default="paradise",
                   help="name of towns/<town>.json (default: paradise)")
    p.add_argument("--cell-m", type=float, default=60.0, metavar="M",
                   help="cell edge in ground meters (default: 60)")
    p.add_argument("--quick", action="store_true",
                   help="coarser grid (2x cell size) for fast end-to-end runs")
    return p


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    common.run_stage("grid", args.town,
                     lambda: run(args.town, args.cell_m, args.quick))


if __name__ == "__main__":
    main()
