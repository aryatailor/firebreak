"""ros.py - rate-of-spread model: fuel x slope x wind, directional.

Stage 3 of the Firebreak pipeline (see implementation-notes.md "## Plan").

- Relative base rate per Scott & Burgan FBFM40 fuel group: grass fastest, timber
  litter slowest. Non-burnable classes are 0 (impassable) EXCEPT urban NB91, which
  gets a small tunable "structure" rate - LANDFIRE marks towns non-burnable, and the
  fire must be able to enter Paradise the way the Camp Fire actually did
  (structure-to-structure).
- Slope factor along the direction of travel (computed per-edge from the DEM).
- Wind factor is directional: downwind spread is many times faster than upwind.
- Cleared (fuel-break) cells get a multiplier (default 0.05, i.e. 20x slower), so
  breaks delay the fire rather than hard-block it.

Imported by simulate.py; run directly with --selftest to print the factor tables.

STUB - pipeline code lands after the Phase 1 plan is approved. Only --help works.
"""
from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ros.py",
        description="Spread-rate model (fuel x slope x wind). Library module; --selftest prints factor tables.",
    )
    p.add_argument("--selftest", action="store_true",
                   help="print the fuel-group base rates and slope/wind factor tables")
    return p


def main(argv=None) -> None:
    build_parser().parse_args(argv)
    sys.exit("ros.py is a stub - pipeline code lands after the Phase 1 plan is approved "
             "(see implementation-notes.md).")


if __name__ == "__main__":
    main()
