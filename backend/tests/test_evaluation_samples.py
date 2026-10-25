"""Completeness flags and sample descriptions: each number must say what it was computed from."""
from datetime import date, datetime, time, timedelta, timezone

from app import analysis
from app.data import build_dataset

UTC = timezone.utc
FMT = "%Y-%m-%dT%H:%M:%SZ"
DECISION = date(2026, 1, 14)   # delivery day 15 Jan


def day_ds(skip_forecast=(), skip_actual=(), drop_half_hour_actual=()):
    acts, fcs = [], []
    for h in range(24):
        t = datetime(2026, 1, 15, h, tzinfo=UTC)
        if h not in skip_actual:
            acts.append((t.strftime(FMT), 1000.0))
            if h not in drop_half_hour_actual:
                acts.append(((t + timedelta(minutes=30)).strftime(FMT), 1000.0))
        if h not in skip_forecast:
            fcs.append((t.strftime(FMT), "2026-01-14T07:30:00Z", 1100.0))
    return build_dataset(acts, fcs)


def summary(ds):
    return analysis.decision_view(ds, DECISION, time(9, 0))["summary"]


# ---------- actual_complete: true only when all 24 hourly actuals exist ----------

def test_full_day_is_complete():
    s = summary(day_ds())
    assert s["actual_complete"] and s["forecast_complete"] and s["actual_hours"] == s["scored_hours"] == 24


def test_hour_missing_both_forecast_and_actual_is_NOT_complete():
    # Regression: the old condition (matched == forecast hours) said True here with only 23 actuals.
    s = summary(day_ds(skip_forecast={5}, skip_actual={5}))
    assert s["actual_hours"] == 23 and s["scored_hours"] == 23
    assert s["actual_complete"] is False


def test_missing_actual_hour_is_not_complete_even_when_forecast_is():
    s = summary(day_ds(skip_actual={7}))
    assert s["actual_complete"] is False and s["forecast_complete"] is True


def test_one_missing_half_hour_actual_makes_the_hour_and_day_incomplete():
    s = summary(day_ds(drop_half_hour_actual={9}))
    assert s["actual_hours"] == 23 and s["actual_complete"] is False


def test_complete_actuals_with_a_missing_forecast_hour_are_flagged_separately():
    s = summary(day_ds(skip_forecast={3}))
    assert s["actual_complete"] is True and s["forecast_complete"] is False
    assert s["scored_hours"] == 23    # the UI must not compare totals over different hours


# ---------- sample descriptions ----------

def two_vintage_ds(days=range(2, 9)):
    """Each hour has a 07:30 D-1 forecast (lead 16.5-39.5 h) and a 20:30 D-1 forecast (lead 3.5-26.5 h)."""
    acts, fcs = [], []
    for d in days:
        for h in range(24):
            t = datetime(2026, 1, d, h, tzinfo=UTC)
            acts += [(t.strftime(FMT), 1000.0), ((t + timedelta(minutes=30)).strftime(FMT), 1000.0)]
            prev = datetime(2026, 1, d, tzinfo=UTC) - timedelta(days=1)
            fcs.append((t.strftime(FMT), (prev + timedelta(hours=7, minutes=30)).strftime(FMT), 1100.0))
            fcs.append((t.strftime(FMT), (prev + timedelta(hours=20, minutes=30)).strftime(FMT), 1050.0))
    return build_dataset(acts, fcs)


def test_headline_and_horizon_samples_are_described_and_differ():
    rep = analysis.accuracy_report(two_vintage_ds(), time(9, 0))
    s = rep["samples"]
    assert s["headline"]["hours"] == rep["overall"]["n"] == 7 * 24
    assert s["horizon"]["forecasts"] == sum(b["n"] for b in rep["horizon"]) == 2 * 7 * 24
    assert s["horizon"]["hours"] == 7 * 24
    assert s["horizon"]["forecasts"] > s["headline"]["hours"]        # different, larger sample
    assert s["headline"]["first_delivery"] == "2026-01-02"


def test_highlighted_lead_times_follow_the_cutoff_not_a_hard_coded_window():
    ds = two_vintage_ds()
    morning = analysis.accuracy_report(ds, time(9, 0))
    evening = analysis.accuracy_report(ds, time(22, 0))
    assert morning["lead_time_h"] == {"min": 16.5, "max": 39.5}
    assert evening["lead_time_h"] == {"min": 3.5, "max": 26.5}
    flags = lambda rep: {b["label"] for b in rep["horizon"] if b["decision_relevant"]}
    assert flags(morning) == {"12-24h", "24-36h", "36-48h"}
    assert flags(evening) == {"0-6h", "6-12h", "12-24h", "24-36h"}


def test_coverage_report_counts_scorable_hours():
    ds = day_ds(skip_forecast={1}, skip_actual={2})
    cov = analysis.coverage_report(ds, time(9, 0))
    assert cov["hours"] == 31 * 24
    assert (cov["with_forecast"], cov["with_actual"], cov["scored"]) == (23, 23, 22)
