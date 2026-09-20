from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parents[1]))

import common
import ensemble
import restore_intermediates
import robust
import ros
from simulate import Simulator

ROOT = Path(__file__).parents[2]


def _params(town: str) -> dict:
    return json.loads(
        (ROOT / "pipeline" / "out" / town / "config.json").read_text()
    )["params"]


def test_restore_round_trip_rates():
    restore_intermediates.restore("paradise")
    physics = json.loads((ROOT / "web" / "data" / "physics.json").read_text())
    grid = physics["grid"]
    classes = np.frombuffer(
        base64.b64decode(physics["fuel_class_b64"]), dtype=np.uint8
    ).reshape(grid["rows"], grid["cols"])
    fuel = np.load(ROOT / "pipeline" / "out" / "paradise" / "fuel.npy")
    rates = np.asarray([0.0] + [physics["rates"][str(i)] for i in range(1, 8)])
    expected = rates[classes]
    np.testing.assert_allclose(ros.base_rate(fuel, _params("paradise")), expected)


def test_historical_ignition_override_matches_default():
    sim = Simulator("paradise")
    params = _params("paradise")
    default = sim.arrival(params)
    overridden = sim.arrival(params, ignition=sim.ignition)
    np.testing.assert_array_equal(default, overridden)


def test_paradise_ensemble_is_deterministic():
    ensemble.run("paradise")
    path = ROOT / "pipeline" / "out" / "paradise" / "ensemble.json"
    first = json.loads(path.read_text())
    ensemble.run("paradise")
    second = json.loads(path.read_text())
    assert len(first["scenarios"]) == 25
    assert all(
        scenario["baseline_homes_hit"] >= 0.03 * 11000
        for scenario in first["scenarios"]
    )
    first.pop("generated")
    second.pop("generated")
    assert first == second


def test_synthetic_upwind_break_has_positive_objective(tmp_path, monkeypatch):
    cfg = {
        "name": "Synthetic",
        "story": "test",
        "bounds": {"west": -1, "south": -1, "east": 1, "north": 1},
        "ignition": {"lat": 0, "lon": 0},
        "wind": {"speed_mph": 5, "from_deg": 90},
        "town_center": {"lat": 0, "lon": 0},
        "horizon_min": 20,
        "budgets": [1],
    }
    monkeypatch.setattr(common, "load_town", lambda _: cfg)
    monkeypatch.setattr(common, "out_dir", lambda _: tmp_path)
    fuel = np.full((20, 20), 101, dtype=np.int16)
    np.save(tmp_path / "fuel.npy", fuel)
    np.save(tmp_path / "elev.npy", np.zeros((20, 20), dtype=np.float32))
    np.savez(
        tmp_path / "buildings.npz",
        row=np.array([10], dtype=np.int32),
        col=np.array([18], dtype=np.int32),
        lat=np.array([0.0]),
        lon=np.array([0.0]),
    )
    (tmp_path / "grid_meta.json").write_text(
        json.dumps(
            {
                "rows": 20,
                "cols": 20,
                "cell_m": 60.0,
                "cell_merc": 60.0,
                "bounds_3857": {"west_m": 0.0, "north_m": 1200.0},
            }
        )
    )
    sim = Simulator("synthetic")
    sim.ignition = (10, 1)
    params = {"scale": 60.0, "k_w": 0.0, "structure_rate": 0.08,
              "break_mult": 0.05}
    monkeypatch.setattr(robust, "_WORKER_SIM", sim)
    monkeypatch.setattr(robust, "_WORKER_PARAMS", params)
    monkeypatch.setattr(
        robust,
        "_WORKER_CELLS",
        [np.array([r * 20 + 10 for r in range(20)], dtype=np.int64)],
    )
    monkeypatch.setattr(
        robust,
        "_WORKER_SCENARIOS",
        [
            {
                "ignition_rc": [10, 1],
                "wind": cfg["wind"],
                "weight": 1.0,
                "baseline_homes_hit": 1,
            }
        ],
    )
    _, saved = robust.candidate_saved(0)
    assert saved > 0
