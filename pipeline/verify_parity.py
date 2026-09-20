"""verify_parity.py - reimplement the fire model from physics.json alone.

This deliberately imports NOTHING from the pipeline. It reads a town's
physics.json exactly as the browser would, follows the algorithm as written in
CONTRACT.md, and checks the result against parity.json. If this script passes,
the spec in CONTRACT.md is complete and correct enough to reimplement - which is
the whole promise made to the frontend.

Usage: python pipeline/verify_parity.py web/data web/towns/altadena
"""
from __future__ import annotations

import base64
import heapq
import json
import math
import sys
from pathlib import Path

# neighbour order fixed by CONTRACT.md (dr, dc); dr +1 = south, dc +1 = east
OFFSETS = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def dijkstra(phys: dict, cleared: set[int] | None = None) -> list[float]:
    rows = phys["grid"]["rows"]
    cols = phys["grid"]["cols"]
    cell_m = phys["grid"]["cell_m"]
    n = rows * cols

    fuel = base64.b64decode(phys["fuel_class_b64"])
    elev_raw = base64.b64decode(phys["elev_m_b64"])
    elev = [int.from_bytes(elev_raw[i * 2:i * 2 + 2], "little", signed=True)
            for i in range(n)]

    rates = {int(k): float(v) for k, v in phys["rates"].items()}
    base = [rates.get(fuel[i], 0.0) for i in range(n)]
    if cleared:
        mult = phys["break_mult"]
        for i in cleared:
            base[i] *= mult

    k_w = phys["k_w"]
    scale = phys["scale_m_per_min"]
    v_mph = phys["wind"]["speed_mph"]
    downwind = math.radians((phys["wind"]["from_deg"] + 180.0) % 360.0)

    edge = []
    for dr, dc in OFFSETS:
        dist = cell_m * math.hypot(dr, dc)
        bearing = math.atan2(dc, -dr)
        f_wind = math.exp(k_w * (v_mph / 10.0) * math.cos(bearing - downwind))
        edge.append((dr, dc, dist, f_wind))

    INF = float("inf")
    dist_min = [INF] * n
    r0, c0 = phys["ignition_rc"]
    src = r0 * cols + c0
    dist_min[src] = 0.0
    heap = [(0.0, src)]
    done = bytearray(n)

    while heap:
        d, i = heapq.heappop(heap)
        if done[i]:
            continue
        done[i] = 1
        bi = base[i]
        if bi <= 0.0:
            continue
        ri, ci = divmod(i, cols)
        for dr, dc, dist, f_wind in edge:
            rj, cj = ri + dr, ci + dc
            if rj < 0 or rj >= rows or cj < 0 or cj >= cols:
                continue
            j = rj * cols + cj
            if done[j]:
                continue
            b = base[j] if base[j] < bi else bi
            if b <= 0.0:
                continue
            theta = math.degrees(math.atan((elev[j] - elev[i]) / dist))
            f_slope = min(max(2.0 ** (theta / 10.0), 0.5), 8.0)
            nd = d + dist / (b * f_slope * f_wind * scale)
            if nd < dist_min[j]:
                dist_min[j] = nd
                heapq.heappush(heap, (nd, j))
    return dist_min


def bucketise(dist_min: list[float], horizon: float, bucket: float) -> bytes:
    return bytes(round(d / bucket) if d <= horizon else 255 for d in dist_min)


def compare(name: str, mine: bytes, theirs: bytes) -> bool:
    if len(mine) != len(theirs):
        print(f"  {name}: LENGTH MISMATCH {len(mine)} vs {len(theirs)}")
        return False
    n = len(mine)
    exact = sum(1 for a, b in zip(mine, theirs) if a == b)
    within1 = sum(1 for a, b in zip(mine, theirs) if abs(a - b) <= 1)
    worst = max(abs(a - b) for a, b in zip(mine, theirs)) if n else 0
    ok = within1 / n >= 0.99
    print(f"  {name}: {exact / n:.4%} exact, {within1 / n:.4%} within 1 bucket, "
          f"worst {worst} buckets -> {'PASS' if ok else 'FAIL'}")
    return ok


def check(data_dir: Path) -> bool:
    phys = json.loads((data_dir / "physics.json").read_text(encoding="utf-8"))
    par = json.loads((data_dir / "parity.json").read_text(encoding="utf-8"))
    horizon, bucket = par["horizon_min"], par["bucket_min"]
    cols = phys["grid"]["cols"]
    print(f"{data_dir}: {phys['grid']['rows']}x{cols}, "
          f"wind {phys['wind']['speed_mph']} mph from {phys['wind']['from_deg']}, "
          f"scale {phys['scale_m_per_min']}, k_w {phys['k_w']}")

    ok = compare("baseline", bucketise(dijkstra(phys), horizon, bucket),
                 base64.b64decode(par["baseline_b64"]))
    cleared = {r * cols + c for r, c in par["break_cells"]}
    ok &= compare("with break",
                  bucketise(dijkstra(phys, cleared), horizon, bucket),
                  base64.b64decode(par["break_b64"]))
    return ok


def main(argv=None) -> None:
    dirs = [Path(a) for a in (argv or sys.argv[1:])]
    if not dirs:
        dirs = [Path("web/data")]
    all_ok = True
    for d in dirs:
        all_ok &= check(d)
    print("parity: ALL PASS" if all_ok else "parity: FAILURES ABOVE")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
