import pytest

from txnforge import generate, validate
from txnforge.levers import ALL_INCIDENT_TYPES, INCIDENT_INFO, Levers

BASE = dict(seed=8, cards=1200, merchants=200, days=6, incidents=False, fraud_rate=0)


def run(*specs, **kw):
    return generate(levers=dict(custom_incidents=list(specs), **kw), **BASE)


def window(result):
    inc = result["incidents"][0]
    return inc["start"], inc["end"]


def requests(result, start, end, **where):
    out = []
    for m in result["messages"]:
        if m["mti"] in ("0100", "0200") and start <= m["ts"] < end:
            if all(m["de"].get(k) == v for k, v in where.items()):
                out.append(m)
    return out


def responses(result, start, end):
    return [m for m in result["messages"] if m["mti"] in ("0110", "0210") and start <= m["ts"] < end]


def spec(kind, **kw):
    return dict(type=kind, start_day=2, start_hour=0, duration_hours=24, **kw)


def test_every_incident_type_has_a_description_and_a_valid_default():
    assert len(ALL_INCIDENT_TYPES) == 12
    for kind, info in INCIDENT_INFO.items():
        sev = info["severity"]
        assert sev["min"] <= sev["default"] <= sev["max"], kind
        assert info["label"] and info["help"] and info["target"] in ("issuer", "acquirer", "country", "state", "mcc", "none")
        Levers(custom_incidents=[dict(type=kind)])             # defaults alone are a valid incident


def test_custom_incident_is_recorded_in_the_answer_key():
    r = run(spec("network_outage", severity=.9))
    inc = r["incidents"][0]
    assert inc["id"] == "INC-C1" and inc["custom"] and inc["start"] == "2026-03-04T00:00:00Z"
    assert inc["end"] == "2026-03-05T00:00:00Z" and inc["share"] == .9


def test_custom_incidents_apply_even_when_prepared_ones_are_off_and_add_to_them_when_on():
    assert len(run(spec("network_outage"))["incidents"]) == 1
    both = generate(seed=8, cards=300, merchants=100, days=6, levers=dict(custom_incidents=[spec("network_outage")]))
    assert len(both["incidents"]) == 6


def test_network_outage_declines_nearly_everything_with_91():
    r = run(spec("network_outage", severity=.95))
    s, e = window(r)
    inside = responses(r, s, e)
    assert sum(m["de"]["39"] == "91" for m in inside) / len(inside) > .85
    outside = responses(r, "2026-03-02T00:00:00Z", s)
    assert sum(m["de"]["39"] == "91" for m in outside) / len(outside) < .03     # 91 also occurs in ordinary traffic


def test_acquirer_outage_removes_that_acquirers_volume():
    base = generate(**BASE)
    r = run(spec("acquirer_outage", target="ACQ01", severity=.9))
    s, e = window(r)
    before, after = len(requests(base, s, e, **{"32": "ACQ01"})), len(requests(r, s, e, **{"32": "ACQ01"}))
    assert before > 50 and after < .3 * before


def test_decline_spike_raises_do_not_honor():
    base = generate(**BASE)
    r = run(spec("decline_spike", severity=.5))
    s, e = window(r)
    share = lambda res: sum(m["de"]["39"] == "05" for m in responses(res, s, e)) / len(responses(res, s, e))
    assert share(r) > share(base) + .3


def test_holiday_surge_multiplies_volume():
    base = generate(**BASE)
    r = run(spec("holiday_surge", severity=2.0))
    s, e = window(r)
    assert len(requests(r, s, e)) > 1.6 * len(requests(base, s, e))


def test_chip_firmware_bug_forces_fallback_at_one_acquirer():
    base = generate(**BASE)
    r = run(spec("chip_firmware_bug", target="ACQ01", severity=.8))
    s, e = window(r)
    fallback = lambda res: sum(m["de"]["22"]["1"] == "80" for m in requests(res, s, e, **{"32": "ACQ01"}))
    assert fallback(r) > fallback(base) + 15


def test_clearing_delay_pushes_clearings_past_the_window():
    base = generate(**BASE)
    r = run(spec("clearing_delay", target="ACQ01", severity=9))
    late = lambda res: sum(f["rule"] == "R03" for f in validate(res["messages"])["findings"])
    assert late(r) > late(base) + 5


def test_fraud_attack_wave_adds_labeled_cases_inside_the_window():
    r = run(spec("fraud_attack_wave", severity=.05))
    s, e = window(r)
    cases = [lab for lab in r["labels"] if lab["kind"] == "fraud"]
    assert len(cases) == 60 and all(lab["incident"] == "INC-C1" for lab in cases)
    first = {}
    for m in r["messages"]:
        first.setdefault(m["sim"]["txn"], m["ts"])
    assert all(s <= min(first[t] for t in lab["txns"]) < "2026-03-07T00:00:00Z" for lab in cases)


def test_latency_spike_multiplies_response_time():
    base = generate(**BASE)
    r = run(spec("latency_spike", target="US", severity=10))
    s, e = window(r)
    mean = lambda res: sum(m["meta"]["latency_ms"] for m in responses(res, s, e)
                           if m["de"]["37"] and m["meta"].get("latency_ms")) / len(responses(res, s, e))
    # the latency target is the merchant country; US merchants are most of the feed
    assert mean(r) > 3 * mean(base)


@pytest.mark.parametrize("bad", [
    dict(type="meteor"),
    dict(type="network_outage", severity=1.5),
    dict(type="network_outage", start_hour=24),
    dict(type="network_outage", duration_hours=0),
    dict(type="network_outage", start_day=-1),
    dict(type="network_outage", colour="red"),
    "not an object",
])
def test_bad_custom_incidents_are_rejected(bad):
    with pytest.raises(ValueError):
        Levers(custom_incidents=[bad])
