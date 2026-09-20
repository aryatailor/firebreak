"""Shared multiprocessing helpers for robust scenario evaluation."""
from __future__ import annotations

import multiprocessing as mp
import os

import numpy as np
from simulate import Simulator

_WORKER_SIM: Simulator | None = None
_WORKER_PARAMS: dict | None = None
_WORKER_SCENARIOS: list[dict] | None = None
_WORKER_CELLS: list[np.ndarray] | None = None


def _init_worker(
    town: str,
    params: dict,
    scenarios: list[dict],
    cells: list[np.ndarray] | None,
) -> None:
    global _WORKER_SIM, _WORKER_PARAMS, _WORKER_SCENARIOS, _WORKER_CELLS
    _WORKER_SIM = Simulator(town)
    _WORKER_PARAMS = params
    _WORKER_SCENARIOS = scenarios
    _WORKER_CELLS = cells


def make_pool(
    town: str,
    params: dict,
    scenarios: list[dict],
    cells: list[np.ndarray] | None = None,
    processes: int | None = None,
):
    if processes is None:
        processes = min(8, os.cpu_count() or 1)
    context = mp.get_context("fork")
    return context.Pool(
        processes=processes,
        initializer=_init_worker,
        initargs=(town, params, scenarios, cells),
    )


def candidate_saved(candidate_id: int) -> tuple[int, float]:
    if _WORKER_SIM is None or _WORKER_PARAMS is None:
        raise RuntimeError("robust worker is not initialized")
    if _WORKER_CELLS is None or _WORKER_SCENARIOS is None:
        raise RuntimeError("candidate worker data is not initialized")
    mask = np.zeros(_WORKER_SIM.fuel.shape, dtype=bool)
    mask.flat[_WORKER_CELLS[candidate_id]] = True
    saved = []
    for scenario in _WORKER_SCENARIOS:
        arrival = _WORKER_SIM.arrival(
            _WORKER_PARAMS,
            break_mask=mask,
            wind=scenario["wind"],
            ignition=tuple(scenario["ignition_rc"]),
        )
        hit = int(
            np.count_nonzero(
                arrival[_WORKER_SIM.b_row, _WORKER_SIM.b_col]
                <= _WORKER_SIM.cfg["horizon_min"]
            )
        )
        saved.append(scenario["baseline_homes_hit"] - hit)
    weights = np.asarray([s["weight"] for s in _WORKER_SCENARIOS])
    return candidate_id, float(np.dot(weights, saved))


def scenario_hit(task: tuple[int, np.ndarray]) -> tuple[int, int]:
    if _WORKER_SIM is None or _WORKER_PARAMS is None:
        raise RuntimeError("robust worker is not initialized")
    if _WORKER_SCENARIOS is None:
        raise RuntimeError("scenario worker data is not initialized")
    scenario_id, mask = task
    scenario = _WORKER_SCENARIOS[scenario_id]
    arrival = _WORKER_SIM.arrival(
        _WORKER_PARAMS,
        break_mask=mask,
        wind=scenario["wind"],
        ignition=tuple(scenario["ignition_rc"]),
    )
    hit = int(
        np.count_nonzero(
            arrival[_WORKER_SIM.b_row, _WORKER_SIM.b_col]
            <= _WORKER_SIM.cfg["horizon_min"]
        )
    )
    return scenario_id, hit
