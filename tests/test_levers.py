import pytest

from txnforge import generate, validate
from txnforge.levers import Levers, parse_assignment

SMALL = dict(cards=400, merchants=100, days=10)
DEFECT_LEVERS = dict(duplicate_rate=.05, missing_terminal_rate=.05, amount_mismatch_rate=.05,
                     late_clearing_rate=.05, orphan_clearing_rate=.05)


def kinds(result, kind):
    return [lab for lab in result["labels"] if lab["kind"] == kind]


def test_defaults_inject_no_defects():
    r = generate(seed=3, **SMALL)
    assert kinds(r, "defect") == []


def test_fraud_rate_scales_fraud_cases():
    low = generate(seed=3, levers=dict(fraud_rate=.005), **SMALL)
    high = generate(seed=3, levers=dict(fraud_rate=.05), **SMALL)
    assert len(kinds(high, "fraud")) > 3 * len(kinds(low, "fraud"))


def test_fraud_mix_limits_patterns():
    r = generate(seed=3, levers=dict(fraud_rate=.05, fraud_mix={"geo_velocity": 1}), **SMALL)
    patterns = {lab["pattern"] for lab in kinds(r, "fraud")}
    assert patterns == {"geo_velocity"}


def test_adjudicated_rate_controls_fraud_chargebacks():
    none = generate(seed=3, levers=dict(fraud_rate=.05, adjudicated_rate=0), **SMALL)
    allfraud = generate(seed=3, levers=dict(fraud_rate=.05, adjudicated_rate=1), **SMALL)
    cb = lambda r: [lab for lab in kinds(r, "chargeback") if lab["cause"] == "fraud"]
    assert cb(none) == [] and len(cb(allfraud)) > 0


def test_incident_selection():
    r = generate(seed=3, levers=dict(incidents=("latency_spike",)), **SMALL)
    assert [i["type"] for i in r["incidents"]] == ["latency_spike"]
    assert generate(seed=3, levers=dict(incidents=()), **SMALL)["incidents"] == []


def test_decline_rate_lever_moves_declines():
    share = lambda r: sum(1 for m in r["messages"] if m["mti"] == "0110" and m["de"]["39"] != "00") / \
        sum(1 for m in r["messages"] if m["mti"] == "0110")
    lo = generate(seed=3, incidents=False, levers=dict(base_decline_rate=.02), **SMALL)
    hi = generate(seed=3, incidents=False, levers=dict(base_decline_rate=.30), **SMALL)
    assert share(hi) > share(lo) + .15


def test_defect_labels_match_the_data():
    r = generate(seed=5, incidents=False, fraud_rate=0, levers=DEFECT_LEVERS, **SMALL)
    counts = r["manifest"]["label_counts"]
    for defect in ("duplicate_request", "missing_terminal_id", "amount_mismatch", "late_clearing", "orphan_clearing"):
        assert counts.get("defect:" + defect, 0) > 0, defect
    # share of card-present requests missing a terminal ID is close to the lever
    base = generate(seed=5, incidents=False, fraud_rate=0, **SMALL)
    base_reqs = [m for m in base["messages"] if m["mti"] in ("0100", "0200")
                 and m["de"]["22"]["1"] in ("05", "07", "80", "90") and "41" in m["de"]]
    missing = counts["defect:missing_terminal_id"]
    assert .02 < missing / len(base_reqs) < .09


def test_defects_do_not_shift_the_base_feed():
    base = generate(seed=5, incidents=False, fraud_rate=0, **SMALL)
    dup = generate(seed=5, incidents=False, fraud_rate=0, levers=dict(duplicate_rate=.1), **SMALL)
    n_dups = dup["manifest"]["label_counts"]["defect:duplicate_request"]
    assert dup["manifest"]["n_messages"] - n_dups == base["manifest"]["n_messages"]


def test_defects_are_visible_to_the_validator():
    r = generate(seed=5, incidents=False, fraud_rate=0, levers=DEFECT_LEVERS, **SMALL)
    rules = {f["rule"] for f in validate(r["messages"])["findings"]}
    assert {"R03", "R09", "R11"} <= rules


def test_manifest_records_levers():
    r = generate(seed=3, levers=dict(refund_rate=.1), **SMALL)
    assert r["manifest"]["levers"]["refund_rate"] == .1


def test_same_levers_same_feed():
    a = generate(seed=9, levers=DEFECT_LEVERS, **SMALL)["messages"]
    b = generate(seed=9, levers=DEFECT_LEVERS, **SMALL)["messages"]
    assert a == b


def test_bad_levers_are_rejected():
    with pytest.raises(ValueError):
        Levers(fraud_rate=1.5)
    with pytest.raises(ValueError):
        Levers.coerce({"no_such_lever": 1})
    with pytest.raises(ValueError):
        Levers(incidents=("meteor",))
    with pytest.raises(ValueError):
        Levers(fraud_mix={"made_up": 1})


def test_parse_assignment():
    assert parse_assignment("fraud_rate=0.02") == ("fraud_rate", .02)
    assert parse_assignment("incidents=issuer_outage,latency_spike") == ("incidents", ("issuer_outage", "latency_spike"))
    assert parse_assignment("incidents=") == ("incidents", ())
    assert parse_assignment("fraud_mix=card_testing:3,geo_velocity") == \
        ("fraud_mix", {"card_testing": 3.0, "geo_velocity": 1.0})
