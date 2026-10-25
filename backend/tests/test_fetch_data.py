"""fetch_data against a fake Elexon: record selection, de-duplication, fetch windows and coverage."""
from datetime import date, datetime, timedelta, timezone

import pytest

import scripts.fetch_data as fd
from app.data import load_dataset

UTC = timezone.utc
Z = "%Y-%m-%dT%H:%M:%SZ"


def make_fake(calls, extra_forecast_rows=(), null_forecast=False):
    def fake_get(client, path, params):
        calls.append((path, dict(params)))
        if path.endswith("FUELHH/stream"):
            d, last = date.fromisoformat(params["settlementDateFrom"]), date.fromisoformat(params["settlementDateTo"])
            rows = []
            while d <= last:
                for i in range(48):
                    t = datetime(d.year, d.month, d.day, tzinfo=UTC) + timedelta(minutes=30 * i)
                    rows.append({"fuelType": "WIND", "startTime": t.strftime(Z), "generation": 1000.0 + i,
                                 "publishTime": t.strftime(Z)})
                    rows.append({"fuelType": "NUCLEAR", "startTime": t.strftime(Z), "generation": 4000.0})
                d += timedelta(days=1)
            return rows
        lo = datetime.strptime(params["publishDateTimeFrom"], Z).replace(tzinfo=UTC)
        hi = datetime.strptime(params["publishDateTimeTo"], Z).replace(tzinfo=UTC)
        rows = []
        pub = datetime(2025, 12, 1, tzinfo=UTC)
        while pub <= datetime(2026, 2, 5, tzinfo=UTC):
            if lo <= pub <= hi:                    # inclusive window: a publish on a boundary appears twice
                for k in range(1, 49):
                    rows.append({"startTime": (pub + timedelta(hours=k)).strftime(Z), "publishTime": pub.strftime(Z),
                                 "generation": 2000.0 + k})
            pub += timedelta(days=1)
        rows += list(extra_forecast_rows)
        if null_forecast:
            rows.append({"startTime": "2026-01-10T00:00:00Z", "publishTime": "2026-01-09T00:00:00Z", "generation": None})
        return rows
    return fake_get


def run(monkeypatch, tmp_path, **kw):
    calls = []
    monkeypatch.setattr(fd, "_get", make_fake(calls, **kw))
    monkeypatch.setattr(fd, "DATA_DIR", tmp_path)
    fd.main()
    return calls, load_dataset(tmp_path)


def test_keeps_only_wind_with_exact_window_and_no_duplicates(monkeypatch, tmp_path):
    calls, ds = run(monkeypatch, tmp_path)
    assert len(ds.actuals) == 45 * 48                              # 19 Dec .. 1 Feb, exact half-hourly grid
    assert all(1000.0 <= v < 1048.0 for v in ds.actuals.values())   # no NUCLEAR rows leaked in
    assert ds.meta["actual_duplicate_conflicts"] == 0 and ds.meta["forecast_duplicate_conflicts"] == 0
    assert ds.meta["coverage_at_0900"]["scored"] == ds.meta["coverage_at_0900"]["hours"] == 744
    # boundary publishes are returned by two windows but stored once (24 vintages per target hour at most)
    assert max(len(p) for p, _ in ds.forecasts.values()) <= 2


def test_fetch_windows_cover_the_whole_scope_without_gaps_or_over_long_requests(monkeypatch, tmp_path):
    calls, ds = run(monkeypatch, tmp_path)
    fuel = [p for path, p in calls if path.endswith("FUELHH/stream")]
    d = [(date.fromisoformat(p["settlementDateFrom"]), date.fromisoformat(p["settlementDateTo"])) for p in fuel]
    assert d[0][0] == date(2025, 12, 19) and d[-1][1] == date(2026, 2, 1)
    assert all(b[0] == a[1] + timedelta(days=1) for a, b in zip(d, d[1:]))     # contiguous, no overlap
    wind = [p for path, p in calls if path.endswith("WINDFOR/stream")]
    w = [(datetime.strptime(p["publishDateTimeFrom"], Z), datetime.strptime(p["publishDateTimeTo"], Z)) for p in wind]
    assert w[0][0] == datetime(2025, 12, 15) and w[-1][1] == datetime(2026, 2, 2)
    assert all(b[0] == a[1] for a, b in zip(w, w[1:]))                          # contiguous
    assert all(hi - lo <= timedelta(days=7) for lo, hi in w)                    # documented per-request limit
    # lookback before the first delivery day exceeds the longest lead time seen in real data (66.5 h)
    assert datetime(2025, 12, 19) - w[0][0] >= timedelta(hours=72)


def test_conflicting_and_null_forecast_rows_are_counted_not_hidden(monkeypatch, tmp_path):
    dup = {"startTime": "2026-01-10T01:00:00Z", "publishTime": "2026-01-10T00:00:00Z", "generation": 999.0}
    calls, ds = run(monkeypatch, tmp_path, extra_forecast_rows=[dup], null_forecast=True)
    assert ds.meta["forecast_duplicate_conflicts"] >= 1
    assert ds.meta["forecast_null_skipped"] >= 1
