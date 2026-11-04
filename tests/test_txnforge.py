import json
from collections import Counter

import pytest

from txnforge.generator import generate, luhn_ok
from txnforge.rules import NETWORK_BIN_RANGES
from txnforge.scenarios import SCENARIOS, run
from txnforge.validator import parse_ts, validate

SMALL = dict(cards=600, merchants=200, days=12)


@pytest.fixture(scope="module")
def feed():
    return generate(seed=3, **SMALL)


def test_deterministic():
    a = generate(seed=5, cards=150, merchants=60, days=4)
    b = generate(seed=5, cards=150, merchants=60, days=4)
    assert a["messages"] == b["messages"]


def test_pans_are_valid(feed):
    for m in feed["messages"]:
        pan = m["de"].get("2")
        if pan:
            assert luhn_ok(pan)
            assert any(lo <= int(pan[:6]) <= hi for lo, hi in NETWORK_BIN_RANGES)


def test_every_response_has_a_request(feed):
    reqs = {m["de"]["48"]["63"] for m in feed["messages"] if m["mti"] in ("0100", "0200")}
    for m in feed["messages"]:
        if m["mti"] in ("0110", "0210", "0400", "0410", "1240"):
            assert m["de"]["48"]["63"] in reqs


def test_answer_key_is_not_in_the_feed(feed):
    blob = json.dumps(feed["messages"]).lower()
    assert "fraud" not in blob and "adjudicated" not in blob and "inc-" not in blob


def test_clean_baseline_passes_network_rules():
    clean = generate(seed=4, incidents=False, fraud_rate=0, **SMALL)
    rep = validate(clean["messages"])
    assert not {"R01", "R02", "R03", "R05", "R06", "R07", "R11"} & set(rep["by_rule"]), rep["by_rule"]


def test_mapping_break_is_isolated_to_one_acquirer(feed):
    rep = validate(feed["messages"])
    r06 = [f for f in rep["findings"] if f["rule"] == "R06"]
    assert r06
    inc = next(i for i in feed["incidents"] if i["type"] == "mapping_break")
    assert {f["acquirer"] for f in r06} == {inc["acquirer"]}
    assert all(parse_ts(f["ts"]) >= parse_ts(inc["start"]) for f in r06)


def test_issuer_outage_shows_up_as_91(feed):
    inc = next(i for i in feed["incidents"] if i["type"] == "issuer_outage")
    issuer_of = {m["sim"]["txn"]: m["de"]["sim_issuer"] for m in feed["messages"] if m["mti"] in ("0100", "0200")}
    hit = [m for m in feed["messages"]
           if m["mti"] == "0110" and inc["start"] <= m["ts"] < inc["end"] and issuer_of.get(m["sim"]["txn"]) == inc["issuer"]]
    assert hit, "increase cards or days: no traffic for the outage issuer in the window"
    assert Counter(m["de"]["39"] for m in hit)["91"] / len(hit) > .6


def test_atm_amounts_are_whole_dollars_and_single_message(feed):
    atm = [m for m in feed["messages"] if m["de"].get("18") == "6011" and m["mti"] in ("0100", "0200")]
    assert atm and all(m["mti"] == "0200" and int(m["de"]["4"]) % 100 == 0 for m in atm)
    atm_txns = {m["sim"]["txn"] for m in atm}
    assert not [m for m in feed["messages"] if m["mti"] == "1240" and m["sim"]["txn"] in atm_txns]


def test_fuel_never_ecommerce_before_the_break():
    clean = generate(seed=4, incidents=False, **SMALL)
    pumps = [m for m in clean["messages"] if m["de"].get("18") == "5542" and m["mti"] in ("0100", "1240")]
    assert pumps and all(m["de"]["22"]["1"] != "81" for m in pumps)


def mutate(feed, mti, fn):
    msgs = json.loads(json.dumps(feed["messages"]))
    fn(next(m for m in msgs if m["mti"] == mti))
    return msgs


def test_validator_catches_expired_clearing(feed):
    def late(m):
        m["ts"] = "2026-06-30T00:00:00Z"
    assert "R03" in validate(mutate(feed, "1240", late))["by_rule"]


def test_validator_catches_oversized_reversal(feed):
    def big(m):
        m["de"]["95"] = "999999999999"
    assert "R02" in validate(mutate(feed, "0400", big))["by_rule"]


def test_validator_catches_orphan_reversal(feed):
    def orphan(m):
        m["de"]["48"]["63"] = "NOPE00000000"
    assert "R01" in validate(mutate(feed, "0400", orphan))["by_rule"]


def test_validator_catches_bad_pan(feed):
    def bad(m):
        m["de"]["2"] = m["de"]["2"][:-1] + str((int(m["de"]["2"][-1]) + 1) % 10)
    assert "R05" in validate(mutate(feed, "0100", bad))["by_rule"]


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_every_scenario_passes_the_validator(name):
    msgs = run(name, seed=2)
    assert msgs
    hard = [f for f in validate(msgs)["findings"] if f["severity"] == "error"]
    assert not hard, hard


def test_chip_fallback_scenario_carries_entry_mode_80():
    assert run("chip_fallback")[0]["de"]["22"]["1"] == "80"


def test_api_roundtrip():
    from fastapi.testclient import TestClient
    from txnforge.api import app
    c = TestClient(app)
    assert len(c.get("/scenarios").json()) == len(SCENARIOS)
    r = c.post("/scenarios/fuel_two_step?seed=3").json()
    assert r["messages"][0]["mti"] == "0100"
    assert c.post("/validate", json=r["messages"]).json()["n_findings"] == 0
    assert c.post("/scenarios/nope").status_code == 404
