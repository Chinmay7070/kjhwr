"""Generate SYNTHETIC demo data with the same shape as the Elexon data.

    python -m scripts.make_sample_data

Only for running the app offline or in tests. The app labels it "demo data".
Replace it with real data via `python -m scripts.fetch_data`.
"""
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone

from app.config import DATA_DIR
from app.data import iso, save_dataset

UTC = timezone.utc


def main(seed: int = 42) -> None:
    rng = random.Random(seed)
    start = datetime(2025, 12, 12, tzinfo=UTC)
    end = datetime(2026, 2, 2, tzinfo=UTC)
    step = timedelta(minutes=30)
    n = int((end - start) / step)

    # "True" wind: slow-moving AR(1) squashed into a plausible 0.5-13 GW range.
    x, wind = 0.0, []
    for _ in range(n):
        x = 0.992 * x + rng.gauss(0, 0.12)
        wind.append(max(150.0, 6500 + 4800 * math.tanh(x) + rng.gauss(0, 60)))
    actual_rows = [[iso(start + i * step), round(w, 1)] for i, w in enumerate(wind)]

    # Forecasts every 3h; error grows with look-ahead and is smooth across the horizon.
    forecast_rows = []
    pub = start
    while pub < end - timedelta(hours=1):
        z = rng.gauss(0, 1)
        for k in range(1, 121):  # up to 60h ahead
            idx = int((pub - start) / step) + k
            if idx >= n:
                break
            z = 0.95 * z + math.sqrt(1 - 0.95**2) * rng.gauss(0, 1)
            if k % 2:  # real WINDFOR is hourly: keep only on-the-hour targets
                continue
            hours = k / 2
            sigma = 150 + 20 * hours
            hour_mean = (wind[idx] + wind[min(idx + 1, n - 1)]) / 2  # forecast targets the hourly mean
            value = max(0.0, hour_mean * 0.99 + sigma * z)
            forecast_rows.append([iso(start + idx * step), iso(pub), round(value, 1)])
        pub += timedelta(hours=3)

    save_dataset(DATA_DIR, actual_rows, forecast_rows, {
        "source": "synthetic",
        "fetched_at": iso(datetime.now(UTC)),
        "actual_rows": len(actual_rows),
        "forecast_rows": len(forecast_rows),
    })
    print(f"Wrote synthetic data: {len(actual_rows)} actuals, {len(forecast_rows)} forecasts -> {DATA_DIR}")


if __name__ == "__main__":
    main()
