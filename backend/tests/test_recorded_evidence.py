"""Checks that the numbers quoted in the README match the recorded investigation output.

docs/evidence/investigate_bias_2026-10-24.txt is the unedited output of `python -m scripts.investigate_bias`
run on the real January 2026 data plus the AGWS download. These tests do not re-fetch anything; they make
sure the README's claims are consistent with what was recorded.
"""
import re
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[2] / "docs" / "evidence" / "investigate_bias_2026-10-24.txt"
pytestmark = pytest.mark.skipif(not PATH.exists(), reason="recorded evidence file not present")


def text():
    raw = PATH.read_bytes()
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return raw.decode(enc)
        except UnicodeError:
            continue
    raise AssertionError("cannot decode evidence file")


def num(pattern):
    m = re.search(pattern, text())
    assert m, pattern
    return float(m.group(1).replace(",", ""))


def test_decomposition_identity_in_the_recorded_output():
    vs_fuelhh = num(r"WINDFOR minus FUELHH: bias \+?(-?\d+)")
    vs_agws = num(r"WINDFOR minus AGWS\s*: bias ([+-]?\d+)")
    gap = num(r"AGWS - FUELHH \+?(-?\d+) MW")
    assert vs_fuelhh == pytest.approx(vs_agws + gap, abs=2)        # -418 + 2,166 = 1,748


def test_forecast_is_much_closer_to_agws_than_to_fuelhh():
    mae_fuelhh = num(r"WINDFOR minus FUELHH: bias [+-]?\d+\s+MAE (\d+)")
    mae_agws = num(r"WINDFOR minus AGWS\s*: bias [+-]?\d+\s+MAE (\d+)")
    assert mae_agws < 0.7 * mae_fuelhh                              # 1,330 vs 2,039
    assert abs(num(r"WINDFOR minus AGWS\s*: bias ([+-]?\d+)")) < 0.3 * num(r"WINDFOR minus FUELHH: bias \+?(-?\d+)")


def test_agws_is_on_the_same_unit_scale_and_has_no_17_8_gw_ceiling():
    fuelhh, agws = num(r"mean FUELHH (\d+) MW"), num(r"mean AGWS wind (\d+) MW")
    assert 1.05 < agws / fuelhh < 1.35                              # same order: MW, counted once (not 0.5x or 2x)
    assert num(r"max hourly AGWS (\d+)") > 20000 > num(r"max hourly FUELHH (\d+)")


def test_agws_tracks_the_forecast_during_the_flat_fuelhh_episodes():
    m = re.search(r"forecast (\d+) \| AGWS (\d+) \| FUELHH (\d+)", text())
    fc, agws, fuelhh = map(float, m.groups())
    assert abs(fc - agws) < 200 and fuelhh < agws - 3000


# ---------------- section F of the investigation (files recorded on the real data) ----------------

PATH_F = Path(__file__).resolve().parents[2] / "docs" / "evidence" / "investigate_bias_section_F_2026-10-24.txt"
needs_f = pytest.mark.skipif(not PATH_F.exists(), reason="section F evidence file not present")


def text_f():
    raw = PATH_F.read_bytes()
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return raw.decode(enc)
        except UnicodeError:
            continue
    raise AssertionError("cannot decode evidence file")


def numf(pattern, group=1):
    m = re.search(pattern, text_f())
    assert m, pattern
    return float(m.group(group))


@needs_f
def test_onshore_plus_offshore_equals_the_agws_total_counted_once():
    on, off = numf(r"Wind Onshore\s+mean\s+(\d+)"), numf(r"Wind Offshore\s+mean\s+(\d+)")
    assert on + off == pytest.approx(numf(r"mean AGWS wind (\d+) MW"), abs=2)      # 5,370 + 8,079 = 13,449
    assert numf(r"Wind Onshore\s+mean\s+\d+ MW\s+max\s+\d+ MW\s+hours (\d+)") == 744   # present for every scope hour


@needs_f
def test_fuelhh_and_agws_share_a_clock_so_no_shift_is_justified():
    corr = {int(m.group(1)): float(m.group(2)) for m in re.finditer(r"shift ([+-]\d+) h: n=\s*\d+\s+correlation ([\d.]+)", text_f())}
    assert max(corr, key=corr.get) == 0                      # peak at shift 0
    assert abs(corr[-1] - corr[1]) < 0.005                   # symmetric around 0
    assert corr[2] < corr[0] - 0.015                         # a +2 h shift would be clearly worse


@needs_f
def test_fuelhh_share_of_agws_matches_the_documented_coverage_but_falls_with_output():
    share = numf(r"mean FUELHH (\d+) MW") / numf(r"mean AGWS wind (\d+) MW")
    assert 0.80 <= share <= 0.88                              # observed 0.839; NESO documents ~82% of wind has operational meters
    ratios = [float(x) for x in re.findall(r"FUELHH/AGWS ([\d.]+)", text_f())]
    assert len(ratios) == 4 and ratios == sorted(ratios, reverse=True) and ratios[0] - ratios[-1] > 0.03   # 0.863 -> 0.816


@needs_f
def test_worst_hours_split_into_series_gap_and_real_forecast_misses():
    pat = r"01-(\d\d)T(\d\d)\s+forecast\s+(\d+)\s+FUELHH\s+(\d+)\s+AGWS\s+(\d+)\s+vs FUELHH\s+\+(\d+)\s+vs AGWS\s+\+(\d+)"
    worst = {(m.group(1), m.group(2)): tuple(map(float, m.groups()[2:])) for m in re.finditer(pat, text_f())}
    jan12 = [v for (d, h), v in worst.items() if d == "12"]
    assert len(jan12) == 7 and all(v[3] > 6300 and v[4] < 2300 for v in jan12)      # vs FUELHH > 6.3 GW but only ~+2 GW vs AGWS
    jan28 = [v for (d, h), v in worst.items() if d == "28"]
    assert jan28 and all(v[4] > 4000 for v in jan28)                                  # a genuine forecast miss vs AGWS
