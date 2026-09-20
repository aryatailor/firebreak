# Pipeline run log

Every stage appends here on failure (and on notable events): timestamp, stage name,
the input it was given, and the exception. Written by the stage-boundary try/except
in each pipeline script — no silent failures.

(empty — no runs yet)
- 2026-09-19T21:52:32 [fetch/paradise] LANDFIRE worker failed (exit 1) - falling back to WorldCover+CopDEM
