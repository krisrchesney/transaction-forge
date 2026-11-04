import json

import pytest

from txnforge import generate, validate
from txnforge.levers import Levers, estimate
from txnforge.lookup import build, lookup

SMALL = dict(seed=6, cards=500, merchants=120, days=4)
NOISY = dict(fraud_rate=.03, duplicate_rate=.02, missing_terminal_rate=.02, amount_mismatch_rate=.02,
             late_clearing_rate=.02, orphan_clearing_rate=.02)


def traces(messages):
    return {(m["de"].get("48") or {}).get("63") for m in messages}


def requests(r):
    return sum(m["mti"] in ("0100", "0200") for m in r["messages"])


def test_card_activity_scales_purchases():
    one = generate(levers=dict(fraud_rate=0), **SMALL)
    two = generate(levers=dict(fraud_rate=0, card_activity=2), **SMALL)
    assert 1.8 < requests(two) / requests(one) < 2.2


def test_estimate_follows_card_activity():
    for activity in (.4, 2.5):
        lv = dict(card_activity=activity)
        actual = len(generate(seed=21, cards=800, merchants=100, days=7, levers=lv)["messages"])
        assert abs(estimate(800, 7, lv)["messages"] - actual) / actual < .06, activity


@pytest.mark.parametrize("bad", [0, -1, 11])
def test_card_activity_must_be_in_range(bad):
    with pytest.raises(ValueError):
        Levers(card_activity=bad)


def test_stripped_feed_has_no_simulator_fields_but_labels_still_map_by_trace():
    r = generate(strip_sim=True, levers=NOISY, **SMALL)
    assert all("sim" not in m and "sim_issuer" not in m["de"] for m in r["messages"])
    seen = traces(r["messages"])
    labeled = [t for lab in r["labels"] for t in (lab.get("traces") or [lab.get("trace")]) if lab["kind"] != "benign_lookalike"]
    assert labeled and all(t in seen for t in labeled)
    assert {row["trace"] for row in r["truth"] if "trace" in row} <= seen
    kinds = {row["kind"] for row in r["truth"]}
    assert {"fraud", "chargeback", "defect", "benign_lookalike"} <= kinds


def test_stripped_feed_still_validates_and_loads_into_lookup():
    r = generate(strip_sim=True, levers=NOISY, **SMALL)
    report = validate(r["messages"])
    assert report["findings"] and all(f["trace"] for f in report["findings"] if f["rule"] != "R11")
    conn = build(r["messages"])
    some = next(t for t in traces(r["messages"]) if t)
    assert lookup(conn, some)["found"]


def test_unstripped_feed_keeps_them_by_default():
    r = generate(levers=dict(fraud_rate=0), **SMALL)
    assert all("sim" in m for m in r["messages"]) and any("sim_issuer" in m["de"] for m in r["messages"])


def test_streamed_run_writes_the_per_trace_label_table(tmp_path):
    r = generate(out_dir=tmp_path, strip_sim=True, levers=NOISY, shard_cards=200, **SMALL)
    assert not list(tmp_path.glob("*.part*"))
    rows = [json.loads(line) for line in open(tmp_path / "labels_by_trace.jsonl", encoding="utf-8")]
    msgs = [json.loads(line) for line in open(tmp_path / "messages.jsonl", encoding="utf-8")]
    assert rows and all("sim" not in m for m in msgs)
    assert {row["trace"] for row in rows if "trace" in row} <= traces(msgs)
    assert json.loads((tmp_path / "manifest.json").read_text())["strip_sim"] is True
    assert r["manifest"]["shards"] == 3
