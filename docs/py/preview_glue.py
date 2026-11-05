"""Runs inside the browser (Pyodide) for the Event Builder's preview, and in the tests.

preview() generates a small feed with the real generator, hides the simulator-only fields, masks card numbers and picks
a mix of ordinary and labelled cases to show. It returns one JSON string for the page.
"""
import datetime as dt
import json
import random
import time
from collections import defaultdict

from txnforge import generate

MAX_CARDS = 600
MAX_DAYS = 7
MIN_DAYS = 3
MAX_MERCHANTS = 300
ORDINARY_CASES = 8
PER_LABEL_TYPE = 2
MAX_LABELLED_CASES = 14
MAX_TRACES_PER_CASE = 6


def mask(pan):
    return pan[:6] + "*" * (len(pan) - 10) + pan[-4:] if pan and len(pan) > 10 else pan


def trace_of(m):
    return (m["de"].get("48") or {}).get("63")


def label_type(row):
    return row["kind"] + (":" + str(row.get("pattern") or row.get("defect") or "") if row["kind"] != "chargeback" else "")


def preview(levers_json, seed, cards, merchants, days, start):
    t0 = time.time()
    cards = max(50, min(int(cards), MAX_CARDS))
    days = max(MIN_DAYS, min(int(days), MAX_DAYS))
    merchants = max(100, min(int(merchants), MAX_MERCHANTS))
    r = generate(seed=int(seed), cards=cards, merchants=merchants, days=days, start=start,
                 levers=json.loads(levers_json), strip_sim=True)
    msgs = r["messages"]
    end = (dt.datetime.fromisoformat(start) + dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    incidents = [i for i in r["incidents"] if i["start"] < end]   # incidents scheduled after the window never happen
    for m in msgs:
        if m["de"].get("2"):
            m["de"]["2"] = mask(m["de"]["2"])
    by_trace = defaultdict(list)
    for m in msgs:
        by_trace[trace_of(m)].append(m)
    truth = [row for row in r["truth"] if row.get("trace")]
    merchant_notes = [row for row in r["truth"] if not row.get("trace")]
    labelled_traces = {row["trace"] for row in truth}

    # labelled cases: a fraud case is every labelled purchase of one card and pattern; other labels are one purchase each
    cases = defaultdict(list)
    for row in truth:
        msgs_here = by_trace.get(row["trace"])
        if not msgs_here:
            continue
        if row["kind"] == "fraud":
            key = ("fraud", row.get("pattern"), msgs_here[0]["de"].get("2"))
        else:
            key = (row["kind"], row["trace"])
        cases[key].append(row)
    rng = random.Random(f"{seed}-preview")
    keys = sorted(cases, key=str)
    rng.shuffle(keys)
    taken, per_type = [], defaultdict(int)
    for key in keys:
        t = label_type(cases[key][0])
        if per_type[t] < PER_LABEL_TYPE and len(taken) < MAX_LABELLED_CASES:
            per_type[t] += 1
            taken.append(key)

    shown = []
    for key in taken:
        rows = cases[key]
        traces = []
        for row in rows:
            if row["trace"] not in traces:
                traces.append(row["trace"])
        traces = traces[:MAX_TRACES_PER_CASE]
        shown.append(dict(labels=rows, traces=traces))

    ordinary = [t for t, ms in by_trace.items() if t not in labelled_traces and ms[0]["mti"] in ("0100", "0200")]
    ordinary.sort()
    rng.shuffle(ordinary)
    for t in ordinary[:ORDINARY_CASES]:
        shown.append(dict(labels=[], traces=[t]))

    out_cases = []
    for c in shown:
        rows = sorted((m for t in c["traces"] for m in by_trace[t]), key=lambda m: (m["ts"], m["de"]["11"]))
        out_cases.append(dict(labels=c["labels"], messages=rows))
    out_cases.sort(key=lambda c: c["messages"][0]["ts"])
    for i, c in enumerate(out_cases, 1):
        c["n"] = i

    return json.dumps(dict(
        cases=out_cases,
        summary=dict(messages=len(msgs), purchases=len(by_trace), cards=cards, days=days, merchants=merchants,
                     seconds=round(time.time() - t0, 1), label_counts=r["manifest"]["label_counts"],
                     incidents=incidents, merchant_notes=len(merchant_notes)),
        feed=[json.dumps(m) for m in msgs],
        key=[json.dumps(row) for row in r["truth"]],
    ), separators=(",", ":"))
