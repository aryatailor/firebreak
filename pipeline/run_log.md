# Pipeline run log

Every stage appends here on failure (and on notable events): timestamp, stage name,
the input it was given, and the exception. Written by the stage-boundary try/except
in each pipeline script — no silent failures.

(empty — no runs yet)
- 2026-09-19T21:52:32 [fetch/paradise] LANDFIRE worker failed (exit 1) - falling back to WorldCover+CopDEM
- 2026-09-19T22:11:09 [grid/paradise] FAILED: OSError: [Errno 22] Invalid argument: 'C:\\Users\\speck\\.claude\\firebreak\\pipeline\\out\\paradise\\alignment_check.png'
- 2026-09-19T22:13:49 [calibrate/paradise] no sweep combo in target band; closest: {'scale': 150.0, 'k_w': 0.35, 'minutes_to_town': 173.3, 'frac_homes_hit': 1.0, 'sim_seconds': 0.131}
- 2026-09-19T22:13:50 [calibrate/paradise] FAILED: SystemExit: GATE FAILED: the ring break only cut homes hit by 0.0% (< 30%). The fire is not responding to breaks - STOP, tell Zach (plan section 5). config.json still written for inspection.
- 2026-09-20T00:51:03 [physics/paradise] FAILED: SystemExit: C:\Users\speck\.claude\firebreak\web\data is 20.30 MB > 20 MB cap - STOP
- 2026-09-20T07:07:34 [calibrate/altadena] FAILED: SystemExit: GATE FAILED for every candidate x break_mult [0.05, 0.02, 0.01] - the fire does not respond to breaks, STOP, tell Zach (plan section 5). config.json holds all attempts.
- 2026-09-20T07:07:40 [simulate/altadena] FAILED: TypeError: 'NoneType' object is not subscriptable
- 2026-09-20T07:07:44 [candidates/altadena] FAILED: SystemExit: arrival_baseline.npy missing - run simulate first: python pipeline/simulate.py --town altadena
