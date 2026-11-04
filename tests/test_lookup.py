import json

import pytest

from txnforge.generator import generate
from txnforge.lookup import build, lookup, quality_report
from txnforge.validator import validate

CFG = dict(seed=3, cards=700, merchants=220, days=14)


@pytest.fixture(scope="module")
def feed():
    return generate(**CFG)


@pytest.fixture(scope="module")
def conn(feed):
    return build(feed["messages"])


def trace_for(feed, cause):
    labs = [l for l in feed["labels"] if l["kind"] == "chargeback" and l["cause"] == cause]
    assert labs, f"no {cause} chargebacks generated"
    txn = labs[0]["txn"]
    return next(m["de"]["48"]["63"] for m in feed["messages"] if m["sim"]["txn"] == txn and m["mti"] == "1240")


def test_chargebacks_exist_reference_cleared_sales_and_pass_validation(feed):
    cbs = [m for m in feed["messages"] if m["mti"] == "1442"]
    assert cbs
    assert "R12" not in validate(feed["messages"])["by_rule"]
    assert {m["de"]["25"] for m in cbs} <= {"4837", "4853", "4834"}


def test_orphan_chargeback_is_caught(feed):
    msgs = json.loads(json.dumps(feed["messages"]))
    next(m for m in msgs if m["mti"] == "1442")["de"]["48"]["63"] = "NOPE00000000"
    assert "R12" in validate(msgs)["by_rule"]


def test_lookup_returns_full_lifecycle_in_order(feed, conn):
    r = lookup(conn, trace_for(feed, "fraud"), requester_region="NA")
    mtis = [e["mti"] for e in r["events"]]
    assert mtis[0] in ("0100", "0200") and "1240" in mtis and mtis[-1] == "1442"
    assert r["status"] == "disputed"


def find_trace(feed, conn, region):
    for m in feed["messages"]:
        if m["mti"] == "1240":
            trace = m["de"]["48"]["63"]
            if lookup(conn, trace, region)["data_region"] == region:
                return trace
    raise AssertionError(f"no {region} trace")


def test_same_region_sees_full_pan_other_region_sees_masked(feed, conn):
    t = find_trace(feed, conn, "NA")
    own = lookup(conn, t, "NA")
    other = lookup(conn, t, "EU")
    assert own["policy"] == "full" and "*" not in own["events"][0]["pan"]
    assert other["policy"] == "mask_pan" and other["events"][0]["pan"].startswith("************")


def test_eu_data_is_denied_for_analytics_from_na_but_allowed_for_disputes_with_masking(feed, conn):
    t = find_trace(feed, conn, "EU")
    denied = lookup(conn, t, "NA", purpose="analytics")
    assert denied["policy"] == "deny" and denied["events"] == []
    ok = lookup(conn, t, "NA", purpose="dispute")
    assert ok["policy"] == "mask_pan_and_location"
    assert all("zip" not in e and "city" not in e for e in ok["events"])


def test_unknown_trace(conn):
    assert lookup(conn, "NOPE", "NA")["found"] is False


def test_quality_report_has_every_metric_and_clean_data_is_complete(feed, conn):
    q = quality_report(conn)
    assert {"lifecycle_completeness", "orphan_clearings", "dispute_rate_by_region", "latency_ms_by_region"} <= set(q)
    assert q["orphan_clearings"][0]["orphan_clearings"] == 0
    assert all(r["p95"] >= r["p50"] for r in q["latency_ms_by_region"])
    assert {r["region"] for r in q["dispute_rate_by_region"]} >= {"NA", "EU"}


def test_lookup_api(feed, conn, monkeypatch):
    from fastapi.testclient import TestClient
    import txnforge.api as api
    monkeypatch.setattr(api, "_conn", conn)
    c = TestClient(api.app)
    t = find_trace(feed, conn, "EU")
    assert c.get(f"/lookup/{t}?requester_region=NA&purpose=analytics").json()["policy"] == "deny"
    assert c.get("/lookup/NOPE").status_code == 404
    assert c.get(f"/lookup/{t}?purpose=bogus").status_code == 422
    assert "lifecycle_completeness" in c.get("/quality").json()
