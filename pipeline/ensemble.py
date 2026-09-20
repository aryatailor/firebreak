"""Generate deterministic ignition and wind scenarios for robust solving."""
from __future__ import annotations

import argparse
import datetime
import json
import math

import common
import numpy as np
import ros
from scipy.ndimage import distance_transform_edt
from simulate import Simulator

MIN_HOME_FRACTION = 0.03
MIN_URBAN_DISTANCE_M = 2000.0
COARSE_STRIDE = 4


def _wind_variants(cfg: dict) -> list[dict]:
    wind = cfg["wind"]
    return [
        {"speed_mph": wind["speed_mph"], "from_deg": wind["from_deg"]},
        {
            "speed_mph": wind["speed_mph"] + 10,
            "from_deg": (wind["from_deg"] + 15) % 360,
        },
        {
            "speed_mph": max(5, wind["speed_mph"] - 10),
            "from_deg": (wind["from_deg"] - 15) % 360,
        },
    ]


def _upwind_candidates(sim: Simulator, params: dict) -> np.ndarray:
    rate = ros.base_rate(sim.fuel, params)
    urban = sim.fuel == 91
    far_from_urban = distance_transform_edt(~urban) * sim.cell_m

    theta = math.radians(sim.cfg["wind"]["from_deg"])
    from_vector = np.array([-math.cos(theta), math.sin(theta)])
    rr, cc = np.indices(sim.fuel.shape)
    dr = rr - sim.town_center[0]
    dc = cc - sim.town_center[1]
    distance = np.hypot(dr, dc)
    dot = np.divide(
        dr * from_vector[0] + dc * from_vector[1],
        distance,
        out=np.zeros_like(distance, dtype=float),
        where=distance > 0,
    )
    mask = (
        (rate > 0)
        & ~urban
        & (far_from_urban >= MIN_URBAN_DISTANCE_M)
        & (dot >= math.cos(math.radians(75)))
        & ((rr % COARSE_STRIDE) == 0)
        & ((cc % COARSE_STRIDE) == 0)
    )
    return np.column_stack(np.nonzero(mask)).astype(np.int32)


def _homes_hit(sim: Simulator, arrival: np.ndarray) -> int:
    return int(
        np.count_nonzero(
            arrival[sim.b_row, sim.b_col] <= sim.cfg["horizon_min"]
        )
    )


def _ignition_lonlat(sim: Simulator, ignition: tuple[int, int]) -> dict:
    lon, lat = common.rowcol_to_lonlat(sim.geom, *ignition)
    return {"lat": lat, "lon": lon}


def _sample_ignitions(
    sim: Simulator, params: dict, candidates: np.ndarray
) -> list[tuple[int, int]]:
    if not len(candidates):
        return []
    historical = np.asarray(sim.ignition, dtype=np.int32)
    remaining = np.ones(len(candidates), dtype=bool)
    min_distance = np.full(len(candidates), np.inf)
    selected: list[tuple[int, int]] = []
    required_hits = MIN_HOME_FRACTION * sim.b_row.size
    first = int(np.argmin(np.sum((candidates - historical) ** 2, axis=1)))
    validation_winds = _wind_variants(sim.cfg)

    while remaining.any() and len(selected) < 11:
        if not selected:
            candidate_id = first
        else:
            scores = np.where(remaining, min_distance, -1.0)
            candidate_id = int(np.argmax(scores))
        if not remaining[candidate_id]:
            break
        remaining[candidate_id] = False
        point = tuple(int(v) for v in candidates[candidate_id])
        hits = []
        for wind in validation_winds:
            arrival = sim.arrival(params, wind=wind, ignition=point)
            hits.append(_homes_hit(sim, arrival))
        if min(hits) >= required_hits:
            selected.append(point)
        distance = np.sum((candidates - candidates[candidate_id]) ** 2, axis=1)
        min_distance = np.minimum(min_distance, distance)
    return selected


def _scenario_list(
    sim: Simulator,
    params: dict,
    winds: list[dict],
    ignitions: list[tuple[int, int]],
) -> list[dict]:
    choices: list[tuple[int, int]] = []
    for ignition_id in range(len(ignitions)):
        choices.append((ignition_id, 0))
    for ignition_id in range(1, len(ignitions)):
        choices.append((ignition_id, 1 if ignition_id % 2 == 1 else 2))
    choices.extend([(0, 1), (0, 2)])

    scenarios = []
    weight = 1.0 / len(choices)
    for ignition_id, wind_id in choices:
        ignition = ignitions[ignition_id]
        arrival = sim.arrival(
            params,
            wind=winds[wind_id],
            ignition=ignition,
        )
        stats = sim.stats(arrival)
        scenarios.append(
            {
                "ignition_rc": [int(ignition[0]), int(ignition[1])],
                "ignition_lonlat": _ignition_lonlat(sim, ignition),
                "wind": winds[wind_id],
                "weight": weight,
                "baseline_homes_hit": stats["homes_hit"],
                "minutes_to_first_home": stats["minutes_to_first_home"],
            }
        )
    return scenarios


def run(town: str) -> None:
    cfg = common.load_town(town)
    sim = Simulator(town)
    params_path = common.out_dir(town) / "config.json"
    params = json.loads(params_path.read_text(encoding="utf-8"))["params"]
    candidates = _upwind_candidates(sim, params)
    sampled = _sample_ignitions(sim, params, candidates)
    ignitions = [sim.ignition, *sampled]
    winds = _wind_variants(cfg)
    scenarios = _scenario_list(sim, params, winds, ignitions)
    data = {
        "winds": winds,
        "ignitions": [
            {
                "rc": [int(ignition[0]), int(ignition[1])],
                "lat": _ignition_lonlat(sim, ignition)["lat"],
                "lon": _ignition_lonlat(sim, ignition)["lon"],
                "historical": index == 0,
            }
            for index, ignition in enumerate(ignitions)
        ],
        "scenarios": scenarios,
        "generated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    path = common.out_dir(town) / "ensemble.json"
    path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    print(
        f"ensemble: {town}, {len(candidates)} eligible coarse cells, "
        f"{len(sampled)} sampled ignitions, {len(scenarios)} scenarios"
    )
    if len(sampled) < 11:
        print("  fewer than 11 valid sampled ignitions were available")
    print(f"ensemble OK -> {path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ensemble.py",
        description="Generate deterministic robust fire scenarios.",
    )
    parser.add_argument("--town", default="paradise")
    return parser


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    common.run_stage("ensemble", args.town, lambda: run(args.town))


if __name__ == "__main__":
    main()
