"""region.py - build a free-play region around a point, reusing the real chain.

A free-play region is a town config written on the fly: a box around the point,
the newest LANDFIRE fuels, homes proxied one per developed cell, a default wind,
an ignition on the upwind edge, and NO solver. Same stages as a curated town
(fetch -> grid -> calibrate -> export -> physics), just with the gate and the
solver switched off, so free play and the story run on identical physics.

Used by serve.py's /api/region; also runnable directly:
    python pipeline/region.py --lat 38.45 --lon -122.71 --name "Santa Rosa, CA"
"""
from __future__ import annotations

import argparse
import json
import math
import re
import unicodedata

import common
import build_grid
import calibrate
import export
import fetch_data
import physics
import simulate

HALF_LAT = 0.1          # box is lat +/- this
HALF_LON = 0.13         # and lon +/- this
DEFAULT_WIND = {"speed_mph": 30, "from_deg": 45}
IGNITION_FRAC = 0.35    # how far from centre toward the upwind edge
HOMES_CAP = 20000


def slugify(name: str, lat: float, lon: float) -> str:
    """Stable id. Named places slug their name; anonymous clicks use rounded
    coordinates, which is also the cache key - the same point twice is the same
    region."""
    if name:
        s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
        s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
        s = re.sub(r"-+", "-", s)[:40].strip("-")
        if s:
            return s
    return f"r{lat:.2f}_{lon:.2f}".replace(".", "p").replace("-", "m")


def newest_fuel_layer() -> str:
    """Newest FBFM40 layer the live LFPS offers - free play is about today's
    landscape, so unlike the historical towns there is no pre-fire cutoff."""
    import requests
    r = requests.get(f"{fetch_data.LFPS_API}/products", timeout=30,
                     headers=fetch_data.HTTP_HEADERS)
    r.raise_for_status()
    names = [p.get("layerName", "") for p in r.json().get("products", [])]
    fbfm = sorted(n for n in names
                  if re.fullmatch(r"LF\d{4}_FBFM40", n or ""))
    if not fbfm:
        raise RuntimeError("live LFPS product table lists no plain LF####_FBFM40 "
                           "layer - refusing to guess")
    return fbfm[-1]


def write_config(town: str, lat: float, lon: float, name: str,
                 fuel_layer: str) -> dict:
    """The town config a free-play region runs on. Ignition sits upwind of the
    clicked point so the fire runs toward it."""
    b = {"west": lon - HALF_LON, "south": lat - HALF_LAT,
         "east": lon + HALF_LON, "north": lat + HALF_LAT}
    wd = math.radians(DEFAULT_WIND["from_deg"])
    ign_lat = lat + math.cos(wd) * (2 * HALF_LAT) * IGNITION_FRAC
    ign_lon = lon + math.sin(wd) * (2 * HALF_LON) * IGNITION_FRAC
    label = name or f"{lat:.3f}, {lon:.3f}"
    cfg = {
        "name": name or f"Region {label}",
        "story": f"Free play. {label}.",
        "crawl": [f"{label}.",
                  "Click anywhere to start a fire. Draw breaks to stop it."],
        "bounds": b,
        "ignition": {"lat": round(ign_lat, 5), "lon": round(ign_lon, 5),
                     "label": "Ignition"},
        "wind": dict(DEFAULT_WIND),
        "town_center": {"lat": lat, "lon": lon},
        "homes_cap": HOMES_CAP,
        "fuel_layer": fuel_layer,
        "web_dir": f"towns/{town}",
        "horizon_min": 720,
        "budgets": [500000, 1000000, 2000000, 3000000],
        "free_play": True,
    }
    common.TOWNS.mkdir(parents=True, exist_ok=True)
    (common.TOWNS / f"{town}.json").write_text(json.dumps(cfg, indent=2),
                                               encoding="utf-8")
    return cfg


def index_entry(town: str, cfg: dict, event: str = "Free play") -> None:
    """Append (or update) this region in web/towns/index.json. Read-modify-write
    under the same process lock serve.py holds, so concurrent builds cannot
    interleave."""
    path = common.REPO / "web" / "towns" / "index.json"
    entries = []
    if path.exists():
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            common.log_event("region", town, "index.json was unparseable - rebuilt")
    entries = [e for e in entries if e.get("id") != town]
    entries.append({"id": town, "name": cfg["name"], "event": event,
                    "data_dir": f"towns/{town}/", "story": False})
    path.write_text(json.dumps(entries, indent=1), encoding="utf-8")


def build(lat: float, lon: float, name: str = "", progress=None,
          event: str = "Free play") -> dict:
    """Run the whole free-play chain. progress(stage, pct, message) is called as
    it goes; it is the only thing serve.py needs to stream status."""
    def say(stage, pct, message=""):
        print(f"  [{pct:>3}%] {stage}: {message}")
        if progress:
            progress(stage, pct, message)

    town = slugify(name, lat, lon)
    web = common.REPO / "web" / "towns" / town
    if (web / "meta.json").exists() and (web / "physics.json").exists():
        say("Ready", 100, "already built")
        return {"id": town, "data_dir": f"towns/{town}/", "cached": True}

    say("Finding data", 5, "Asking LANDFIRE what it has")
    fuel_layer = newest_fuel_layer()
    cfg = write_config(town, lat, lon, name, fuel_layer)

    say("Fetching terrain", 15, f"LANDFIRE {fuel_layer} and elevation")
    fetch_data.run(town, timeout_min=3.0)

    say("Building grid", 45, "Reprojecting fuels and terrain to 60 m cells")
    build_grid.run(town)

    say("Calibrating", 65, "Fitting the spread rate to this landscape")
    calibrate.run(town, gate=False)

    say("Simulating", 80, "First run of the fire")
    simulate.run(town)

    say("Exporting", 90, "Writing the map and the model")
    export.run(town, with_solver=False)
    physics.run(town)
    index_entry(town, cfg, event)

    say("Ready", 100, cfg["name"])
    return {"id": town, "data_dir": f"towns/{town}/", "cached": False}


def main(argv=None) -> None:
    p = argparse.ArgumentParser(
        prog="region.py",
        description="Build a free-play region around a point (no solver).")
    p.add_argument("--lat", type=float, required=True)
    p.add_argument("--lon", type=float, required=True)
    p.add_argument("--name", default="")
    p.add_argument("--event", default="Free play")
    args = p.parse_args(argv)
    out = build(args.lat, args.lon, args.name, event=args.event)
    print(json.dumps(out))


if __name__ == "__main__":
    main()
