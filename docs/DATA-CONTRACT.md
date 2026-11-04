# Data contract: what Transaction Forge hands to a detector

Transaction Forge writes five files per run. A detector reads `messages.jsonl` and must never read the answer-key files or `incidents.json` until it is being scored.

| File | Who may read it | Content |
|---|---|---|
| `messages.jsonl` | the detector | One JSON message per line, ordered by time |
| `answer_key.jsonl` | the scorer only | One label per injected oddity, benign look-alike or chargeback |
| `labels_by_trace.jsonl` | the scorer only | The same labels flattened to one row per trace, for joining to the feed |
| `incidents.json` | the scorer only | Operational incidents with start, end and the dimension they affect |
| `manifest.json` | anyone | Seed, population sizes, the settings used, and a count of each label type |

Prebuilt sample datasets are described at the end; `tools/make_datasets.py` regenerates them.

## messages.jsonl

```json
{"mti": "0100", "ts": "2026-03-02T14:31:07Z",
 "de": {"2": "<PAN>", "3": "000000", "4": "000000004250", "11": "000123", "18": "5411",
        "22": {"1": "05", "2": "1"}, "32": "ACQ03", "37": "<RRN>", "41": "T0000042", "42": "<MID>",
        "43": {"name": "...", "city": "...", "state": "MO", "country": "USA", "zip": "63101"},
        "48": {"63": "<trace id>"}, "49": "840", "61": {"7": "0"}},
 "meta": {"latency_ms": 181}}
```

| Message | Meaning |
|---|---|
| 0100 / 0110 | Authorization request / response (response code in DE 39) |
| 0200 / 0210 | Single-message financial request / response (ATM cash) |
| 0400 / 0410 | Reversal request / response; DE 90 and DE 48 SE 63 point at the original |
| 1240 | First presentment (clearing) |
| 1442 | Chargeback; reason in DE 25 |

Amounts are in minor units, zero-padded (DE 4). Currency is the ISO numeric code (DE 49). Timestamps are UTC.

**Join on the trace ID** (`de["48"]["63"]`), as a real analyst would. One trace is one purchase: its authorization and response, any reversal, the clearing and any chargeback. The ATM, refund and each step of a pay-at-pump purchase get their own traces.

Three fields exist only because this is a simulation:

- `sim.txn`, a transaction id that groups a lifecycle (the same grouping as the trace)
- `sim_issuer` inside `de`, the issuer name for the card
- `meta.latency_ms`, response time, present on responses (kept: a real monitor has network timing too)

Generate with `--strip-sim` (the sample datasets all do) to remove `sim` and `sim_issuer`, so a detector cannot use them. Labels still map to messages through trace IDs. Without the flag the first two are present and a detector should ignore them.

## answer_key.jsonl

One line per label, tagged by `kind`. Every label that refers to transactions carries `traces` (or `trace`) next to `txns` (or `txn`):

| kind | Fields | Meaning |
|---|---|---|
| `fraud` | `pattern`, `pan_last4`, `traces`, `adjudicated`, optional `incident` | A compromised card. `pattern` is one of card_testing, skimmed_fallback, geo_velocity, electronics_ring. `adjudicated` is true when a real shop would eventually know. `incident` names the fraud_attack_wave that caused it |
| `chargeback` | `trace`, `reason`, `cause` | A 1442 on that purchase; `cause` is `fraud` (reason 4837) or `ordinary dispute` (4853, 4834) |
| `benign_lookalike` | `mid`, `what` | Looks wrong but is valid, for example a merchant with a bad chip reader. A detector should not alarm on it |
| `defect` | `defect`, `trace`, `note` | A data-quality defect: duplicate_request, missing_terminal_id, amount_mismatch, late_clearing, orphan_clearing |

`labels_by_trace.jsonl` is the same information as `{trace, kind, pattern?, adjudicated?, reason?, cause?, defect?, incident?}` rows, plus `{kind: "benign_lookalike", mid}` rows, so a scorer can join it to the feed on `trace` without unpacking lists.

Two views of the truth:

- **Omniscient**: every `fraud` label. Use it to measure how good a detector could be.
- **Adjudicated-only**: fraud labels with `adjudicated: true`, plus `chargeback` rows with cause `fraud`. This is what a real team has to train on, and it is incomplete and arrives weeks late.

Defect notes for scoring:

- `duplicate_request`: both copies carry the same trace and STAN. Score the later copy (by `ts`) as the defect.
- `orphan_clearing`: the authorization is absent, so only the clearing (and any chargeback) carries that trace.
- `missing_terminal_id`: the request lacks DE 41.
- `amount_mismatch`: the clearing's DE 4 is 5 to 30% above the authorization's.
- `late_clearing`: the clearing's timestamp is 8 to 12 days after a final authorization.

## incidents.json

A list of operational events. Each has `id`, `type`, `start`, `end`, `effect`, and the field it affects.

| type | Affects | Visible as |
|---|---|---|
| issuer_outage | one issuer | Authorizations decline with 91 for hours |
| latency_spike | one country | Response latency several times normal |
| volume_drop | one US state | Volume falls then recovers |
| fraud_rule_tightened | one MCC, e-commerce | Declines with 59 rise |
| mapping_break | one acquirer and MCC | A wrong entry mode appears after a release |

Those five are the prepared set. A run can also contain custom incidents (IDs `INC-C1`, `INC-C2`, ...) of twelve types: the five above plus network_outage, acquirer_outage, decline_spike, holiday_surge, chip_firmware_bug, clearing_delay and fraud_attack_wave. Each record has `custom: true`, its window, and the target and severity fields for its type.

## Scoring a detector

Unit of scoring:

- **Fraud and defects**: the trace. A detector outputs a set or ranking of traces.
- **Incidents**: a time window plus the affected dimension (issuer, country, state, MCC, acquirer). An alert counts as a hit if it overlaps the incident and names the right dimension.

Suggested metrics:

- precision and recall per label type, and for fraud also at a fixed review capacity (the top 1% and 5% of traces by score), because a team can only review so many
- for incidents: time to detect (alert time minus incident start), hits, and false alarms per day
- false-positive rate on `benign_lookalike` merchants and on the storm period, because "unusual but valid" is the hard part

## Sample datasets

Built by `python tools/make_datasets.py` into `out/datasets/<name>/` (git-ignored, about 665 MB together, about 2 minutes). All are stripped of simulator-only fields. `out/datasets/index.json` lists them.

| Name | Messages | Purpose |
|---|---|---|
| clean | 124,051 | No fraud, no incidents, no defects. A detector should raise no alarms. It still contains ordinary disputes and some unusually large but valid tickets, so a "ticket far above range" heuristic will fire on it by design |
| default | 119,813 | Development set: default fraud (12 cases), the five prepared incidents |
| long | 374,969 | 90 days, for per-card profiles and weekly patterns |
| noisy | 432,732 | Training and scoring: 2% of cards compromised and every defect type at 1% (about 6,000 defects) |
| industry | 399,366 | Fraud near the industry share of volume: only 3 fraud cases, so a realism check, not a measuring stick |
| drill | 127,489 | The prepared incidents plus a network outage, an acquirer outage and a holiday surge |
| holdout | 435,042 | Final test, run once: noisy settings plus four incident types the others lack, new seed. Never tune on it |

## Known limits of the data

- Fourteen days at the default size is too short for per-card profiles to mature; use the `long` set for any detector that learns a card's normal.
- The fraud rate at default is about 13 times the industry's share of volume. The `industry` set has a realistic share, with very few cases.
- Purchases per card per day (about 0.95 by default) is a modelling choice with no single industry standard; see the Event Builder's activity setting for reference points.
- Field values other than DE 22 value 07 are not yet checked against the network's interface specification.
- The generator's fraud patterns are plausible, not observed, so scores show a method works on planted problems.
