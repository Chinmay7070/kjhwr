"""Regression tests for the analysis rules. Each test pins one rule that a reviewer could question."""
import math
from datetime import date, datetime, time, timedelta, timezone

import pytest

from app import analysis
from app.data import build_dataset, parse_ts

UTC = timezone.utc
D = date(2026, 1, 15)  # delivery day used in most tests; decision day is 14 Jan


def ds_single_hour():
    # Hour 00:00 on 15 Jan. Actuals 1000 and 2000 MW in its two half-hours -> hourly mean 1500.
    t = "2026-01-15T00:00:00Z"
    forecasts = [
        (t, "2026-01-13T21:00:00Z", 1000.0),  # older vintage
        (t, "2026-01-14T08:00:00Z", 2000.0),  # latest before cutoff -> must be chosen
        (t, "2026-01-14T10:00:00Z", 9999.0),  # after cutoff -> must be ignored
    ]
    actuals = [(t, 1000.0), ("2026-01-15T00:30:00Z", 2000.0)]
    return build_dataset(actuals, forecasts)


# ---------- resolution: hourly forecast vs mean of two half-hourly actuals ----------

def test_actual_is_mean_of_two_half_hours():
    ds = ds_single_hour()
    assert analysis.hourly_actual(ds, parse_ts("2026-01-15T00:00:00Z")) == 1500.0


def test_hour_is_dropped_when_a_half_hour_actual_is_missing():
    ds = build_dataset([("2026-01-15T00:00:00Z", 1000.0)],
                       [("2026-01-15T00:00:00Z", "2026-01-14T08:00:00Z", 2000.0)])
    row = analysis.delivery_rows(ds, D, time(9, 0))[0]
    assert row["actual"] is None and row["error"] is None
    assert row["forecast"] == 2000.0  # forecast still shown, just not scored


def test_forecast_is_never_copied_onto_half_hour_slots():
    ds = ds_single_hour()
    assert all(t.minute == 0 for t in ds.forecasts)
    rows = analysis.delivery_rows(ds, D, time(9, 0))
    assert len(rows) == 24
    assert all(r["start"].endswith(":00:00Z") for r in rows)


# ---------- UTC alignment ----------

def test_timestamps_are_normalised_to_utc():
    assert parse_ts("2026-01-15T00:00:00Z") == datetime(2026, 1, 15, tzinfo=UTC)
    assert parse_ts("2026-01-15T01:00:00+01:00") == datetime(2026, 1, 15, tzinfo=UTC)
    assert parse_ts("2026-01-15T00:00:00") == datetime(2026, 1, 15, tzinfo=UTC)  # naive treated as UTC


def test_delivery_day_spans_00_to_24_utc():
    rows = analysis.delivery_rows(ds_single_hour(), D, time(9, 0))
    assert rows[0]["start"] == "2026-01-15T00:00:00Z"
    assert rows[-1]["start"] == "2026-01-15T23:00:00Z"


# ---------- publishTime cut-off ----------

def test_latest_forecast_ignores_later_publications():
    row = analysis.delivery_rows(ds_single_hour(), D, time(9, 0))[0]
    assert row["forecast"] == 2000.0
    assert row["publish_time"] == "2026-01-14T08:00:00Z"
    assert row["error"] == 500.0  # 2000 - mean(1000, 2000)
    assert row["horizon_h"] == 16.0


def test_forecast_published_exactly_at_cutoff_is_eligible():
    ds = build_dataset([("2026-01-15T00:00:00Z", 1.0), ("2026-01-15T00:30:00Z", 1.0)],
                       [("2026-01-15T00:00:00Z", "2026-01-14T09:00:00Z", 7.0)])
    assert analysis.delivery_rows(ds, D, time(9, 0))[0]["forecast"] == 7.0
    assert analysis.delivery_rows(ds, D, time(8, 59))[0]["forecast"] is None


def test_cutoff_changes_vintage():
    ds = ds_single_hour()
    assert analysis.delivery_rows(ds, D, time(8, 0))[0]["forecast"] == 2000.0
    assert analysis.delivery_rows(ds, D, time(7, 59))[0]["forecast"] == 1000.0
    assert analysis.delivery_rows(ds, D, time(10, 0))[0]["forecast"] == 9999.0


def test_cutoff_is_on_the_day_before_delivery():
    # Published on the delivery day itself is always after the D-1 cutoff.
    ds = build_dataset([("2026-01-15T00:00:00Z", 1.0), ("2026-01-15T00:30:00Z", 1.0)],
                       [("2026-01-15T00:00:00Z", "2026-01-15T00:00:00Z", 5.0)])
    assert analysis.delivery_rows(ds, D, time(23, 59))[0]["forecast"] is None


def test_no_forecast_before_cutoff_gives_none():
    ds = build_dataset([("2026-01-15T00:00:00Z", 1.0), ("2026-01-15T00:30:00Z", 1.0)],
                       [("2026-01-15T00:00:00Z", "2026-01-14T10:00:00Z", 9999.0)])
    row = analysis.delivery_rows(ds, D, time(9, 0))[0]
    assert row["forecast"] is None and row["error"] is None


def test_conflicting_duplicate_forecasts_are_counted():
    t = "2026-01-15T00:00:00Z"
    ds = build_dataset([], [(t, "2026-01-14T08:00:00Z", 1.0), (t, "2026-01-14T08:00:00Z", 2.0)])
    assert ds.meta["duplicate_conflicts"] == 1


# ---------- January 2026 scope ----------

def test_decision_dates_are_exactly_january_2026():
    days = analysis.decision_dates()
    assert days[0] == date(2026, 1, 1) and days[-1] == date(2026, 1, 31) and len(days) == 31


def test_accuracy_covers_only_january_decisions_not_warmup_or_february_decisions():
    def day(d, fc, act):
        t0 = datetime.combine(d, time(0), tzinfo=UTC)
        acts = [(t0.strftime("%Y-%m-%dT%H:%M:%SZ"), act),
                ((t0 + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ"), act)]
        pub = (t0 - timedelta(days=1)).replace(hour=8).strftime("%Y-%m-%dT%H:%M:%SZ")
        return acts, [(t0.strftime("%Y-%m-%dT%H:%M:%SZ"), pub, fc)]

    rows_a, rows_f = [], []
    for d in (date(2025, 12, 20), date(2026, 1, 2), date(2026, 2, 1), date(2026, 2, 2)):
        a, f = day(d, 110.0, 100.0)
        rows_a += a
        rows_f += f
    rep = analysis.accuracy_report(build_dataset(rows_a, rows_f), time(9, 0))
    assert [d["delivery_date"] for d in rep["daily"]] == ["2026-01-02", "2026-02-01"]
    assert rep["overall"]["n"] == 2


# ---------- metrics & units ----------

def test_summarize_known_values():
    s = analysis.summarize([100.0, -200.0, 300.0, 0.0], [1000.0] * 4)
    assert (s["n"], s["mae"], s["bias"], s["wmape"], s["max_abs"]) == (4, 150.0, 50.0, 15.0, 300.0)
    assert s["rmse"] == pytest.approx(187.1, abs=0.1)


def test_summarize_empty():
    assert analysis.summarize([], [])["n"] == 0


def test_quantile_interpolates():
    assert analysis.quantile([1, 2, 3, 4], 0.5) == 2.5
    assert analysis.quantile([5], 0.9) == 5


def _random_dataset(seed=3, days=6):
    import random
    rng = random.Random(seed)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    acts, fcs = [], []
    for h in range(days * 24):
        t = base + timedelta(hours=h)
        a1, a2 = rng.uniform(500, 9000), rng.uniform(500, 9000)
        acts += [(t.strftime(fmt), a1), ((t + timedelta(minutes=30)).strftime(fmt), a2)]
        pub = (t - timedelta(days=1)).replace(hour=8, minute=30)
        fcs.append((t.strftime(fmt), pub.strftime(fmt), (a1 + a2) / 2 + rng.gauss(300, 700)))
    return build_dataset(acts, fcs)


def test_metrics_match_an_independent_calculation():
    ds = _random_dataset()
    rows = []
    for d in range(2, 7):
        rows += analysis.delivery_rows(ds, date(2026, 1, d), time(9, 0))
    pairs = [(r["forecast"], r["actual"]) for r in rows if r["error"] is not None]
    errs = [f - a for f, a in pairs]
    n = len(pairs)
    s = analysis.summarize([r["error"] for r in rows if r["error"] is not None],
                           [r["actual"] for r in rows if r["error"] is not None])
    assert s["n"] == n
    assert s["mae"] == pytest.approx(sum(abs(e) for e in errs) / n, abs=0.06)
    assert s["rmse"] == pytest.approx(math.sqrt(sum(e * e for e in errs) / n), abs=0.06)
    assert s["bias"] == pytest.approx(sum(errs) / n, abs=0.06)
    assert s["wmape"] == pytest.approx(100 * sum(abs(e) for e in errs) / sum(a for _, a in pairs), abs=0.06)
    assert s["rmse"] >= s["mae"] >= abs(s["bias"])


def test_daily_volume_uses_same_matched_hours_and_hourly_units():
    ds = _random_dataset()
    rep = analysis.accuracy_report(ds, time(9, 0))
    day = next(d for d in rep["daily"] if d["delivery_date"] == "2026-01-03")
    rows = analysis.delivery_rows(ds, date(2026, 1, 3), time(9, 0))
    matched = [r for r in rows if r["error"] is not None]
    assert day["n"] == len(matched)
    # MW x 1 hour = MWh, and the volume miss is the sum of the SAME hourly errors.
    assert day["forecast_mwh"] == pytest.approx(sum(r["forecast"] for r in matched), abs=0.06)
    assert day["actual_mwh"] == pytest.approx(sum(r["actual"] for r in matched), abs=0.06)
    assert day["volume_error_mwh"] == pytest.approx(sum(r["error"] for r in matched), abs=0.1)
    assert day["volume_error_mwh"] == pytest.approx(day["bias"] * day["n"], abs=0.06 * day["n"])


def test_single_hour_units():
    # forecast 2000 MW vs hourly actual 1500 MW for one hour: miss is 500 MWh, not 250.
    view = analysis.decision_view(ds_single_hour(), date(2026, 1, 14), time(9, 0))
    assert view["summary"]["forecast_mwh"] == 2000.0
    assert view["summary"]["actual_mwh"] == 1500.0
    assert view["summary"]["slots"] == 1


def test_horizon_report_excludes_forecasts_published_after_target_and_unmatched_hours():
    t = "2026-01-15T00:00:00Z"
    ds = build_dataset([(t, 100.0), ("2026-01-15T00:30:00Z", 100.0)], [
        (t, "2026-01-14T00:00:00Z", 110.0),   # 24h ahead, error +10
        (t, "2026-01-15T02:00:00Z", 999.0),   # published after the target: must be skipped
    ])
    rep = analysis.horizon_report(ds, date(2026, 1, 15), date(2026, 1, 15))
    assert sum(b["n"] for b in rep) == 1
    assert rep[0]["bias"] == 10.0


def test_histogram_counts_everything():
    errs = [-300.0, -10.0, 10.0, 260.0, 260.0]
    bins = analysis.histogram(errs, 250.0)
    assert sum(b["count"] for b in bins) == len(errs) and bins[0]["from"] == -500.0


def test_decision_view_without_history_has_no_band():
    view = analysis.decision_view(ds_single_hour(), date(2026, 1, 14), time(9, 0))
    assert view["band"] is None and view["rows"][0]["forecast"] == 2000.0


def test_band_uses_only_days_finished_before_the_cutoff():
    # Errors on delivery day 15 Jan must NOT feed the band for the decision made on 14 Jan.
    ds = _random_dataset(days=20)
    v14 = analysis.decision_view(ds, date(2026, 1, 14), time(9, 0))
    used = {r["start"][:10] for r in v14["rows"]}
    assert used == {"2026-01-15"}
    assert v14["history"]["days_used"] <= 13  # 2..13 Jan are the only finished days in the data
