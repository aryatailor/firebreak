"""build_grid.py - reproject every input onto one EPSG:3857 grid.

Stage 2 of the Firebreak pipeline (see implementation-notes.md "## Plan").

- Single target grid in EPSG:3857 (Web Mercator, matches Leaflet tiles) with a cell
  edge of 60 m ground distance (nominal Mercator size adjusted by 1/cos(lat)).
- Nearest-neighbor resampling for categorical layers (fuel model), bilinear for
  elevation.
- Writes alignment_check.png (fuel colors + building dots) so roads/canyons can be
  eyeballed against reality.
- Computes basemap.png here: hillshade from elevation, blended with fuel-group
  colors - the offline base layer per CONTRACT.md.

STUB - pipeline code lands after the Phase 1 plan is approved. Only --help works.
"""
from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="build_grid.py",
        description="Reproject fuels/terrain/buildings onto one EPSG:3857 grid; write alignment_check.png.",
    )
    p.add_argument("--cell-m", type=float, default=60.0, metavar="M",
                   help="cell edge in ground meters (default: 60)")
    p.add_argument("--quick", action="store_true",
                   help="coarser grid (2x cell size) for fast end-to-end runs")
    return p


def main(argv=None) -> None:
    build_parser().parse_args(argv)
    sys.exit("build_grid.py is a stub - pipeline code lands after the Phase 1 plan is approved "
             "(see implementation-notes.md).")


if __name__ == "__main__":
    main()
