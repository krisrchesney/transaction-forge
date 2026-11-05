"""Spec-shaped synthetic card transactions with full lifecycles, labeled fraud and operational incidents.

Output messages are ISO 8583-style JSON (numbered data elements). They are *inspired by* real network
formats, not wire-compatible. The answer key (labels, incidents) is written to separate files so a
consumer of the feed never sees it.
"""
import copy
import datetime as dt
import heapq
import json
import math
import os
import random
import time
from pathlib import Path

from .settings import INCIDENT_INFO, Settings
from .rules import (BASE_DECLINE_MIX, FINAL_AUTH_PROTECTION_DAYS, MCCS, PREAUTH_PROTECTION_DAYS,
                    NETWORK_BIN_RANGES, RESPONSE_CODES)

COUNTRIES = {  # share of cards, UTC offset (no DST, kept simple), ISO numeric currency, curve name
    "US": dict(share=.70, tz=-6, cur="840", curve="us", fx=1.00, scale=1.00, name="USA"),
    "GB": dict(share=.15, tz=0, cur="826", curve="uk", fx=1.27, scale=0.80, name="GBR"),
    "ES": dict(share=.15, tz=1, cur="978", curve="es", fx=1.08, scale=0.92, name="ESP"),
}
US_STATES = {"FL": .12, "TX": .18, "CA": .25, "NY": .15, "MO": .10, "IL": .10, "OH": .10}
ACQUIRERS = [f"ACQ{n:02d}" for n in range(1, 9)]


# ---- small helpers ---------------------------------------------------------------------------
def luhn_check_digit(body: str) -> str:
    total = 0
    for i, ch in enumerate(reversed(body)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            d -= 9 if d > 9 else 0
        total += d
    return str((10 - total % 10) % 10)


def luhn_ok(pan: str) -> bool:
    return pan.isdigit() and luhn_check_digit(pan[:-1]) == pan[-1]


def poisson(rng, lam):
    if lam <= 0:
        return 0
    if lam > 30:
        return max(0, int(rng.gauss(lam, math.sqrt(lam))))
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def gauss(h, mu, sd):
    d = min(abs(h - mu), 24 - abs(h - mu))
    return math.exp(-0.5 * (d / sd) ** 2)


def hour_curve(name, h):
    if name == "us":
        return gauss(h, 20, 3.5) + 0.35 * gauss(h, 12.5, 2) + 0.03
    if name == "uk":
        return gauss(h, 19, 3.5) + 0.4 * gauss(h, 12.5, 2) + 0.03
    if name == "es":   # long lunch lull, late evening
        return max(0.01, gauss(h, 22.5, 2.8) + 0.5 * gauss(h, 11, 1.5) + 0.05 - 0.2 * gauss(h, 15.5, 1.2))
    return 1.0


def peak_curve(name, h):
    return {"daytime": gauss(h, 13, 4.5), "lunch": gauss(h, 12.5, 1.4), "dinner": gauss(h, 19, 2),
            "commute": gauss(h, 8, 1.5) + gauss(h, 17, 1.8), "evening": gauss(h, 20, 3.2),
            "midnight": gauss(h, 0.5, 2.5) + 0.2}[name]


def pick(rng, weights: dict):
    r = rng.random() * sum(weights.values())
    for k, w in weights.items():
        r -= w
        if r <= 0:
            return k
    return k


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- population --------------------------------------------------------------------------------
def make_pan(rng, issuer_bin):
    body = f"{issuer_bin}{rng.randrange(10**9):09d}"
    return body + luhn_check_digit(body)


def build_world(rng, n_merchants, n_issuers=25):
    bins = []
    while len(bins) < n_issuers:
        lo, hi = NETWORK_BIN_RANGES[0 if rng.random() < .15 else 1]
        b = rng.randint(lo, hi)
        if b not in bins:
            bins.append(b)
    issuers = {str(b): f"ISS{i:02d}" for i, b in enumerate(bins, 1)}

    merchants, by_key = [], {}
    mcc_w = {c: m.weight for c, m in MCCS.items()}
    for i in range(n_merchants):
        cc = pick(rng, {k: v["share"] for k, v in COUNTRIES.items()})
        code = pick(rng, mcc_w)
        m = MCCS[code]
        unattended = code in ("5542", "6011")
        merch = dict(
            mid=f"{i:015d}", mcc=code, country=cc, acquirer=rng.choice(ACQUIRERS),
            name=f"{m.label.upper()} {i % 997:03d}", city=f"CITY{rng.randrange(60):02d}",
            state=pick(rng, US_STATES) if cc == "US" else "",
            zip=f"{rng.randrange(10**5):05d}", tid=f"T{i:07d}", unattended=unattended,
            ecom_only=(m.modes.get("81", 0) + m.modes.get("10", 0) > .9),
            chip_ok=rng.random() < .985, broken_chip=False,
        )
        merchants.append(merch)
        by_key.setdefault((cc, code), []).append(merch)
        by_key.setdefault(("*", code), []).append(merch)
    return issuers, bins, merchants, by_key


def make_cards(rng, n_cards, bins, issuers):
    mcc_w = {c: m.weight for c, m in MCCS.items()}
    cards = []
    for i in range(n_cards):
        cc = pick(rng, {k: v["share"] for k, v in COUNTRIES.items()})
        b = rng.choice(bins)
        affinity = {c: rng.random() ** 2 * w for c, w in mcc_w.items()}
        cards.append(dict(
            pan=make_pan(rng, b), issuer=issuers[str(b)], country=cc,
            state=pick(rng, US_STATES) if cc == "US" else "", spend=math.exp(rng.gauss(0, .35)),
            rate=max(.05, rng.gammavariate(2.0, .45)), affinity=affinity,
            exp=f"{rng.randint(27, 31)}{rng.randint(1, 12):02d}",
        ))
    return cards


def build_population(rng, n_cards, n_merchants, n_issuers=25):
    issuers, bins, merchants, by_key = build_world(rng, n_merchants, n_issuers)
    return issuers, merchants, by_key, make_cards(rng, n_cards, bins, issuers)


# ---- message builders --------------------------------------------------------------------------
class Feed:
    def __init__(self, rng, settings=None, shard=0):
        self.rng, self.msgs, self.stan, self.txn, self.shard = rng, [], 0, 0, shard
        self.labels = []
        self.incs = []          # the incidents in force; some change how messages are built (clearing delay)
        self.lv = Settings.coerce(settings)

    def next_txn(self):
        self.txn += 1
        return f"T{self.txn:08d}" if self.shard == 0 else f"T{self.shard:03d}{self.txn:08d}"

    def msg(self, mti, t, de, txn, **meta):
        self.stan += 1
        de = {"7": t.strftime("%m%d%H%M%S"), "11": f"{self.stan % 10**6:06d}", **de}
        rec = {"mti": mti, "ts": iso(t), "de": de, "sim": {"txn": txn}}
        if meta:
            rec["meta"] = meta
        self.msgs.append(rec)
        return rec


def amount_str(amt):
    return f"{int(round(amt * 100)):012d}"


def make_auth_de(card, merch, amt, mode, cur, status="0", proc="00"):
    cc = COUNTRIES[merch["country"]]
    de = {
        "2": card["pan"], "3": proc + "0000", "4": amount_str(amt), "14": card["exp"],
        "18": merch["mcc"], "22": {"1": mode, "2": "1" if mode in ("05", "07") else "0"},
        "37": None, "42": merch["mid"],
        "43": {"name": merch["name"], "city": merch["city"], "state": merch["state"],
               "country": cc["name"], "zip": merch["zip"]},
        "49": cur, "61": {"7": status}, "32": merch["acquirer"], "sim_issuer": card["issuer"],
    }
    if mode in ("05", "07", "90", "80", "01") and not merch["ecom_only"]:
        de["41"] = merch["tid"]
    return de


def to_local(t, country):
    return t + dt.timedelta(hours=COUNTRIES[country]["tz"])


# ---- incidents (the answer key for operational monitoring) -------------------------------------
def default_incidents(start, days, settings=None, merchants=None, prepared=True):
    """The five prepared incidents. Targets that depend on who exists in the world (which acquirer runs pay-at-pump
    merchants) are chosen from `merchants`, so an incident always lands on something."""
    lv = Settings.coerce(settings)
    d = lambda n, h=0: start + dt.timedelta(days=n, hours=h)
    fuel = [m["acquirer"] for m in (merchants or []) if m["mcc"] == "5542"]
    acq = max(sorted(set(fuel)), key=fuel.count) if fuel else "ACQ03"
    chosen = [inc for inc in _incident_list(d, days, lv, acq) if inc["type"] in lv.incidents] if prepared else []
    return chosen + _custom_incidents(start, lv, acq)


# type -> (field that holds the target, field that holds the severity) in the internal incident record
_INCIDENT_FIELDS = {
    "issuer_outage": ("issuer", "share"), "network_outage": (None, "share"), "acquirer_outage": ("acquirer", "share"),
    "latency_spike": ("country", "multiplier"), "volume_drop": ("state", "factor"),
    "holiday_surge": ("country", "factor"), "fraud_rule_tightened": ("mcc", "share"),
    "decline_spike": ("issuer", "share"), "mapping_break": ("acquirer", "share"),
    "chip_firmware_bug": ("acquirer", "share"), "clearing_delay": ("acquirer", "delay_days"),
    "fraud_attack_wave": (None, "share"),
}


def _custom_incidents(start, lv, fuel_acquirer):
    out = []
    for n, spec in enumerate(lv.custom_incidents, 1):
        kind = spec["type"]
        info = INCIDENT_INFO[kind]
        target_field, severity_field = _INCIDENT_FIELDS[kind]
        begin = start + dt.timedelta(days=spec.get("start_day", 0), hours=spec.get("start_hour", 0))
        inc = {"id": f"INC-C{n}", "type": kind, "custom": True, "start": iso(begin),
               "end": iso(begin + dt.timedelta(hours=spec.get("duration_hours", 4))),
               "effect": f'{info["label"]}: {info["help"]}'}
        if target_field:
            default = fuel_acquirer if kind == "mapping_break" else info["target_default"]
            inc[target_field] = spec.get("target") or default
        inc[severity_field] = spec.get("severity", info["severity"]["default"])
        if kind == "mapping_break":
            inc.update(mcc="5542", to_mode="81")
        out.append(inc)
    return out


def _incident_list(d, days, lv, acq="ACQ03"):
    return [
        {"id": "INC-1", "type": "issuer_outage", "issuer": "ISS07", "share": .9, "start": iso(d(2, 14)), "end": iso(d(2, 17)),
         "effect": "Most authorizations for this issuer decline with 91 for three hours."},
        {"id": "INC-2", "type": "latency_spike", "country": "GB", "multiplier": 6, "start": iso(d(4, 9)), "end": iso(d(4, 11)),
         "effect": "UK response latency about 6x normal for two hours."},
        {"id": "INC-3", "type": "volume_drop", "state": "FL", "start": iso(d(6, 0)), "end": iso(d(8, 12)),
         "factor": .15, "effect": "Storm: Florida card volume falls to about 15%, then recovers over a day."},
        {"id": "INC-4", "type": "fraud_rule_tightened", "mcc": "5732", "share": .35, "start": iso(d(5, 0)), "end": iso(d(7, 0)),
         "effect": "Electronics e-commerce declines with 59 jump for two days."},
        {"id": "INC-5", "type": "mapping_break", "acquirer": acq, "mcc": "5542", "start": iso(d(9, 0)),
         "end": iso(d(days, 0)), "to_mode": "81", "share": lv.mapping_break_share,
         "effect": "After a release, one acquirer sends pay-at-pump transactions with e-commerce entry mode."},
    ]


def parse(ts):
    return dt.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")


def active(inc, t):
    return parse(inc["start"]) <= t < parse(inc["end"])


def overlap_fraction(inc, day_start):
    """Share of the 24 hours starting at day_start that falls inside the incident window."""
    lo, hi = max(parse(inc["start"]), day_start), min(parse(inc["end"]), day_start + dt.timedelta(days=1))
    return max(0.0, (hi - lo).total_seconds() / 86400)


# ---- the main generator -------------------------------------------------------------------------
SHARD_TARGET_MESSAGES = 250_000


def plan_shards(cards, days, shard_cards=None):
    """Cards are independent of each other, so a run is cut into groups of cards that are generated, written to
    disk and freed one at a time. Returns the number of cards in each group."""
    per = shard_cards or max(500, int(SHARD_TARGET_MESSAGES / (days * 3.0)))
    n = max(1, -(-cards // per))
    base, extra = divmod(cards, n)
    return [base + (1 if i < extra else 0) for i in range(n)]


def _run_shard(job):
    """Generate one group of cards end to end: purchases, fraud, chargebacks, defects. Runs in a worker process."""
    lv = Settings.coerce(job["settings"])
    seed, idx, days = job["seed"], job["idx"], job["days"]
    start_dt = dt.datetime.fromisoformat(job["start"])
    issuers, bins, merch_list, by_key = build_world(random.Random(f"{seed}-world"), job["merchants"])
    rng = random.Random(f"{seed}-shard-{idx}")
    card_list = make_cards(rng, job["n_cards"], bins, issuers)
    feed = Feed(rng, lv, idx)
    incs = default_incidents(start_dt, days, lv, merch_list, prepared=job["incidents"])
    by_type = lambda ty: [i for i in incs if i["type"] == ty]
    feed.incs = incs

    # a few merchants have genuinely bad chip readers: a high fallback rate that is valid, not fraud
    bad = [m for m in merch_list if m["mcc"] in ("5411", "5812", "5310") and m["country"] == "US"][:lv.broken_chip_merchants]
    for m in bad:
        m["broken_chip"] = True
        if idx == 0:
            feed.labels.append({"kind": "benign_lookalike", "what": "high chip fallback, bad reader", "mid": m["mid"]})

    for day in range(days):
        day_start = start_dt + dt.timedelta(days=day)
        for card in card_list:
            lam = card["rate"] * lv.card_activity * (1.12 if (day_start.weekday() >= 5) else 1.0)
            for inc in by_type("holiday_surge"):
                if not inc["country"] or card["country"] == inc["country"]:
                    lam *= 1 + (inc["factor"] - 1) * overlap_fraction(inc, day_start)
            for inc in by_type("volume_drop"):
                if card["state"] == inc["state"]:
                    mid_t = day_start + dt.timedelta(hours=12)
                    if active(inc, mid_t):
                        lam *= inc["factor"]
                    elif mid_t < parse(inc["end"]) + dt.timedelta(days=1) and mid_t >= parse(inc["end"]):
                        lam *= .6
            for _ in range(poisson(rng, lam)):
                emit_purchase(feed, rng, card, day_start, by_key, merch_list, by_type, incs)

    k = min(job["fraud_k"], len(card_list))
    for card in (rng.sample(card_list, k) if k else []):
        emit_fraud(feed, rng, card, start_dt, days, by_key, merch_list)
    for inc in by_type("fraud_attack_wave"):
        attacked = min(len(card_list), round(len(card_list) * inc["share"]))
        for card in (rng.sample(card_list, attacked) if attacked else []):
            emit_fraud(feed, rng, card, start_dt, days, by_key, merch_list, window=(parse(inc["start"]), parse(inc["end"])),
                       incident=inc["id"])

    add_chargebacks(feed, rng)
    inject_defects(feed, random.Random(f"{seed}-defects-{idx}"), lv)    # own stream: defects never shift the base feed
    feed.msgs.sort(key=lambda m: (m["ts"], m["de"]["11"]))
    _attach_traces(feed.msgs, feed.labels)
    truth = truth_rows(feed.labels)
    if job.get("strip_sim"):
        for m in feed.msgs:                          # fields a real network feed would not carry
            m.pop("sim", None)
            m["de"].pop("sim_issuer", None)
    result = dict(idx=idx, n_messages=len(feed.msgs), issuers=len(issuers), label_counts=label_counts(feed.labels))
    if job["out_dir"]:
        result["part"], result["labels_part"], result["truth_part"] = _write_parts(Path(job["out_dir"]), idx, feed, truth)
    else:
        result["messages"], result["labels"], result["truth"] = feed.msgs, feed.labels, truth
    return result


def _attach_traces(msgs, labels):
    """Give every label the trace IDs of its transactions, so a feed without simulator fields can still be scored."""
    trace_of = {}
    for m in msgs:
        trace = (m["de"].get("48") or {}).get("63")
        if trace:
            trace_of.setdefault(m["sim"]["txn"], trace)
    for lab in labels:
        if "txns" in lab:
            lab["traces"] = [trace_of.get(t) for t in lab["txns"]]
        elif "txn" in lab:
            lab["trace"] = trace_of.get(lab["txn"])


def truth_rows(labels):
    """One row per labeled trace (or merchant, for look-alikes): the flat table a scorer joins to the feed."""
    keep = ("pattern", "adjudicated", "reason", "cause", "defect", "incident")
    rows = []
    for lab in labels:
        base = {k: lab[k] for k in keep if k in lab}
        if lab["kind"] == "benign_lookalike":
            rows.append({"kind": "benign_lookalike", "mid": lab["mid"]})
        elif "traces" in lab:
            rows.extend({"trace": t, "kind": lab["kind"], **base} for t in lab["traces"] if t)
        elif lab.get("trace"):
            rows.append({"trace": lab["trace"], "kind": lab["kind"], **base})
    return rows


def _write_parts(out, idx, feed, truth):
    messages, labels = out / f"messages.part{idx:04d}.jsonl", out / f"answer_key.part{idx:04d}.jsonl"
    truth_path = out / f"labels_by_trace.part{idx:04d}.jsonl"
    with open(messages, "w", encoding="utf-8", newline="\n") as f:
        for m in feed.msgs:
            f.write(json.dumps(m, separators=(",", ":")) + "\n")
    with open(labels, "w", encoding="utf-8", newline="\n") as f:     # never feed this to the analyzer
        for lab in feed.labels:
            f.write(json.dumps(lab) + "\n")
    with open(truth_path, "w", encoding="utf-8", newline="\n") as f:
        for row in truth:
            f.write(json.dumps(row) + "\n")
    return messages.name, labels.name, truth_path.name


def run_jobs(jobs, workers, progress=None):
    results = []
    if workers <= 1 or len(jobs) == 1:
        for job in jobs:
            results.append(_run_shard(job))
            if progress:
                progress(len(results), len(jobs), results[-1])
        return results
    from concurrent.futures import ProcessPoolExecutor, as_completed
    with ProcessPoolExecutor(max_workers=min(workers, len(jobs))) as pool:
        for fut in as_completed([pool.submit(_run_shard, job) for job in jobs]):
            results.append(fut.result())
            if progress:
                progress(len(results), len(jobs), results[-1])
    return results


def merge_sorted(parts, dest):
    """Merge time-sorted part files into one file without holding them in memory. Each line starts with
    {"mti":"0100","ts":"2026-03-02T14:31:07Z", so the timestamp is bytes 20 to 40."""
    if len(parts) == 1:
        os.replace(parts[0], dest)
        return
    files = [open(p, "rb") for p in parts]

    def lines(i, f):
        for seq, line in enumerate(f):
            yield line[20:40], i, seq, line

    try:
        with open(dest, "wb") as out:
            for _, _, _, line in heapq.merge(*(lines(i, f) for i, f in enumerate(files))):
                out.write(line)
    finally:
        for f in files:
            f.close()
    for p in parts:
        os.remove(p)


def generate(seed=7, cards=3000, merchants=400, days=14, start="2026-03-02", incidents=True,
             fraud_rate=None, out_dir=None, settings=None, workers=1, shard_cards=None, progress=None,
             strip_sim=False, levers=None):
    """Without out_dir the feed comes back in memory (fine up to a few hundred thousand messages). With out_dir it is
    generated group by group, in parallel across `workers` processes, streamed to disk and merged into time order, so
    size is limited by disk rather than memory. In that case only the manifest comes back."""
    lv = Settings.coerce(settings if settings is not None else levers)   # `levers` is the old name for `settings`
    if fraud_rate is not None:
        lv = lv.with_overrides(fraud_rate=fraud_rate)
    start_dt = dt.datetime.fromisoformat(start)
    sizes = plan_shards(cards, days, shard_cards)
    n = len(sizes)
    total = min(cards, max(2, int(cards * lv.fraud_rate))) if lv.fraud_rate else 0
    out = Path(out_dir) if out_dir else None
    if out:
        out.mkdir(parents=True, exist_ok=True)
    jobs = [dict(seed=seed, idx=i, n_cards=sizes[i], fraud_k=total // n + (1 if i < total % n else 0),
                 merchants=merchants, days=days, start=start, incidents=incidents, settings=lv.as_dict(),
                 strip_sim=strip_sim, out_dir=str(out) if out else None) for i in range(n)]
    t0 = time.time()
    results = sorted(run_jobs(jobs, workers if out else 1, progress), key=lambda r: r["idx"])
    world = build_world(random.Random(f"{seed}-world"), merchants)[2]
    incs = default_incidents(start_dt, days, lv, world, prepared=incidents)
    counts = {}
    for r in results:
        for key, c in r["label_counts"].items():
            counts[key] = counts.get(key, 0) + c
    manifest = dict(seed=seed, cards=cards, merchants=merchants, days=days, start=start,
                    n_messages=sum(r["n_messages"] for r in results), issuers=results[0]["issuers"],
                    settings=lv.as_dict(), label_counts=dict(sorted(counts.items())), shards=n,
                    workers=min(workers, n) if out else 1, seconds=round(time.time() - t0, 1), strip_sim=strip_sim)
    if out:
        merge_sorted([out / r["part"] for r in results], out / "messages.jsonl")
        for final, key in (("answer_key.jsonl", "labels_part"), ("labels_by_trace.jsonl", "truth_part")):
            with open(out / final, "wb") as dst:
                for r in results:
                    part = out / r[key]
                    dst.write(part.read_bytes())
                    part.unlink()
        (out / "incidents.json").write_text(json.dumps(incs, indent=2), encoding="utf-8")
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return dict(messages=None, labels=None, incidents=incs, manifest=manifest)
    messages = [m for r in results for m in r["messages"]]
    messages.sort(key=lambda m: (m["ts"], m["de"]["11"]))
    return dict(messages=messages, labels=[lab for r in results for lab in r["labels"]], incidents=incs,
                truth=[row for r in results for row in r["truth"]], manifest=manifest)


DISPUTE_REASONS = {"4853": "cardholder dispute", "4834": "point-of-interaction error"}


def label_counts(labels):
    out = {}
    for lab in labels:
        key = lab.get("defect") or lab.get("pattern") or lab["kind"]
        key = f'{lab["kind"]}:{key}' if key != lab["kind"] else key
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


def add_chargebacks(feed, rng):
    """Adjudicated fraud and a small share of ordinary sales come back as chargebacks (1442) weeks later."""
    cleared = {}
    for m in feed.msgs:
        if m["mti"] == "1240":
            cleared.setdefault(m["sim"]["txn"], m)
    fraud_txns = {t for lab in feed.labels if lab["kind"] == "fraud" and lab["adjudicated"] for t in lab["txns"]}

    def charge(first, reason, txn, label):
        de = {k: first["de"][k] for k in ("2", "4", "18", "32", "42", "49") if k in first["de"]}
        de.update({"25": reason, "48": {"63": first["de"]["48"]["63"]}, "43": first["de"].get("43")})
        t = parse(first["ts"]) + dt.timedelta(days=rng.uniform(15, 60))
        feed.msg("1442", t, de, txn)
        feed.labels.append(label)

    for txn, first in cleared.items():
        if txn in fraud_txns:
            charge(first, "4837", txn, {"kind": "chargeback", "reason": "4837 no cardholder authorization",
                                        "txn": txn, "cause": "fraud"})
        elif first["de"]["3"][:2] == "00" and rng.random() < feed.lv.dispute_rate:
            code = rng.choice(list(DISPUTE_REASONS))
            charge(first, code, txn, {"kind": "chargeback", "reason": f"{code} {DISPUTE_REASONS[code]}",
                                      "txn": txn, "cause": "ordinary dispute"})


def inject_defects(feed, rng, lv):
    """Data-quality defects layered on after the feed is built. Each one is a share of the eligible messages and
    is recorded in the answer key; a clearing is damaged at most once."""
    if not any((lv.duplicate_rate, lv.missing_terminal_rate, lv.amount_mismatch_rate, lv.late_clearing_rate,
                lv.orphan_clearing_rate)):
        return
    msgs = feed.msgs
    trace_of = lambda m: (m["de"].get("48") or {}).get("63")
    requests = [m for m in msgs if m["mti"] in ("0100", "0200")]
    first_auth = {}
    for m in requests:
        first_auth.setdefault(trace_of(m), m)
    clearings = [m for m in msgs if m["mti"] == "1240"]
    reversed_traces = {trace_of(m) for m in msgs if m["mti"] == "0400"}    # orphaning these would break R01 too

    def label(defect, txn, note):
        feed.labels.append({"kind": "defect", "defect": defect, "txn": txn, "note": note})

    extra = []
    for m in requests:
        if rng.random() < lv.duplicate_rate:
            twin = copy.deepcopy(m)
            twin["ts"] = iso(parse(m["ts"]) + dt.timedelta(seconds=rng.uniform(1, 3)))
            extra.append(twin)
            label("duplicate_request", m["sim"]["txn"], "same request sent twice with the same STAN")
    for m in requests:
        if "41" in m["de"] and rng.random() < lv.missing_terminal_rate:
            del m["de"]["41"]
            label("missing_terminal_id", m["sim"]["txn"], "card-present request without DE 41")

    touched, dropped = set(), set()
    for c in clearings:
        req = first_auth.get(trace_of(c))
        if rng.random() < lv.amount_mismatch_rate:
            c["de"]["4"] = amount_str(int(c["de"]["4"]) / 100 * rng.uniform(1.05, 1.30))
            touched.add(id(c))
            label("amount_mismatch", c["sim"]["txn"], "clearing amount above the authorized amount")
        elif id(c) not in touched and req and (req["de"].get("61") or {}).get("7") != "4" \
                and rng.random() < lv.late_clearing_rate:
            c["ts"] = iso(parse(req["ts"]) + dt.timedelta(days=rng.uniform(8, 12)))
            touched.add(id(c))
            label("late_clearing", c["sim"]["txn"], "cleared more than 7 days after a final authorization")
        elif id(c) not in touched and req and trace_of(c) not in reversed_traces \
                and rng.random() < lv.orphan_clearing_rate:
            dropped.add(trace_of(c))
            touched.add(id(c))
            label("orphan_clearing", c["sim"]["txn"], "authorization removed from the feed")
    msgs.extend(extra)
    if dropped:
        msgs[:] = [m for m in msgs if not (m["mti"] in ("0100", "0110", "0200", "0210") and trace_of(m) in dropped)]


def choose_time(rng, card, day_start, mcc):
    hours = [hour_curve(COUNTRIES[card["country"]]["curve"], h) * (peak_curve(mcc.peak, h) + .08) for h in range(24)]
    h_local = pick(rng, {h: w for h, w in enumerate(hours)})
    t_local = day_start.replace(hour=0) + dt.timedelta(hours=h_local, minutes=rng.random() * 60, seconds=rng.random() * 60)
    return t_local - dt.timedelta(hours=COUNTRIES[card["country"]]["tz"])


def emit_purchase(feed, rng, card, day_start, by_key, merch_list, by_type, incs):
    mcc_code = pick(rng, {c: w for c, w in card["affinity"].items()})
    mcc = MCCS[mcc_code]
    t = choose_time(rng, card, day_start, mcc)
    pool = by_key.get((card["country"], mcc_code)) or by_key.get(("*", mcc_code))
    if not pool:
        return
    merch = rng.choice(pool)
    for inc in by_type("acquirer_outage"):
        if active(inc, t) and merch["acquirer"] == inc["acquirer"] and rng.random() < inc["share"]:
            return                                      # the merchant could not send the transaction at all
    cc = COUNTRIES[merch["country"]]
    cur = cc["cur"]
    mode = pick(rng, mcc.modes)
    if merch["ecom_only"] and mode not in ("81", "10"):
        mode = "81"
    if mode in ("05", "07"):
        if not merch["chip_ok"]:
            mode = "90"                                 # terminal has no chip reader at all
        elif rng.random() < (feed.lv.broken_chip_fallback_rate if merch["broken_chip"] else feed.lv.fallback_rate):
            mode = "80"                                 # technical fallback
    if mode == "90" and merch["chip_ok"] and mcc_code not in ("5542", "5541") and rng.random() < .6:
        mode = "05"
    for inc in by_type("chip_firmware_bug"):
        if active(inc, t) and merch["acquirer"] == inc["acquirer"] and mode in ("05", "07") and rng.random() < inc["share"]:
            mode = "80"                                 # a bad terminal update: chip and tap fall back to the stripe
    wire_mode = mode
    for inc in by_type("mapping_break"):
        if active(inc, t) and merch["acquirer"] == inc["acquirer"] and merch["mcc"] == inc["mcc"] and rng.random() < inc["share"]:
            wire_mode = inc["to_mode"]
    amt = max(1.0, rng.lognormvariate(math.log(mcc.median * card["spend"] * cc["scale"]), mcc.sigma))
    amt = float(round(amt / 20) * 20) if mcc.whole_dollars else round(amt, 2)
    amt = max(amt, 20.0) if mcc.whole_dollars else amt

    decision, latency = decide(rng, card, merch, mode, t, by_type, feed.lv.base_decline_rate)
    txn = feed.next_txn()
    if mcc_code == "5542" and rng.random() < .45:
        return fuel_two_step(feed, rng, card, merch, t, amt, cur, wire_mode, decision, latency, txn)
    if mcc.preauth:
        return preauth_flow(feed, rng, card, merch, t, amt, cur, wire_mode, decision, latency, txn)
    return sale(feed, rng, card, merch, t, amt, cur, wire_mode, decision, latency, txn)


def decide(rng, card, merch, mode, t, by_type, base_decline=.06):
    p = base_decline +(.03 if mode in ("81", "10") else 0) - (.015 if mode == "07" else 0)
    mix = dict(BASE_DECLINE_MIX)
    latency = max(40, rng.lognormvariate(math.log(180), .35))
    for inc in by_type("issuer_outage"):
        if active(inc, t) and card["issuer"] == inc["issuer"] and rng.random() < inc.get("share", .9):
            return "91", latency * 4
    for inc in by_type("network_outage"):
        if active(inc, t) and rng.random() < inc["share"]:
            return "91", latency * 4
    for inc in by_type("fraud_rule_tightened"):
        if active(inc, t) and merch["mcc"] == inc["mcc"] and mode in ("81", "10") and rng.random() < inc.get("share", .35):
            return "59", latency
    for inc in by_type("decline_spike"):
        if active(inc, t) and (not inc["issuer"] or card["issuer"] == inc["issuer"]) and rng.random() < inc["share"]:
            return "05", latency
    for inc in by_type("latency_spike"):
        if active(inc, t) and merch["country"] == inc["country"]:
            latency *= inc.get("multiplier", 6)
    if rng.random() < p:
        return pick(rng, mix), latency
    return "00", latency


def auth_pair(feed, rng, card, merch, t, amt, cur, mode, code, latency, txn, status="0", proc="00"):
    de = make_auth_de(card, merch, amt, mode, cur, status, proc)
    rrn = f"{rng.randrange(10**12):012d}"
    trace = f"{rng.randrange(36**9):09X}"[:9] + t.strftime("%m%d")
    de["37"] = rrn
    de.setdefault("48", {})["63"] = trace
    single = merch["mcc"] == "6011"      # ATM cash: single-message financial request, no later clearing
    req = feed.msg("0200" if single else "0100", t, de, txn)
    resp_de = {k: req["de"][k] for k in ("2", "3", "4", "18", "37", "42", "49", "11")}
    resp_de["39"] = code
    resp_de["48"] = {"63": trace}
    if code in ("00", "10"):
        resp_de["38"] = f"{rng.randrange(10**6):06d}"
    feed.msg("0210" if single else "0110", t + dt.timedelta(milliseconds=latency), resp_de, txn,
             latency_ms=round(latency))
    return req, trace


def clearing(feed, rng, t_auth, card, merch, amt, cur, mode, trace, txn, preauth):
    window = PREAUTH_PROTECTION_DAYS if preauth else FINAL_AUTH_PROTECTION_DAYS
    delay = rng.uniform(.2, min(window - 1, 3 if not preauth else 4))
    for inc in feed.incs:
        if inc["type"] == "clearing_delay" and active(inc, t_auth) and merch["acquirer"] == inc["acquirer"]:
            delay += inc["delay_days"]                  # the acquirer's batch ran late
    t = t_auth + dt.timedelta(days=delay)
    de = make_auth_de(card, merch, amt, mode, cur)
    de["48"] = {"63": trace}
    de.pop("37")
    feed.msg("1240", t, de, txn)


def sale(feed, rng, card, merch, t, amt, cur, mode, code, latency, txn):
    req, trace = auth_pair(feed, rng, card, merch, t, amt, cur, mode, code, latency, txn)
    if code != "00" or req["mti"] == "0200":
        return
    if rng.random() < feed.lv.reversal_timeout_rate:   # acquirer never got the response: full reversal, no clearing
        rev(feed, t + dt.timedelta(seconds=35), req, trace, 0.0, txn)
        return
    clearing(feed, rng, t, card, merch, amt, cur, mode, trace, txn, False)
    if rng.random() < feed.lv.refund_rate:              # later refund
        rt = t + dt.timedelta(days=rng.uniform(2, 10))
        _, rtrace = auth_pair(feed, rng, card, merch, rt, amt, cur, mode, "00", 150, feed.next_txn(), proc="20")


def preauth_flow(feed, rng, card, merch, t, amt, cur, mode, code, latency, txn):
    hold = round(amt * rng.choice([1.2, 1.5]) , 2)
    req, trace = auth_pair(feed, rng, card, merch, t, hold, cur, mode, code, latency, txn, status="4")
    if code != "00":
        return
    final = round(min(hold, amt * rng.uniform(.9, 1.4)), 2)
    if final < hold and rng.random() < .6:
        rev(feed, t + dt.timedelta(days=1, hours=1), req, trace, final, txn)    # release the unused part of the hold
    clearing(feed, rng, t + dt.timedelta(days=1), card, merch, final, cur, mode, trace, txn, True)


def fuel_two_step(feed, rng, card, merch, t, amt, cur, mode, code, latency, txn):
    """Common pattern: a small first authorization, then the real amount."""
    first = 1.0 if rng.random() < .5 else 50.0
    req, trace = auth_pair(feed, rng, card, merch, t, first, cur, mode, code, latency, txn, status="4")
    if code != "00":
        return
    rev(feed, t + dt.timedelta(minutes=8), req, trace, 0.0, txn)           # drop the first hold
    t2 = t + dt.timedelta(minutes=rng.uniform(3, 9))
    txn2 = feed.next_txn()
    _, trace2 = auth_pair(feed, rng, card, merch, t2, amt, cur, mode, "00", latency, txn2)
    clearing(feed, rng, t2, card, merch, amt, cur, mode, trace2, txn2, False)


def rev(feed, t, req, trace, new_amt, txn):
    de = {"2": req["de"]["2"], "3": req["de"]["3"], "4": req["de"]["4"], "18": req["de"]["18"],
          "42": req["de"]["42"], "49": req["de"]["49"], "32": req["de"]["32"],
          "37": req["de"]["37"], "95": amount_str(new_amt),
          "90": {"mti": req["mti"], "stan": req["de"]["11"], "ts": req["de"]["7"]}, "48": {"63": trace}}
    feed.msg("0400", t, de, txn)
    feed.msg("0410", t + dt.timedelta(milliseconds=90), {"37": de["37"], "39": "00", "48": {"63": trace}}, txn)


# ---- fraud with ground truth ---------------------------------------------------------------------
def emit_fraud(feed, rng, card, start_dt, days, by_key, merch_list, window=None, incident=None):
    kind = pick(rng, feed.lv.fraud_mix)
    if window:
        t0 = window[0] + (window[1] - window[0]) * rng.random()
    else:
        t0 = start_dt + dt.timedelta(days=rng.uniform(1, days - 3), hours=rng.uniform(0, 23))
    txns = []
    cur_us = "840"
    ecom = [m for m in merch_list if m["ecom_only"]]
    if kind == "card_testing":
        m = rng.choice(ecom)
        for i in range(rng.randint(6, 14)):
            t = t0 + dt.timedelta(seconds=25 * i + rng.random() * 10)
            code = "00" if rng.random() < .35 else rng.choice(["14", "05", "54"])
            txn = feed.next_txn(); txns.append(txn)
            sale(feed, rng, card, m, t, round(rng.uniform(1, 3), 2), COUNTRIES[m["country"]]["cur"], "81", code, 170, txn)
        big = feed.next_txn(); txns.append(big)
        sale(feed, rng, card, rng.choice(ecom), t0 + dt.timedelta(minutes=9), round(rng.uniform(250, 900), 2),
             cur_us, "81", "00", 190, big)
    elif kind == "skimmed_fallback":
        pool = [m for m in merch_list if m["country"] == "US" and m["mcc"] in ("5732", "5999", "5651", "5310")]
        for i in range(rng.randint(3, 6)):
            m = rng.choice(pool)
            t = t0 + dt.timedelta(hours=rng.uniform(0, 30))
            txn = feed.next_txn(); txns.append(txn)
            sale(feed, rng, card, m, t, round(rng.uniform(180, 700), 2), cur_us, "80", "00", 190, txn)
    elif kind == "geo_velocity":
        us = [m for m in merch_list if m["country"] == "US" and m["mcc"] in ("5411", "5310", "5999")]
        es = [m for m in merch_list if m["country"] == "ES" and m["mcc"] in ("5411", "5310", "5999", "5651", "5732")]
        if us and es:
            a, b = rng.choice(us), rng.choice(es)
            for m, dtm in ((a, 0), (b, 70)):
                txn = feed.next_txn(); txns.append(txn)
                sale(feed, rng, card, m, t0 + dt.timedelta(minutes=dtm), round(rng.uniform(60, 240), 2),
                     COUNTRIES[m["country"]]["cur"], "05", "00", 190, txn)
    else:
        pool = by_key.get(("US", "5732")) or by_key.get(("*", "5732"))
        if not pool:
            return
        for i in range(rng.randint(3, 6)):
            txn = feed.next_txn(); txns.append(txn)
            sale(feed, rng, card, rng.choice(pool), t0 + dt.timedelta(minutes=12 * i), round(rng.uniform(400, 1500), 2),
                 cur_us, "81", "00", 190, txn)
    label = {"kind": "fraud", "pattern": kind, "pan_last4": card["pan"][-4:], "txns": txns,
             "adjudicated": rng.random() < feed.lv.adjudicated_rate,
             "note": "labels are the answer key; a real shop only knows adjudicated cases"}
    if incident:
        label["incident"] = incident
    feed.labels.append(label)
