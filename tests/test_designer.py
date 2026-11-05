import json
import re
from pathlib import Path

from txnforge import generate
from txnforge.settings import ESTIMATE, Settings, estimate

PAGE = Path(__file__).resolve().parents[1] / "docs" / "index.html"


def page_defaults():
    text = PAGE.read_text(encoding="utf-8")
    block = re.search(r"/\*DEFAULTS\*/\s*const DEFAULTS = (\{.*?\});\s*/\*END DEFAULTS\*/", text, re.S)
    return text, json.loads(block.group(1))


def test_designer_defaults_match_python_defaults():
    _, defaults = page_defaults()
    assert defaults == Settings().as_dict()


def test_designer_estimate_coefficients_match_python():
    text = PAGE.read_text(encoding="utf-8")
    block = re.search(r"/\*ESTIMATE\*/\s*const ESTIMATE = (\{.*?\});\s*/\*END ESTIMATE\*/", text, re.S)
    assert json.loads(block.group(1)) == ESTIMATE


def test_designer_incident_catalog_matches_python():
    from txnforge.settings import INCIDENT_INFO
    text = PAGE.read_text(encoding="utf-8")
    block = re.search(r"/\*INCIDENT_INFO\*/\s*const INCIDENT_INFO = (\{.*?\});\s*/\*END INCIDENT_INFO\*/", text, re.S)
    assert json.loads(block.group(1)) == INCIDENT_INFO


def test_designer_defines_every_glossary_term_it_promises():
    text = PAGE.read_text(encoding="utf-8")
    for term in ("Basis points", "MCC", "STAN", "Chargeback", "Adjudicated", "Orphan clearing", "Message", "Transaction"):
        assert f'["{term}' in text, term


def test_designer_has_a_control_for_every_setting():
    text, _ = page_defaults()
    controls = {"fraud_mix", "incidents", "custom_incidents"}      # built from their own sections, not the LEVERS table
    for name in Settings().as_dict():
        assert name in controls or f'key: "{name}"' in text, name


def test_size_estimate_is_close_to_real_feeds():
    cases = [
        (800, 7, 100, {}),
        (1500, 10, 200, dict(fraud_rate=.03, refund_rate=.1, reversal_timeout_rate=.05, base_decline_rate=.15)),
        (1200, 10, 150, dict(duplicate_rate=.02, orphan_clearing_rate=.02, dispute_rate=.02,
                             fraud_mix={"card_testing": 3, "geo_velocity": 1})),
    ]
    for cards, days, merchants, lv in cases:
        actual = generate(seed=21, cards=cards, merchants=merchants, days=days, settings=lv)["messages"]
        est = estimate(cards, days, lv)["messages"]
        assert abs(est - len(actual)) / len(actual) < .06, (cards, days, est, len(actual))


def test_merchant_count_does_not_change_the_estimate():
    assert estimate(1000, 10)["messages"] == estimate(1000, 10, {})["messages"]


def test_designer_output_is_accepted_by_python():
    _, defaults = page_defaults()
    defaults["fraud_mix"] = {"card_testing": 3, "geo_velocity": 1}
    defaults["incidents"] = ["issuer_outage"]
    defaults["duplicate_rate"] = .01
    assert Settings.coerce(defaults).duplicate_rate == .01


def test_designer_lets_you_type_a_number_of_events():
    text = PAGE.read_text(encoding="utf-8")
    assert 'id: "s-target"' in text and "--target-messages" in text and "function cardsForTarget" in text


def test_sample_data_is_small_masked_and_stripped():
    text = PAGE.read_text(encoding="utf-8")
    block = re.search(r"/\*SAMPLE\*/\s*const SAMPLE = (\{.*?\});\s*/\*END SAMPLE\*/", text, re.S)
    sample = json.loads(block.group(1))
    messages = [m for ex in sample["examples"] for m in ex["messages"]]
    assert 5 <= len(sample["examples"]) <= 15 and len(messages) <= 100        # a sample, not the full file
    assert all(ex["story"] and ex["watch"] and ex["messages"] for ex in sample["examples"])
    assert not re.search(r"\d{16}", block.group(1))                            # card numbers are masked
    assert all("sim" not in m and "sim_issuer" not in m["de"] for m in messages)
    assert all(re.fullmatch(r"\d{6}\*+\d{4}", m["de"]["2"]) for m in messages if m["de"].get("2"))


def test_browser_preview_copies_match_the_source():
    """The page loads these copies into Pyodide; run tools/sync_pages_py.py if this fails."""
    import sys
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "tools"))
    from sync_pages_py import MODULES
    for name in MODULES:
        src = (root / "src" / "txnforge" / name).read_bytes().replace(b"\r\n", b"\n")
        assert (root / "docs" / "py" / "txnforge" / name).read_bytes().replace(b"\r\n", b"\n") == src, name
    page = PAGE.read_text(encoding="utf-8")
    assert all(f'"{name}"' in page for name in MODULES)                        # the page fetches every module


def test_browser_preview_returns_masked_stripped_mixed_cases():
    import importlib.util
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("preview_glue", root / "docs" / "py" / "preview_glue.py")
    glue = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(glue)
    settings = Settings().as_dict()
    settings.update(fraud_rate=.03, dispute_rate=.03, duplicate_rate=.01)
    out = json.loads(glue.preview(json.dumps(settings), 5, 5000, 400, 30, "2026-03-02"))   # caps must apply
    s = out["summary"]
    assert s["cards"] == glue.MAX_CARDS and s["days"] == glue.MAX_DAYS and s["merchants"] == glue.MAX_MERCHANTS
    cases = out["cases"]
    assert 8 <= len(cases) <= glue.ORDINARY_CASES + glue.MAX_LABELLED_CASES
    assert any(c["labels"] for c in cases) and any(not c["labels"] for c in cases)    # mixed
    text = json.dumps(cases)
    assert not re.search(r"\d{16}", text) and "sim_issuer" not in text and '"sim"' not in text
    assert all(re.fullmatch(r"\d{6}\*+\d{4}", m["de"]["2"]) for c in cases for m in c["messages"] if m["de"].get("2"))
    assert all(i["start"] < "2026-03-09T00:00:00Z" for i in s["incidents"])
    assert len(out["feed"]) == s["messages"] and out["key"]
