# Implementation notes

Sections: **Plan** (Phase 1, written before any pipeline code), **Decisions** (choices
made along the way, one line of why each), **Deviations** (where reality contradicted
the plan — conservative option taken, reason logged).

## Plan

_Pending — written at the end of Phase 0, then STOPPED for Zach's approval before any
pipeline code is written._

## Decisions

- Leaflet 1.9.4 vendored from unpkg into `web/vendor/leaflet/` (js + css + images) —
  the demo laptop has unreliable wifi; no CDN anywhere.
- Mock generator is pure-stdlib Python (no numpy/PIL) so `web/mock/` can be
  regenerated before the geo stack is even installed.
- Mock grid is 150 rows × 200 cols on the **real** Paradise bounds — every value is
  fake, but Leaflet placement/alignment is real, so Arya's layer code carries over
  unchanged.
- Mock arrival field is straight-line anisotropic travel time (fast downwind toward
  the southwest, slow upwind) — cheap to generate, and it produces the right visual: a
  wedge from Pulga (top-right) into Paradise.

## Deviations

(none yet)
