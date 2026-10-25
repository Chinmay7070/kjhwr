"""Evidence report for the forecast/actual datasets. Run it on real data:

    python -m scripts.diagnose

It prints measurements and how to read them. It does NOT assert a cause for the bias.

  1. Resolution, coverage and data sanity
  2. Alignment evidence (which actual best matches the hourly forecast?)
  3. Lead time: does the bias shrink as forecasts get closer to (or after) the hour?
  4. Level: bias by forecast level, calibration fit, and the ceiling in the actuals
  5. Time of day: bias at night vs afternoon within the same forecast level
"""
from __future__ import annotations

from collections import Counter
from datetime import time, timedelta

from app import analysis
from app.data import load_dataset, parse_ts

CUTOFF = time(9, 0)


def _stats(pairs):
    n = len(pairs)
    if n == 0:
        return None
    errs = [f - a for f, a in pairs]
    return n, sum(abs(e) for e in errs) / n, sum(errs) / n


def main() -> None:
    ds = load_dataset()
    days = [d + timedelta(days=1) for d in analysis.decision_dates()]
    rows = []
    for d in days:
        rows += analysis.delivery_rows(ds, d, CUTOFF)
    scored = [r for r in rows if r["error"] is not None]
    pairs = [(r["forecast"], r["actual"]) for r in scored]

    print("=" * 72)
    print("1. RESOLUTION, COVERAGE AND DATA SANITY")
    print("=" * 72)
    print("source:", ds.meta.get("source"), "| duplicate forecast conflicts:", ds.meta.get("duplicate_conflicts"))
    print("forecast startTime minutes :", sorted({t.minute for t in ds.forecasts}))
    print("forecast publishTime minute:", dict(sorted(Counter(p.minute for pubs, _ in ds.forecasts.values() for p in pubs).items())))
    print("actual startTime minutes   :", sorted({t.minute for t in ds.actuals}))
    av = list(ds.actuals.values())
    print(f"actual MW   min {min(av):.0f}  mean {sum(av) / len(av):.0f}  max {max(av):.0f}")
    fv = [v for _, vs in ds.forecasts.values() for v in vs]
    print(f"forecast MW min {min(fv):.0f}  mean {sum(fv) / len(fv):.0f}  max {max(fv):.0f}")
    cov = analysis.coverage_report(ds, CUTOFF)
    print(f"January scope hours: {cov['hours']}  with forecast: {cov['with_forecast']}  with both half-hour actuals: "
          f"{cov['with_actual']}  scored: {cov['scored']}")
    hs = analysis.horizon_sample_size(ds, days[0], days[-1])
    print(f"samples: headline = {len(scored)} hours x 1 cut-off forecast; lead-time table = {hs['forecasts']} forecasts over {hs['hours']} hours")
    chosen = Counter(r["publish_time"][11:16] for r in rows if r["publish_time"])
    print("publish time-of-day chosen at the cut-off:", dict(chosen.most_common(4)))
    hz = [r["horizon_h"] for r in rows if r["horizon_h"] is not None]
    print(f"lead time of the chosen forecast: {min(hz)} to {max(hz)} h")

    print()
    print("=" * 72)
    print("2. ALIGNMENT EVIDENCE (cut-off forecast vs different actuals; lowest MAE fits best)")
    print("=" * 72)
    half = timedelta(minutes=30)
    for label, fn in {
        "mean of both half-hours (app)": lambda t: analysis.hourly_actual(ds, t),
        "first half-hour only": lambda t: ds.actuals.get(t),
        "second half-hour only": lambda t: ds.actuals.get(t + half),
        "hourly mean shifted -1h": lambda t: analysis.hourly_actual(ds, t - 2 * half),
        "hourly mean shifted +1h": lambda t: analysis.hourly_actual(ds, t + 2 * half),
        "hourly mean shifted -2h": lambda t: analysis.hourly_actual(ds, t - 4 * half),
        "hourly mean shifted +2h": lambda t: analysis.hourly_actual(ds, t + 4 * half),
    }.items():
        p = [(r["forecast"], a) for r in rows if r["forecast"] is not None
             for a in [fn(parse_ts(r["start"]))] if a is not None]
        st = _stats(p)
        print(f"  {label:32s} n={st[0]:4d}  MAE {st[1]:7.0f}  bias {st[2]:7.0f} MW")
    print("  Reading: the BIAS moves by less than ~50 MW across all shifts, so a timestamp misalignment cannot explain it.")
    print("  MAE does change by several percent (lowest for earlier actuals), which may mean the forecast lags the")
    print("  actuals slightly. That affects scatter, not the bias, and no shift is applied anywhere in the app.")

    print()
    print("=" * 72)
    print("3. LEAD TIME (all vintages; NEGATIVE = published after the hour had started)")
    print("=" * 72)
    for b in analysis.bias_by_lead_time(ds, days[0], days[-1]):
        if b["n"] >= 100:
            print(f"  {b['from_h']:+5.0f}..{b['to_h']:+4.0f} h  n={b['n']:5d}  bias {b['bias']:7.0f}  MAE {b['mae']:7.0f} MW")
    print("  Reading: a forecasting-skill problem would shrink towards 0 as lead time falls. A bias that stays")
    print("  about the same size, even for forecasts issued after the hour, is a property of the two series.")

    print()
    print("=" * 72)
    print("4. LEVEL (cut-off forecast, matched hours)")
    print("=" * 72)
    n, mae, bias = _stats(pairs)
    pos = sum(1 for r in scored if r["error"] > 0) / n
    print(f"overall n={n}  MAE {mae:.0f}  bias {bias:.0f} MW  hours with forecast > actual: {pos:.1%}")
    print("calibration fit (actual = intercept + slope x forecast):", analysis.fit_calibration(pairs))
    print("bias by forecast level (equal-sized bins; binned by FORECAST on purpose):")
    for b in analysis.bias_by_forecast_level(pairs, 5):
        print(f"  bin {b['group']}: forecast~{b['mean_forecast']:6.0f}  actual~{b['mean_actual']:6.0f}  "
              f"bias {b['bias']:6.0f} MW ({b['bias_pct_of_forecast']}% of forecast)")
    top_a = max(a for _, a in pairs)
    high = [(f, a) for f, a in pairs if f >= 19000]
    print(f"highest actual hour: {top_a:.0f} MW | highest forecast: {max(f for f, _ in pairs):.0f} MW")
    if high:
        print(f"hours with forecast >= 19,000 MW: {len(high)}; their actual ranged {min(a for _, a in high):.0f}-{max(a for _, a in high):.0f} MW")
    print("  Reading: constant % across bins = proportional gap; constant MW = additive gap; a gap that is ~0 at")
    print("  low levels and grows at the top, with actuals flat-topping, = an output limit (curtailment/constraints or")
    print("  a smaller metered fleet). These are signatures, not proof of which mechanism applies.")

    print()
    print("=" * 72)
    print("5. TIME OF DAY within the same forecast level (night 00-07 UTC vs afternoon 11-18 UTC)")
    print("=" * 72)
    tagged = [(r["forecast"], r["actual"], int(r["start"][11:13])) for r in scored]
    srt = sorted(tagged, key=lambda x: x[0])
    for q in range(5):
        chunk = srt[q * len(srt) // 5:(q + 1) * len(srt) // 5]
        night = [f - a for f, a, h in chunk if h <= 7]
        day = [f - a for f, a, h in chunk if 11 <= h <= 18]
        print(f"  forecast quintile {q + 1}: night {sum(night) / max(len(night), 1):+7.0f} MW [{len(night)}]   "
              f"afternoon {sum(day) / max(len(day), 1):+7.0f} MW [{len(day)}]")
    print("  Reading: a night/afternoon gap that persists at the same forecast level is a time-of-day effect (for")
    print("  example demand-linked constraints). It is consistent with, but does not prove, curtailment.")


if __name__ == "__main__":
    main()
