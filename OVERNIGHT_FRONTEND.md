# OVERNIGHT_FRONTEND.md

Frontend session, night of 2026-09-19 into 2026-09-20. All six tiers shipped.
Every tier was committed and pushed separately, and the headless check ran green
before each push. Decisions are logged in `web/FRONTEND_NOTES.md`.

## The one thing to look at first

Open the page and press Enter. **The opening is the thing that changed.** The map
starts at state scale and flies to Paradise over ten seconds while the crawl reads
out, then the fire runs. Nobody sees a fire start without being told what it is.

## Morning command

`serve.py` had not landed when this was written, so use whichever exists:

```
.\demo.ps1                      # if the pipeline session landed it
python -m http.server -d web 8000
```

Then open http://localhost:8000. Useful flags: `?intro=0` skips the title card,
`?offline=1` forces the offline basemap, `?town=altadena` loads the second story.

With `demo.ps1` running, the free-play panel also shows a search box for building
any region in the US. On the plain static server that box stays hidden by design.

## What shipped, by tier

**Tier 1, typography and title.** Sediment's font is **Selawik** (Microsoft, SIL
Open Font License), declared at weights 400, 600 and 700 in their stylesheet. It is
freely licensed, so it is used directly, vendored to `web/vendor/fonts/`, no CDN.
Their mono stack came across too. The title card renders FIREBREAK twice: a red
`#ff2a1a` copy behind, blurred 12 px and pushed through `feTurbulence` plus
`feDisplacementMap` with seed and frequency animating, so heat waves off the edge;
the white copy carries no filter and never flickers. The logo is one white line with
a clean break in it, matched by `web/favicon.svg`.

**Tier 2, cinematic opening.** Black, then the map fades in at zoom 6 and flies to
the region over 10 s while `meta.crawl` plays a line at a time, full bleed with all
chrome hidden. Then "This is what happened." and the fire. After the burn, the homes
lost and "What would a budget have bought?" with the slider lit and an arrow pointing
at it. Drag, run it again over the ghosted baseline perimeter, read the result, and
"Now start your own fire." Skip is bottom right throughout.

**Tier 3, panel and map controls.** The panel is two things, budget and homes saved,
plus one status line. "More" is gone. The scrubber is a thin strip on the bottom edge
of the map. The curve and the simplifications live behind one small **i**. Region
switching is a mono select top left. `minZoom 4`, fast wheel zoom, a **US** button,
and below zoom 9 a labelled pin for every region in the index.

**Tier 4, free play.** `web/sim.js` runs the contract's model in a Web Worker.
Click the map to place a fire, drag the compass or the speed slider to change the
wind, draw two-cell breaks with undo and clear, and read homes saved and cost by
fuel class. Solver files are now optional, so `story:false` regions open straight
into free play.

**Tier 5, look.** Swept for gradients, shadows and corners over 3 px and removed the
two I had introduced. Added a quiet low wind bed under the crawl that stops when the
fire starts. Nothing autoplays before the Enter click.

**Tier 6, self-judge.** Four cycles of screenshot, critique and fix over thirteen
states, all in `deck/shots/`.

## Parity: the browser model matches Python exactly

Measured against `web/data/parity.json`, 152,304 cells:

| case | within 1 bucket | exact | worst delta |
|---|---|---|---|
| baseline | **100.000%** | **100.000%** | 0 |
| with test break | **100.000%** | **100.000%** | 0 |

Target was 99% within one bucket. It is cell for cell identical, so the
`POST /api/simulate` fallback was never needed. A run takes **73 to 101 ms** against
a 500 ms budget; free play issues two per change, one without the drawn breaks to
get a fair baseline.

## Other numbers

- Load to interactive **91 to 99 ms**, first contentful paint 84 ms.
- Home drift versus Leaflet's own projection: **0.05 / 0.73 / 1.84 px** at zoom
  10 / 12 / 14, against grid pixels of 0.26 / 1.02 / 4.09 px. Always under half a
  grid pixel, with no growth across zoom. Homes are pixels in the fire's grid canvas
  now, so they cannot move independently of it.
- No console errors in any state. The single 404 is the deliberate `api/health`
  probe on a static server.
- Copy sweep clean: no em dashes, no exclamation marks, nothing over 12 words.

## What did not ship, and why

- **The region search happy path is untested end to end.** `serve.py` was not on
  disk at any point during the night, so `/api/health`, `/api/region` and the status
  poll are built to the contract but only the degraded path was exercised. If the
  API behaves differently from the spec, that flow is where it will show.
- **No `/api/simulate` fallback path was wired**, because parity passed exactly on
  the first attempt. The brief allowed two attempts before falling back; it took
  none. If the model ever drifts, the fallback still has to be written.
- The drawn-break cost uses the contract's class mapping and the flat
  `cost_per_acre` table. It is not discounted for terrain or access, same as the
  solver.

## Known issues

- In a dense town such as Altadena the standing homes read as a pale field at region
  zoom, because there is a home on nearly every developed cell. Zooming past 13
  resolves them. The colour and alpha are the ones specified.
- The ghost baseline perimeter is hard to see when the breaks barely change the
  burn's outer edge, since the two outlines then coincide. It is doing its job; there
  is just little to show in that case.
- The free-play evacuation figure is the delay to the **first** home, not to a town
  centre, because a custom ignition has no town centre. It reads 0 when a break is
  drawn behind the fire rather than in front of it, which is correct but looks null.
- `web/mock/` still exists and still works, but nothing points at it now that
  `DATA_DIR` defaults to the real data and the town index drives the choice.

## Files touched

`web/index.html`, `web/app.js`, `web/style.css`, `web/sim.js` (new),
`web/favicon.svg` (new), `web/vendor/fonts/selawik-{400,600,700}.woff2` (new),
`web/FRONTEND_NOTES.md` (new), `deck/shots/*` (14 screenshots), this file.

Untouched, as required: `web/data/`, `web/towns/`, `web/vendor/leaflet/`,
`CONTRACT.md`, and everything under `pipeline/`.
