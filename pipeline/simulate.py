"""simulate.py - minimum-travel-time fire spread over the grid.

Stage 4 of the Firebreak pipeline (see implementation-notes.md "## Plan").

- Directed 8-neighbor graph over the grid; edge weight i->j = ground distance /
  ROS(i->j) with ROS from ros.py (directed, because wind and slope are asymmetric).
- Single-source scipy.sparse.csgraph.dijkstra from the ignition cell returns fire
  arrival time in minutes for every cell.
- Must run in < 0.5 s on the full grid, or the grid gets downsampled - the solver
  calls this hundreds of times.

STUB - pipeline code lands after the Phase 1 plan is approved. Only --help works.
"""
from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="simulate.py",
        description="Minimum-travel-time fire simulation (scipy dijkstra); writes/returns arrival minutes per cell.",
    )
    p.add_argument("--bench", action="store_true",
                   help="time one full-grid simulation and print it")
    return p


def main(argv=None) -> None:
    build_parser().parse_args(argv)
    sys.exit("simulate.py is a stub - pipeline code lands after the Phase 1 plan is approved "
             "(see implementation-notes.md).")


if __name__ == "__main__":
    main()
