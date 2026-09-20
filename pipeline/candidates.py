"""candidates.py - generate candidate fuel-break strips for the solver.

Stage 6 of the Firebreak pipeline (see implementation-notes.md "## Plan").

- 2-cell-wide contiguous strips, 1-2 km long, in 4 orientations, plus arcs that
  follow the town edge at fixed offsets.
- Candidates may only occupy burnable WILDLAND cells - never urban, water,
  agriculture, or other non-burnable classes (you don't masticate Main Street).
- Pruned to the baseline burn corridor (cells the $0 fire actually reaches) dilated
  by 500 m - breaks far from the fire's path are wasted evaluations.
- Each candidate carries its cell list and dollar cost from the per-fuel-group cost
  table in meta.cost_per_acre.

STUB - pipeline code lands after the Phase 1 plan is approved. Only --help works.
"""
from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="candidates.py",
        description="Generate candidate fuel-break strips (wildland-only, pruned to the burn corridor).",
    )
    p.add_argument("--spacing-m", type=float, default=480.0, metavar="M",
                   help="lattice spacing between candidate centers (default: 480)")
    return p


def main(argv=None) -> None:
    build_parser().parse_args(argv)
    sys.exit("candidates.py is a stub - pipeline code lands after the Phase 1 plan is approved "
             "(see implementation-notes.md).")


if __name__ == "__main__":
    main()
