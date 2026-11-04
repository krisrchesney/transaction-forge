import json
import re
from pathlib import Path

from txnforge import generate
from txnforge.levers import ESTIMATE, Levers, estimate

PAGE = Path(__file__).resolve().parents[1] / "docs" / "index.html"


def page_defaults():
    text = PAGE.read_text(encoding="utf-8")
    block = re.search(r"/\*DEFAULTS\*/\s*const DEFAULTS = (\{.*?\});\s*/\*END DEFAULTS\*/", text, re.S)
    return text, json.loads(block.group(1))


def test_designer_defaults_match_python_defaults():
    _, defaults = page_defaults()
    assert defaults == Levers().as_dict()


def test_designer_estimate_coefficients_match_python():
    text = PAGE.read_text(encoding="utf-8")
    block = re.search(r"/\*ESTIMATE\*/\s*const ESTIMATE = (\{.*?\});\s*/\*END ESTIMATE\*/", text, re.S)
    assert json.loads(block.group(1)) == ESTIMATE


def test_designer_incident_catalog_matches_python():
    from txnforge.levers import INCIDENT_INFO
    text = PAGE.read_text(encoding="utf-8")
    block = re.search(r"/\*INCIDENT_INFO\*/\s*const INCIDENT_INFO = (\{.*?\});\s*/\*END INCIDENT_INFO\*/", text, re.S)
    assert json.loads(block.group(1)) == INCIDENT_INFO


def test_designer_defines_every_glossary_term_it_promises():
    text = PAGE.read_text(encoding="utf-8")
    for term in ("Basis points", "MCC", "STAN", "Chargeback", "Adjudicated", "Orphan clearing", "Message", "Transaction"):
        assert f'["{term}' in text, term


def test_designer_has_a_control_for_every_lever():
    text, _ = page_defaults()
    controls = {"fraud_mix", "incidents", "custom_incidents"}      # built from their own sections, not the LEVERS table
    for name in Levers().as_dict():
        assert name in controls or f'key: "{name}"' in text, name


def test_size_estimate_is_close_to_real_feeds():
    cases = [
        (800, 7, 100, {}),
        (1500, 10, 200, dict(fraud_rate=.03, refund_rate=.1, reversal_timeout_rate=.05, base_decline_rate=.15)),
        (1200, 10, 150, dict(duplicate_rate=.02, orphan_clearing_rate=.02, dispute_rate=.02,
                             fraud_mix={"card_testing": 3, "geo_velocity": 1})),
    ]
    for cards, days, merchants, lv in cases:
        actual = generate(seed=21, cards=cards, merchants=merchants, days=days, levers=lv)["messages"]
        est = estimate(cards, days, lv)["messages"]
        assert abs(est - len(actual)) / len(actual) < .06, (cards, days, est, len(actual))


def test_merchant_count_does_not_change_the_estimate():
    assert estimate(1000, 10)["messages"] == estimate(1000, 10, {})["messages"]


def test_designer_output_is_accepted_by_python():
    _, defaults = page_defaults()
    defaults["fraud_mix"] = {"card_testing": 3, "geo_velocity": 1}
    defaults["incidents"] = ["issuer_outage"]
    defaults["duplicate_rate"] = .01
    assert Levers.coerce(defaults).duplicate_rate == .01


def test_designer_lets_you_type_a_number_of_events():
    text = PAGE.read_text(encoding="utf-8")
    assert 'id: "s-target"' in text and "--target-messages" in text and "function cardsForTarget" in text
