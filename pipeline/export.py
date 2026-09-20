"""export.py - write web/data/* exactly per CONTRACT.md.

Stage 8 of the Firebreak pipeline (see implementation-notes.md "## Plan").

- Writes meta.json, baseline.json, solutions.json, curve.json, buildings.geojson,
  basemap.png, fuel_legend.json.
- Grid arrays: base64 of little-endian uint16, row-major, top row = north,
  65535 = never reached within horizon.
- Enforces the <= 5 MB total budget for web/data/ and FAILS LOUD if exceeded -
  never silently truncates.

STUB - pipeline code lands after the Phase 1 plan is approved. Only --help works.
"""
from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="export.py",
        description="Write web/data/* per CONTRACT.md; enforce the 5 MB total size budget.",
    )
    p.add_argument("--max-mb", type=float, default=5.0, metavar="MB",
                   help="hard size budget for web/data in MB (default: 5)")
    return p


def main(argv=None) -> None:
    build_parser().parse_args(argv)
    sys.exit("export.py is a stub - pipeline code lands after the Phase 1 plan is approved "
             "(see implementation-notes.md).")


if __name__ == "__main__":
    main()
