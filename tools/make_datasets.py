"""Build the sample datasets a detector is developed and scored on.

    python tools/make_datasets.py            # all of them
    python tools/make_datasets.py clean long # just these

Each goes to out/datasets/<name>/ (git-ignored) with simulator-only fields removed, so a detector reads
messages.jsonl and a scorer reads answer_key.jsonl, labels_by_trace.jsonl and incidents.json.
`holdout` is the final test: generate it once, never tune on it.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from txnforge import generate  # noqa: E402

TRAINING = dict(fraud_rate=.02, duplicate_rate=.01, missing_terminal_rate=.01, amount_mismatch_rate=.01,
                late_clearing_rate=.01, orphan_clearing_rate=.01)
DRILL = [
    {"type": "acquirer_outage", "start_day": 3, "start_hour": 10, "duration_hours": 4, "target": "ACQ01", "severity": .9},
    {"type": "network_outage", "start_day": 5, "start_hour": 2, "duration_hours": 1, "severity": .95},
    {"type": "holiday_surge", "start_day": 8, "start_hour": 0, "duration_hours": 24, "severity": 1.8},
]
HOLDOUT_INCIDENTS = [
    {"type": "decline_spike", "start_day": 6, "start_hour": 14, "duration_hours": 6, "severity": .3},
    {"type": "clearing_delay", "start_day": 10, "start_hour": 0, "duration_hours": 48, "target": "ACQ04", "severity": 8},
    {"type": "chip_firmware_bug", "start_day": 15, "start_hour": 9, "duration_hours": 8, "target": "ACQ02", "severity": .4},
    {"type": "fraud_attack_wave", "start_day": 20, "start_hour": 18, "duration_hours": 6, "severity": .01},
]

DATASETS = {
    "clean": dict(why="Proves a detector raises no alarms on valid data.", seed=101, cards=3000, merchants=400, days=14,
                  incidents=False, settings=dict(fraud_rate=0, broken_chip_merchants=0)),
    "default": dict(why="Development set: default fraud, the five prepared incidents, no defects.", seed=102,
                    cards=3000, merchants=400, days=14, incidents=True, settings={}),
    "long": dict(why="90 days, for per-card profiles and weekly patterns.", seed=103, cards=1500, merchants=300,
                 days=90, incidents=True, settings={}),
    "noisy": dict(why="Training and scoring: 2% fraud and every data defect at 1%.", seed=104, cards=5000,
                  merchants=600, days=30, incidents=True, settings=TRAINING),
    "industry": dict(why="Realism check: fraud near the industry share of volume (very few cases).", seed=105,
                     cards=10000, merchants=800, days=14, incidents=True,
                     settings=dict(fraud_rate=.00035, base_decline_rate=.08)),
    "drill": dict(why="Incident practice: the prepared five plus a network outage, an acquirer outage and a surge.",
                  seed=106, cards=3000, merchants=400, days=14, incidents=True, settings=dict(custom_incidents=DRILL)),
    "holdout": dict(why="Final test, run once: noisy settings plus four incidents of other types, new seed.", seed=999,
                    cards=5000, merchants=600, days=30, incidents=True,
                    settings=dict(TRAINING, custom_incidents=HOLDOUT_INCIDENTS)),
}


def main(names):
    root = Path(__file__).resolve().parents[1] / "out" / "datasets"
    index = {}
    for name in names or DATASETS:
        spec = dict(DATASETS[name])
        why = spec.pop("why")
        t = time.time()
        r = generate(out_dir=root / name, workers=4, strip_sim=True, **spec)
        m = r["manifest"]
        index[name] = dict(why=why, messages=m["n_messages"], cards=m["cards"], days=m["days"], seed=m["seed"],
                           label_counts=m["label_counts"], incidents=[i["id"] + " " + i["type"] for i in r["incidents"]],
                           folder=str(root / name))
        print(f"{name:9} {m['n_messages']:>9,} messages  {m['cards']:>6,} cards  {m['days']:>3} days  "
              f"{time.time() - t:5.1f}s  {why}", flush=True)
    (root / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    print("wrote", root)


if __name__ == "__main__":
    main(sys.argv[1:])
