# FRONTEND_NOTES.md

Decisions made during the overnight frontend session (2026-09-19 into 2026-09-20).
Conservative choice taken whenever something was ambiguous; each one is logged here.

## Copy rules in force

No em dashes, no exclamation marks, no emoji, no "Let's". No UI sentence over 12
words. Numbers carry units. One idea per line. Labels that can be deleted are
deleted.

## Tier 1: typography and title

- **Font discovery.** `sedimentlabs.ai` loads `/assets/index-*.css`, which declares
  three `@font-face` rules for **Selawik** at weights 400, 600 and 700, plus a mono
  stack `ui-monospace, Cascadia Mono, Consolas, Menlo, monospace`.
- **Selawik is freely licensed** (Microsoft, SIL Open Font License 1.1 — it is their
  open metric-compatible replacement for Segoe UI), so it is used directly rather
  than substituted. The three woff2 files are vendored to `web/vendor/fonts/` and
  loaded from there. No CDN, works offline.
- The woff2 files are Sediment's Latin subsets (about 14 KB each). Glyph coverage is
  verified in the headless check; if a glyph were missing the check would catch it
  as a fallback-metrics mismatch.
- Mono stack copied from Sediment as well, replacing the previous JetBrains Mono
  request (which was never vendored and so never actually loaded).
- **Title card**: the word is rendered twice. The back copy is `#ff2a1a`, blurred
  12 px and passed through `feTurbulence` + `feDisplacementMap`; `seed` and
  `baseFrequency` drift under requestAnimationFrame so the red edge waves like heat.
  The white copy carries no filter at all, so it never flickers. The animation stops
  when the card is dismissed.
- **Logo**: one horizontal white line with a clean gap in the middle (the break),
  with the wordmark beside it in Selawik. Same mark as `web/favicon.svg`.
- Panel heading removed: the brand mark top-left is now the only place the product
  names itself.
