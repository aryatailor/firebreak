# How we built it

1. One repo, two people, one contract: CONTRACT.md pins the JSON the pipeline writes and the page reads — we built against a fake-data mock all night without ever blocking each other.
2. Data: LANDFIRE fuels + elevation. The python client's endpoint turned out to be retired, so we wrote a direct client for the live LFPS REST API mid-hackathon; product codes are validated against the live product table, never guessed.
3. Buildings came from OSM — then got swapped for a density proxy, because 2026 OSM maps post-fire Paradise and the story is November 8, 2018.
4. Everything is reprojected once onto a 60 m EPSG:3857 grid, so the fire canvas aligns with Leaflet tiles with zero client-side math.
5. The fire is Dijkstra over a directed 8-neighbor graph — edge speed = fuel class × slope × wind — 152k cells, 1.2M edges, 0.12 seconds per full run.
6. One free speed parameter, calibrated against the historical outcome; a hard gate (a hand-placed test ring must cut homes hit ≥ 30%) proves the fire responds to breaks before the solver may run.
7. Candidates: ~4,800 two-cell-wide strips and town-hugging arcs, priced at rough mechanical-treatment cost per acre by fuel type.
8. The solver is lazy greedy (CELF) on homes-saved-per-dollar, re-running the fire ~1,600 times, checkpointing after every accepted break.
9. Budget answers are greedy prefixes, and steps.json ships the fire at all 70 steps — that's what makes the budget slider continuous instead of five stops.
10. The demo is fully offline: vendored Leaflet, a DEM-hillshade basemap PNG, every byte of data committed as static files.
11. A second town is one JSON config — bounds, ignition, wind, fuel vintage — plus `python pipeline/run_all.py --town altadena`.
12. Raw data to running demo: about 40 minutes, most of it the solver arguing with 1,600 simulated fires.
