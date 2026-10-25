"""Dataset storage and loading.

Raw Elexon rows are stored compactly as gzipped JSON arrays and indexed in
memory for fast "latest forecast published before time X" lookups.
"""
from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from .config import DATA_DIR

UTC = timezone.utc


class DataNotFound(RuntimeError):
    """Raised when the cached dataset files are missing."""


def parse_ts(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Dataset:
    # half-hour start time -> actual wind generation (MW, average over the half-hour)
    actuals: dict[datetime, float]
    # hour start time -> (publish times sorted ascending, hourly forecast MW in same order)
    forecasts: dict[datetime, tuple[list[datetime], list[float]]]
    meta: dict


def build_dataset(
    actual_rows: Iterable[tuple[str, float]],
    forecast_rows: Iterable[tuple[str, str, float]],
    meta: dict | None = None,
) -> Dataset:
    """actual_rows: (startTime, generation); forecast_rows: (startTime, publishTime, generation)."""
    actuals = {parse_ts(s): float(g) for s, g in actual_rows}
    staged: dict[datetime, dict[datetime, float]] = {}
    conflicts = 0
    for start, pub, gen in forecast_rows:
        by_pub = staged.setdefault(parse_ts(start), {})
        key = parse_ts(pub)
        if key in by_pub and by_pub[key] != float(gen):
            conflicts += 1  # same (startTime, publishTime) with different values; last one wins
        by_pub[key] = float(gen)
    forecasts = {}
    for target, by_pub in staged.items():
        pubs = sorted(by_pub)
        forecasts[target] = (pubs, [by_pub[p] for p in pubs])
    # NOTE: forecasts are deliberately NOT copied onto half-hour slots. WINDFOR is
    # hourly; analysis compares each hourly forecast with the mean of the two
    # half-hourly FUELHH readings in that hour (see analysis.hourly_actual).
    return Dataset(actuals, forecasts, {**(meta or {}), "duplicate_conflicts": conflicts})


def collapse_actuals(rows: Iterable[dict]) -> tuple[dict[str, float], int]:
    """One value per startTime. If Elexon returns several rows for the same startTime,
    keep the one with the latest publishTime (deterministic), and count startTimes whose
    rows disagree so the problem is visible instead of silently depending on row order."""
    best: dict[str, tuple[str, float]] = {}
    seen_values: dict[str, set[float]] = {}
    for r in rows:
        start, gen = r["startTime"], float(r["generation"])
        pub = r.get("publishTime") or ""
        seen_values.setdefault(start, set()).add(gen)
        if start not in best or pub >= best[start][0]:
            best[start] = (pub, gen)
    conflicts = sum(1 for v in seen_values.values() if len(v) > 1)
    return {k: v[1] for k, v in best.items()}, conflicts


WIND_PSR_TYPES = ("Wind Onshore", "Wind Offshore")


def collapse_agws(rows: Iterable[dict]) -> tuple[dict[str, float], dict]:
    """AGWS (B1630) rows -> total wind MW per half-hour (onshore + offshore).

    A half-hour is kept only when BOTH wind types are present, otherwise the total would be
    understated; such half-hours are counted. Duplicates keep the latest publishTime."""
    best: dict[tuple[str, str], tuple[str, float]] = {}
    for r in rows:
        psr = r.get("psrType")
        if psr not in WIND_PSR_TYPES or r.get("quantity") is None:
            continue
        key, pub = (r["startTime"], psr), r.get("publishTime") or ""
        if key not in best or pub >= best[key][0]:
            best[key] = (pub, float(r["quantity"]))
    starts = {k[0] for k in best}
    totals, incomplete = {}, 0
    for st in starts:
        if all((st, p) in best for p in WIND_PSR_TYPES):
            totals[st] = sum(best[(st, p)][1] for p in WIND_PSR_TYPES)
        else:
            incomplete += 1
    return totals, {"half_hours": len(totals), "incomplete_half_hours": incomplete}


def load_agws(directory: Path = DATA_DIR) -> dict[datetime, float] | None:
    """Total AGWS wind per half-hour, or None if scripts.fetch_agws has not been run."""
    path = directory / "wind_agws.json.gz"
    if not path.exists():
        return None
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        rows = json.load(fh)
    totals, _ = collapse_agws({"startTime": s, "psrType": p, "quantity": q} for s, p, q in rows)
    return {parse_ts(k): v for k, v in totals.items()}


def load_agws_components(directory: Path = DATA_DIR) -> dict[str, dict[datetime, float]] | None:
    """AGWS wind split by type ({"Wind Onshore": {...}, "Wind Offshore": {...}}), or None if not fetched."""
    path = directory / "wind_agws.json.gz"
    if not path.exists():
        return None
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        rows = json.load(fh)
    out: dict[str, dict[datetime, float]] = {p: {} for p in WIND_PSR_TYPES}
    for start, psr, qty in rows:
        if psr in out and qty is not None:
            out[psr][parse_ts(start)] = float(qty)
    return out


def save_dataset(directory: Path, actual_rows: list, forecast_rows: list, meta: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, rows in (("wind_actuals", actual_rows), ("wind_forecasts", forecast_rows)):
        with gzip.open(directory / f"{name}.json.gz", "wt", encoding="utf-8") as fh:
            json.dump(rows, fh, separators=(",", ":"))
    (directory / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def load_dataset(directory: Path = DATA_DIR) -> Dataset:
    try:
        with gzip.open(directory / "wind_actuals.json.gz", "rt", encoding="utf-8") as fh:
            actual_rows = json.load(fh)
        with gzip.open(directory / "wind_forecasts.json.gz", "rt", encoding="utf-8") as fh:
            forecast_rows = json.load(fh)
        meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DataNotFound(
            "No dataset found. Run `python -m scripts.fetch_data` (real Elexon data) "
            "or `python -m scripts.make_sample_data` (synthetic demo data)."
        ) from exc
    return build_dataset(actual_rows, forecast_rows, meta)


@lru_cache(maxsize=1)
def get_dataset() -> Dataset:
    return load_dataset()
