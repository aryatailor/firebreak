"""fetch_data.py - download LANDFIRE fuels/terrain + OSM buildings into pipeline/data_raw/<town>/.

Stage 1 of the Firebreak pipeline (see implementation-notes.md "## Plan").

- LANDFIRE via LFPS (the LANDFIRE Product Service): FBFM40 fuel model (both
  200F40_19 Remap and 140FBFM40 vintages - build_grid picks per plan section 2)
  + ELEV2020 elevation, one job. Product codes are validated against the landfire
  package's product search before requesting, never guessed.
- The LFPS request runs in a subprocess with a wall-clock timeout (default 20 min);
  on timeout or error the stage falls back to ESA WorldCover 2021 10 m + Copernicus
  DEM GLO-30 (both public AWS COGs, windowed reads, no keys) and records the switch
  in fetch_status.json (surfaces in meta.data at export).
- Buildings: OSM via Overpass (`out center` centroids), mirror list + retries with
  backoff, cached JSON. If fewer than 500 buildings come back, build_grid falls back
  to built-up cells as proxy structures (buildings_proxy in meta.data).
- Every download is cached in pipeline/data_raw/<town>/ and skipped on rerun
  (--force re-downloads). No API keys, ever.

Verify: run it twice - the second run must say every input is cached and change nothing.
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import requests

import common

LANDFIRE_LAYERS = ["200F40_19", "140FBFM40", "ELEV2020"]

OVERPASS_MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
# overpass-api.de answers 406 to the default python-requests User-Agent
HTTP_HEADERS = {"User-Agent": "firebreak-hackmit/1.0 (zdspeck@asu.edu)"}

WORLDCOVER_URL = ("https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/"
                  "ESA_WorldCover_10m_2021_v200_{tile}_Map.tif")
COPDEM_URL = ("https://copernicus-dem-30m.s3.amazonaws.com/"
              "Copernicus_DSM_COG_10_{tile}_DEM/Copernicus_DSM_COG_10_{tile}_DEM.tif")


# --- LANDFIRE (primary source) --------------------------------------------------

def _walk_strings(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _walk_strings(v)
    elif isinstance(obj, str):
        yield obj
    else:
        d = getattr(obj, "__dict__", None)
        if d:
            yield from _walk_strings(d)


def _available_layer_codes(ps) -> set[str]:
    """Layer codes straight from the product search - never guessed. get_layers()
    is the documented path; walking get_products() is the defensive fallback."""
    try:
        layers = ps.get_layers()
        if isinstance(layers, (list, tuple, set)) and layers and all(
                isinstance(x, str) for x in layers):
            return set(layers)
    except Exception as e:
        print(f"  ProductSearch.get_layers() unusable ({e}); walking get_products()")
    return set(_walk_strings(ps.get_products()))


def landfire_worker(town: str) -> None:
    """Runs in a subprocess so the parent can enforce a hard wall-clock timeout
    (the landfire package polls LFPS with no timeout of its own)."""
    from landfire import Landfire
    from landfire.product.search import ProductSearch

    cfg = common.load_town(town)
    raw = common.raw_dir(town)

    available = _available_layer_codes(ProductSearch())
    missing = [l for l in LANDFIRE_LAYERS if l not in available]
    if missing:
        sys.exit(f"layer codes not present in the landfire product search: {missing} "
                 f"- refusing to guess (plan section 2)")
    print(f"  product search confirms layers: {LANDFIRE_LAYERS}")

    b = cfg["bounds"]
    bbox = f"{b['west']} {b['south']} {b['east']} {b['north']}"
    out_zip = raw / "landfire.zip"
    print(f"  LFPS job: bbox=({bbox}) layers={LANDFIRE_LAYERS} -> {out_zip}")
    # show_status=False: the package's tqdm status lines contain emoji that raise
    # UnicodeEncodeError on a piped cp1252 stdout (Windows), killing the worker.
    Landfire(bbox=bbox).request_data(layers=LANDFIRE_LAYERS, output_path=str(out_zip),
                                     show_status=False)
    if not out_zip.exists() or out_zip.stat().st_size < 10_000:
        sys.exit(f"LFPS produced no usable zip at {out_zip}")
    print(f"  LFPS done: {out_zip.stat().st_size / 1e6:.1f} MB")


def _landfire_band_map(lf_dir: Path) -> dict:
    """Map each requested layer -> (tif path, band). LFPS normally returns one
    multiband tif in request order; also handles one-tif-per-layer. Anything else
    fails loud."""
    import rasterio

    tifs = sorted(p for p in lf_dir.rglob("*.tif") if not p.name.endswith(".aux.tif"))
    if len(tifs) == 1:
        with rasterio.open(tifs[0]) as src:
            if src.count != len(LANDFIRE_LAYERS):
                raise RuntimeError(f"{tifs[0]} has {src.count} bands, expected "
                                   f"{len(LANDFIRE_LAYERS)} ({LANDFIRE_LAYERS})")
        return {layer: {"path": str(tifs[0]), "band": i + 1}
                for i, layer in enumerate(LANDFIRE_LAYERS)}
    if len(tifs) == len(LANDFIRE_LAYERS):
        mapping = {}
        for layer in LANDFIRE_LAYERS:
            match = [t for t in tifs if layer.lower() in t.name.lower()]
            if len(match) != 1:
                raise RuntimeError(f"cannot match layer {layer} to one of "
                                   f"{[t.name for t in tifs]}")
            mapping[layer] = {"path": str(match[0]), "band": 1}
        return mapping
    raise RuntimeError(f"unexpected LFPS zip contents in {lf_dir}: "
                       f"{[t.name for t in tifs]}")


def fetch_landfire(town: str, raw: Path, timeout_min: float, force: bool) -> dict | None:
    """Returns the layer->band mapping on success (cached or fresh), None on
    timeout/failure (caller falls back). The subprocess is killed at the deadline."""
    bands_json = raw / "landfire_bands.json"
    if bands_json.exists() and not force:
        mapping = json.loads(bands_json.read_text(encoding="utf-8"))
        if all(Path(v["path"]).exists() for v in mapping.values()):
            print(f"  LANDFIRE cached ({bands_json})")
            return mapping

    # A previous run already fell back and cached WorldCover+CopDEM: a rerun must
    # not re-block on LFPS for another 20 minutes (idempotency). --force retries.
    status_path = raw / "fetch_status.json"
    if status_path.exists() and not force:
        prev = json.loads(status_path.read_text(encoding="utf-8"))
        if (prev.get("fuel_terrain", {}).get("fallback")
                and (raw / "worldcover.tif").exists()
                and (raw / "copdem.tif").exists()):
            print("  previous run fell back to WorldCover+CopDEM (cached) - "
                  "skipping the LANDFIRE retry (--force to retry LFPS)")
            return None

    cmd = [sys.executable, str(Path(__file__).resolve()),
           "--landfire-worker", "--town", town]
    print(f"  LANDFIRE via LFPS, hard timeout {timeout_min:g} min ...")
    t0 = time.monotonic()
    try:
        # PYTHONUTF8: the worker inherits a piped cp1252 stdout on Windows;
        # any non-ASCII output from the landfire package would kill it.
        result = subprocess.run(cmd, timeout=timeout_min * 60,
                                env={**os.environ, "PYTHONUTF8": "1"})
    except subprocess.TimeoutExpired:
        common.log_event("fetch", town,
                         f"LANDFIRE timed out after {timeout_min:g} min - falling back "
                         f"to WorldCover+CopDEM (sanctioned fallback, plan section 10)")
        print(f"  LANDFIRE TIMED OUT after {timeout_min:g} min -> fallback")
        return None
    if result.returncode != 0:
        common.log_event("fetch", town,
                         f"LANDFIRE worker failed (exit {result.returncode}) - falling "
                         f"back to WorldCover+CopDEM")
        print(f"  LANDFIRE worker failed (exit {result.returncode}) -> fallback")
        return None
    print(f"  LFPS finished in {(time.monotonic() - t0) / 60:.1f} min")

    # The delivered zip can still disappoint (truncated, error payload, unexpected
    # layout) - that is the same sanctioned fallback, not a crash. The extract dir
    # is cleared first: LFPS tifs are named by unique job id, so a rerun would
    # otherwise accumulate stale tifs and break the band mapping.
    try:
        lf_dir = raw / "landfire"
        shutil.rmtree(lf_dir, ignore_errors=True)
        lf_dir.mkdir()
        with zipfile.ZipFile(raw / "landfire.zip") as z:
            z.extractall(lf_dir)
        mapping = _landfire_band_map(lf_dir)
    except Exception as e:
        common.log_event("fetch", town,
                         f"LANDFIRE zip unusable ({type(e).__name__}: {e}) - falling "
                         f"back to WorldCover+CopDEM")
        print(f"  LANDFIRE zip unusable ({e}) -> fallback")
        return None
    bands_json.write_text(json.dumps(mapping, indent=2), encoding="utf-8")
    print(f"  extracted -> {lf_dir}, band map -> {bands_json}")
    return mapping


# --- WorldCover + Copernicus DEM (fallback source) ------------------------------

def _worldcover_tiles(b: dict) -> list[str]:
    tiles = []
    for la in range(int(math.floor(b["south"] / 3)) * 3,
                    int(math.floor(b["north"] / 3)) * 3 + 1, 3):
        for lo in range(int(math.floor(b["west"] / 3)) * 3,
                        int(math.floor(b["east"] / 3)) * 3 + 1, 3):
            ns = "N" if la >= 0 else "S"
            ew = "E" if lo >= 0 else "W"
            tiles.append(f"{ns}{abs(la):02d}{ew}{abs(lo):03d}")
    return tiles


def _copdem_tiles(b: dict) -> list[str]:
    tiles = []
    for la in range(int(math.floor(b["south"])), int(math.floor(b["north"])) + 1):
        for lo in range(int(math.floor(b["west"])), int(math.floor(b["east"])) + 1):
            ns = "N" if la >= 0 else "S"
            ew = "E" if lo >= 0 else "W"
            tiles.append(f"{ns}{abs(la):02d}_00_{ew}{abs(lo):03d}_00")
    return tiles


def _fetch_cog_window(urls: list[str], b: dict, out_tif: Path) -> None:
    """Windowed read of one or more public COG tiles (EPSG:4326), mosaicked to the
    town bbox, written locally."""
    import rasterio
    from rasterio.merge import merge

    srcs = [rasterio.open(u) for u in urls]
    try:
        data, transform = merge(srcs, bounds=(b["west"], b["south"], b["east"], b["north"]))
        profile = {
            "driver": "GTiff", "height": data.shape[1], "width": data.shape[2],
            "count": data.shape[0], "dtype": data.dtype, "crs": srcs[0].crs,
            "transform": transform, "nodata": srcs[0].nodata,
            "compress": "deflate",
        }
        with rasterio.open(out_tif, "w", **profile) as dst:
            dst.write(data)
    finally:
        for s in srcs:
            s.close()


def fetch_fallback(cfg: dict, raw: Path, force: bool) -> None:
    b = cfg["bounds"]
    wc_tif = raw / "worldcover.tif"
    dem_tif = raw / "copdem.tif"
    if wc_tif.exists() and not force:
        print(f"  WorldCover cached ({wc_tif})")
    else:
        urls = [WORLDCOVER_URL.format(tile=t) for t in _worldcover_tiles(b)]
        print(f"  WorldCover windowed read: {urls}")
        _fetch_cog_window(urls, b, wc_tif)
        print(f"  -> {wc_tif} ({wc_tif.stat().st_size / 1e6:.1f} MB)")
    if dem_tif.exists() and not force:
        print(f"  Copernicus DEM cached ({dem_tif})")
    else:
        urls = [COPDEM_URL.format(tile=t) for t in _copdem_tiles(b)]
        print(f"  Copernicus DEM windowed read: {urls}")
        _fetch_cog_window(urls, b, dem_tif)
        print(f"  -> {dem_tif} ({dem_tif.stat().st_size / 1e6:.1f} MB)")


# --- OSM buildings via Overpass -------------------------------------------------

def fetch_buildings(cfg: dict, raw: Path, force: bool) -> dict:
    """Returns {"source": ..., "count": N}. On total Overpass failure the stage
    does NOT die: count 0 is recorded and build_grid uses the sanctioned proxy
    (built-up cells). The failed attempt is NOT cached, so a rerun retries."""
    cache = raw / "buildings_overpass.json"
    if cache.exists() and not force:
        js = json.loads(cache.read_text(encoding="utf-8"))
        n = len(js.get("elements", []))
        print(f"  buildings cached: {n} elements ({cache})")
        return {"source": js.get("_source", "overpass (cached)"), "count": n}

    b = cfg["bounds"]
    bbox = f"{b['south']},{b['west']},{b['north']},{b['east']}"
    query = (
        "[out:json][timeout:180];\n"
        f"( node[\"building\"]({bbox});\n"
        f"  way[\"building\"]({bbox});\n"
        f"  relation[\"building\"]({bbox}); );\n"
        "out center;\n"
    )
    errors = []
    for mirror in OVERPASS_MIRRORS:
        for attempt in range(1, 4):
            try:
                print(f"  Overpass {mirror} (attempt {attempt}/3) ...")
                r = requests.post(mirror, data={"data": query}, timeout=200,
                                  headers=HTTP_HEADERS)
                if r.status_code != 200:
                    raise RuntimeError(f"HTTP {r.status_code}")
                js = r.json()
                # Overpass reports server-side aborts (query timeout, memory) as
                # HTTP 200 + a "remark" + PARTIAL elements - caching that would
                # silently undercount buildings forever.
                if js.get("remark"):
                    raise RuntimeError(f"overpass remark: {js['remark']}")
                n = len(js.get("elements", []))
                if n == 0:
                    raise RuntimeError("0 elements (likely a rate-limit/empty response)")
                js["_source"] = f"overpass ({mirror})"
                cache.write_text(json.dumps(js), encoding="utf-8")
                print(f"  -> {n} building elements, cached to {cache}")
                return {"source": js["_source"], "count": n}
            except Exception as e:
                errors.append(f"{mirror} #{attempt}: {type(e).__name__}: {e}")
                time.sleep(5 * attempt)
    common.log_event("fetch", cfg["town"],
                     f"Overpass failed on all mirrors ({'; '.join(errors[-2:])}) - "
                     f"build_grid will use proxy buildings (sanctioned fallback)")
    print("  Overpass FAILED on all mirrors -> proxy buildings at build_grid time")
    return {"source": "overpass-failed", "count": 0}


# --- stage entry ----------------------------------------------------------------

def run(town: str, force: bool = False, timeout_min: float = 20.0) -> None:
    cfg = common.load_town(town)
    raw = common.raw_dir(town)
    print(f"fetch_data: town={town} ({cfg['name']}) -> {raw}")

    band_map = fetch_landfire(town, raw, timeout_min, force)
    if band_map is None:
        fetch_fallback(cfg, raw, force)
        fuel_terrain = {"source": "worldcover+copdem", "fallback": True,
                        "detail": "ESA WorldCover 2021 10m + Copernicus DEM GLO-30 "
                                  "(LANDFIRE unavailable within timeout)"}
    else:
        fuel_terrain = {"source": "landfire-lfps", "fallback": False,
                        "layers": LANDFIRE_LAYERS}

    buildings = fetch_buildings(cfg, raw, force)

    status = {
        "town": town,
        "fetched": datetime.date.today().isoformat(),
        "fuel_terrain": fuel_terrain,
        "buildings": buildings,
    }
    status_path = raw / "fetch_status.json"
    status_path.write_text(json.dumps(status, indent=2), encoding="utf-8")
    print(f"fetch_data OK -> {status_path}")
    print(json.dumps(status, indent=2))
    print(f"\nverify: python pipeline/fetch_data.py --town {town}  "
          f"(rerun must report everything cached)")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fetch_data.py",
        description="Download LANDFIRE fuels/terrain + OSM buildings into "
                    "pipeline/data_raw/<town>/ (cached, idempotent).")
    p.add_argument("--town", default="paradise",
                   help="name of towns/<town>.json (default: paradise)")
    p.add_argument("--force", action="store_true",
                   help="re-download even if a cached copy exists")
    p.add_argument("--timeout-min", type=float, default=20.0, metavar="MIN",
                   help="wall-clock timeout for the LANDFIRE request before "
                        "falling back (default: 20)")
    p.add_argument("--landfire-worker", action="store_true", help=argparse.SUPPRESS)
    return p


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    if args.landfire_worker:
        landfire_worker(args.town)
        return
    common.run_stage("fetch", args.town,
                     lambda: run(args.town, args.force, args.timeout_min))


if __name__ == "__main__":
    main()
