"""Settings: every knob that decides how many oddities the generated feed contains.

The defaults reproduce the original feed (clean except for a little fraud, the five operational incidents
and the ordinary noise of real traffic). Defect rates default to zero, so a feed has no data-quality
defects unless you ask for them.
"""
from dataclasses import dataclass, field, fields, replace

FRAUD_PATTERNS = ("card_testing", "skimmed_fallback", "geo_velocity", "electronics_ring")
INCIDENT_TYPES = ("issuer_outage", "latency_spike", "volume_drop", "fraud_rule_tightened", "mapping_break")
DEFECTS = ("duplicate_request", "missing_terminal_id", "amount_mismatch", "late_clearing", "orphan_clearing")


def _sev(label, default, lo, hi, step):
    return {"label": label, "default": default, "min": lo, "max": hi, "step": step}


# Every operational incident the generator can inject. The first five are the prepared set (toggled with
# `incidents`); all twelve can be placed anywhere with `custom_incidents`. target kinds: issuer, acquirer,
# country, state, mcc, or none. docs/index.html embeds this table and a test keeps them identical.
INCIDENT_INFO = {
    "issuer_outage": {"label": "Issuer outage", "target": "issuer", "target_default": "ISS07",
        "severity": _sev("Share of that issuer's authorizations declined (code 91)", .9, 0, 1, .05),
        "help": "One issuer cannot be reached, so most of its authorizations decline with 91."},
    "latency_spike": {"label": "Latency spike", "target": "country", "target_default": "GB",
        "severity": _sev("Response-time multiplier", 6, 1, 20, .5),
        "help": "Approvals in one country take several times longer than normal."},
    "volume_drop": {"label": "Volume drop (storm)", "target": "state", "target_default": "FL",
        "severity": _sev("Share of normal volume that remains", .15, 0, 1, .05),
        "help": "Card volume in one US state falls, then recovers over about a day."},
    "fraud_rule_tightened": {"label": "Fraud rule change", "target": "mcc", "target_default": "5732",
        "severity": _sev("Share of online purchases newly declined (code 59)", .35, 0, 1, .05),
        "help": "A tighter fraud rule declines online purchases in one merchant category."},
    "mapping_break": {"label": "Mapping break", "target": "acquirer", "target_default": "",
        "severity": _sev("Share of pay-at-pump purchases with the wrong entry mode", .7, 0, 1, .05),
        "help": "After a release, one acquirer sends pay-at-pump purchases with an online entry mode. Blank target picks an acquirer that runs pumps."},
    "network_outage": {"label": "Network outage", "target": "none", "target_default": "",
        "severity": _sev("Share of all authorizations declined (code 91)", .95, 0, 1, .05),
        "help": "The network cannot reach issuers, so almost every authorization declines with 91."},
    "acquirer_outage": {"label": "Acquirer outage", "target": "acquirer", "target_default": "ACQ01",
        "severity": _sev("Share of that acquirer's purchases lost", .9, 0, 1, .05),
        "help": "Merchants behind one acquirer cannot send transactions, so their volume vanishes."},
    "decline_spike": {"label": "Decline-code spike", "target": "issuer", "target_default": "",
        "severity": _sev("Extra share declined (code 05, do not honor)", .15, 0, 1, .05),
        "help": "More authorizations decline with 05. Blank target hits every issuer."},
    "holiday_surge": {"label": "Holiday or promotion surge", "target": "country", "target_default": "",
        "severity": _sev("Volume multiplier", 1.5, 1, 5, .1),
        "help": "Card volume rises across all cards (or one country) for the window."},
    "chip_firmware_bug": {"label": "Chip-reader firmware bug", "target": "acquirer", "target_default": "ACQ01",
        "severity": _sev("Share of chip and tap purchases that fall back to the stripe", .3, 0, 1, .05),
        "help": "A bad terminal update makes chip and tap purchases fall back to the magnetic stripe."},
    "clearing_delay": {"label": "Clearing delay", "target": "acquirer", "target_default": "ACQ01",
        "severity": _sev("Extra days before clearing", 6, 1, 20, 1),
        "help": "An acquirer's batch runs late, so clearings arrive days after authorization, some past the 7-day window."},
    "fraud_attack_wave": {"label": "Fraud attack wave", "target": "none", "target_default": "",
        "severity": _sev("Share of cards attacked", .01, 0, .1, .001),
        "help": "A burst of fraud cases concentrated in the window, using the fraud-pattern mix."},
}
ALL_INCIDENT_TYPES = tuple(INCIDENT_INFO)
CUSTOM_INCIDENT_KEYS = {"type", "start_day", "start_hour", "duration_hours", "target", "severity"}


def check_custom_incident(spec, where="custom_incidents"):
    if not isinstance(spec, dict):
        raise ValueError(f"{where}: each incident must be an object")
    t = spec.get("type")
    if t not in INCIDENT_INFO:
        raise ValueError(f"{where}: unknown type {t!r}; choose from {list(ALL_INCIDENT_TYPES)}")
    extra = set(spec) - CUSTOM_INCIDENT_KEYS
    if extra:
        raise ValueError(f"{where}: unknown field(s) {sorted(extra)}; allowed: {sorted(CUSTOM_INCIDENT_KEYS)}")
    sev = INCIDENT_INFO[t]["severity"]
    if not 0 <= spec.get("start_day", 0):
        raise ValueError(f"{where}: start_day cannot be negative")
    if not 0 <= spec.get("start_hour", 0) < 24:
        raise ValueError(f"{where}: start_hour must be from 0 to 23")
    if not spec.get("duration_hours", 4) > 0:
        raise ValueError(f"{where}: duration_hours must be above zero")
    if not sev["min"] <= spec.get("severity", sev["default"]) <= sev["max"]:
        raise ValueError(f"{where}: severity for {t} must be between {sev['min']} and {sev['max']}")


@dataclass
class Settings:
    # volume
    card_activity: float = 1.0             # multiplier on how often each card buys; 1.0 is about 0.95 purchase attempts per card per day
    # fraud
    fraud_rate: float = .004              # share of cards compromised (at least 2 when above zero)
    fraud_mix: dict = field(default_factory=lambda: {p: 1.0 for p in FRAUD_PATTERNS})   # relative weights
    adjudicated_rate: float = .8           # share of fraud cases that come back as 4837 chargebacks
    # ordinary behavior that looks odd but is valid
    base_decline_rate: float = .06         # share of authorizations the issuer declines
    reversal_timeout_rate: float = .01     # approved sales that end in a timeout reversal, never cleared
    refund_rate: float = .02               # cleared sales that are refunded later
    dispute_rate: float = .004             # ordinary (non-fraud) cleared sales that become chargebacks
    fallback_rate: float = .01             # chip purchases that fall back to magnetic stripe, per transaction
    broken_chip_merchants: int = 3         # merchants with a bad chip reader (benign look-alike)
    broken_chip_fallback_rate: float = .35
    # operational incidents (answer key: incidents.json)
    incidents: tuple = INCIDENT_TYPES      # which incidents to inject; empty for none
    mapping_break_share: float = .7        # share of the affected acquirer's transactions that carry the wrong mode
    custom_incidents: list = field(default_factory=list)   # extra incidents: {type, start_day, start_hour, duration_hours, target, severity}
    # data-quality defects, each a share of the eligible messages (all zero by default)
    duplicate_rate: float = 0.0            # authorization requests sent twice
    missing_terminal_rate: float = 0.0     # card-present requests missing the terminal ID (DE 41)
    amount_mismatch_rate: float = 0.0      # clearings larger than their authorization
    late_clearing_rate: float = 0.0        # clearings after the 7-day window
    orphan_clearing_rate: float = 0.0      # clearings whose authorization is missing from the feed

    def __post_init__(self):
        for f in fields(self):
            v = getattr(self, f.name)
            if f.name.endswith("_rate") or f.name.endswith("_share"):
                if not 0 <= v <= 1:
                    raise ValueError(f"{f.name} must be between 0 and 1, got {v}")
        if self.broken_chip_merchants < 0:
            raise ValueError("broken_chip_merchants cannot be negative")
        if not 0 < self.card_activity <= 10:
            raise ValueError(f"card_activity must be above 0 and at most 10, got {self.card_activity}")
        self.incidents = tuple(self.incidents)
        bad = set(self.incidents) - set(INCIDENT_TYPES)
        if bad:
            raise ValueError(f"unknown incident type(s): {sorted(bad)}; choose from {list(INCIDENT_TYPES)}")
        for i, spec in enumerate(self.custom_incidents):
            check_custom_incident(spec, f"custom_incidents[{i}]")
        unknown = set(self.fraud_mix) - set(FRAUD_PATTERNS)
        if unknown:
            raise ValueError(f"unknown fraud pattern(s): {sorted(unknown)}; choose from {list(FRAUD_PATTERNS)}")
        if self.fraud_rate and sum(self.fraud_mix.values()) <= 0:
            raise ValueError("fraud_mix needs at least one positive weight")

    @classmethod
    def coerce(cls, value):
        if value is None:
            return cls()
        if isinstance(value, cls):
            return value
        names = {f.name for f in fields(cls)}
        unknown = set(value) - names
        if unknown:
            raise ValueError(f"unknown setting(s): {sorted(unknown)}; run 'txnforge settings' for the list")
        return cls(**value)

    def with_overrides(self, **kw):
        return replace(self, **kw)

    def as_dict(self):
        d = {f.name: getattr(self, f.name) for f in fields(self)}
        d["incidents"] = list(d["incidents"])
        return d


# Coefficients measured on generated feeds (seed 11). docs/index.html embeds the same block and a test
# keeps the two identical.
ESTIMATE = {
    "requests_per_card_day": 0.945,     # authorization requests per card per day, before refunds and fraud
    "clear_base": 0.938, "clear_per_timeout": 0.87,           # clearings per approved request
    "reversal_base": 0.1057, "reversal_per_timeout": 1.86,    # reversal messages per approved request
    "refund_share": 0.93,                                     # share of cleared sales that can be refunded
    "orphan_share": 0.9,                                      # share of clearings that can lose their authorization
    "fraud_msgs": {                                           # messages per compromised card: [not adjudicated, adjudicated]
        "card_testing": [25.8, 30.4], "skimmed_fallback": [13.4, 17.8],
        "geo_velocity": [6.0, 8.0], "electronics_ring": [13.1, 17.4],
    },
    "messages_per_second": 9000,                              # one process, measured on the author's PC
    "parallel_efficiency": 0.85,                              # 4 workers ran 10M messages at 30.8k/s (4 x 9k x 0.85)
    "group_messages": 250000,                                 # target size of one group of cards
    "max_workers": 4,
    "disk_bytes_per_message": 370,                            # messages.jsonl: 3,705 MB for 10,057,020 messages
    "ram_gb_per_worker": 0.45,                                # peak 1.72 GB across 4 workers
}


def run_cost(messages, workers=None):
    """Rough time, disk and memory for a run of this size when written to disk with `txnforge generate`."""
    e = ESTIMATE
    groups = max(1, -(-int(messages) // e["group_messages"]))
    workers = min(workers or e["max_workers"], groups)
    rate = e["messages_per_second"] * (1 if workers == 1 else workers * e["parallel_efficiency"])
    return {"groups": groups, "workers": workers, "seconds": messages / rate,
            "disk_gb": messages * e["disk_bytes_per_message"] / 1e9, "ram_gb": workers * e["ram_gb_per_worker"]}



def estimate(cards, days, settings=None):
    """Rough message counts for a run: good to within a few percent on feeds of a few thousand cards or more."""
    lv = Settings.coerce(settings)
    e = ESTIMATE
    requests = cards * days * e["requests_per_card_day"] * lv.card_activity
    approved = requests * (1 - lv.base_decline_rate)
    clearings = approved * (e["clear_base"] - e["clear_per_timeout"] * lv.reversal_timeout_rate)
    reversals = approved * (e["reversal_base"] + e["reversal_per_timeout"] * lv.reversal_timeout_rate)
    refunds = lv.refund_rate * clearings * e["refund_share"]
    compromised = max(2, int(cards * lv.fraud_rate)) if lv.fraud_rate else 0
    weight = sum(lv.fraud_mix.values()) or 1
    per_card = sum(w * (e["fraud_msgs"][p][0] + lv.adjudicated_rate * (e["fraud_msgs"][p][1] - e["fraud_msgs"][p][0]))
                   for p, w in lv.fraud_mix.items()) / weight
    fraud = compromised * per_card
    chargebacks = lv.dispute_rate * clearings
    duplicates = lv.duplicate_rate * (requests + refunds)
    orphans = lv.orphan_clearing_rate * clearings * e["orphan_share"]
    parts = {"authorizations": 2 * (requests + refunds) - 2 * orphans, "clearings": clearings,
             "reversals": reversals, "chargebacks": chargebacks, "fraud cases": fraud, "duplicates": duplicates}
    return {"messages": sum(parts.values()), "transactions": requests + refunds + fraud / 2.6,
            "compromised_cards": compromised, "parts": parts}


def cards_for_target(target_messages, days, settings=None):
    """How many cards give roughly this many messages (uses estimate(); a few percent off)."""
    per_card = estimate(100000, days, settings)["messages"] / 100000
    return max(2, round(target_messages / per_card))


def parse_assignment(text):
    """'fraud_rate=0.02' or 'incidents=issuer_outage,latency_spike' or 'fraud_mix=card_testing:3,geo_velocity:1'."""
    key, _, raw = text.partition("=")
    key = key.strip()
    if not raw and key != "incidents":
        raise ValueError(f"expected name=value, got '{text}'")
    if key == "incidents":
        return key, tuple(x for x in raw.split(",") if x)
    if key == "fraud_mix":
        mix = {}
        for part in raw.split(","):
            name, _, w = part.partition(":")
            mix[name.strip()] = float(w or 1)
        return key, mix
    if key == "broken_chip_merchants":
        return key, int(raw)
    return key, float(raw)
