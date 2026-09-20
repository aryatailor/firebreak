"""solve.py - pick fuel breaks: lazy greedy (CELF), buildings saved per dollar.

Stage 7 of the Firebreak pipeline (see implementation-notes.md "## Plan").

- Objective: buildings NOT reached within the horizon. Lazy greedy (CELF) picks the
  candidate with the best marginal saved-per-dollar, re-simulating (one dijkstra) per
  evaluation; stale heap entries are re-evaluated only when they surface.
- Checkpoints the incumbent solution to disk after every accepted break, so Ctrl-C
  still leaves usable output.
- The exported solution at each budget in meta.budgets is the greedy prefix that fits
  that budget. --budget N solves/exports any single budget for live use.
- Progress bar with ETA (tqdm).

STUB - pipeline code lands after the Phase 1 plan is approved. Only --help works.
"""
from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="solve.py",
        description="Lazy greedy (CELF) fuel-break selection maximizing buildings saved per dollar.",
    )
    p.add_argument("--budget", type=int, default=None, metavar="N",
                   help="solve for a single budget in dollars (default: all budgets in meta)")
    p.add_argument("--resume", action="store_true",
                   help="resume from the last checkpoint on disk")
    return p


def main(argv=None) -> None:
    build_parser().parse_args(argv)
    sys.exit("solve.py is a stub - pipeline code lands after the Phase 1 plan is approved "
             "(see implementation-notes.md).")


if __name__ == "__main__":
    main()
