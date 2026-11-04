"""One transaction at a time: the sandbox view. Each scenario returns the full message lifecycle."""
import datetime as dt
import random

from .generator import Feed, auth_pair, build_population, fuel_two_step, preauth_flow, rev, sale

SCENARIOS = {
    "sale_approved": "Retail purchase approved, then cleared within 7 days.",
    "sale_declined_51": "Retail purchase declined, insufficient funds (DE 39 = 51).",
    "fuel_two_step": "Pay-at-pump: a small first authorization, released, then the real amount.",
    "hotel_preauth": "Lodging preauthorization, partial reversal of the unused hold, clearing at checkout.",
    "atm_withdrawal": "ATM cash: single-message 0200/0210, whole-dollar amount, no clearing.",
    "reversal_timeout": "Acquirer never receives the response and sends a full reversal (DE 95 = 0).",
    "chip_fallback": "Chip failed, magnetic stripe used (DE 22 = 80): the pattern with very high fraud rates.",
}
MCC_FOR = {"sale_approved": "5411", "sale_declined_51": "5310", "fuel_two_step": "5542", "hotel_preauth": "7011",
           "atm_withdrawal": "6011", "reversal_timeout": "5814", "chip_fallback": "5732"}
DEFAULT_AMOUNT = {"fuel_two_step": 46.2, "hotel_preauth": 180.0, "atm_withdrawal": 100.0, "chip_fallback": 420.0}


def run(name, seed=1, amount=None):
    if name not in SCENARIOS:
        raise KeyError(name)
    rng = random.Random(seed)
    _, merchants, _, cards = build_population(rng, 1, 300)
    merch = next((m for m in merchants if m["mcc"] == MCC_FOR[name]), merchants[0])
    merch.update(mcc=MCC_FOR[name], ecom_only=False, unattended=MCC_FOR[name] in ("5542", "6011"),country="US", state="MO", chip_ok=True, broken_chip=False, acquirer="ACQ01")
    card = cards[0]
    card.update(country="US", state="MO")
    feed = Feed(rng)
    t = dt.datetime(2026, 3, 2, 18, 30, 0)
    amt = amount if amount is not None else DEFAULT_AMOUNT.get(name, 27.5)
    txn = feed.next_txn()
    if name == "sale_approved":
        sale(feed, rng, card, merch, t, amt, "840", "07", "00", 180, txn)
    elif name == "reversal_timeout":
        req, trace = auth_pair(feed, rng, card, merch, t, amt, "840", "05", "00", 180, txn)
        rev(feed, t + dt.timedelta(seconds=35), req, trace, 0.0, txn)
    elif name == "sale_declined_51":
        sale(feed, rng, card, merch, t, amt, "840", "05", "51", 200, txn)
    elif name == "fuel_two_step":
        fuel_two_step(feed, rng, card, merch, t, amt, "840", "05", "00", 180, txn)
    elif name == "hotel_preauth":
        preauth_flow(feed, rng, card, merch, t, amt, "840", "05", "00", 180, txn)
    elif name == "atm_withdrawal":
        sale(feed, rng, card, merch, t, amt, "840", "05", "00", 180, txn)
    elif name == "chip_fallback":
        sale(feed, rng, card, merch, t, amt, "840", "80", "00", 190, txn)
    return feed.msgs
