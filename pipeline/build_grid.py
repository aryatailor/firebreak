"""build_grid.py - reproject every input onto the one EPSG:3857 grid.

Stage 2 of the Firebreak pipeline (see implementation-notes.md "## Plan").

- Single target grid in EPSG:3857 (matches Leaflet tiles), cell edge 60 m ground
  distance (Mercator size inflated by 1/cos(center lat)), anchored NW, snapped to
  whole cells. Geometry comes from common.grid_geometry(town bounds).
- Nearest-neighbor resampling for the categorical fuel layer, bilinear for elevation.
- Fuel vintage rule (plan section 2 + approval amendment): compare 200F40_19 (Remap)
  against 140FBFM40 in the ignition->town corridor; a clear burn-scar signature or an
  ambiguous result both pick 140FBFM40; only a clean Remap uses it. Numbers recorded
  in grid_meta.json and later meta.data.
- Buildings from the cached Overpass JSON -> (row, col); fewer than 500 -> proxy
  points from built-up (urban) cells, flagged buildings_proxy.
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


def choose_fuel_vintage(remap: np.ndarray, lf2014: np.ndarray, geom: dict,
                        cfg: dict) -> tuple[np.ndarray, dict]:
    """A fresh burn scar shows as woody 2014 fuels (shrub/timber) turned grass or
    barren in the Remap layer across the ignition->town corridor. Clear scar or
    ambiguous -> 140FBFM40 (approved default); only a clean Remap is used."""
    corridor = _corridor_mask(geom, cfg)
    woody_codes = (common.FUEL_GROUPS["shrub"] + common.FUEL_GROUPS["timber_understory"]
                   + common.FUEL_GROUPS["timber_litter"])
    scar_codes = common.FUEL_GROUPS["grass"] + [99]
    woody14 = np.isin(lf2014, woody_codes) & corridor
    denom = int(woody14.sum())
    scar = int((woody14 & np.isin(remap, scar_codes)).sum())
    scar_frac = scar / denom if denom else 1.0
    changed_frac = float((remap != lf2014)[corridor].mean()) if corridor.any() else 1.0

    if scar_frac > 0.20:
        choice, reason = "140FBFM40", "burn-scar signature in Remap over the corridor"
    elif scar_frac < 0.05:
        choice, reason = "200F40_19", "no scar signature - Remap is pre-fire here"
    else:
        choice, reason = "140FBFM40", "ambiguous - approved default (amendment)"

    check = {"scar_frac": round(scar_frac, 4), "changed_frac": round(changed_frac, 4),
             "woody_2014_corridor_cells": denom, "choice": choice, "reason": reason}
    print(f"  fuel vintage check: scar_frac={scar_frac:.3f} changed_frac="
          f"{changed_frac:.3f} (n={denom}) -> {choice} ({reason})")
    return (lf2014 if choice == "140FBFM40" else remap), check


# --- buildings ------------------------------------------------------------------

def load_buildings(raw: Path, geom: dict, fuel: np.ndarray, town: str) -> tuple[dict, dict]:
    """Returns ({row, col, lat, lon arrays}, info). Fewer than 500 real buildings ->
    proxy points at built-up (urban NB91) cell centers, buildings_proxy=true."""
    pts = []
    source = "overpass-failed"
    cache = raw / "buildings_overpass.json"
    if cache.exists():
        js = json.loads(cache.read_text(encoding="utf-8"))
        source = js.get("_source", "overpass")
        for el in js.get("elements", []):
            if el.get("type") == "node" and "lat" in el:
                lat, lon = el["lat"], el["lon"]
            else:
                c = el.get("center")
                if not c:
                    continue
                lat, lon = c["lat"], c["lon"]
            pts.append((lat, lon))

    rows, cols = geom["rows"], geom["cols"]
    keep = []
    for lat, lon in pts:
        r, c = common.lonlat_to_rowcol(geom, lon, lat)
        if 0 <= r < rows and 0 <= c < cols:
            keep.append((r, c, lat, lon))

    proxy = len(keep) < 500
    if proxy:
        common.log_event("grid", town,
                         f"only {len(keep)} OSM buildings in-grid - using built-up "
                         f"cells as proxy structures (buildings_proxy=true)")
        print(f"  only {len(keep)} OSM buildings -> proxy from urban cells")
        rr, cc = np.nonzero(fuel == 91)
        keep = []
        for r, c in zip(rr.tolist(), cc.tolist()):
            lon, lat = common.rowcol_to_lonlat(geom, r, c)
            keep.append((r, c, lat, lon))
        source = "proxy: built-up land-cover cells"
        if not keep:
            sys.exit("no OSM buildings AND no urban cells - nothing to protect; "
                     "check the town bbox")

    keep.sort()
    arr = {
        "row": np.array([k[0] for k in keep], dtype=np.int32),
        "col": np.array([k[1] for k in keep], dtype=np.int32),
        "lat": np.array([k[2] for k in keep], dtype=np.float64),
        "lon": np.array([k[3] for k in keep], dtype=np.float64),
    }
    info = {"source": source, "count": len(keep), "proxy": proxy}
    print(f"  buildings: {info['count']} ({info['source']})")
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
    for r, c in zip(bld["row"].tolist(), bld["col"].tolist()):
        x, y = c * scale, r * scale
        draw.rectangle([x, y, x + 1, y + 1], fill=(176, 0, 32))
    for key, color in (("ignition", (255, 0, 0)), ("town_center", (0, 60, 255))):
        r, c = common.lonlat_to_rowcol(geom, cfg[key]["lon"], cfg[key]["lat"])
        x, y = c * scale, r * scale
        draw.ellipse([x - 10, y - 10, x + 10, y + 10], outline=color, width=3)
        draw.line([x - 14, y, x + 14, y], fill=color, width=1)
        draw.line([x, y - 14, x, y + 14], fill=color, width=1)
    lines = [f"{cfg['name']}  {rows}x{cols} @ {geom['cell_m']:g} m",
             f"fuel: {fuel_product}",
             f"buildings: {bld_info['count']}"
             + (" (PROXY)" if bld_info["proxy"] else " (OSM)"),
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
        bands = json.loads((raw / "landfire_bands.json").read_text(encoding="utf-8"))
        print("  reprojecting LANDFIRE bands (Albers -> EPSG:3857) ...")
        remap = reproject_band(bands["200F40_19"]["path"], bands["200F40_19"]["band"],
                               geom, categorical=True, dst_nodata=-1)
        lf2014 = reproject_band(bands["140FBFM40"]["path"], bands["140FBFM40"]["band"],
                                geom, categorical=True, dst_nodata=-1)
        elev = reproject_band(bands["ELEV2020"]["path"], bands["ELEV2020"]["band"],
                              geom, categorical=False, dst_nodata=-9999.0)
        fuel, fuel_check = choose_fuel_vintage(remap, lf2014, geom, cfg)
        fuel_product = (f"LANDFIRE {fuel_check['choice']} "
                        f"({'LF 2014' if fuel_check['choice'] == '140FBFM40' else 'LF 2016 Remap'})")
        terrain_source = "LANDFIRE ELEV2020"
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

    bld, bld_info = load_buildings(raw, geom, fuel, town)

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
