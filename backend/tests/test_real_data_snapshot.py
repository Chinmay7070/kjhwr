"""Characterisation of the REAL Elexon data fetched for this project (snapshot, not a requirement).

These tests document what was observed in the January 2026 data, so a reviewer can see the
evidence behind the README's bias findings. They skip automatically on demo data. If you refetch
and Elexon has revised values, tolerances here are wide enough for small revisions but a large
change will (correctly) fail and need a README update.
"""
from datetime import date, time, timedelta

import pytest

from app import analysis
from app.data import DataNotFound, get_dataset

try:
    DS = get_dataset()
except DataNotFound:
    DS = None

pytestmark = pytest.mark.skipif(DS is None or DS.meta.get("source") != "elexon", reason="needs real Elexon data")

FIRST, LAST = date(2026, 1, 2), date(2026, 2, 1)


def cutoff_rows():
    rows = []
    for d in analysis.decision_dates():
        rows += analysis.delivery_rows(DS, d + timedelta(days=1), time(9, 0))
    return rows


def test_source_resolution_and_coverage():
    assert {t.minute for t in DS.forecasts} == {0}                        # WINDFOR is hourly
    assert {t.minute for t in DS.actuals} == {0, 30}                      # FUELHH is half-hourly
    assert DS.meta["duplicate_conflicts"] == 0
    rows = cutoff_rows()
    assert len(rows) == 744 and all(r["error"] is not None for r in rows)  # every January hour scored


def test_cutoff_selection_uses_only_day_ahead_vintages():
    rows = cutoff_rows()
    assert all(r["publish_time"] <= datetime_cutoff(r) for r in rows)
    assert 15 <= min(r["horizon_h"] for r in rows) and max(r["horizon_h"] for r in rows) <= 40


def datetime_cutoff(row):
    day = date.fromisoformat(row["start"][:10])
    return analysis.cutoff_for(day, time(9, 0)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_headline_bias_reproduces():
    o = analysis.accuracy_report(DS, time(9, 0))["overall"]
    assert o["n"] == 744
    assert 1700 <= o["bias"] <= 1800                       # observed: +1,748 MW
    assert o["rmse"] >= o["mae"] >= abs(o["bias"])


def test_bias_is_mostly_systematic_not_noise():
    errs = [r["error"] for r in cutoff_rows()]
    assert sum(e > 0 for e in errs) / len(errs) > 0.80       # observed: 85% of hours forecast > actual


def test_bias_does_not_vanish_as_lead_time_shrinks_or_goes_negative():
    by = {b["from_h"]: b for b in analysis.bias_by_lead_time(DS, FIRST, LAST)}
    after_the_hour = [b["bias"] for k, b in by.items() if k < 0 and b["n"] >= 300]
    day_ahead = [b["bias"] for k, b in by.items() if 18 <= k < 42]
    assert min(after_the_hour) > 1000                         # forecasts issued AFTER the hour: still ~+1.5-1.9 GW
    assert min(day_ahead) > 1000


def test_actual_output_has_a_ceiling_while_the_forecast_exceeds_it():
    rows = cutoff_rows()
    assert max(r["actual"] for r in rows) < 18000             # observed max 17,756 MW
    high_fc = [r for r in rows if r["forecast"] >= 19000]
    assert len(high_fc) >= 50 and all(r["actual"] < 17800 for r in high_fc)


def test_gap_is_not_a_uniform_percentage_scale():
    ps = [(r["forecast"], r["actual"]) for r in cutoff_rows()]
    bins = analysis.bias_by_forecast_level(ps, 5)
    assert bins[-1]["bias"] > 2 * bins[0]["bias"]             # much larger in the top forecast quintile
    assert analysis.fit_calibration(ps)["slope"] < 0.9        # observed 0.81


def test_likely_range_is_close_to_its_nominal_80_percent_but_misses_mostly_on_the_downside():
    inside = below = above = 0
    for d in analysis.decision_dates():
        view = analysis.decision_view(DS, d, time(9, 0))
        for r in view["rows"]:
            if r["actual"] is None or "band_low" not in r:
                continue
            if r["actual"] < r["band_low"]:
                below += 1
            elif r["actual"] > r["band_high"]:
                above += 1
            else:
                inside += 1
    n = inside + below + above
    assert n == 744
    assert 0.75 <= inside / n <= 0.87        # observed 81.5% against a nominal 80%
    assert below > above                     # observed 13.3% below vs 5.2% above: wind arrives short more often than long


def _scored():
    return [r for r in cutoff_rows() if r["error"] is not None]


def test_ceiling_hours_are_a_small_part_of_the_bias():
    """Corrects an earlier reading: the bias is not mainly a top-of-range effect."""
    s = analysis.split_contribution(_scored(), lambda r: r["actual"] >= 16500)
    assert s["share_of_total_error"] < 0.15            # observed 10.9%
    assert s["bias_rest"] > 1500                       # observed +1,727 MW outside the ceiling hours


def test_flat_actual_episodes_explain_only_part_of_the_bias():
    rows = _scored()
    eps = analysis.flat_actual_episodes(rows)
    starts = {r["start"] for r in rows for e in eps if e["start"] <= r["start"] <= e["end"]}
    s = analysis.split_contribution(rows, lambda r: r["start"] in starts)
    assert 5 <= len(eps) <= 12                         # observed 8 episodes
    assert 0.12 <= s["share_of_total_error"] <= 0.30   # observed 19.5%
    assert s["bias_rest"] > 1300                       # observed +1,540 MW outside them
    assert len({round(e["mean_actual"], -3) for e in eps}) >= 3   # different levels: not one fixed cap


def test_error_is_spread_over_many_days_not_one_event():
    by_day = {}
    for r in _scored():
        by_day[r["start"][:10]] = by_day.get(r["start"][:10], 0.0) + r["error"]
    total = sum(by_day.values())
    top5 = sum(sorted(by_day.values(), reverse=True)[:5])
    assert 0.30 <= top5 / total <= 0.50                # observed 41%
    assert sum(v > 0 for v in by_day.values()) >= 27   # observed 27 of 31 days with positive error


def test_broad_proportional_shortfall_in_the_mid_range():
    curve = analysis.calibration_curve([(r["forecast"], r["actual"]) for r in _scored()])
    mid = [b for b in curve if 8000 <= b["from"] < 14000]
    assert mid and all(0.80 <= b["actual_over_forecast"] <= 0.90 for b in mid)   # observed 0.85-0.87


def test_forecast_timing_gives_no_support_for_a_two_hour_shift():
    """Measurement of the real forecast against FUELHH. Nothing in the app shifts any series."""
    fc = {analysis.parse_ts(r["start"]): r["forecast"] for r in _scored()}
    fu = {t: analysis.hourly_actual(DS, t) for t in DS.actuals if t.minute == 0}
    corr = {a["shift_h"]: a["correlation"] for a in analysis.lag_correlation(fc, fu)}
    assert max(corr, key=corr.get) in (-1, 0)                  # observed peak at -1 h
    assert corr[-1] - corr[0] < 0.01                           # observed 0.9327 vs 0.9309: negligible
    assert corr[2] < corr[0] - 0.01 and corr[-2] < corr[-1]    # +2 h (0.911) is clearly worse than 0 h (0.931)
