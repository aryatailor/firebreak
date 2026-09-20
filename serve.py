"""serve.py - the demo server: static page plus the free-play API.

    python serve.py            # http://localhost:8000
    .\\demo.ps1                 # same, and opens Chrome

Everything already exported works from a plain static server; this adds the
endpoints in CONTRACT.md ("The local API"):

    GET  /api/health                  is the API here, and is LANDFIRE reachable
    POST /api/region                  build a new free-play region around a point
    GET  /api/region/{id}/status      progress for that build
    POST /api/simulate                run the Python fire model (JS sim fallback)

Region builds run in a background thread, one at a time, and report progress
into a status dict the frontend polls. Nothing here mutates the curated towns.
"""
from __future__ import annotations

import json
import sys
import threading
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parent
WEB = REPO / "web"
sys.path.insert(0, str(REPO / "pipeline"))

import base64                                                    # noqa: E402
import numpy as np                                               # noqa: E402
import uvicorn                                                   # noqa: E402
from fastapi import FastAPI, HTTPException                       # noqa: E402
from fastapi.responses import JSONResponse                       # noqa: E402
from fastapi.staticfiles import StaticFiles                      # noqa: E402
from pydantic import BaseModel                                   # noqa: E402

import common                                                    # noqa: E402
import region as region_mod                                      # noqa: E402
from simulate import Simulator                                   # noqa: E402

app = FastAPI(title="Firebreak", docs_url=None, redoc_url=None)

_jobs: dict[str, dict] = {}          # region id -> status dict
_jobs_lock = threading.Lock()
_build_lock = threading.Lock()       # one region build at a time
_sims: dict[str, Simulator] = {}     # town -> loaded grid, reused across calls
_sims_lock = threading.Lock()


def _towns() -> list[dict]:
    path = WEB / "towns" / "index.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def _simulator(town: str) -> Simulator:
    with _sims_lock:
        if town not in _sims:
            _sims[town] = Simulator(town)
        return _sims[town]


# --- health ---------------------------------------------------------------------

@app.get("/api/health")
def health() -> dict:
    reachable = False
    try:
        import requests
        r = requests.get(f"{region_mod.fetch_data.LFPS_API}/products", timeout=5,
                         headers=region_mod.fetch_data.HTTP_HEADERS)
        reachable = r.status_code == 200
    except Exception:
        reachable = False
    return {"ok": True, "version": "1", "landfire_reachable": reachable,
            "towns": [{"id": t.get("id"), "name": t.get("name"),
                       "story": bool(t.get("story"))} for t in _towns()]}


# --- region build ---------------------------------------------------------------

class RegionRequest(BaseModel):
    lat: float
    lon: float
    name: str | None = None


def _set(job_id: str, **kw) -> None:
    with _jobs_lock:
        _jobs.setdefault(job_id, {"id": job_id}).update(kw)


def _build_worker(job_id: str, lat: float, lon: float, name: str) -> None:
    def progress(stage, pct, message):
        _set(job_id, stage=stage, pct=pct, message=message)
    try:
        with _build_lock:
            out = region_mod.build(lat, lon, name, progress=progress)
        _set(job_id, status="ready", pct=100, stage="Ready",
             data_dir=out["data_dir"], cached=out["cached"])
    except BaseException as e:                       # noqa: BLE001 - report it all
        traceback.print_exc()
        common.log_event("api/region", job_id, f"FAILED: {type(e).__name__}: {e}")
        _set(job_id, status="error", stage="Failed",
             error=f"{type(e).__name__}: {e}")


@app.post("/api/region")
def build_region(req: RegionRequest) -> dict:
    if not (-90 <= req.lat <= 90 and -180 <= req.lon <= 180):
        raise HTTPException(400, "lat/lon out of range")
    name = (req.name or "").strip()
    job_id = region_mod.slugify(name, req.lat, req.lon)
    web = WEB / "towns" / job_id

    if (web / "meta.json").exists() and (web / "physics.json").exists():
        if not any(t.get("id") == job_id for t in _towns()):
            cfg_path = common.TOWNS / f"{job_id}.json"
            if cfg_path.exists():
                region_mod.index_entry(
                    job_id, json.loads(cfg_path.read_text(encoding="utf-8")))
        _set(job_id, status="ready", pct=100, stage="Ready",
             data_dir=f"towns/{job_id}/", cached=True)
        return {"id": job_id, "data_dir": f"towns/{job_id}/",
                "status": "ready", "cached": True}

    with _jobs_lock:
        running = _jobs.get(job_id, {}).get("status") == "building"
    if not running:
        _set(job_id, status="building", pct=0, stage="Queued",
             message="Waiting for a build slot", data_dir=f"towns/{job_id}/")
        threading.Thread(target=_build_worker, daemon=True,
                         args=(job_id, req.lat, req.lon, name)).start()
    return {"id": job_id, "data_dir": f"towns/{job_id}/",
            "status": "building", "cached": False}


@app.get("/api/region/{region_id}/status")
def region_status(region_id: str) -> dict:
    with _jobs_lock:
        job = dict(_jobs.get(region_id, {}))
    if job:
        return job
    web = WEB / "towns" / region_id
    if (web / "meta.json").exists():
        return {"id": region_id, "status": "ready", "pct": 100, "stage": "Ready",
                "data_dir": f"towns/{region_id}/"}
    raise HTTPException(404, f"no region '{region_id}'")


# --- simulate -------------------------------------------------------------------

class SimRequest(BaseModel):
    town: str = "paradise"
    ignition_rc: list[int] | None = None
    wind: dict | None = None
    breaks: list[list[int]] | None = None


@app.post("/api/simulate")
def simulate_api(req: SimRequest) -> dict:
    if not (common.TOWNS / f"{req.town}.json").exists():
        raise HTTPException(404, f"no town '{req.town}'")
    sim = _simulator(req.town)
    params = json.loads(
        (common.out_dir(req.town) / "config.json").read_text(encoding="utf-8")
    )["params"]
    horizon = sim.cfg["horizon_min"]

    if req.ignition_rc:
        r, c = req.ignition_rc
        if not (0 <= r < sim.rows and 0 <= c < sim.cols):
            raise HTTPException(400, "ignition_rc outside the grid")
        sim.ignition = (int(r), int(c))

    mask = None
    if req.breaks:
        mask = np.zeros(sim.fuel.shape, dtype=bool)
        for rc in req.breaks:
            r, c = rc
            if 0 <= r < sim.rows and 0 <= c < sim.cols:
                mask[r, c] = True

    arr = sim.arrival(params, break_mask=mask, wind=req.wind)
    buckets = np.where(np.isfinite(arr) & (arr <= horizon),
                       np.round(arr / 5.0), 255).astype(np.uint8)
    return {"arrival_b64": base64.b64encode(buckets.tobytes()).decode("ascii"),
            "bucket_min": 5, "horizon_min": horizon,
            "rows": sim.rows, "cols": sim.cols,
            "stats": sim.stats(arr)}


@app.exception_handler(Exception)
def unhandled(_request, exc: Exception) -> JSONResponse:
    traceback.print_exception(type(exc), exc, exc.__traceback__)
    return JSONResponse(status_code=500,
                        content={"error": f"{type(exc).__name__}: {exc}"})


# Static LAST: every /api route above is matched first, everything else is the page.
app.mount("/", StaticFiles(directory=str(WEB), html=True), name="web")


def main() -> None:
    import argparse
    p = argparse.ArgumentParser(prog="serve.py",
                                description="Serve web/ plus the free-play API.")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--host", default="127.0.0.1")
    args = p.parse_args()
    towns = ", ".join(t.get("id", "?") for t in _towns()) or "none"
    print(f"Firebreak on http://localhost:{args.port}  (towns: {towns})")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
