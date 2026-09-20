"""Restore solver inputs from the committed browser physics export.

The committed web export contains the quantised model that shipped to users.
This stage reconstructs the NumPy arrays needed by the Python solver without
contacting LANDFIRE or Overpass.
"""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
from pathlib import Path

import common
import numpy as np

CLASS_TO_CODE = {
    0: 98,
    1: common.FUEL_GROUPS["grass"][0],
    2: common.FUEL_GROUPS["grass_shrub"][0],
    3: common.FUEL_GROUPS["shrub"][0],
    4: common.FUEL_GROUPS["slash"][0],
    5: common.FUEL_GROUPS["timber_understory"][0],
    6: common.FUEL_GROUPS["timber_litter"][0],
    7: 91,
}


def _read_web_json(cfg: dict, name: str) -> dict:
    path = common.web_dir(cfg) / name
    if not path.exists():
        raise FileNotFoundError(f"{path} missing from the committed web export")
    return json.loads(path.read_text(encoding="utf-8"))


def _restore_from_git_parent(town: str, name: str, out: Path) -> None:
    path = f"pipeline/out/{town}/{name}"
    result = subprocess.run(
        ["git", "show", f"HEAD~1:{path}"],
        check=True,
        capture_output=True,
        text=True,
    )
    (out / name).write_text(result.stdout, encoding="utf-8")


def _building_arrays(cfg: dict, physics: dict, meta: dict) -> dict[str, np.ndarray]:
    web = common.web_dir(cfg)
    geojson = json.loads((web / "buildings.geojson").read_text(encoding="utf-8"))
    homes = physics["homes_rc"]
    features = geojson.get("features", [])
    if len(features) != len(homes):
        raise ValueError(
            f"physics homes_rc has {len(homes)} entries but buildings.geojson "
            f"has {len(features)} features"
        )

    ordered = True
    coordinates: list[tuple[float, float]] = []
    for rc, feature in zip(homes, features):
        props = feature.get("properties", {})
        if props.get("row") != rc[0] or props.get("col") != rc[1]:
            ordered = False
            break
        coords = feature.get("geometry", {}).get("coordinates")
        if not isinstance(coords, list) or len(coords) < 2:
            ordered = False
            break
        coordinates.append((float(coords[1]), float(coords[0])))

    if not ordered:
        geom = {
            "west_m": meta["bounds_3857"]["west_m"],
            "north_m": meta["bounds_3857"]["north_m"],
            "cell_merc": meta["cell_merc"],
        }
        coordinates = [
            common.rowcol_to_lonlat(geom, int(r), int(c))[::-1]
            for r, c in homes
        ]

    lat, lon = zip(*coordinates)
    return {
        "row": np.asarray([r for r, _ in homes], dtype=np.int32),
        "col": np.asarray([c for _, c in homes], dtype=np.int32),
        "lat": np.asarray(lat, dtype=np.float64),
        "lon": np.asarray(lon, dtype=np.float64),
    }


def restore(town: str) -> None:
    cfg = common.load_town(town)
    out = common.out_dir(town)
    physics = _read_web_json(cfg, "physics.json")
    meta = _read_web_json(cfg, "meta.json")
    grid = physics["grid"]
    rows, cols = int(grid["rows"]), int(grid["cols"])

    fuel_class = np.frombuffer(
        base64.b64decode(physics["fuel_class_b64"]), dtype=np.uint8
    )
    if fuel_class.size != rows * cols:
        raise ValueError("physics fuel_class_b64 length does not match the grid")
    fuel_class = fuel_class.reshape(rows, cols)
    unknown = sorted(set(np.unique(fuel_class).tolist()) - set(CLASS_TO_CODE))
    if unknown:
        raise ValueError(f"unknown browser fuel classes: {unknown}")
    fuel = np.zeros_like(fuel_class, dtype=np.int16)
    for class_id, code in CLASS_TO_CODE.items():
        fuel[fuel_class == class_id] = code

    elev = np.frombuffer(
        base64.b64decode(physics["elev_m_b64"]), dtype="<i2"
    )
    if elev.size != rows * cols:
        raise ValueError("physics elev_m_b64 length does not match the grid")
    elev = elev.reshape(rows, cols).astype(np.float32)

    _restore_from_git_parent(town, "config.json", out)
    _restore_from_git_parent(town, "grid_meta.json", out)
    meta = json.loads((out / "grid_meta.json").read_text(encoding="utf-8"))
    buildings = _building_arrays(cfg, physics, meta)

    np.save(out / "fuel.npy", fuel)
    np.save(out / "elev.npy", elev)
    np.savez_compressed(out / "buildings.npz", **buildings)
    print(
        f"restore: {town} {rows}x{cols}, fuel classes "
        f"{sorted(np.unique(fuel_class).tolist())}, homes {len(buildings['row'])}"
    )
    print(f"  restored {out / 'fuel.npy'}")
    print(f"  restored {out / 'elev.npy'}")
    print(f"  restored {out / 'buildings.npz'}")
    print("  restored config.json and grid_meta.json from HEAD~1")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="restore_intermediates.py",
        description="Restore Python solver intermediates from committed web data.",
    )
    parser.add_argument("--town", default="paradise")
    return parser


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    common.run_stage("restore", args.town, lambda: restore(args.town))


if __name__ == "__main__":
    main()
