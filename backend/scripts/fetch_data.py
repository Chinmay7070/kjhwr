"""Download real wind data from Elexon and cache it under backend/data.

    python -m scripts.fetch_data

Pulls, for delivery days 2026-01-02 .. 2026-02-01 (plus a warm-up window before
January so early-January uncertainty bands have history):
  * FUELHH  (actual half-hourly generation, fuelType == WIND)
  * WINDFOR (every published wind forecast, with publishTime)
Elexon Insights API is public; no key needed.
"""
from __future__ import annotations

import time as _time
from datetime import date, datetime, time, timedelta, timezone

import httpx

from app.config import DATA_DIR, DECISION_END, DECISION_START, ELEXON_BASE_URL, WARMUP_DAYS
from app import analysis
from app.data import build_dataset, collapse_actuals, save_dataset

UTC = timezone.utc


def _get(client: httpx.Client, path: str, params: dict) -> list[dict]:
    for attempt in range(5):
        try:
            resp = client.get(f"{ELEXON_BASE_URL}{path}", params=params, timeout=60)
            resp.raise_for_status()
            body = resp.json()
            return body["data"] if isinstance(body, dict) else body
        except (httpx.HTTPError, ValueError) as exc:
            wait = 2 ** attempt
            print(f"  retry {attempt + 1} after error ({exc}); waiting {wait}s")
            _time.sleep(wait)
    raise SystemExit(f"Giving up on {path} {params}")


def _chunks(start: datetime, end: datetime, step: timedelta):
    cur = start
    while cur < end:
        nxt = min(cur + step, end)
        yield cur, nxt
        cur = nxt


def _z(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> None:
    first_delivery = datetime.combine(DECISION_START - timedelta(days=WARMUP_DAYS - 1), datetime.min.time(), tzinfo=UTC)
    last_delivery_end = datetime.combine(DECISION_END + timedelta(days=2), datetime.min.time(), tzinfo=UTC)
    # Forecasts for the first target day can be published days earlier.
    first_publish = first_delivery - timedelta(days=4)

    with httpx.Client() as client:
        print("Fetching FUELHH (actual wind)...")
        fuelhh_rows: list[dict] = []
        actual_nulls = 0
        for a, b in _chunks(first_delivery, last_delivery_end, timedelta(days=7)):
            rows = _get(client, "/datasets/FUELHH/stream", {
                "settlementDateFrom": a.date().isoformat(),
                "settlementDateTo": (b - timedelta(days=1)).date().isoformat(),
                "fuelType": "WIND",
            })
            wind = [r for r in rows if r.get("fuelType") == "WIND"]
            actual_nulls += sum(r.get("generation") is None for r in wind)
            fuelhh_rows += [r for r in wind if r.get("generation") is not None]
            print(f"  {a.date()} -> {b.date()}: {len(rows)} rows ({len(wind)} WIND)")
        actuals, actual_conflicts = collapse_actuals(fuelhh_rows)
        print(f"  FUELHH WIND rows {len(fuelhh_rows)} -> {len(actuals)} unique startTimes, "
              f"{actual_conflicts} with conflicting values, {actual_nulls} null generation skipped")

        print("Fetching WINDFOR (forecasts)...")
        forecasts: dict[tuple[str, str], float] = {}
        forecast_nulls = forecast_conflicts = 0
        for a, b in _chunks(first_publish, last_delivery_end, timedelta(days=2)):
            rows = _get(client, "/datasets/WINDFOR/stream", {
                "publishDateTimeFrom": _z(a),
                "publishDateTimeTo": _z(b),
            })
            for r in rows:
                if r.get("generation") is None:
                    forecast_nulls += 1
                    continue
                key, value = (r["startTime"], r["publishTime"]), float(r["generation"])
                if key in forecasts and forecasts[key] != value:
                    forecast_conflicts += 1  # same (startTime, publishTime), different MW; last one wins
                forecasts[key] = value        # identical repeats (e.g. at chunk edges) are harmless
            print(f"  published {a.date()} -> {b.date()}: {len(rows)} rows")

    actual_rows = sorted([k, v] for k, v in actuals.items())
    forecast_rows = sorted([s, p, g] for (s, p), g in forecasts.items())
    coverage = analysis.coverage_report(build_dataset(actual_rows, forecast_rows), time(9, 0))
    print(f"Coverage of the January scope at the 09:00 cut-off: {coverage}")
    if coverage["scored"] < coverage["hours"]:
        print(f"WARNING: only {coverage['scored']} of {coverage['hours']} scope hours can be scored; "
              "the data has gaps. Fix this before trusting any metric.")
    save_dataset(DATA_DIR, actual_rows, forecast_rows, {
        "source": "elexon",
        "fetched_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "actual_rows": len(actual_rows),
        "forecast_rows": len(forecast_rows),
        "actual_duplicate_conflicts": actual_conflicts,
        "actual_null_skipped": actual_nulls,
        "forecast_duplicate_conflicts": forecast_conflicts,
        "forecast_null_skipped": forecast_nulls,
        "coverage_at_0900": coverage,
    })
    print(f"Saved {len(actual_rows)} actual rows and {len(forecast_rows)} forecast rows to {DATA_DIR}")


if __name__ == "__main__":
    main()
