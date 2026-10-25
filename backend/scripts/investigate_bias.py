"""Reproducible evidence for the forecast bias. Read-only: changes no data and no app logic.

    python -m scripts.investigate_bias

  A. Largest-error hours and how concentrated the error is
  B. Attribution: ceiling hours, flat-actual episodes, everything else
  C. Calibration curve (actual vs forecast level)
  D. Forecasts published AFTER the hour
  E. FUELHH vs AGWS (B1630) vs WINDFOR on identical UTC hours (needs: python -m scripts.fetch_agws)

It prints measurements and how to read them. It does not assert a cause.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timedelta, timezone

from app import analysis
from app.data import load_agws, load_agws_components, load_dataset, parse_ts

CUTOFF = time(9, 0)
UTC = timezone.utc


def main() -> None:
    ds = load_dataset()
    days = [d + timedelta(days=1) for d in analysis.decision_dates()]
    rows = []
    for d in days:
        rows += analysis.delivery_rows(ds, d, CUTOFF)
    scored = [r for r in rows if r["error"] is not None]
    n, total = len(scored), sum(r["error"] for r in scored)
    print(f"source: {ds.meta.get('source')} | scored hours: {n} | bias {total / n:.0f} MW | "
          f"mean actual {sum(r['actual'] for r in scored) / n:.0f} MW | mean forecast {sum(r['forecast'] for r in scored) / n:.0f} MW")

    print("\n" + "=" * 74 + "\nA. LARGEST-ERROR HOURS (forecast too high)\n" + "=" * 74)
    for r in analysis.largest_errors(scored, 12):
        print(f"  {r['start']}  forecast {r['forecast']:7.0f}  actual {r['actual']:7.0f}  error {r['error']:+6.0f}"
              f"  lead {r['horizon_h']}h  issued {r['publish_time'][5:16]}")
    print("  largest forecast-too-low:", [(r["start"][5:13], round(r["error"])) for r in analysis.largest_errors(scored, 3, over=False)])
    srt = sorted(scored, key=lambda r: -r["error"])
    for k in (50, 100, 150):
        print(f"  top {k} hours ({k / n:.0%} of hours) carry {sum(r['error'] for r in srt[:k]) / total:.0%} of the total error")
    by_day = defaultdict(float)
    for r in scored:
        by_day[r["start"][:10]] += r["error"]
    top_days = sorted(by_day.items(), key=lambda kv: -kv[1])[:5]
    print(f"  worst 5 days {[d[5:] for d, _ in top_days]} carry {sum(v for _, v in top_days) / total:.0%} of the total error")

    print("\n" + "=" * 74 + "\nB. ATTRIBUTION\n" + "=" * 74)
    ceil = analysis.split_contribution(scored, lambda r: r["actual"] >= 16500)
    print(f"  hours with actual >= 16,500 MW: {ceil['n']} ({ceil['share_of_hours']:.1%}); bias there {ceil['bias']} MW;"
          f" share of total error {ceil['share_of_total_error']:.1%}")
    print(f"  all other hours ({ceil['n_rest']}): bias {ceil['bias_rest']} MW   <- the bias is NOT mainly a top-of-range effect")
    eps = analysis.flat_actual_episodes(scored)
    ep_starts = {r["start"] for r in scored for e in eps if e["start"] <= r["start"] <= e["end"]}
    sp = analysis.split_contribution(scored, lambda r: r["start"] in ep_starts)
    print(f"  flat-actual episodes (>=5 h, actual moves <=2%/h, forecast >=2.5 GW higher): {len(eps)} episodes, {sp['n']} hours "
          f"({sp['share_of_hours']:.1%}), {sp['share_of_total_error']:.1%} of total error; bias outside them {sp['bias_rest']} MW")
    for e in eps:
        print(f"    {e['start'][:13]} -> {e['end'][11:13]}h  {e['hours']:2d} h  actual {e['mean_actual']:6.0f}  forecast {e['mean_forecast']:6.0f}  error {e['mean_error']:+6.0f}")
    print("  Reading: flat output at DIFFERENT levels while the forecast is far higher looks like an output limit")
    print("  (constraint, curtailment, shutdown), not one fixed capacity ceiling. It explains only part of the bias.")

    print("\n" + "=" * 74 + "\nC. CALIBRATION CURVE (bins of FORECAST)\n" + "=" * 74)
    pairs = [(r["forecast"], r["actual"]) for r in scored]
    for b in analysis.calibration_curve(pairs):
        print(f"  forecast {b['from']:6.0f}-{b['to']:6.0f}  n={b['n']:3d}  forecast {b['mean_forecast']:6.0f}  actual {b['mean_actual']:6.0f}"
              f"  bias {b['bias']:6.0f}  actual/forecast {b['actual_over_forecast']}")
    print("  fit actual = a + b x forecast: all", analysis.fit_calibration(pairs), "| forecast < 17 GW",
          analysis.fit_calibration([p for p in pairs if p[0] < 17000]))
    print("  Reading: actual/forecast near 0.85 across the mid range is a broad proportional shortfall; the extra")
    print("  shortfall in the top bins is the episodic part. Neither pins down the mechanism.")

    print("\n" + "=" * 74 + "\nD. FORECASTS PUBLISHED AFTER THE HOUR (hindsight vintages)\n" + "=" * 74)
    for b in analysis.bias_by_lead_time(ds, days[0], days[-1], width=12.0):
        if b["from_h"] < 0:
            print(f"  published {abs(b['to_h']):.0f}..{abs(b['from_h']):.0f} h after the hour: n={b['n']:5d}  bias {b['bias']:6.0f} MW")
    print("  Reading: still ~+1.6-1.9 GW a day after the hour, so WINDFOR is not re-anchored to the metered output.")

    print("\n" + "=" * 74 + "\nE. FUELHH vs AGWS (B1630) vs WINDFOR on identical UTC hours\n" + "=" * 74)
    agws = load_agws()
    if agws is None:
        print("  No wind_agws.json.gz found. Run `python -m scripts.fetch_agws`, then rerun this script.")
        return
    hours = [parse_ts(r["start"]) for r in rows]
    cmp_ = analysis.compare_actual_series(ds, agws, hours)
    if not cmp_["n"]:
        print("  AGWS file has no overlapping hours; check the fetch output.")
        return
    print(f"  hours with both series: {cmp_['n']} of {len(hours)}")
    print(f"  mean FUELHH {cmp_['mean_fuelhh']:.0f} MW | mean AGWS wind {cmp_['mean_other']:.0f} MW | AGWS - FUELHH {cmp_['diff']:+.0f} MW | ratio {cmp_['ratio']}")
    print(f"  max hourly FUELHH {cmp_['max_fuelhh']:.0f} | max hourly AGWS {cmp_['max_other']:.0f}  (is the apparent ceiling in both?)")
    both = [(r["forecast"], analysis.hourly_mean(agws, parse_ts(r["start"])), r["actual"]) for r in scored
            if analysis.hourly_mean(agws, parse_ts(r["start"])) is not None]
    if both:
        e_f = [f - a for f, _, a in both]
        e_g = [f - g for f, g, _ in both]
        print(f"  WINDFOR minus FUELHH: bias {sum(e_f) / len(e_f):+.0f}  MAE {sum(map(abs, e_f)) / len(e_f):.0f}")
        print(f"  WINDFOR minus AGWS  : bias {sum(e_g) / len(e_g):+.0f}  MAE {sum(map(abs, e_g)) / len(e_g):.0f}")
        inside = [(f, g, a) for (f, g, a), r in zip(both, scored) if r["start"] in ep_starts]
        if inside:
            m = lambda i: sum(x[i] for x in inside) / len(inside)
            print(f"  during the flat-actual episodes ({len(inside)} h): forecast {m(0):.0f} | AGWS {m(1):.0f} | FUELHH {m(2):.0f}")
    dec = analysis.decompose_bias(scored, agws)
    print(f"  decomposition on identical hours: bias vs FUELHH {dec['bias_vs_fuelhh']:+.0f} = bias vs AGWS {dec['bias_vs_other']:+.0f}"
          f" + (AGWS - FUELHH) {dec['series_gap']:+.0f}   [identity residual {dec['identity_residual']}]")
    print("  the 12 largest forecast-too-high hours, with AGWS alongside:")
    for r in analysis.largest_errors(scored, 12):
        g = analysis.hourly_mean(agws, parse_ts(r["start"]))
        if g is not None:
            print(f"    {r['start'][5:13]}  forecast {r['forecast']:7.0f}  FUELHH {r['actual']:7.0f}  AGWS {g:7.0f}"
                  f"   vs FUELHH {r['error']:+6.0f}   vs AGWS {r['forecast'] - g:+6.0f}")
    print("  How to read E (nothing here is concluded for you):")
    print("   - AGWS ~ FUELHH and WINDFOR-minus-AGWS bias ~ WINDFOR-minus-FUELHH bias -> not a FUELHH coverage gap;")
    print("     the gap sits between WINDFOR and measured output.")
    print("   - AGWS ~ WINDFOR level (WINDFOR-minus-AGWS bias ~ 0) and AGWS well above FUELHH -> a coverage/definition")
    print("     difference between FUELHH and the other two.")
    print("   - AGWS also flat during the episodes -> a real output limit; AGWS rising there -> FUELHH under-measures.")
    print("\n" + "=" * 74 + "\nF. WHY AGWS AND FUELHH DIFFER (units, clock, aggregation, shape)\n" + "=" * 74)
    comps = load_agws_components()
    for psr, series in comps.items():
        vals = [v for t in hours if (v := analysis.hourly_mean(series, t)) is not None]
        if vals:
            print(f"  {psr:13s} mean {sum(vals) / len(vals):7.0f} MW  max {max(vals):7.0f} MW  hours {len(vals)}")
    print("  clock check: correlation of FUELHH(t) with AGWS(t+shift); it should peak at shift 0 if both use the same clock")
    for a in analysis.shift_alignment(ds, agws, hours):
        print(f"    shift {a['shift_h']:+d} h: n={a['n']:3d}  correlation {a['correlation']}")
    print("  gap by AGWS output level (quartiles of AGWS):")
    for g in analysis.gap_by_level(ds, agws, hours):
        print(f"    Q{g['group']}: AGWS~{g['mean_other']:6.0f}  FUELHH~{g['mean_fuelhh']:6.0f}  gap {g['gap']:6.0f} MW  FUELHH/AGWS {g['fuelhh_over_other']}")
    by_h = defaultdict(list)
    for t in hours:
        a, b = analysis.hourly_actual(ds, t), analysis.hourly_mean(agws, t)
        if a is not None and b is not None:
            by_h[t.hour].append(b - a)
    print("  gap by hour of day (UTC):", " ".join(f"{h:02d}:{sum(v) / len(v):+5.0f}" for h, v in sorted(by_h.items())))
    print("  Reading: unit/aggregation errors would show as a ~2x or ~0.5x ratio or a missing component; a clock offset as")
    print("  a correlation peak away from shift 0. A gap that grows with output points to something that scales with wind (such as")
    print("  wind outside FUELHH's metering boundary); a constant MW gap would point elsewhere.")

    print("\n" + "=" * 74 + "\nG. FORECAST TIMING (measurement only; no shift is applied anywhere)\n" + "=" * 74)
    fc_h = {parse_ts(r["start"]): r["forecast"] for r in scored}
    fu_h = {t: analysis.hourly_actual(ds, t) for t in ds.actuals if t.minute == 0}
    ag_h = {t: analysis.hourly_mean(agws, t) for t in agws if t.minute == 0}
    for name, series in (("FUELHH", fu_h), ("AGWS", ag_h)):
        print(f"  corr(forecast(t), {name}(t+shift)):", "  ".join(
            f"{a['shift_h']:+d}h:{a['correlation']}" for a in analysis.lag_correlation(fc_h, series)))
    print("  Reading: a peak at 0 means the forecast is on the same clock as the output. A peak at a negative shift means")
    print("  the forecast resembles EARLIER output (it runs late, a forecast-skill issue). Neither is a clock error between")
    print("  FUELHH and AGWS (section F shows those two agree at shift 0), and the app applies no shift in either case.")


if __name__ == "__main__":
    main()
