"""The investigation helpers, AGWS handling and the AGWS fetch, each checked on planted data."""
import gzip
import json
from datetime import date, datetime, timedelta, timezone

import pytest

import scripts.fetch_agws as fa
from app import analysis
from app.data import build_dataset, collapse_agws, load_agws, parse_ts

UTC = timezone.utc
Z = "%Y-%m-%dT%H:%M:%SZ"


def row(i, forecast, actual):
    t = datetime(2026, 1, 10, tzinfo=UTC) + timedelta(hours=i)
    return {"start": t.strftime(Z), "forecast": forecast, "actual": actual, "error": forecast - actual}


# ---------- flat-actual episodes ----------

def test_flat_actual_episode_is_found_with_its_size_and_gap():
    rows = [row(i, 20000, 14000 + (i % 2) * 50) for i in range(8)]           # 8 flat hours, gap 6 GW
    ep = analysis.flat_actual_episodes(rows)
    assert len(ep) == 1 and ep[0]["hours"] == 8 and ep[0]["mean_error"] == pytest.approx(5975, abs=1)


def test_flat_actual_with_a_small_gap_or_short_run_is_not_an_episode():
    assert analysis.flat_actual_episodes([row(i, 14500, 14000) for i in range(8)]) == []     # gap too small
    assert analysis.flat_actual_episodes([row(i, 20000, 14000) for i in range(4)]) == []     # only 4 hours


def test_moving_actual_is_not_flat_and_a_missing_hour_breaks_the_run():
    assert analysis.flat_actual_episodes([row(i, 20000, 9000 + i * 1500) for i in range(8)]) == []
    rows = [row(i, 20000, 14000) for i in range(8)]
    del rows[4]                                                                # hour gap: two runs of 4 and 3
    assert analysis.flat_actual_episodes(rows) == []


# ---------- attribution, calibration, largest errors ----------

def test_split_contribution_shares_and_rest_bias():
    rows = [row(0, 11000, 10000), row(1, 11000, 10000), row(2, 20000, 14000), row(3, 20000, 14000)]
    s = analysis.split_contribution(rows, lambda r: r["actual"] >= 14000)
    assert s["n"] == 2 and s["bias"] == 6000 and s["bias_rest"] == 1000
    assert s["share_of_total_error"] == pytest.approx(12000 / 14000)


def test_largest_errors_orders_both_directions():
    rows = [row(0, 10, 0), row(1, 50, 0), row(2, 0, 30), row(3, 5, 0)]
    assert [r["error"] for r in analysis.largest_errors(rows, 2)] == [50, 10]
    assert analysis.largest_errors(rows, 1, over=False)[0]["error"] == -30


def test_calibration_curve_bins_by_forecast_and_reports_ratio():
    pairs = [(3000, 2400), (3500, 2800), (9000, 7650), (9500, 8075)]
    c = analysis.calibration_curve(pairs, 2000)
    assert [b["n"] for b in c] == [2, 2] and c[0]["from"] == 2000
    assert c[0]["actual_over_forecast"] == pytest.approx(0.8, abs=0.001)
    assert c[1]["actual_over_forecast"] == pytest.approx(0.85, abs=0.001)


# ---------- AGWS ----------

def agws_rows(start, onshore, offshore, pub="2026-01-10T00:00:00Z", solar=500):
    mk = lambda psr, q, p=pub: {"startTime": start, "psrType": psr, "quantity": q, "publishTime": p}
    return [mk("Wind Onshore", onshore), mk("Wind Offshore", offshore), mk("Solar", solar)]


def test_collapse_agws_sums_wind_types_and_ignores_solar():
    totals, info = collapse_agws(agws_rows("2026-01-10T00:00:00Z", 4000, 6000))
    assert totals == {"2026-01-10T00:00:00Z": 10000.0} and info["incomplete_half_hours"] == 0


def test_collapse_agws_drops_half_hours_missing_a_wind_type_instead_of_understating():
    rows = agws_rows("A", 4000, 6000)[:1]                       # onshore only
    totals, info = collapse_agws(rows)
    assert totals == {} and info["incomplete_half_hours"] == 1


def test_collapse_agws_keeps_latest_publish_for_duplicates():
    rows = agws_rows("A", 1000, 1000, pub="2026-01-01T00:00:00Z") + agws_rows("A", 3000, 1000, pub="2026-01-02T00:00:00Z")
    assert collapse_agws(rows)[0] == {"A": 4000.0}


def test_load_agws_returns_none_when_not_fetched(tmp_path):
    assert load_agws(tmp_path) is None


def test_compare_actual_series_recovers_a_known_offset_on_identical_hours():
    t0 = datetime(2026, 1, 10, tzinfo=UTC)
    fuelhh, agws = [], {}
    for i in range(6):                                           # 3 hours of half-hours
        t = t0 + timedelta(minutes=30 * i)
        fuelhh.append((t.strftime(Z), 10000.0))
        agws[t] = 11500.0
    ds = build_dataset(fuelhh, [])
    hours = [t0 + timedelta(hours=h) for h in range(4)]          # 4th hour has no data in either series
    c = analysis.compare_actual_series(ds, agws, hours)
    assert c["n"] == 3 and c["diff"] == 1500.0 and c["ratio"] == 1.15


def test_compare_actual_series_uses_only_hours_both_series_have():
    t0 = datetime(2026, 1, 10, tzinfo=UTC)
    ds = build_dataset([(t0.strftime(Z), 1.0), ((t0 + timedelta(minutes=30)).strftime(Z), 1.0)], [])
    other = {t0: 5.0}                                            # only one half-hour: hour unusable
    assert analysis.compare_actual_series(ds, other, [t0])["n"] == 0


# ---------- fetch_agws against a fake Elexon ----------

def test_fetch_agws_keeps_wind_only_and_respects_the_7_day_limit(monkeypatch, tmp_path):
    calls = []

    def fake_get(client, path, params):
        calls.append((path, dict(params)))
        lo = datetime.strptime(params["from"], Z).replace(tzinfo=UTC)
        return agws_rows(lo.strftime(Z), 4000, 6000)

    monkeypatch.setattr(fa, "_get", fake_get)
    monkeypatch.setattr(fa, "DATA_DIR", tmp_path)
    fa.main()
    windows = [(datetime.strptime(p["from"], Z), datetime.strptime(p["to"], Z)) for _, p in calls]
    assert all(hi - lo <= timedelta(days=7) for lo, hi in windows)
    assert windows[0][0] == datetime(2025, 12, 19) and windows[-1][1] == datetime(2026, 2, 2)
    assert all(b[0] == a[1] for a, b in zip(windows, windows[1:]))            # contiguous
    stored = json.load(gzip.open(tmp_path / "wind_agws.json.gz", "rt"))
    assert {r[1] for r in stored} == {"Wind Onshore", "Wind Offshore"}          # solar dropped
    agws = load_agws(tmp_path)
    assert all(v == 10000.0 for v in agws.values()) and len(agws) == len(windows)


# ---------- AGWS vs FUELHH diagnostics on planted data ----------

def paired_series(shape, hours=48):
    """FUELHH half-hours and an AGWS-like series built from it with a known relationship."""
    t0 = datetime(2026, 1, 10, tzinfo=UTC)
    fuelhh, other = [], {}
    for i in range(hours * 2):
        t = t0 + timedelta(minutes=30 * i)
        base = 6000.0 + 4000.0 * ((i // 2) % 7) / 6          # varies by hour, same in both halves
        fuelhh.append((t.strftime(Z), base))
        other[t] = shape(base)
    return build_dataset(fuelhh, []), other, [t0 + timedelta(hours=h) for h in range(hours)]


def test_shift_alignment_peaks_at_zero_for_a_shared_clock_and_at_the_offset_otherwise():
    ds, other, hours = paired_series(lambda b: 1.2 * b)
    best = max(analysis.shift_alignment(ds, other, hours), key=lambda a: a["correlation"])
    assert best["shift_h"] == 0 and best["correlation"] == pytest.approx(1.0, abs=1e-6)   # gap does not hide alignment
    delayed = {t + timedelta(hours=2): v for t, v in other.items()}                       # AGWS reports each value 2 h late
    best = max(analysis.shift_alignment(ds, delayed, hours), key=lambda a: a["correlation"])
    assert best["shift_h"] == 2                                                           # look 2 h forward to line it up


def test_gap_by_level_distinguishes_proportional_from_additive_gaps():
    ds, other, hours = paired_series(lambda b: 1.2 * b)
    assert all(g["fuelhh_over_other"] == pytest.approx(1 / 1.2, abs=0.001) for g in analysis.gap_by_level(ds, other, hours))
    ds, other, hours = paired_series(lambda b: b + 2000.0)
    assert all(g["gap"] == pytest.approx(2000.0, abs=0.1) for g in analysis.gap_by_level(ds, other, hours))


def test_decompose_bias_identity_holds_exactly_on_identical_hours():
    t = lambda h: datetime(2026, 1, 10, h, tzinfo=UTC)
    rows = [row(h, 13000.0 + 100 * h, 11000.0 + 90 * h) for h in range(6)]
    for r in rows:
        r["start"] = (t(0) + timedelta(hours=rows.index(r))).strftime(Z)
    other = {}
    for h in range(6):
        for m in (0, 30):
            other[t(h) + timedelta(minutes=m)] = 13400.0 + 20 * h
    d = analysis.decompose_bias(rows, other)
    assert d["n"] == 6 and d["identity_residual"] == 0
    assert d["bias_vs_fuelhh"] == pytest.approx(d["bias_vs_other"] + d["series_gap"], abs=1e-6)


def test_agws_components_are_loaded_by_type_and_sum_to_the_total(tmp_path):
    rows = [["2026-01-10T00:00:00Z", "Wind Onshore", 4000.0], ["2026-01-10T00:00:00Z", "Wind Offshore", 6000.0],
            ["2026-01-10T00:30:00Z", "Wind Onshore", 4100.0], ["2026-01-10T00:30:00Z", "Wind Offshore", 6100.0]]
    with gzip.open(tmp_path / "wind_agws.json.gz", "wt") as fh:
        json.dump(rows, fh)
    from app.data import load_agws_components
    comps = load_agws_components(tmp_path)
    t = datetime(2026, 1, 10, tzinfo=UTC)
    assert comps["Wind Onshore"][t] == 4000.0 and comps["Wind Offshore"][t] == 6000.0
    assert load_agws(tmp_path)[t] == 10000.0                      # total = onshore + offshore, counted once


def test_agws_timestamp_with_a_space_separator_is_normalised_before_storage(monkeypatch, tmp_path):
    """Elexon's documentation example writes startTime with a space; fetch_agws must store ISO 'T' form."""
    def fake_get(client, path, params):
        return [{"startTime": "2026-01-10 00:00:00Z", "psrType": "Wind Onshore", "quantity": 1.0},
                {"startTime": "2026-01-10 00:00:00Z", "psrType": "Wind Offshore", "quantity": 2.0}]
    monkeypatch.setattr(fa, "_get", fake_get)
    monkeypatch.setattr(fa, "DATA_DIR", tmp_path)
    fa.main()
    stored = json.load(gzip.open(tmp_path / "wind_agws.json.gz", "rt"))
    assert {r[0] for r in stored} == {"2026-01-10T00:00:00Z"}
    assert load_agws(tmp_path)[datetime(2026, 1, 10, tzinfo=UTC)] == 3.0


def test_lag_correlation_finds_a_planted_lag_and_zero_for_aligned_series():
    t0 = datetime(2026, 1, 10, tzinfo=UTC)
    vals = [5000.0, 9000.0, 7000.0, 12000.0, 6000.0, 10000.0, 8000.0, 11000.0, 4000.0, 9500.0]
    truth = {t0 + timedelta(hours=i): v for i, v in enumerate(vals)}
    peak = lambda a, b: max(analysis.lag_correlation(a, b), key=lambda x: x["correlation"])["shift_h"]
    assert peak(truth, truth) == 0
    late = {t + timedelta(hours=2): v for t, v in truth.items()}      # series a reports each value 2 h late
    assert peak(late, truth) == -2                                     # a[t] matches b from EARLIER hours: a runs late
