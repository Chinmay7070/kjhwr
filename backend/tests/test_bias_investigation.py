"""Tests that pin the bias investigation.

Part 1 proves the pipeline is neutral: it invents no bias, recovers a known bias exactly, and
the calibration tools can tell different kinds of gap apart. Part 2 (test_real_data_snapshot.py)
records what the real Elexon data looks like.
"""
import random
from datetime import date, datetime, time, timedelta, timezone

import pytest

from app import analysis
from app.data import build_dataset, collapse_actuals, load_dataset, save_dataset

UTC = timezone.utc
FMT = "%Y-%m-%dT%H:%M:%SZ"
DAYS = range(2, 9)  # delivery days 2..8 Jan 2026


def make_ds(actual_fn, forecast_fn, days=DAYS):
    """Hourly forecast published 07:30 on D-1; both half-hour actuals equal the hourly actual."""
    acts, fcs = [], []
    for day in days:
        for h in range(24):
            t = datetime(2026, 1, day, h, tzinfo=UTC)
            a = actual_fn(day, h)
            f = forecast_fn(day, h, a)
            acts += [(t.strftime(FMT), a), ((t + timedelta(minutes=30)).strftime(FMT), a)]
            pub = datetime(2026, 1, day, tzinfo=UTC) - timedelta(days=1) + timedelta(hours=7, minutes=30)
            fcs.append((t.strftime(FMT), pub.strftime(FMT), f))
    return build_dataset(acts, fcs)


def varied(day, h):
    return 3000.0 + ((day * 7 + h * 13) % 11) * 900.0


def pairs_of(ds, cutoff=time(9, 0)):
    out = []
    for d in analysis.decision_dates():
        for r in analysis.delivery_rows(ds, d + timedelta(days=1), cutoff):
            if r["error"] is not None:
                out.append((r["forecast"], r["actual"]))
    return out


# ---------- Part 1a: the pipeline invents no bias and recovers a known one ----------

@pytest.mark.parametrize("cutoff", [time(8, 0), time(9, 0), time(9, 20), time(23, 0)])
def test_perfect_forecast_gives_exactly_zero_error_at_any_cutoff(cutoff):
    ds = make_ds(varied, lambda d, h, a: a)
    rep = analysis.accuracy_report(ds, cutoff)
    assert rep["overall"]["n"] == len(DAYS) * 24
    assert rep["overall"]["bias"] == rep["overall"]["mae"] == rep["overall"]["rmse"] == 0
    assert all(d["volume_error_mwh"] == 0 for d in rep["daily"])


def test_constant_offset_is_recovered_exactly_in_every_metric_and_unit():
    ds = make_ds(varied, lambda d, h, a: a + 1000.0)
    rep = analysis.accuracy_report(ds, time(9, 0))
    o = rep["overall"]
    mean_a = sum(a for _, a in pairs_of(ds)) / o["n"]
    assert (o["bias"], o["mae"], o["rmse"], o["p90_abs"]) == (1000.0, 1000.0, 1000.0, 1000.0)
    assert o["wmape"] == pytest.approx(100 * 1000 / mean_a, abs=0.06)
    assert all(d["volume_error_mwh"] == 24 * 1000.0 for d in rep["daily"])  # 1000 MW x 24 h


def test_each_half_hour_actual_is_used_once_not_twice():
    # Actual swings 4000/8000 between half-hours; the hour must score against 6000, not 4000 or 8000.
    t = "2026-01-02T00:00:00Z"
    ds = build_dataset([(t, 4000.0), ("2026-01-02T00:30:00Z", 8000.0)],
                       [(t, "2026-01-01T07:30:00Z", 6000.0)])
    row = analysis.delivery_rows(ds, date(2026, 1, 2), time(9, 0))[0]
    assert row["actual"] == 6000.0 and row["error"] == 0.0


def test_dataset_round_trip_through_storage_keeps_the_bias(tmp_path):
    ds = make_ds(varied, lambda d, h, a: a + 500.0)
    acts = [[t.strftime(FMT), v] for t, v in sorted(ds.actuals.items())]
    fcs = [[t.strftime(FMT), p.strftime(FMT), v] for t, (ps, vs) in sorted(ds.forecasts.items()) for p, v in zip(ps, vs)]
    save_dataset(tmp_path, acts, fcs, {"source": "test"})
    again = load_dataset(tmp_path)
    assert analysis.accuracy_report(again, time(9, 0))["overall"]["bias"] == 500.0


def test_forecasts_published_after_the_hour_are_never_selected_at_the_cutoff():
    t = "2026-01-02T00:00:00Z"
    ds = build_dataset([(t, 1000.0), ("2026-01-02T00:30:00Z", 1000.0)],
                       [(t, "2026-01-01T07:30:00Z", 2000.0),   # day-ahead, selected
                        (t, "2026-01-02T03:30:00Z", 1000.0)])  # published after the hour: hindsight
    row = analysis.delivery_rows(ds, date(2026, 1, 2), time(9, 0))[0]
    assert row["forecast"] == 2000.0 and row["horizon_h"] == 16.5


# ---------- Part 1b: duplicates in the source are handled deterministically ----------

def test_collapse_actuals_keeps_latest_publish_and_counts_conflicts_regardless_of_order():
    rows = [
        {"startTime": "A", "generation": 1.0, "publishTime": "2026-01-01T00:00:00Z"},
        {"startTime": "A", "generation": 2.0, "publishTime": "2026-01-02T00:00:00Z"},
        {"startTime": "B", "generation": 5.0, "publishTime": "2026-01-01T00:00:00Z"},
        {"startTime": "B", "generation": 5.0, "publishTime": "2026-01-02T00:00:00Z"},
    ]
    for ordering in (rows, rows[::-1]):
        values, conflicts = collapse_actuals(ordering)
        assert values == {"A": 2.0, "B": 5.0}
        assert conflicts == 1  # only A disagrees with itself


# ---------- Part 1c: the diagnostics tell different gaps apart ----------

def test_lead_time_report_includes_negative_lead_times():
    t = "2026-01-15T00:00:00Z"
    ds = build_dataset([(t, 1000.0), ("2026-01-15T00:30:00Z", 1000.0)], [
        (t, "2026-01-15T03:30:00Z", 1000.0),   # published 3.5 h AFTER the hour
        (t, "2026-01-14T04:00:00Z", 1500.0),   # 20 h ahead
    ])
    rep = {b["from_h"]: b for b in analysis.bias_by_lead_time(ds, date(2026, 1, 15), date(2026, 1, 15))}
    assert rep[-6.0]["bias"] == 0 and rep[18.0]["bias"] == 500.0


def test_proportional_gap_has_constant_percentage_and_slope_below_one():
    ds = make_ds(varied, lambda d, h, a: 1.15 * a)
    ps = pairs_of(ds)
    for b in analysis.bias_by_forecast_level(ps, 4):
        assert b["bias_pct_of_forecast"] == pytest.approx(100 * (1 - 1 / 1.15), abs=0.1)
    fit = analysis.fit_calibration(ps)
    assert fit["slope"] == pytest.approx(1 / 1.15, abs=0.001) and abs(fit["intercept"]) < 1


def test_additive_gap_has_constant_mw_and_slope_one():
    ps = pairs_of(make_ds(varied, lambda d, h, a: a + 1000.0))
    assert [b["bias"] for b in analysis.bias_by_forecast_level(ps, 4)] == [1000.0] * 4
    assert analysis.fit_calibration(ps)["slope"] == pytest.approx(1.0, abs=0.001)


def test_output_ceiling_shows_no_bias_below_the_cap_and_positive_bias_above_it():
    cap = 9000.0
    ds = make_ds(lambda d, h: 3000.0 + ((d * 7 + h * 13) % 11) * 1500.0, lambda d, h, a: a)
    # forecast = uncapped potential; actual = what the metered fleet could deliver
    potential = {k: v for k, v in ((t, v) for t, v in ds.actuals.items())}
    acts = [(t.strftime(FMT), min(v, cap)) for t, v in sorted(potential.items())]
    fcs = [(t.strftime(FMT), ps[0].strftime(FMT), vs[0]) for t, (ps, vs) in sorted(ds.forecasts.items())]
    capped = build_dataset(acts, fcs)
    bins = analysis.bias_by_forecast_level(pairs_of(capped), 4)
    assert bins[0]["bias"] == 0 and bins[-1]["bias"] > 1000
    assert analysis.fit_calibration(pairs_of(capped))["slope"] < 0.95


def test_binning_by_forecast_is_unbiased_for_a_calibrated_forecast_but_binning_by_actual_is_not():
    rng = random.Random(7)
    ps = []
    for _ in range(4000):
        f = max(500.0, rng.gauss(9000, 3500))
        ps.append((f, f + rng.gauss(0, 1500)))     # actual scatters around the forecast: calibrated
    for b in analysis.bias_by_forecast_level(ps, 4):
        assert abs(b["bias"]) < 120                  # measured max ~64 MW: no spurious bias by forecast bin
    by_actual = sorted(ps, key=lambda p: p[1])
    q = len(ps) // 4
    top, bottom = by_actual[-q:], by_actual[:q]
    assert sum(f - a for f, a in top) / q < -500     # measured ~-742: binning by actual FAKES a bias
    assert sum(f - a for f, a in bottom) / q > 500   # measured ~+732
    assert analysis.fit_calibration(ps)["slope"] == pytest.approx(1.0, abs=0.03)
