# Video script (max 10 minutes, unlisted YouTube)

Record the deployed app for Part 1 and your editor + browser for Part 2.

## Part 1: client pitch (about 4:30)

**0:00 Problem (30s).** "Every morning your desk commits to tomorrow's power before knowing tomorrow's wind. Wind is the swing factor, so the question is: how much can we count on, and how far can we trust it?"

**0:30 Tomorrow's wind tab (90s).** Pick 14 Jan. Point out: the forecast shown is the newest one issued before the 09:00 cut-off, with its issue time; later forecasts are ignored on purpose. Expected GWh, cautious case, shaded range. Tick "Show what actually happened" and show the miss.

**2:00 Accuracy tab (120s).** Read the plain-language headline: typical miss in MW and % of output, 9-in-10 bound, bias direction. Daily volume miss chart: days where the forecast was too high are the days you under-bought. Horizon chart: error grows with look-ahead; the day-ahead window is the darker bars.

**4:00 What to do with it (30s).** "Use the cautious case as your floor for wind, and size your buffer from the 90th-percentile miss. Change the cut-off to see how a later bid gate changes accuracy."

## Part 2: tech lead review (about 4:30)

**0:00 Architecture (60s).** Elexon -> `scripts/fetch_data.py` -> gzipped cache in `backend/data` -> `app/analysis.py` (pure functions) -> `app/main.py` (FastAPI) -> React (Vite + Recharts). One Docker image serves both API and built UI.

**1:00 The key design decision (90s).** Open `analysis.latest_before`: bisect over publish times, `<= cutoff`, never after. Show the unit test that plants a later forecast and asserts it is ignored. The uncertainty band uses only days finished before the cut-off (no leakage). Sign convention: error = forecast - actual.

**2:30 Metrics (45s).** MAE, RMSE, bias, weighted MAPE (why not plain MAPE), P90/P95, horizon buckets.

**3:15 Code tour and practices (75s).** Repo layout from the README, pytest (14 tests), typed pure functions, config in one file, multi-stage Dockerfile, Vercel config.

**4:30 Assumptions and next steps (30s).** Cut-off is 09:00 UTC by assumption and configurable; the per-slot band is not a daily-total band; next: demand data, probabilistic forecasts, scheduled refresh. State that AI assistance was used.

## Be upfront about the bias (add to both parts)

- Part 1 (30s): "Against FUELHH output the forecast runs about 1.7 GW high. Elexon's own FUELHH page says it leaves out embedded wind and wind farms without operational meters, about 82% of wind. Against AGWS, a broader series, the forecast is within about 3%. Which yardstick is right depends on which demand you net wind against, so that is a question for you."
- Part 2 (45s): show `docs/evidence/investigate_bias_2026-10-24.txt`: AGWS minus FUELHH is +2,166 MW, forecast minus AGWS is -418 MW, and -418 + 2,166 = +1,748. Say what is proven (coverage difference, clocks aligned, no shift applied, aggregation checked) and what is not (the exact makeup of the gap, and why it grows with output). Also say the forecast has genuine misses on 12 and 28 Jan.
