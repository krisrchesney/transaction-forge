"""Rules as data. Every rule says where it came from, so no anomaly is just "what the AI imagined".

kind = "network"   -> stated in the a card network's published processing rules (June 2025 edition)
kind = "heuristic" -> operating knowledge from industry practice (industry practice), not a numbered network rule
Descriptions are paraphrased, not copied from the manuals.
"""
from dataclasses import dataclass

# ---- merchant categories ---------------------------------------------------------------
# median = typical ticket in USD, sigma = lognormal spread, modes = POS entry mode mix (DE 22 sf1),
# peak = hour-of-day curve name, preauth = authorize-now/final-amount-later pattern.
# ATM and fuel are shaped differently on purpose.


@dataclass(frozen=True)
class Mcc:
    code: str
    label: str
    median: float
    sigma: float
    modes: dict
    peak: str
    weight: float                 # share of overall volume
    preauth: bool = False
    whole_dollars: bool = False
    ecom_allowed: bool = True


MCCS = {m.code: m for m in [
    Mcc("5411", "grocery", 48, 0.65, {"05": .38, "07": .42, "81": .12, "90": .04, "01": .01, "10": .03}, "daytime", 20),
    Mcc("5499", "convenience store", 11, 0.7, {"05": .35, "07": .5, "90": .05, "81": .03, "01": .01, "10": .06}, "daytime", 8),
    Mcc("5542", "pay-at-pump fuel", 42, 0.45, {"05": .55, "07": .15, "90": .30}, "commute", 7,
        preauth=True, ecom_allowed=False),
    Mcc("5541", "service station", 38, 0.5, {"05": .5, "07": .3, "90": .15, "01": .05}, "commute", 3,
        ecom_allowed=False),
    Mcc("5812", "restaurant", 34, 0.6, {"05": .4, "07": .4, "90": .04, "81": .12, "01": .02, "10": .02}, "dinner", 10),
    Mcc("5814", "fast food", 11, 0.55, {"05": .3, "07": .5, "81": .15, "90": .02, "10": .03}, "lunch", 10),
    Mcc("5310", "discount store", 36, 0.75, {"05": .42, "07": .4, "81": .12, "90": .03, "01": .01, "10": .02}, "daytime", 7),
    Mcc("5300", "wholesale club", 95, 0.7, {"05": .5, "07": .3, "81": .15, "90": .03, "10": .02}, "daytime", 3),
    Mcc("5912", "pharmacy", 22, 0.7, {"05": .42, "07": .42, "81": .12, "90": .02, "10": .02}, "daytime", 4),
    Mcc("5651", "clothing", 58, 0.7, {"05": .35, "07": .3, "81": .32, "10": .03}, "evening", 3),
    Mcc("5732", "electronics", 190, 0.9, {"05": .3, "07": .2, "81": .47, "10": .03}, "evening", 2),
    Mcc("5999", "specialty retail", 45, 0.9, {"05": .3, "07": .25, "81": .42, "10": .03}, "evening", 4),
    Mcc("4121", "taxi / ride share", 24, 0.6, {"81": .55, "10": .4, "05": .03, "07": .02}, "evening", 3),
    Mcc("4511", "airline", 340, 0.8, {"81": .85, "10": .1, "01": .05}, "evening", 1),
    Mcc("7011", "lodging", 210, 0.6, {"05": .4, "07": .1, "81": .45, "01": .05}, "evening", 1.5, preauth=True),
    Mcc("5968", "subscription", 15, 0.6, {"10": .8, "81": .2}, "midnight", 3, ecom_allowed=True),
    Mcc("4900", "utilities", 120, 0.6, {"10": .5, "81": .45, "01": .05}, "daytime", 2),
    Mcc("4814", "telecom", 70, 0.5, {"10": .55, "81": .4, "01": .05}, "daytime", 2),
    Mcc("7832", "cinema", 28, 0.5, {"05": .3, "07": .3, "81": .4}, "evening", 1),
    Mcc("7523", "parking", 14, 0.7, {"05": .35, "07": .35, "81": .15, "90": .15}, "daytime", 1),
    Mcc("6011", "ATM cash", 80, 0.6, {"05": .85, "07": .08, "90": .07}, "daytime", 3,
        whole_dollars=True, ecom_allowed=False),
]}

# ---- POS entry mode, DE 22 subfield 1 ---------------------------------------------------
# 07 is confirmed in the network's processing rules (EMV-mode contactless). The other values come from
# general knowledge of the network's interface specification, which was not available here: verify them.
ENTRY_MODES = {
    "01": ("manual key entry", "card_not_present_or_keyed"),
    "05": ("chip", "card_present"),
    "07": ("contactless chip", "card_present"),
    "10": ("credential on file", "card_not_present"),
    "80": ("chip fallback to magnetic stripe", "card_present"),
    "81": ("e-commerce", "card_not_present"),
    "90": ("magnetic stripe", "card_present"),
}
CARD_PRESENT = {k for k, v in ENTRY_MODES.items() if v[1] == "card_present"}
ECOM_MODES = {"81", "10"}

# ---- response codes, DE 39 (simplified subset used by the simulator) ----------------------
RESPONSE_CODES = {
    "00": "approved",
    "05": "do not honor",
    "10": "partial approval",
    "14": "invalid card number",
    "51": "insufficient funds",
    "54": "expired card",
    "59": "suspected fraud",
    "91": "issuer or switch unavailable",
    "96": "system malfunction",
}
BASE_DECLINE_MIX = {"05": .45, "51": .30, "54": .06, "14": .04, "59": .05, "91": .05, "96": .05}

# ---- time windows and caps ----------------------------------------------------------------
PREAUTH_PROTECTION_DAYS = 30        # Rulebook 2.8, message reason code 4808 protection
FINAL_AUTH_PROTECTION_DAYS = 7      # Rulebook 2.8
PRESENTMENT_PREAUTH_DAYS = 30       # Rulebook 3.15.1
PRESENTMENT_OTHER_DAYS = 7          # Rulebook 3.15.1
FALLBACK_MONTHLY_CAP = 0.05         # Rulebook 4.1 variation: technical fallback <= 5% of chip txns per location per month
NETWORK_BIN_RANGES = [(222100, 272099), (510000, 559999)]   # (22210000-27209999, 51000000-55999999)


@dataclass(frozen=True)
class Rule:
    id: str
    kind: str          # network | heuristic
    severity: str      # error | warning
    cite: str
    text: str


RULES = {r.id: r for r in [
    Rule("R01", "network", "error", "Rulebook 2.11",
         "A reversal must reference its original authorization (Trace ID and original data elements)."),
    Rule("R02", "network", "error", "Rulebook 2.11",
         "A reversal's replacement amount cannot exceed the amount the issuer approved."),
    Rule("R03", "network", "error", "Rulebook 3.15.1 / 2.8",
         "Clearing must arrive within 7 days of approval (30 days for a preauthorization), or the protection period expires."),
    Rule("R04", "network", "warning", "Rulebook 4.1 (variations)",
         "Technical fallback should stay at or below 5% of chip transactions per merchant location per month."),
    Rule("R05", "network", "error", "Rulebook (BIN ranges)",
         "A card number must be in the 222100-272099 or 510000-559999 range and pass the Luhn check."),
    Rule("R06", "heuristic", "error", "industry practice",
         "Some MCCs cannot be e-commerce (pay-at-pump fuel, ATM cash)."),
    Rule("R07", "heuristic", "warning", "industry practice",
         "ATM cash amounts are normally whole dollars."),
    Rule("R08", "heuristic", "warning", "industry practice",
         "Ticket size far above the usual range for the MCC; unusual, not necessarily wrong."),
    Rule("R09", "network", "warning", "Rulebook 2.5 / 2.8",
         "A clearing amount should not exceed what was authorized (regional exceptions exist, for example gratuities; check)."),
    Rule("R12", "heuristic", "error", "industry practice",
         "A chargeback must reference a cleared transaction (its first presentment) by trace ID."),
    Rule("R11", "heuristic", "error", "industry practice",
         "Every clearing record should trace back to an authorization (except offline chip and similar cases)."),
]}
