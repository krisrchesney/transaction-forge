"""Regional transaction lookup: one trace ID in, the whole lifecycle out, with residency policy applied.

The policy table is illustrative product logic, not legal advice and not any real network's policy.
"""
import sqlite3

COUNTRY_REGION = {"US": "NA", "GB": "EU", "ES": "EU"}
CODE_COUNTRY = {"USA": "US", "GBR": "GB", "ESP": "ES"}

# data region -> what a requester from ANOTHER region may see, by purpose
POLICY = {
    "NA": {"dispute": "mask_pan", "analytics": "mask_pan", "servicing": "mask_pan"},
    "EU": {"dispute": "mask_pan_and_location", "analytics": "deny", "servicing": "deny"},
}

SCHEMA = """
CREATE TABLE msg (
  idx INTEGER PRIMARY KEY, mti TEXT, ts TEXT, trace TEXT, txn TEXT, pan TEXT, amount_minor INTEGER,
  currency TEXT, mcc TEXT, entry_mode TEXT, acquirer TEXT, mid TEXT, resp TEXT, latency_ms INTEGER,
  country TEXT, region TEXT, reason TEXT, terminal TEXT, status TEXT, merchant TEXT, city TEXT, zip TEXT
);
CREATE INDEX msg_trace ON msg(trace);
CREATE INDEX msg_region_mti ON msg(region, mti);
"""


def build(messages):
    # read-only after load, and FastAPI serves sync endpoints from worker threads
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    rows = []
    messages = list(messages)
    # responses and reversals carry no merchant location; take it from any message on the same trace
    trace_country = {}
    for m in messages:
        loc = m["de"].get("43") or {}
        trace = (m["de"].get("48") or {}).get("63")
        if trace and loc.get("country"):
            trace_country.setdefault(trace, CODE_COUNTRY.get(loc["country"], ""))
    for i, m in enumerate(messages):
        de = m["de"]
        loc = de.get("43") or {}
        country = trace_country.get((de.get("48") or {}).get("63"), "")
        amt = de.get("4")
        rows.append((i, m["mti"], m["ts"], (de.get("48") or {}).get("63"), (m.get("sim") or {}).get("txn"), de.get("2"),
                     int(amt) if amt else None, de.get("49"), de.get("18"), (de.get("22") or {}).get("1"),
                     de.get("32"), de.get("42"), de.get("39"), (m.get("meta") or {}).get("latency_ms"),
                     country, COUNTRY_REGION.get(country, ""), de.get("25"), de.get("41"),
                     (de.get("61") or {}).get("7"), loc.get("name"), loc.get("city"), loc.get("zip")))
    conn.executemany("INSERT INTO msg VALUES (" + ",".join("?" * 22) + ")", rows)
    return conn


def status_of(events):
    mtis = [e["mti"] for e in events]
    if "1442" in mtis:
        return "disputed"
    if "1240" in mtis:
        return "cleared"
    if "0400" in mtis:
        return "reversed"
    resp = next((e["resp"] for e in events if e["mti"] in ("0110", "0210")), None)
    if resp and resp != "00":
        return "declined"
    return "authorized, not yet cleared"


def lookup(conn, trace, requester_region="NA", purpose="dispute"):
    rows = [dict(r) for r in conn.execute("SELECT * FROM msg WHERE trace = ? ORDER BY ts, idx", (trace,))]
    if not rows:
        return {"found": False, "trace": trace}
    region = rows[0]["region"]
    rule = "full" if requester_region == region else POLICY.get(region, {}).get(purpose, "deny")
    base = {"found": True, "trace": trace, "data_region": region, "requester_region": requester_region,
            "purpose": purpose, "policy": rule}
    if rule == "deny":
        return {**base, "events": [], "status": None,
                "reason": f"{region} data is not available to {requester_region} for purpose '{purpose}'"}
    redactions = []
    events = []
    for r in rows:
        e = {k: r[k] for k in ("mti", "ts", "amount_minor", "currency", "mcc", "entry_mode", "resp", "reason",
                               "acquirer", "merchant", "city", "zip", "pan", "latency_ms")}
        if rule in ("mask_pan", "mask_pan_and_location") and e["pan"]:
            e["pan"] = "*" * 12 + e["pan"][-4:]
            redactions.append("pan")
        if rule == "mask_pan_and_location":
            e["city"] = e["zip"] = None
            redactions.append("location")
        events.append({k: v for k, v in e.items() if v is not None})
    return {**base, "status": status_of(rows), "events": events, "redactions": sorted(set(redactions))}


# ---- data-quality and operations metrics, plain SQL --------------------------------------------
METRICS = {
    "approved_auths_by_region": """
        SELECT region, COUNT(*) AS n FROM msg WHERE mti IN ('0110') AND resp = '00' GROUP BY region ORDER BY region""",
    "lifecycle_completeness": """
        SELECT a.region,
               ROUND(1.0 * SUM(CASE WHEN EXISTS (SELECT 1 FROM msg c WHERE c.trace = a.trace AND c.mti IN ('1240','0400')) THEN 1 ELSE 0 END) / COUNT(*), 4) AS share_with_outcome,
               COUNT(*) AS approved_auths
        FROM msg a WHERE a.mti = '0100' AND a.trace IN (SELECT trace FROM msg WHERE mti = '0110' AND resp = '00')
          AND a.mcc <> '6011'
        GROUP BY a.region ORDER BY a.region""",
    "orphan_clearings": """
        SELECT COUNT(*) AS orphan_clearings FROM msg c
        WHERE c.mti = '1240' AND NOT EXISTS (SELECT 1 FROM msg a WHERE a.trace = c.trace AND a.mti = '0100')""",
    "dispute_rate_by_region": """
        SELECT region, SUM(mti = '1442') AS chargebacks, SUM(mti = '1240') AS cleared,
               ROUND(1.0 * SUM(mti = '1442') / NULLIF(SUM(mti = '1240'), 0), 5) AS dispute_rate
        FROM msg GROUP BY region ORDER BY region""",
    "missing_terminal_id_on_card_present": """
        SELECT region, COUNT(*) AS card_present_auths, SUM(terminal IS NULL) AS missing_terminal,
               ROUND(1.0 * SUM(terminal IS NULL) / COUNT(*), 4) AS missing_rate
        FROM msg WHERE mti IN ('0100','0200') AND entry_mode IN ('05','07','80','90') GROUP BY region""",
    "decline_rate_by_region": """
        SELECT region, ROUND(1.0 * SUM(resp <> '00') / COUNT(*), 4) AS decline_rate, COUNT(*) AS responses
        FROM msg WHERE mti IN ('0110','0210') GROUP BY region ORDER BY region""",
    "latency_ms_by_region": None,   # percentiles are computed in Python below (SQLite has no percentile function)
}


def percentile(sorted_vals, p):
    if not sorted_vals:
        return None
    return sorted_vals[min(len(sorted_vals) - 1, int(p * len(sorted_vals)))]


def quality_report(conn):
    out = {}
    for name, sql in METRICS.items():
        if sql:
            out[name] = [dict(r) for r in conn.execute(sql)]
    lat = {}
    for r in conn.execute("SELECT region, latency_ms FROM msg WHERE latency_ms IS NOT NULL ORDER BY latency_ms"):
        lat.setdefault(r["region"], []).append(r["latency_ms"])
    out["latency_ms_by_region"] = [{"region": k, "p50": percentile(v, .5), "p95": percentile(v, .95), "n": len(v)}
                                   for k, v in sorted(lat.items())]
    return out
