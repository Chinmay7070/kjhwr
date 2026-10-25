"""Download Elexon AGWS (B1630) wind generation for the same window, for INVESTIGATION only.

    python -m scripts.fetch_agws

AGWS is "actual or estimated wind and solar power generation" per settlement period, split into
Wind Onshore / Wind Offshore / Solar. It is not used by the dashboard; scripts.investigate_bias
compares it with FUELHH and WINDFOR. The endpoint allows at most 7 days per request.
"""
from __future__ import annotations

import gzip
import json
from datetime import datetime, timedelta, timezone

import httpx

from app.config import DATA_DIR, DECISION_END, DECISION_START, WARMUP_DAYS
from scripts.fetch_data import _chunks, _get, _z

UTC = timezone.utc
PATH = "/generation/actual/per-type/wind-and-solar"


def main() -> None:
    first = datetime.combine(DECISION_START - timedelta(days=WARMUP_DAYS - 1), datetime.min.time(), tzinfo=UTC)
    end = datetime.combine(DECISION_END + timedelta(days=2), datetime.min.time(), tzinfo=UTC)
    rows: dict[tuple[str, str], tuple[str, float]] = {}
    with httpx.Client() as client:
        for a, b in _chunks(first, end, timedelta(days=7)):
            data = _get(client, PATH, {"from": _z(a), "to": _z(b), "format": "json"})
            kept = 0
            for r in data:
                if r.get("psrType") in ("Wind Onshore", "Wind Offshore") and r.get("quantity") is not None:
                    key = (r["startTime"].replace(" ", "T"), r["psrType"])
                    pub = r.get("publishTime") or ""
                    if key not in rows or pub >= rows[key][0]:
                        rows[key] = (pub, float(r["quantity"]))
                    kept += 1
            print(f"  {a.date()} -> {b.date()}: {len(data)} rows, {kept} wind")
    out = sorted([s, p, q] for (s, p), (_, q) in rows.items())
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with gzip.open(DATA_DIR / "wind_agws.json.gz", "wt", encoding="utf-8") as fh:
        json.dump(out, fh, separators=(",", ":"))
    print(f"Saved {len(out)} AGWS wind rows to {DATA_DIR / 'wind_agws.json.gz'}")


if __name__ == "__main__":
    main()
