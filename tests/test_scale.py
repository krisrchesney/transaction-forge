import json
from collections import defaultdict

from txnforge import generate, validate
from txnforge.generator import plan_shards
from txnforge.settings import cards_for_target, estimate

SMALL = dict(seed=5, cards=500, merchants=120, days=3, shard_cards=200)      # 3 groups


def read(path):
    return [json.loads(line) for line in open(path, encoding="utf-8")]


def test_plan_shards_covers_every_card_evenly():
    sizes = plan_shards(1003, 14, shard_cards=300)
    assert sum(sizes) == 1003 and max(sizes) - min(sizes) <= 1 and len(sizes) == 4
    assert plan_shards(3000, 14) == [3000]                 # default runs stay one group


def test_grouped_run_is_deterministic_and_clean():
    a = generate(incidents=False, fraud_rate=0, **SMALL)
    b = generate(incidents=False, fraud_rate=0, **SMALL)
    assert a["messages"] == b["messages"] and a["manifest"]["shards"] == 3
    network = [f for f in validate(a["messages"])["findings"] if f["kind"] == "network"]
    assert network == []


def test_transaction_ids_are_unique_across_groups():
    r = generate(**SMALL)
    pans = defaultdict(set)
    for m in r["messages"]:
        pans[m["sim"]["txn"]].add(m["de"].get("2"))
    assert all(len(p - {None}) <= 1 for p in pans.values())      # a txn id never spans two cards


def test_streamed_run_writes_ordered_files_and_manifest(tmp_path):
    r = generate(out_dir=tmp_path, workers=1, **SMALL)
    assert r["messages"] is None
    msgs = read(tmp_path / "messages.jsonl")
    assert len(msgs) == r["manifest"]["n_messages"]
    keys = [(m["ts"], m["de"]["11"]) for m in msgs]
    assert [k[0] for k in keys] == sorted(k[0] for k in keys)        # time order across all groups
    labels = read(tmp_path / "answer_key.jsonl")
    assert sum(r["manifest"]["label_counts"].values()) == len(labels)
    assert not list(tmp_path.glob("*.part*"))                         # part files are cleaned up
    assert json.loads((tmp_path / "manifest.json").read_text())["shards"] == 3


def test_parallel_run_matches_single_process_run(tmp_path):
    one, two = tmp_path / "one", tmp_path / "two"
    generate(out_dir=one, workers=1, **SMALL)
    generate(out_dir=two, workers=2, **SMALL)
    for name in ("messages.jsonl", "answer_key.jsonl", "incidents.json"):
        assert (one / name).read_bytes() == (two / name).read_bytes(), name


def test_in_memory_and_streamed_runs_agree(tmp_path):
    mem = generate(**SMALL)
    generate(out_dir=tmp_path, **SMALL)
    streamed = read(tmp_path / "messages.jsonl")
    assert [m["ts"] for m in streamed] == [m["ts"] for m in mem["messages"]]
    assert sorted(m["sim"]["txn"] for m in streamed) == sorted(m["sim"]["txn"] for m in mem["messages"])


def test_mapping_break_always_lands_on_a_pay_at_pump_acquirer():
    r = generate(seed=3, cards=600, merchants=200, days=12)
    assert any(f["rule"] == "R06" for f in validate(r["messages"])["findings"])


def test_target_messages_picks_a_sensible_card_count():
    cards = cards_for_target(200_000, 14)
    assert abs(estimate(cards, 14)["messages"] - 200_000) / 200_000 < .02


def test_run_cost_matches_the_measured_ten_million_run():
    from txnforge.settings import run_cost
    cost = run_cost(10_057_020)
    assert abs(cost["seconds"] - 327) < 20 and abs(cost["disk_gb"] - 3.7) < .2 and cost["workers"] == 4
    assert run_cost(100_000)["workers"] == 1
