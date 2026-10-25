# Wind forecast desk

Web app for a GB energy-utility day-ahead desk. For each purchase decision in January 2026 it shows
(1) the latest national wind forecast **available at decision time** and (2) a quantified measure of how
accurate those forecasts have been, using only Elexon data (FUELHH actuals, WINDFOR forecasts).

> **AI disclosure:** this project was built with AI assistance (Claude by Anthropic) for code generation and scaffolding.

**Live app:** kjhwr-nine.vercel.app

## Key idea

A decision on day D is for delivery on D+1. WINDFOR holds many forecasts per target hour (one per `publishTime`).
The app picks, for each delivery hour, the **latest forecast with `publishTime <= cut-off`** (default 09:00 UTC on D, an
assumption, adjustable in the UI). A forecast published exactly at the cut-off is eligible; later ones are ignored.
Error = forecast - actual (positive = forecast too high = risk of buying too little).

**Resolution is hourly.** WINDFOR has one value per hour; FUELHH is half-hourly. Each hourly forecast is compared with the
**mean of the two half-hourly FUELHH readings in that hour**; an hour with either reading missing is dropped. Forecasts are
never copied onto half-hour slots. All metrics and all GWh totals use the same matched hours.

Metrics: MAE, RMSE, bias, weighted MAPE, P90/P95 absolute error, error by delivery day / hour / forecast horizon, all on hourly matched observations,
daily volume miss (MWh). The "likely range" on the decision chart uses signed-error quantiles (10th-90th) from the
previous 14 delivery days that had finished before the cut-off.

## Files

```
backend/
  app/config.py        scope dates, default cut-off, lookback
  app/data.py          dataset load/save + indexing
  app/analysis.py      pure analysis functions (cut-off logic, metrics, histogram, horizon)
  app/main.py          FastAPI routes (/api/meta, /api/decision, /api/accuracy) + serves built UI
  scripts/fetch_data.py        download real Elexon data into backend/data
  scripts/diagnose.py          resolution, alignment and bias-decomposition evidence report
  scripts/investigate_bias.py  largest-error hours, attribution, calibration, AGWS comparison
  scripts/fetch_agws.py        downloads AGWS (B1630) wind for the investigation only (not used by the dashboard)
  docs/evidence/               recorded outputs of the investigation scripts on the real data
  scripts/make_sample_data.py  generate SYNTHETIC demo data (labelled in the UI)
  data/                cached REAL Elexon dataset (fetched 2026-10-24; see meta.json); refresh with scripts.fetch_data
  tests/               pytest unit + API tests
frontend/              React (Vite, Recharts): DecisionView, AccuracyView
api/index.py, vercel.json, requirements.txt   Vercel deployment
Dockerfile, docker-compose.yml                single image: API + built UI
docs/VIDEO_SCRIPT.md   script for the demo video
```

## Run it

**1. Data (the repo ships with real Elexon data in `backend/data`; refetch only if you want a fresh pull):**
```bash
cd backend
pip install -r requirements.txt
python -m scripts.fetch_data      # needs internet; takes a few minutes
```

**2a. Docker**
```bash
docker compose up --build          # http://localhost:8000
# or bake real data into the image: set FETCH_DATA: "1" in docker-compose.yml
```

**2b. Local dev**
```bash
cd backend && uvicorn app.main:app --reload --port 8000
cd frontend && npm install && npm run dev      # http://localhost:5173 (proxies /api)
```

**Tests:** `cd backend && pip install -r requirements-dev.txt && pytest`

## Deploy

* **Vercel:** import the repo; `vercel.json` builds the frontend and serves `api/index.py`. Commit the real `backend/data` files first.
* **Heroku (container):** `heroku stack:set container`, then `heroku container:push web && heroku container:release web`.

## Data semantics and what has (not) been verified

| Question | Status |
|---|---|
| WINDFOR is hourly (one `startTime` per hour) | Confirmed on the real dataset (`forecast startTime minutes: [0]`) and consistent with other public write-ups of the API |
| FUELHH is half-hourly | Part of the assignment brief |
| Comparing an hourly forecast with the mean of the two half-hours | **Chosen method.** Needs no invented `:30` forecast, scores every hour once, gives MW x 1 h = MWh. Elexon documents FUELHH as the **average MW over each 30 minutes**, and NESO's forecasting tool as producing an **hourly** forecast, so the mean of the two half-hours is the like-for-like actual. Whether each hourly value represents the hour's average or a point value is not stated in the pages I read |
| FUELHH units, shape and coverage | Elexon: average MW over 30 minutes, national total per fuel type, from operational metering of **transmission-connected** units. Elexon states it excludes embedded generation and wind farms without operational meters (about 82% of wind has operational meters, NESO estimate for 2025). So FUELHH WIND is deliberately a partial measure of GB wind |
| WINDFOR scope | Elexon: forecast for wind farms visible to the system operator with operational metering; the exact boundary is not documented in the pages read. **On the real data WINDFOR sits within 3% of AGWS and 19% above FUELHH WIND, so in practice it behaves like the broader series** (see the bias section) |
| `startTime` / `publishTime` meaning | `startTime` = start of the delivery interval, `publishTime` = when that forecast version was issued (stated in third-party write-ups; Elexon's pages show the fields but I found no definition). Alignment is tested on your data (`scripts.diagnose` section 2) |
| Stream endpoint returns history | One third-party write-up says the WINDFOR stream ignored its filters. Not the case here: the fetched file has publications from 15 Dec 2025 to 1 Feb 2026, so the publish filters worked |
| Cut-off 09:00 UTC | Assumption. GB day-ahead auctions close around 09:20 (EPEX) and 09:50 (N2EX) UK time; change it in the UI |

An earlier version of this app copied each hourly forecast onto both half-hours. That was replaced by the hourly comparison
above because it created forecast values the source does not contain.

## What each number is computed from

| Output | Sample |
|---|---|
| Headline metrics, daily, hourly and spread charts | 744 hours (delivery 2 Jan to 1 Feb), one forecast per hour: the latest published at or before the cut-off |
| Lead-time chart | **Every** published forecast with lead time >= 0 for the same hours (14,383 forecasts, up to 72 h ahead), so its values differ from the headline. Highlighted bars are the buckets overlapping the lead time actually used at the chosen cut-off (16.5-39.5 h at 09:00); buckets are 12 h wide |
| Decision view `actual_complete` | True only when **all 24 hourly actuals exist** (both half-hours for every hour). `forecast_complete` is the same test for forecasts. The "What happened" total is shown only when both are true, because otherwise the forecast and actual totals would cover different hours. The previous condition compared matched hours with forecast hours, so an hour missing from both sides still counted as complete |

## The forecast bias: what is proven, what is not

Against FUELHH output the 09:00 forecast ran high by **+1,748 MW** on average over 744 January hours (MAE 2,039 MW,
RMSE 2,505 MW, 18.1% of output). Against Elexon's AGWS wind series on the same hours it is **-418 MW** (MAE 1,330 MW).
Recorded outputs: `docs/evidence/` (produced by `scripts.fetch_agws` and `scripts.investigate_bias`; the README numbers are
checked against these files by `tests/test_recorded_evidence.py`).

### 1. Established facts (measured on the real data)

| # | Fact | Evidence |
|---|---|---|
| 1 | The app selects the right forecast and period | An independent recomputation from the raw files matches the app on all 744 hours (0 mismatches); the cut-off, hourly mean and January filter are unit-tested |
| 2 | The data are complete and in MW | Actuals: exact half-hourly grid, nothing missing or duplicated. Forecasts: every publication has 73 rows, 8 per day, none missing, no conflicting duplicates |
| 3 | FUELHH and AGWS share a clock | Correlation peaks at shift 0 (0.993) and is symmetric (0.987 at -1 h, 0.986 at +1 h); at +2 h it is 0.971 |
| 4 | **No time shift is justified, and none is applied** | The forecast correlates best with FUELHH at -1 h by only 0.0018 over 0 h (0.9327 vs 0.9309); at +2 h it is clearly worse (0.911) |
| 5 | Onshore/offshore aggregation is right | Onshore 5,370 + offshore 8,079 = 13,449 MW = AGWS total; both present for all 744 hours; same MW scale as FUELHH and WINDFOR (a unit error would give about 0.5x or 2x) |
| 6 | **The bias is the gap between two actuals series** | Mean FUELHH 11,284, AGWS 13,449, WINDFOR 13,031 MW. -418 (forecast vs AGWS) + 2,166 (AGWS minus FUELHH) = +1,748. Forecast error against AGWS is 3% of output and 35% lower in MAE |
| 7 | FUELHH covers about 84% of AGWS, less at high output | FUELHH/AGWS = 0.839 overall; by AGWS quartile 0.863, 0.857, 0.844, 0.816 (gap 0.9, 1.6, 2.5, 3.6 GW) |
| 8 | The 17.8 GW "ceiling" is in FUELHH, not in the wind | Highest hour: FUELHH 17,756 MW, AGWS 22,515 MW |
| 9 | The forecast also has real misses, mostly in two spells | 12 Jan 00-06 UTC: forecast 20.4-20.9 GW, AGWS 18.2-19.0 GW, error vs AGWS +1.8 to +2.2 GW (about +11%) on top of a 4.4-4.8 GW series gap. 28 Jan 03-04 and 21 UTC: error vs AGWS +4.1 to +4.7 GW (forecast 57-65% above AGWS) |
| 10 | Against FUELHH the bias is broad, not a top-of-range effect | The 73 hours at or above 16.5 GW carry 10.9% of the error; the other 671 hours show +1,727 MW. Worst 5 days carry 41%; 27 of 31 days positive |
| 11 | The bias is almost independent of lead time | +1.4 to +1.9 GW at every lead time, including forecasts published up to 36 h after the hour |

### 2. What the official documentation says

* **FUELHH** (Elexon, "Generation by fuel type": https://bmrs.elexon.co.uk/generation-by-fuel-type): an indication of GB
  generation outturn, average MW over 30 minutes, built from operational metering of units connected to the transmission
  system. Elexon states that renewables are underestimated in this report because it excludes embedded generation and wind
  farms without operational meters, and that NESO estimates wind farms with operational meters represent **about 82%** of
  wind as of 2025.
* **WINDFOR** (Elexon wind page): a forecast for wind farms visible to the system operator with operational metering. The
  same page draws its outturn line from the Generation by Fuel Type data "for a direct comparison".
* **AGWS / B1630** (Elexon endpoint title): "actual or estimated wind and solar power generation". I found no Elexon text
  saying exactly what the estimate includes.
* **Embedded wind** (NESO data portal, embedded wind and solar forecast): farms without transmission metering are invisible
  to the system operator and their effect is to suppress demand; NESO publishes an estimate of their output.

### 3. Conclusion the evidence supports

**Most of the +1,748 MW is a coverage difference between the "actual" series used for scoring and the quantity WINDFOR
effectively forecasts, not forecasting error.** Elexon documents that FUELHH wind leaves out embedded generation and wind farms
without operational meters (about 82% coverage); the measured FUELHH/AGWS ratio is 0.839; and WINDFOR sits within 3% of
AGWS. These three independent facts agree. The forecast does have its own errors (fact 9), but they are smaller than the
headline suggests.

Caveat: I take AGWS to be broader than FUELHH because the data say so (facts 6 to 8) and the documentation describes it as
"actual or estimated"; no Elexon page I could read states that AGWS includes embedded wind.

### 4. Not proven (hypotheses)

* **The exact makeup of the 2,166 MW gap**: embedded wind versus wind farms without operational meters versus other effects.
* **Why FUELHH/AGWS falls with output** (0.863 to 0.816) **and to about 0.75 on 12 Jan** (FUELHH 14.1 GW flat while AGWS
  was about 18.8 GW): a larger embedded share in windy conditions, curtailment or constraints on transmission-connected wind,
  or how AGWS is estimated. A third-party site reports sizeable "wind paid to stop" volumes on some days, so curtailment is
  plausible, but no curtailment data were analysed here.
* **Why WINDFOR matches AGWS, not FUELHH**: Elexon's wording suggests operationally metered farms, and the glossary notes
  such wind farms can be embedded; the exact forecast boundary is not documented in the pages read.
* **The night-versus-afternoon pattern**: the AGWS-FUELHH gap varies little by hour (2.05 to 2.30 GW), so it explains only
  part of the larger night-time bias against FUELHH; the rest (forecast versus AGWS by hour) has not been analysed.

### 5. Ruled out

| Candidate | Evidence |
|---|---|
| Missing, duplicated or mis-scaled actuals or forecasts | Facts 1, 2 and 5 |
| Wrong vintage selection | All 744 hours use a day-ahead forecast (published 07:30, lead 16.5 to 39.5 h); later forecasts are never selected (tested) |
| Hourly/half-hourly mapping | Scoring against the first or second half-hour alone gives bias 1,746 and 1,750 MW |
| Timestamp misalignment | Facts 3 and 4; shifting actuals by +/-2 h moves the bias by under 50 MW |
| Units or date filter | MW x 1 h = MWh, daily volume miss = bias x hours (tested); scope is delivery days 2 Jan to 1 Feb |
| Forecasting skill alone | Fact 11 |

### 6. What this means for the desk

* The dashboard still scores against **FUELHH**, because the assignment specifies FUELHH and WINDFOR only. AGWS is used by
  the investigation scripts, not by the dashboard. No correction, scaling or time shift is applied anywhere.
* Which yardstick is right depends on the demand the wind is netted against. Third-party descriptions say Elexon's national
  demand (INDO) is net of embedded generation. If the desk nets WINDFOR against such transmission-level demand, the forecast
  counts embedded wind that demand already excludes and would overstate the offset by roughly 1.7 to 2.2 GW here; against
  total demand it is close to AGWS (-0.4 GW). This is a question for the client.

### 7. Evidence still needed

1. A definition from Elexon or NESO of what the AGWS estimate includes and what WINDFOR's forecast boundary is.
2. Curtailment volumes for wind units, to test whether curtailment explains the extra FUELHH shortfall at high output and on
   12 Jan.
3. Forecast versus AGWS by hour of day, to explain the night/afternoon difference.
4. Other months, to see whether the 19% gap is structural (it was +25% in the flat-FUELHH episodes).
5. The client's demand basis, to choose the production yardstick.

### 8. Likely range calibration

The range on the Tomorrow's wind tab (10th to 90th percentile of the previous 14 days' errors, against FUELHH) contained the
actual in 81.5% of January hours against a nominal 80%. Misses are lopsided: 13.3% of hours fell below the range and 5.2%
above. Three days had under 50% coverage (worst 0%), so it is an average property, not a per-day guarantee.

### 9. Reproduce

```bash
cd backend
python -m scripts.fetch_agws        # AGWS wind, for the investigation only
python -m scripts.investigate_bias  # sections A-G: errors, attribution, calibration, AGWS comparison, clock, timing
python -m scripts.diagnose          # resolution, alignment and bias decomposition on FUELHH/WINDFOR alone
```

## Regression tests

`cd backend && pytest` (97 tests; 13 characterise the real data and skip on demo data, 8 check this README against the recorded investigation output). They pin: hourly mean of two half-hours, dropping hours with a missing half-hour, no
forecast on `:30` slots, UTC normalisation, inclusive cut-off and D-1 timing, January-only scope (warm-up and February
decisions excluded), metrics against an independent calculation, daily volume = sum of the same hourly errors = bias x
matched hours, MW x 1 h units, horizon report ignoring forecasts published after the target, band history limited to
finished days, a null-bias test (a perfect forecast scores exactly zero at any cut-off), exact recovery of a known offset,
calibration tools that separate additive, proportional and ceiling-type gaps, the regression-to-the-mean trap in binning by
actual, deterministic de-duplication of FUELHH rows, `actual_complete` (all 24 hourly actuals), the sample descriptions and
cut-off-dependent highlighted lead times, and `fetch_data` against a fake Elexon (WIND-only selection, contiguous fetch
windows, boundary duplicates, counted conflicts and nulls). The suite was mutation-checked: breaking cut-off inclusivity, the MWh factor, or the hourly mean each fails it.

## Assumptions and limits

* Cut-off 09:00 UTC (= UK time in January); verify against your auction gate closure.
* Delivery day is 00:00-24:00 UTC (24 hourly observations). The range is per hour, so it is not a confidence interval on the daily total.
* National wind only; no demand, prices or other fuels.
