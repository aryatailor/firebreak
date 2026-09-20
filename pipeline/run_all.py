"""run_all.py - the whole pipeline chain: fetch -> grid -> sim -> calibrate ->
candidates -> solve -> export.

Stage 0/orchestrator of the Firebreak pipeline (see implementation-notes.md "## Plan").

- Runs every stage in order with a try/except at each stage boundary that prints the
  stage name, the input it was given, and the exception, then appends to
  pipeline/run_log.md. No silent failures, no bare excepts.
- --quick uses cached downloads and a coarser grid for a fast end-to-end smoke run.

Final verification once Phase 1 is implemented:
    python pipeline/run_all.py --quick
    python -m http.server -d web 8000    # the fire shows on the map

STUB - pipeline code lands after the Phase 1 plan is approved. Only --help works.
"""
from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_all.py",
        description="Run the whole pipeline: fetch -> grid -> sim -> calibrate -> candidates -> solve -> export.",
    )
    p.add_argument("--quick", action="store_true",
                   help="cached downloads + coarser grid (fast end-to-end smoke run)")
    p.add_argument("--skip-fetch", action="store_true",
                   help="assume pipeline/data_raw/ is already populated")
    return p


def main(argv=None) -> None:
    build_parser().parse_args(argv)
    sys.exit("run_all.py is a stub - pipeline code lands after the Phase 1 plan is approved "
             "(see implementation-notes.md).")


if __name__ == "__main__":
    main()
