"""Check a message feed against the rule pack and say which rule each finding comes from."""
import datetime as dt
import json
import math
from collections import Counter, defaultdict

from .generator import COUNTRIES, luhn_ok
from .rules import (ECOM_MODES, FALLBACK_MONTHLY_CAP, NETWORK_BIN_RANGES, MCCS, PREAUTH_PROTECTION_DAYS,
                    PRESENTMENT_OTHER_DAYS, RULES)

MIN_CHIP_TXNS = 40     # ignore tiny locations: two fallbacks out of 30 is noise
REQUESTS = {"0100", "0200"}
CURRENCY_SCALE = {c["cur"]: c["scale"] for c in COUNTRIES.values()}


def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def parse_ts(s):
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")


def minor(s):
    return int(s)


def in_network_range(pan):
    return any(lo <= int(pan[:6]) <= hi for lo, hi in NETWORK_BIN_RANGES)


def validate(messages):
    msgs = list(messages)
    requests, responses = {}, {}
    for m in msgs:
        trace = m["de"].get("48", {}).get("63")
        if m["mti"] in REQUESTS and trace:
            requests[trace] = m
        elif m["mti"] in ("0110", "0210") and trace:
            responses[trace] = m

    cleared_traces = {m["de"].get("48", {}).get("63") for m in msgs if m["mti"] == "1240"}
    findings = []

    def add(rule, m, detail):
        r = RULES[rule]
        findings.append(dict(rule=rule, kind=r.kind, severity=r.severity, cite=r.cite, txn=(m.get("sim") or {}).get("txn"), trace=(m["de"].get("48") or {}).get("63"),
                             ts=m["ts"], mti=m["mti"], acquirer=m["de"].get("32"), mid=m["de"].get("42"),
                             detail=detail))

    chip_by_loc = defaultdict(lambda: [0, 0])      # (mid, month) -> [chip total, fallback]
    for m in msgs:
        de, mti = m["de"], m["mti"]
        if mti in REQUESTS or mti == "1240":
            pan = de.get("2", "")
            if not (len(pan) == 16 and luhn_ok(pan) and in_network_range(pan)):
                add("R05", m, f"PAN ending {pan[-4:]} fails range or Luhn check")
            mcc = MCCS.get(de.get("18"))
            mode = de.get("22", {}).get("1")
            amt = minor(de["4"]) / 100
            if mcc:
                if mode in ECOM_MODES and not mcc.ecom_allowed:
                    add("R06", m, f"MCC {mcc.code} ({mcc.label}) arrived with entry mode {mode}")
                if mcc.whole_dollars and amt != int(amt):
                    add("R07", m, f"ATM amount {amt}")
                med = mcc.median * CURRENCY_SCALE.get(de.get("49"), 1.0)
                if not mcc.whole_dollars and amt > 0:
                    z = math.log(amt / med) / mcc.sigma
                    if z > 3.5 and mti in REQUESTS and de["3"][:2] == "00":
                        add("R08", m, f"{amt} is {z:.1f} sigma above the usual {mcc.label} ticket")
            if mti in REQUESTS and mode in ("05", "07", "80"):
                month = m["ts"][:7]
                c = chip_by_loc[(de.get("42"), month)]
                c[0] += 1
                c[1] += mode == "80"

        elif mti == "0400":
            trace = de.get("48", {}).get("63")
            orig = requests.get(trace)
            if not de.get("90") or orig is None or de["90"].get("stan") != orig["de"]["11"]:
                add("R01", m, "reversal does not point at a known original authorization")
            else:
                resp = responses.get(trace)
                approved = minor(resp["de"]["4"]) if resp else minor(orig["de"]["4"])
                if minor(de["95"]) > approved:
                    add("R02", m, f"replacement amount {de['95']} exceeds approved {approved}")

        if mti == "1442":
            if de.get("48", {}).get("63") not in cleared_traces:
                add("R12", m, "chargeback does not reference a cleared transaction")

        if mti == "1240":
            trace = de.get("48", {}).get("63")
            orig = requests.get(trace)
            if orig is None:
                add("R11", m, "clearing record has no matching authorization")
                continue
            preauth = orig["de"].get("61", {}).get("7") == "4"
            window = PREAUTH_PROTECTION_DAYS if preauth else PRESENTMENT_OTHER_DAYS
            age = (parse_ts(m["ts"]) - parse_ts(orig["ts"])).total_seconds() / 86400
            if age > window:
                add("R03", m, f"cleared {age:.1f} days after authorization; window is {window}")
            if minor(de["4"]) > minor(orig["de"]["4"]):
                add("R09", m, f"cleared {minor(de['4'])} against authorized {minor(orig['de']['4'])}")

    for (mid, month), (total, fb) in chip_by_loc.items():
        if total >= MIN_CHIP_TXNS and fb / total > FALLBACK_MONTHLY_CAP:
            first = next(m for m in msgs if m["de"].get("42") == mid and m["ts"].startswith(month) and m["mti"] in REQUESTS)
            add("R04", first, f"merchant {mid} {month}: fallback {fb}/{total} = {fb / total:.0%}")

    by_rule = Counter(f["rule"] for f in findings)
    return dict(n_messages=len(msgs), n_findings=len(findings), by_rule=dict(by_rule), findings=findings)


def explain(report, top=5):
    lines = [f"{report['n_messages']:,} messages checked, {report['n_findings']:,} findings"]
    for rule, n in sorted(report["by_rule"].items()):
        r = RULES[rule]
        lines.append(f"  {rule} [{r.kind}/{r.severity}] x{n}  {r.text}  ({r.cite})")
    r06 = [f for f in report["findings"] if f["rule"] == "R06"]
    if r06:
        by_acq = Counter(f["acquirer"] for f in r06)
        lines.append("  R06 by acquirer: " + ", ".join(f"{a}={n}" for a, n in by_acq.most_common(top)))
    return "\n".join(lines)
