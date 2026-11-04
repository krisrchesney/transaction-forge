import argparse
import json
import os
import sys
from pathlib import Path

from .generator import generate, plan_shards
from .levers import Levers, cards_for_target, estimate, parse_assignment, run_cost
from .validator import explain, read_jsonl, validate


def build_levers(path, assignments):
    data = json.loads(Path(path).read_text(encoding="utf-8")) if path else {}
    for text in assignments:
        key, value = parse_assignment(text)
        data[key] = value
    return Levers.coerce(data)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="txnforge", description="Transaction Forge: synthetic card transactions")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="write a feed, answer key and incident list")
    g.add_argument("--out", default="out/demo")
    g.add_argument("--seed", type=int, default=7)
    g.add_argument("--cards", type=int, default=3000)
    g.add_argument("--target-messages", type=int, metavar="N",
                   help="choose the number of cards to give about N messages (overrides --cards)")
    g.add_argument("--merchants", type=int, default=400)
    g.add_argument("--days", type=int, default=14)
    g.add_argument("--start", default="2026-03-02")
    g.add_argument("--no-incidents", action="store_true")
    g.add_argument("--levers", help="JSON file of lever values")
    g.add_argument("--set", dest="assignments", action="append", default=[], metavar="LEVER=VALUE",
                   help="override one lever, e.g. --set fraud_rate=0.02 (repeatable; see 'txnforge levers')")
    g.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1),
                   help="worker processes (default: up to 4); big runs are split into card groups run in parallel")
    g.add_argument("--shard-cards", type=int, help="cards per group (default: sized for about 250,000 messages)")
    g.add_argument("--strip-sim", action="store_true",
                   help="remove the simulator-only fields (sim, sim_issuer) so a detector cannot use them; "
                        "labels still map to messages through trace IDs")
    sub.add_parser("levers", help="list every lever with its default")
    v = sub.add_parser("validate", help="check a messages.jsonl against the rule pack")
    v.add_argument("path")
    v.add_argument("--json", dest="json_out", help="write all findings to this file")
    a = ap.parse_args(argv)

    if a.cmd == "levers":
        for name, value in Levers().as_dict().items():
            print(f"{name:28} {value}")
    elif a.cmd == "generate":
        lv = build_levers(a.levers, a.assignments)
        cards = cards_for_target(a.target_messages, a.days, lv) if a.target_messages else a.cards
        groups = len(plan_shards(cards, a.days, a.shard_cards))
        msgs = estimate(cards, a.days, lv)["messages"]
        cost = run_cost(msgs, a.workers)
        print(f"{cards:,} cards x {a.days} days: about {msgs:,.0f} messages in {groups} group(s) on "
              f"{min(a.workers, groups)} worker(s); expect about {cost['seconds'] / 60:.1f} min, "
              f"{cost['disk_gb']:.1f} GB on disk, {cost['ram_gb']:.1f} GB of memory", file=sys.stderr)

        def progress(done, total, r):
            print(f"  group {done}/{total} done ({r['n_messages']:,} messages)", file=sys.stderr, flush=True)

        r = generate(seed=a.seed, cards=cards, merchants=a.merchants, days=a.days, start=a.start,
                     incidents=not a.no_incidents, out_dir=a.out, levers=lv, workers=a.workers,
                     shard_cards=a.shard_cards, progress=progress, strip_sim=a.strip_sim)
        print(json.dumps(r["manifest"], indent=2))
        print(f"wrote {Path(a.out).resolve()}")
    else:
        report = validate(read_jsonl(a.path))
        print(explain(report))
        if a.json_out:
            Path(a.json_out).write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
