# Firebreak

**Fuel-break optimizer on real terrain: where to cut to stop a wildfire, per dollar.**
Built at HackMIT 2026.

One town — Paradise, CA (the 2018 Camp Fire). Press play: a fire starts at Pulga and runs
into Paradise over real terrain and real LANDFIRE fuel data. Move the budget slider: the
solver's fuel breaks appear, the fire re-runs and stalls short of town. The panel shows
buildings hit before/after, dollars spent, and a houses-saved-per-dollar curve.

> Planscape does all of California in hours for planners; this does one town in seconds
> so a fire-safe council can argue over it in a meeting.

## Quick start — frontend (mock data)

```
python -m http.server -d web 8000
# open http://localhost:8000
```

The page loads `web/mock/` — fake data that exactly matches [CONTRACT.md](CONTRACT.md).
When the pipeline has produced `web/data/`, flip one constant in `web/app.js`
(`DATA_DIR`: `'mock'` → `'data'`).

## Quick start — pipeline

```
pip install -r requirements.txt --only-binary=:all:
python pipeline/run_all.py --quick     # cached downloads + coarse grid
python -m http.server -d web 8000
```

## Repo map

| Path | What | Owner |
|---|---|---|
| `pipeline/` | data fetch → grid → fire sim → solver → export | Zach |
| `web/` | static page: Leaflet map + canvas fire + panel | Arya |
| `web/mock/` | fake data matching CONTRACT.md — build against this | generated |
| `web/data/` | real pipeline output, committed, ≤ 5 MB total | Zach |
| `web/vendor/leaflet/` | Leaflet 1.9.4 vendored — no CDN anywhere | — |
| [CONTRACT.md](CONTRACT.md) | the data interface between pipeline and page | both — change = message first |
| [DESIGN.md](DESIGN.md) | the screen, exactly | Arya |
| [TASKS.md](TASKS.md) | who does what, when | both |
| [implementation-notes.md](implementation-notes.md) | Plan / Decisions / Deviations | Zach |

Start with CONTRACT.md — everything else hangs off it.

## Constraints that shaped this

- 24-hour hackathon; hacking stops Sunday 11:00 ET.
- Demo laptop has unreliable wifi → everything works offline (vendored Leaflet, an
  offline `basemap.png`, all data committed as static files, no API keys anywhere).
- No frameworks, no build step. `python -m http.server` is the whole deployment.
