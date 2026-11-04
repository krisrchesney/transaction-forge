"""Sandbox API: ask for a scenario, get the messages; post any feed, get rule findings with citations."""
from fastapi import FastAPI, HTTPException

from .rules import RULES
from .scenarios import SCENARIOS, run
from .validator import validate

app = FastAPI(title="txnforge sandbox", version="0.1.0",
              description="Synthetic card transaction lifecycles and a rule-citing validator. Not affiliated with any card network.")

MAX_FINDINGS = 500


@app.get("/scenarios")
def scenarios():
    return [{"name": k, "description": v} for k, v in SCENARIOS.items()]


@app.get("/rules")
def rules():
    return [r.__dict__ for r in RULES.values()]


@app.post("/scenarios/{name}")
def scenario(name: str, seed: int = 1, amount: float | None = None):
    try:
        msgs = run(name, seed=seed, amount=amount)
    except KeyError:
        raise HTTPException(404, f"unknown scenario {name}; see GET /scenarios")
    return {"scenario": name, "messages": msgs}


_conn = None


def get_conn():
    """Lookup runs on a small generated feed unless a connection is injected (tests do)."""
    global _conn
    if _conn is None:
        from .generator import generate
        from .lookup import build
        _conn = build(generate(seed=7, cards=400, merchants=150, days=10)["messages"])
    return _conn


@app.get("/lookup/{trace}")
def lookup_trace(trace: str, requester_region: str = "NA", purpose: str = "dispute"):
    from .lookup import lookup
    if requester_region not in ("NA", "EU") or purpose not in ("dispute", "analytics", "servicing"):
        raise HTTPException(422, "requester_region is NA or EU; purpose is dispute, analytics or servicing")
    result = lookup(get_conn(), trace, requester_region, purpose)
    if not result["found"]:
        raise HTTPException(404, f"no transaction with trace {trace}")
    return result


@app.get("/quality")
def quality():
    from .lookup import quality_report
    return quality_report(get_conn())


@app.post("/validate")
def check(messages: list[dict]):
    report = validate(messages)
    report["findings"] = report["findings"][:MAX_FINDINGS]
    return report
