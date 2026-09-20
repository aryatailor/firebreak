"""fetch_data.py - download LANDFIRE fuels/terrain + OSM buildings into pipeline/data_raw/.

Stage 1 of the Firebreak pipeline (see implementation-notes.md "## Plan").

- LANDFIRE via LFPS (the LANDFIRE Product Service): FBFM40 fuel model + elevation.
  Product codes are discovered via the landfire package's product search, never
  guessed. Chosen codes and version are printed at fetch time.
- 20-minute wall-clock timeout on the LANDFIRE request; on timeout, switch to
  ESA WorldCover 10 m (public AWS COG, windowed read by bbox) for fuel groups and
  Copernicus DEM 30 m (public AWS COG) for elevation, and record the switch in
  meta.data.
- Buildings: OSM via Overpass (retries with backoff, multiple mirrors). If Overpass
  returns < 500 buildings, fall back to built-up land-cover cells as proxy structures
  and set meta.data.buildings_proxy = true.
- Every download is cached in pipeline/data_raw/ and skipped on rerun.
- No API keys, ever.

STUB - pipeline code lands after the Phase 1 plan is approved. Only --help works.
"""
from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fetch_data.py",
        description="Download LANDFIRE fuels/terrain + OSM buildings into pipeline/data_raw/ (cached).",
    )
    p.add_argument("--force", action="store_true",
                   help="re-download even if a cached copy exists")
    p.add_argument("--timeout-min", type=float, default=20.0, metavar="MIN",
                   help="wall-clock timeout for the LANDFIRE request before falling back (default: 20)")
    return p


def main(argv=None) -> None:
    build_parser().parse_args(argv)
    sys.exit("fetch_data.py is a stub - pipeline code lands after the Phase 1 plan is approved "
             "(see implementation-notes.md).")


if __name__ == "__main__":
    main()
