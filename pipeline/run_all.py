"""run_all.py - the whole pipeline chain: fetch -> grid -> sim -> calibrate ->
candidates -> solve -> export.

Orchestrator of the Firebreak pipeline (see implementation-notes.md "## Plan").

- Runs every implemented stage in order inside the fail-loud stage boundary
  (prints stage + input + exception, appends to pipeline/run_log.md, re-raises).
- --quick uses cached downloads and a 2x coarser grid for a fast end-to-end run.
- Stops with a clear message at the first stage that is not implemented yet.

Verify (today): python pipeline/run_all.py --town paradise
"""
from __future__ import annotations

import argparse
import sys

import common
import fetch_data
import build_grid
import calibrate
import simulate
import candidates
import solve
import export


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_all.py",
        description="Run the whole pipeline: fetch -> grid -> sim -> calibrate -> "
                    "candidates -> solve -> export.")
    p.add_argument("--town", default="paradise",
                   help="name of towns/<town>.json (default: paradise)")
    p.add_argument("--quick", action="store_true",
                   help="cached downloads + coarser grid (fast end-to-end run)")
    p.add_argument("--skip-fetch", action="store_true",
                   help="assume pipeline/data_raw/<town>/ is already populated")
    p.add_argument("--force", action="store_true",
                   help="re-download inputs even if cached")
    return p


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)

    if not args.skip_fetch:
        common.run_stage("fetch", args.town,
                         lambda: fetch_data.run(args.town, force=args.force))
    common.run_stage("grid", args.town,
                     lambda: build_grid.run(args.town, quick=args.quick))
    common.run_stage("calibrate", args.town, lambda: calibrate.run(args.town))
    common.run_stage("simulate", args.town, lambda: simulate.run(args.town))
    common.run_stage("candidates", args.town, lambda: candidates.run(args.town))
    common.run_stage("solve", args.town, lambda: solve.run(args.town))
    common.run_stage("export", args.town, lambda: export.run(args.town))

    print("\nrun_all: full chain done - web/data is live. "
          "python -m http.server -d web 8000 to see it.")
    sys.exit(0)


if __name__ == "__main__":
    main()
