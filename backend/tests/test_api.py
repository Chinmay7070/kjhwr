import pytest
from fastapi.testclient import TestClient

from app.data import DataNotFound, get_dataset
from app.main import app

client = TestClient(app)

try:
    get_dataset()
    HAVE_DATA = True
except DataNotFound:
    HAVE_DATA = False

pytestmark = pytest.mark.skipif(not HAVE_DATA, reason="no dataset in backend/data")


def test_health():
    assert client.get("/api/health").json() == {"status": "ok"}


def test_meta_lists_all_january_decisions():
    body = client.get("/api/meta").json()
    assert len(body["decision_dates"]) == 31
    assert body["default_cutoff"] == "09:00"


def test_decision_shape_and_no_leakage():
    body = client.get("/api/decision", params={"decision_date": "2026-01-14"}).json()
    assert body["delivery_date"] == "2026-01-15"
    assert len(body["rows"]) == 24
    cutoff = body["cutoff_utc"]
    for r in body["rows"]:
        assert r["publish_time"] is None or r["publish_time"] <= cutoff
    assert body["band"] is not None  # warm-up history exists


def test_decision_rejects_out_of_scope_date():
    assert client.get("/api/decision", params={"decision_date": "2026-03-01"}).status_code == 422


def test_bad_cutoff():
    assert client.get("/api/accuracy", params={"cutoff": "9am"}).status_code == 422


def test_accuracy_shape():
    body = client.get("/api/accuracy").json()
    assert body["overall"]["n"] > 700  # 31 days x 24 hours = 744 at most
    assert body["overall"]["mae"] > 0
    assert len(body["daily"]) >= 28
    assert any(h["decision_relevant"] for h in body["horizon"])
    # Longer look-ahead should not be more accurate than the shortest bucket.
    assert body["horizon"][0]["mae"] <= body["horizon"][-1]["mae"]


def test_accuracy_internal_consistency():
    body = client.get("/api/accuracy").json()
    o = body["overall"]
    assert o["rmse"] >= o["mae"] >= abs(o["bias"])
    for d in body["daily"]:
        # volume miss (MWh) is the sum of the same hourly errors that give the bias (MW)
        assert d["volume_error_mwh"] == pytest.approx(d["bias"] * d["n"], abs=0.06 * d["n"])
    assert body["daily"][0]["delivery_date"] == "2026-01-02"
    assert body["daily"][-1]["delivery_date"] == "2026-02-01"


def test_accuracy_describes_its_samples_and_lead_time():
    body = client.get("/api/accuracy").json()
    assert body["samples"]["headline"]["hours"] == body["overall"]["n"]
    assert body["samples"]["horizon"]["forecasts"] == sum(b["n"] for b in body["horizon"])
    assert body["lead_time_h"]["min"] < body["lead_time_h"]["max"]


def test_decision_completeness_flags_are_booleans():
    s = client.get("/api/decision", params={"decision_date": "2026-01-14"}).json()["summary"]
    assert isinstance(s["actual_complete"], bool) and isinstance(s["forecast_complete"], bool)
    assert s["actual_hours"] <= 24 and s["scored_hours"] <= s["actual_hours"]
