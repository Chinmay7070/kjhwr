"""Pure analysis functions: no I/O, no framework. Easy to unit test.

Conventions
-----------
* All times are UTC. A delivery day runs 00:00-24:00 UTC (== UK time in January).
* Resolution is HOURLY. WINDFOR has one value per hour (startTime on the hour);
  FUELHH is half-hourly. The actual for an hour is the mean of its two half-hourly
  readings, and an hour with either reading missing is dropped. Forecasts are never
  copied onto half-hour slots.
* error = forecast - actual (MW). Positive error means the forecast was too high,
  i.e. the desk would have relied on wind that did not arrive and bought too little.
* One hour at X MW is X MWh. Every metric and every GWh total uses the SAME matched
  hours, so daily volume miss == sum of hourly errors == bias * matched hours.
"""
from __future__ import annotations

import math
from bisect import bisect_right
from datetime import date, datetime, time, timedelta, timezone

from .config import DECISION_END, DECISION_START, LOOKBACK_DAYS, MIN_BAND_SAMPLES
from .data import Dataset, iso, parse_ts

UTC = timezone.utc
SLOTS_PER_DAY = 24  # hourly resolution
HALF_HOUR = timedelta(minutes=30)
HORIZON_BUCKETS = [(0, 6), (6, 12), (12, 24), (24, 36), (36, 48), (48, 72), (72, 10_000)]
# A day-ahead decision at 09:00 D-1 looks at targets 15h to ~39h ahead.
DECISION_HORIZON_H = (15.0, 40.0)


def parse_cutoff(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


def quantile(values: list[float], q: float) -> float:
    """Linear-interpolated quantile of an unsorted list."""
    s = sorted(values)
    if not s:
        raise ValueError("quantile of empty list")
    pos = (len(s) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def _r(x: float | None, nd: int = 1) -> float | None:
    return None if x is None else round(x, nd)


def summarize(errors: list[float], actuals: list[float]) -> dict:
    """Error statistics for paired forecast errors and actuals."""
    n = len(errors)
    if n == 0:
        return {"n": 0, "mae": None, "rmse": None, "bias": None, "wmape": None,
                "p90_abs": None, "p95_abs": None, "max_abs": None}
    abs_e = [abs(e) for e in errors]
    total_actual = sum(actuals)
    return {
        "n": n,
        "mae": _r(sum(abs_e) / n),
        "rmse": _r(math.sqrt(sum(e * e for e in errors) / n)),
        "bias": _r(sum(errors) / n),
        # Weighted MAPE: total absolute error over total actual output. Plain MAPE
        # explodes in calm hours when actual output is near zero.
        "wmape": _r(100 * sum(abs_e) / total_actual) if total_actual > 0 else None,
        "p90_abs": _r(quantile(abs_e, 0.90)),
        "p95_abs": _r(quantile(abs_e, 0.95)),
        "max_abs": _r(max(abs_e)),
    }


def latest_before(ds: Dataset, target: datetime, cutoff: datetime) -> tuple[datetime, float] | None:
    """Latest forecast for `target` published at or before `cutoff` (never after)."""
    entry = ds.forecasts.get(target)
    if not entry:
        return None
    pubs, vals = entry
    idx = bisect_right(pubs, cutoff) - 1
    return (pubs[idx], vals[idx]) if idx >= 0 else None


def hourly_mean(series: dict[datetime, float], hour_start: datetime) -> float | None:
    """Mean MW of the two half-hourly readings in the hour; None if either is missing."""
    a, b = series.get(hour_start), series.get(hour_start + HALF_HOUR)
    return (a + b) / 2 if a is not None and b is not None else None


def hourly_actual(ds: Dataset, hour_start: datetime) -> float | None:
    """Hourly FUELHH actual: mean of the hour's two half-hourly readings; None if either is missing."""
    return hourly_mean(ds.actuals, hour_start)


def cutoff_for(delivery_day: date, cutoff: time) -> datetime:
    return datetime.combine(delivery_day - timedelta(days=1), cutoff, tzinfo=UTC)


def delivery_rows(ds: Dataset, delivery_day: date, cutoff: time) -> list[dict]:
    """24 hourly rows for one delivery day, using only forecasts published at or before the cutoff.

    `actual` is the mean of the hour's two half-hourly FUELHH readings (None if either is missing)."""
    cut = cutoff_for(delivery_day, cutoff)
    day_start = datetime.combine(delivery_day, time(0), tzinfo=UTC)
    rows = []
    for i in range(SLOTS_PER_DAY):
        t = day_start + timedelta(hours=i)
        found = latest_before(ds, t, cut)
        actual = hourly_actual(ds, t)
        forecast = found[1] if found else None
        error = forecast - actual if forecast is not None and actual is not None else None
        rows.append({
            "start": iso(t),
            "forecast": forecast,
            "publish_time": iso(found[0]) if found else None,
            "horizon_h": _r((t - found[0]).total_seconds() / 3600) if found else None,
            "actual": actual,
            "error": error,
        })
    return rows


def _paired(rows: list[dict]) -> tuple[list[float], list[float]]:
    pairs = [(r["error"], r["actual"]) for r in rows if r["error"] is not None]
    return [p[0] for p in pairs], [p[1] for p in pairs]


def decision_view(ds: Dataset, decision_date: date, cutoff: time) -> dict | None:
    """Everything the desk needs for one decision: forecast, vintage, uncertainty band."""
    delivery_day = decision_date + timedelta(days=1)
    rows = delivery_rows(ds, delivery_day, cutoff)
    if not any(r["forecast"] is not None for r in rows):
        return None

    # Uncertainty band from PAST days only (delivery days that finished before the
    # cutoff), so the band never uses information the desk would not have had.
    hist_errors: list[float] = []
    hist_actuals: list[float] = []
    days_used = 0
    for back in range(1, LOOKBACK_DAYS + 1):
        e, a = _paired(delivery_rows(ds, decision_date - timedelta(days=back), cutoff))
        if e:
            days_used += 1
            hist_errors += e
            hist_actuals += a

    band = None
    if len(hist_errors) >= MIN_BAND_SAMPLES:
        q10, q90 = quantile(hist_errors, 0.10), quantile(hist_errors, 0.90)
        band = {"q10": _r(q10), "q90": _r(q90)}
        for r in rows:
            if r["forecast"] is None:
                continue
            low = max(0.0, r["forecast"] - q90)
            high = max(low, r["forecast"] - q10)
            r["band_low"], r["band_high"] = _r(low), _r(high)

    fc = [r for r in rows if r["forecast"] is not None]
    matched = [r for r in rows if r["error"] is not None]
    actual_hours = sum(1 for r in rows if r["actual"] is not None)
    publishes = sorted(r["publish_time"] for r in fc)
    horizons = [r["horizon_h"] for r in fc]
    summary = {
        "slots": len(fc),
        "missing_forecast_slots": SLOTS_PER_DAY - len(fc),
        "avg_forecast_mw": _r(sum(r["forecast"] for r in fc) / len(fc)),
        "forecast_mwh": _r(sum(r["forecast"] for r in fc)),
        "cautious_mwh": _r(sum(r["band_low"] for r in fc)) if band else None,
        "optimistic_mwh": _r(sum(r["band_high"] for r in fc)) if band else None,
        "first_publish": publishes[0],
        "last_publish": publishes[-1],
        "min_horizon_h": min(horizons),
        "max_horizon_h": max(horizons),
        # actual_mwh and forecast_mwh above cover different hours unless the day is fully covered,
        # so the sample is spelled out. The UI compares the two only when both flags are True.
        "actual_mwh": _r(sum(r["actual"] for r in matched)) if matched else None,
        "actual_hours": actual_hours,
        "scored_hours": len(matched),
        "actual_complete": actual_hours == SLOTS_PER_DAY,      # all 24 hourly actuals exist
        "forecast_complete": len(fc) == SLOTS_PER_DAY,         # all 24 hours have a forecast
    }
    return {
        "decision_date": decision_date.isoformat(),
        "delivery_date": delivery_day.isoformat(),
        "cutoff_utc": iso(cutoff_for(delivery_day, cutoff)),
        "summary": summary,
        "band": band,
        "history": {**summarize(hist_errors, hist_actuals), "days_used": days_used,
                    "window_days": LOOKBACK_DAYS},
        "rows": rows,
    }


def fit_calibration(pairs: list[tuple[float, float]]) -> dict | None:
    """Regress ACTUAL on FORECAST: actual = intercept + slope * forecast.

    For a calibrated forecast the slope is 1 and the intercept 0. (Regressing forecast on
    actual would be biased towards slope < 1 by noise in the actuals, so it is not used.)"""
    n = len(pairs)
    if n < 3:
        return None
    mf = sum(f for f, _ in pairs) / n
    ma = sum(a for _, a in pairs) / n
    sff = sum((f - mf) ** 2 for f, _ in pairs)
    saa = sum((a - ma) ** 2 for _, a in pairs)
    sfa = sum((f - mf) * (a - ma) for f, a in pairs)
    if sff == 0 or saa == 0:
        return None
    slope = sfa / sff
    return {"slope": _r(slope, 3), "intercept": _r(ma - slope * mf), "r2": _r(sfa * sfa / (sff * saa), 3), "n": n}


def bias_by_forecast_level(pairs: list[tuple[float, float]], groups: int = 4) -> list[dict]:
    """Bias within equal-sized bins of FORECAST level.

    Binning by forecast is the valid way to ask "when the forecast says X, what happens?".
    Binning by actual would show a spurious bias from regression to the mean even for a
    perfectly calibrated forecast."""
    ordered = sorted(pairs, key=lambda p: p[0])
    n = len(ordered)
    out = []
    for g in range(groups):
        chunk = ordered[g * n // groups:(g + 1) * n // groups]
        if not chunk:
            continue
        mean_f = sum(f for f, _ in chunk) / len(chunk)
        mean_a = sum(a for _, a in chunk) / len(chunk)
        out.append({"group": g + 1, "n": len(chunk), "mean_forecast": _r(mean_f), "mean_actual": _r(mean_a),
                    "bias": _r(mean_f - mean_a), "bias_pct_of_forecast": _r(100 * (mean_f - mean_a) / mean_f)
                    if mean_f else None})
    return out


def bias_by_lead_time(ds: Dataset, first_day: date, last_day: date, width: float = 6.0) -> list[dict]:
    """Error by lead time over ALL vintages, INCLUDING negative lead times.

    A negative lead time means the forecast was published after the target hour had started.
    If forecast error were a pure forecasting-skill effect it would shrink towards zero as lead
    time falls; a bias that stays the same size is a property of the two series, not of lead time."""
    lo = datetime.combine(first_day, time(0), tzinfo=UTC)
    hi = datetime.combine(last_day + timedelta(days=1), time(0), tzinfo=UTC)
    buckets: dict[int, tuple[list[float], list[float]]] = {}
    for target, (pubs, vals) in ds.forecasts.items():
        if target.minute != 0 or not (lo <= target < hi):
            continue
        actual = hourly_actual(ds, target)
        if actual is None:
            continue
        for pub, val in zip(pubs, vals):
            k = math.floor((target - pub).total_seconds() / 3600 / width)
            errs, acts = buckets.setdefault(k, ([], []))
            errs.append(val - actual)
            acts.append(actual)
    return [{"from_h": k * width, "to_h": (k + 1) * width, **summarize(*buckets[k])} for k in sorted(buckets)]


# ---------------- investigation helpers (pure; used by scripts.investigate_bias) ----------------

def largest_errors(rows: list[dict], k: int = 15, over: bool = True) -> list[dict]:
    """The k scored rows with the largest forecast-too-high (over=True) or too-low errors."""
    scored = [r for r in rows if r["error"] is not None]
    return sorted(scored, key=lambda r: -r["error"] if over else r["error"])[:k]


def split_contribution(rows: list[dict], predicate) -> dict:
    """How much of the total error comes from the rows matching `predicate`, and the bias of the rest."""
    scored = [r for r in rows if r["error"] is not None]
    total = sum(r["error"] for r in scored)
    hit = [r for r in scored if predicate(r)]
    rest = [r for r in scored if not predicate(r)]
    mean = lambda xs: sum(r["error"] for r in xs) / len(xs) if xs else None
    return {"n": len(hit), "share_of_hours": len(hit) / len(scored) if scored else None,
            "bias": _r(mean(hit)), "share_of_total_error": sum(r["error"] for r in hit) / total if total else None,
            "n_rest": len(rest), "bias_rest": _r(mean(rest))}


def calibration_curve(pairs: list[tuple[float, float]], width: float = 2000.0) -> list[dict]:
    """E[actual | forecast bin]. Binned by FORECAST (valid); binning by actual fakes a bias."""
    bins: dict[int, list[tuple[float, float]]] = {}
    for f, a in pairs:
        bins.setdefault(int(f // width), []).append((f, a))
    out = []
    for k in sorted(bins):
        b = bins[k]
        mf, ma = sum(f for f, _ in b) / len(b), sum(a for _, a in b) / len(b)
        out.append({"from": k * width, "to": (k + 1) * width, "n": len(b), "mean_forecast": _r(mf),
                    "mean_actual": _r(ma), "bias": _r(mf - ma), "actual_over_forecast": _r(ma / mf, 3)})
    return out


def flat_actual_episodes(rows: list[dict], min_hours: int = 5, max_step_pct: float = 2.0,
                         min_mean_error: float = 2500.0) -> list[dict]:
    """Runs of consecutive hours where actual output barely moves while the forecast is far above it.

    Output that stays flat for hours while the forecast says much more is what an output limit
    (constraint, curtailment, shutdown) would look like. It is a signature, not proof."""
    ordered = sorted((r for r in rows if r["error"] is not None), key=lambda r: r["start"])
    out, i, n = [], 0, len(ordered)
    while i < n:
        j = i
        while (j + 1 < n
               and parse_ts(ordered[j + 1]["start"]) - parse_ts(ordered[j]["start"]) == timedelta(hours=1)
               and abs(ordered[j + 1]["actual"] - ordered[j]["actual"]) <= ordered[j]["actual"] * max_step_pct / 100):
            j += 1
        seg = ordered[i:j + 1]
        if len(seg) >= min_hours:
            mean_err = sum(r["error"] for r in seg) / len(seg)
            if mean_err >= min_mean_error:
                out.append({"start": seg[0]["start"], "end": seg[-1]["start"], "hours": len(seg),
                            "mean_actual": _r(sum(r["actual"] for r in seg) / len(seg)),
                            "mean_forecast": _r(sum(r["forecast"] for r in seg) / len(seg)),
                            "mean_error": _r(mean_err)})
        i = j + 1
    return out


def compare_actual_series(ds: Dataset, other: dict[datetime, float], hours: list[datetime]) -> dict:
    """FUELHH vs another half-hourly actual series (e.g. AGWS wind) on identical UTC hours.

    Only hours where BOTH series have both half-hours are used; diff = other - FUELHH."""
    pairs = []
    for t in hours:
        a, b = hourly_actual(ds, t), hourly_mean(other, t)
        if a is not None and b is not None:
            pairs.append((a, b))
    if not pairs:
        return {"n": 0}
    n = len(pairs)
    mf = sum(a for a, _ in pairs) / n
    mo = sum(b for _, b in pairs) / n
    return {"n": n, "mean_fuelhh": _r(mf), "mean_other": _r(mo), "diff": _r(mo - mf), "ratio": _r(mo / mf, 3),
            "max_fuelhh": _r(max(a for a, _ in pairs)), "max_other": _r(max(b for _, b in pairs)),
            "mae_between": _r(sum(abs(b - a) for a, b in pairs) / n)}


def shift_alignment(ds: Dataset, other: dict[datetime, float], hours: list[datetime],
                    shifts: tuple[int, ...] = (-3, -2, -1, 0, 1, 2, 3)) -> list[dict]:
    """Correlation between FUELHH at hour t and `other` at hour t+shift (hours).

    Correlation, not mean difference, because a constant or proportional gap between the series
    would swamp a mean difference. If both series use the same clock the correlation peaks at
    shift 0. This only DETECTS a clock offset; nothing in the app applies a shift."""
    out = []
    for sh in shifts:
        xs, ys = [], []
        for t in hours:
            a, b = hourly_actual(ds, t), hourly_mean(other, t + timedelta(hours=sh))
            if a is not None and b is not None:
                xs.append(a)
                ys.append(b)
        n = len(xs)
        corr = None
        if n >= 3:
            mx, my = sum(xs) / n, sum(ys) / n
            sxx = sum((x - mx) ** 2 for x in xs)
            syy = sum((y - my) ** 2 for y in ys)
            sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
            corr = _r(sxy / (sxx * syy) ** 0.5, 4) if sxx and syy else None
        out.append({"shift_h": sh, "n": n, "correlation": corr})
    return out


def lag_correlation(a: dict[datetime, float], b: dict[datetime, float],
                    shifts: tuple[int, ...] = (-3, -2, -1, 0, 1, 2, 3)) -> list[dict]:
    """Correlation of a[t] with b[t + shift hours] for two hourly series keyed by hour start.

    Peak at shift 0: same timing. Peak at a negative shift: a[t] resembles b from EARLIER hours
    (a runs late); positive: a[t] resembles later b (a runs early). A measurement only."""
    out = []
    for sh in shifts:
        xs, ys = [], []
        for t, x in a.items():
            y = b.get(t + timedelta(hours=sh))
            if x is not None and y is not None:
                xs.append(x)
                ys.append(y)
        n = len(xs)
        corr = None
        if n >= 3:
            mx, my = sum(xs) / n, sum(ys) / n
            sxx, syy = sum((x - mx) ** 2 for x in xs), sum((y - my) ** 2 for y in ys)
            sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
            corr = _r(sxy / (sxx * syy) ** 0.5, 4) if sxx and syy else None
        out.append({"shift_h": sh, "n": n, "correlation": corr})
    return out


def gap_by_level(ds: Dataset, other: dict[datetime, float], hours: list[datetime], groups: int = 4) -> list[dict]:
    """How the FUELHH shortfall relative to `other` varies with output, binned by `other`.

    A constant ratio means a proportional gap; a constant MW difference means an additive one."""
    pairs = []
    for t in hours:
        a, b = hourly_actual(ds, t), hourly_mean(other, t)
        if a is not None and b is not None:
            pairs.append((b, a))
    pairs.sort()
    out = []
    for g in range(groups):
        chunk = pairs[g * len(pairs) // groups:(g + 1) * len(pairs) // groups]
        if chunk:
            mo, ma = sum(b for b, _ in chunk) / len(chunk), sum(a for _, a in chunk) / len(chunk)
            out.append({"group": g + 1, "n": len(chunk), "mean_other": _r(mo), "mean_fuelhh": _r(ma),
                        "gap": _r(mo - ma), "fuelhh_over_other": _r(ma / mo, 3)})
    return out


def decompose_bias(rows: list[dict], other: dict[datetime, float]) -> dict | None:
    """bias(forecast vs FUELHH) = bias(forecast vs other) + (other - FUELHH), on identical hours."""
    used = [(r, hourly_mean(other, parse_ts(r["start"]))) for r in rows if r["error"] is not None]
    used = [(r, o) for r, o in used if o is not None]
    if not used:
        return None
    n = len(used)
    vs_fuelhh = sum(r["error"] for r, _ in used) / n
    vs_other = sum(r["forecast"] - o for r, o in used) / n
    gap = sum(o - r["actual"] for r, o in used) / n
    return {"n": n, "bias_vs_fuelhh": _r(vs_fuelhh), "bias_vs_other": _r(vs_other), "series_gap": _r(gap),
            "identity_residual": _r(vs_fuelhh - (vs_other + gap), 6)}


def decision_dates() -> list[date]:
    n = (DECISION_END - DECISION_START).days + 1
    return [DECISION_START + timedelta(days=i) for i in range(n)]


def histogram(errors: list[float], width: float = 250.0) -> list[dict]:
    if not errors:
        return []
    lo = math.floor(min(errors) / width) * width
    hi = max(math.ceil(max(errors) / width) * width, lo + width)
    nbins = int(round((hi - lo) / width))
    counts = [0] * nbins
    for e in errors:
        counts[min(int((e - lo) // width), nbins - 1)] += 1
    return [{"from": lo + i * width, "to": lo + (i + 1) * width,
             "mid": lo + (i + 0.5) * width, "count": c} for i, c in enumerate(counts)]


def _horizon_samples(ds: Dataset, first_day: date, last_day: date):
    """(target, lead_h, error, actual) for EVERY published forecast of every scored hour in the window.

    Only forecasts published at or before the hour starts (lead >= 0) are included, and only hours
    with both half-hourly actuals. This is a different, larger sample than the headline metrics,
    which use one forecast per hour (the one available at the cut-off)."""
    lo = datetime.combine(first_day, time(0), tzinfo=UTC)
    hi = datetime.combine(last_day + timedelta(days=1), time(0), tzinfo=UTC)
    for target, (pubs, vals) in ds.forecasts.items():
        if target.minute != 0 or not (lo <= target < hi):
            continue
        actual = hourly_actual(ds, target)
        if actual is None:
            continue
        for pub, val in zip(pubs, vals):
            lead = (target - pub).total_seconds() / 3600
            if lead >= 0:
                yield target, lead, val - actual, actual


def horizon_sample_size(ds: Dataset, first_day: date, last_day: date) -> dict:
    samples = list(_horizon_samples(ds, first_day, last_day))
    return {"forecasts": len(samples), "hours": len({t for t, *_ in samples})}


def horizon_report(ds: Dataset, first_day: date, last_day: date,
                   relevant: tuple[float, float] = DECISION_HORIZON_H) -> list[dict]:
    """Error by how far ahead a forecast was published, over ALL published vintages.

    `relevant` is the lead-time range actually used by the cut-off decision; buckets that
    overlap it are flagged so the chart can highlight them."""
    buckets: dict[tuple[int, int], tuple[list[float], list[float]]] = {b: ([], []) for b in HORIZON_BUCKETS}
    for _, lead, err, actual in _horizon_samples(ds, first_day, last_day):
        for b in HORIZON_BUCKETS:
            if b[0] <= lead < b[1]:
                buckets[b][0].append(err)
                buckets[b][1].append(actual)
                break
    out = []
    for (a, b), (errs, acts) in buckets.items():
        s = summarize(errs, acts)
        if s["n"] == 0:
            continue
        out.append({
            "label": f"{a}h+" if b >= 10_000 else f"{a}-{b}h",
            "from_h": a, "to_h": b,
            "decision_relevant": a < relevant[1] and b > relevant[0],
            **s,
        })
    return out


def accuracy_report(ds: Dataset, cutoff: time) -> dict:
    """Accuracy of the forecast the desk actually had at each January decision."""
    daily, all_e, all_a = [], [], []
    by_hour: dict[int, tuple[list[float], list[float]]] = {h: ([], []) for h in range(24)}
    for d in decision_dates():
        rows = delivery_rows(ds, d + timedelta(days=1), cutoff)
        errs, acts = _paired(rows)
        if not errs:
            continue
        matched = [r for r in rows if r["error"] is not None]
        f_mwh = sum(r["forecast"] for r in matched)  # MW x 1 h = MWh, matched hours only
        a_mwh = sum(r["actual"] for r in matched)
        daily.append({
            "decision_date": d.isoformat(),
            "delivery_date": (d + timedelta(days=1)).isoformat(),
            **summarize(errs, acts),
            "forecast_mwh": _r(f_mwh),
            "actual_mwh": _r(a_mwh),
            "volume_error_mwh": _r(f_mwh - a_mwh),
            "avg_actual_mw": _r(sum(acts) / len(acts)),
        })
        all_e += errs
        all_a += acts
        for r in matched:
            hr = int(r["start"][11:13])
            by_hour[hr][0].append(r["error"])
            by_hour[hr][1].append(r["actual"])
    first = decision_dates()[0] + timedelta(days=1)
    last = decision_dates()[-1] + timedelta(days=1)
    leads = [r["horizon_h"] for d in decision_dates()
             for r in delivery_rows(ds, d + timedelta(days=1), cutoff) if r["horizon_h"] is not None]
    lead_range = (min(leads), max(leads)) if leads else DECISION_HORIZON_H
    horizon = horizon_report(ds, first, last, lead_range)
    h_sample = horizon_sample_size(ds, first, last)
    return {
        "cutoff": cutoff.strftime("%H:%M"),
        "overall": {**summarize(all_e, all_a),
                    "avg_actual_mw": _r(sum(all_a) / len(all_a)) if all_a else None,
                    "days": len(daily)},
        "daily": daily,
        "histogram": histogram(all_e),
        "by_hour": [{"hour": h, **summarize(*by_hour[h])} for h in range(24) if by_hour[h][0]],
        "horizon": horizon,
        # Lead time of the forecast actually used at this cut-off (drives the highlighted buckets).
        "lead_time_h": {"min": lead_range[0], "max": lead_range[1]},
        # What each part of the Accuracy tab is computed from. The two samples differ on purpose.
        "samples": {
            "headline": {"hours": len(all_e), "days": len(daily), "first_delivery": first.isoformat(),
                         "last_delivery": last.isoformat()},
            "horizon": {**h_sample, "max_lead_h": max((b["to_h"] for b in horizon if b["to_h"] < 10_000), default=None)},
        },
    }


def coverage_report(ds: Dataset, cutoff: time) -> dict:
    """How many scope hours can actually be scored. Used by fetch_data, diagnose and tests."""
    hours = with_forecast = with_actual = scored = 0
    for d in decision_dates():
        for r in delivery_rows(ds, d + timedelta(days=1), cutoff):
            hours += 1
            with_forecast += r["forecast"] is not None
            with_actual += r["actual"] is not None
            scored += r["error"] is not None
    return {"hours": hours, "with_forecast": with_forecast, "with_actual": with_actual, "scored": scored}
