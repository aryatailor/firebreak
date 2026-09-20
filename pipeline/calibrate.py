"""calibrate.py - sweep the free parameters; pick defaults; sanity-gate the model.

Stage 5 of the Firebreak pipeline (see implementation-notes.md "## Plan").

- Sweeps ROS scale x wind strength and prints a table of (minutes to town center,
  % buildings hit at the horizon) per combination.
- Picks defaults that hit 60-90% of buildings within 2-4 hours and writes them to
  pipeline/config.json (read by every later stage).
- Gate: places one hand-made ring break around the town's NE edge and asserts hits
  drop by >= 30%. If they don't, this stage STOPS the pipeline loudly - the solver is
  pointless until the fire responds to breaks.

STUB - pipeline code lands after the Phase 1 plan is approved. Only --help works.
"""
from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="calibrate.py",
        description="Sweep ROS scale x wind; print calibration table; write chosen defaults to config.json.",
    )
    p.add_argument("--scales", type=str, default=None, metavar="A,B,C",
                   help="comma-separated ROS scale values to sweep (default: built-in sweep)")
    p.add_argument("--winds", type=str, default=None, metavar="A,B,C",
                   help="comma-separated wind strengths to sweep (default: built-in sweep)")
    return p


def main(argv=None) -> None:
    build_parser().parse_args(argv)
    sys.exit("calibrate.py is a stub - pipeline code lands after the Phase 1 plan is approved "
             "(see implementation-notes.md).")


if __name__ == "__main__":
    main()
