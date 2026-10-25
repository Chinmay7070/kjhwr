"""Central configuration. Everything an analyst might want to change lives here."""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
STATIC_DIR = Path(os.getenv("STATIC_DIR", BASE_DIR / "static"))

# Elexon Insights API: public, no key required.
ELEXON_BASE_URL = "https://data.elexon.co.uk/bmrs/api/v1"

# Scope of the pilot: purchase decisions taken in January 2026.
# A decision taken on day D is for delivery on day D+1.
DECISION_START = date(2026, 1, 1)
DECISION_END = date(2026, 1, 31)

# ASSUMPTION: the desk finalises its day-ahead bids at 09:00 on D-1.
# In January the UK is on GMT, so UK local time == UTC (no DST handling needed).
# The GB day-ahead auction gates close around 09:20-09:50, so 09:00 leaves
# time to submit. It is a query parameter everywhere, not a hard-coded rule.
DEFAULT_CUTOFF = "09:00"

# How many past delivery days feed the uncertainty band for a decision.
LOOKBACK_DAYS = 14
# Minimum hourly error samples before a band is shown (3 days).
MIN_BAND_SAMPLES = 24 * 3

# Warm-up window fetched before January so early-January decisions have history.
WARMUP_DAYS = 14
