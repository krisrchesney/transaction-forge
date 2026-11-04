# Transaction Forge (`txnforge`)

Synthetic card transactions built **from the rules**, with dials for how many oddities the data contains. The job of this project is to *make* the dataset: a realistic, labeled feed you can turn up or down, with the answer key kept separate. Finding the oddities is a different project; this one only supplies its test data. (A rule validator ships alongside because it proves the clean feed is valid, and a lookup layer remains from v0.2.)

Most synthetic payments data is random rows with plausible columns. This one models what actually happens to a card transaction: the message types, the lifecycle (authorize, reverse, clear, refund), the merchant category, how the card was read, time-of-day by country, per-cardholder spending, and then injects *labeled* fraud and *operational incidents* on top. The answer key is written to a separate file, so whatever consumes the feed (a pipeline, an analyst, an AI agent) never sees it.

It is a learning and design project. It is not affiliated with any card network and is not wire-compatible with any network format. Its rule citations paraphrase a published card-network processing rulebook (June 2025 edition); section numbers are kept so a reader with that document can check them. It is no substitute for a network's own specifications.

**Try the Event Builder in your browser:** https://krisrchesney.github.io/transaction-forge/ (it runs entirely in the page; nothing is uploaded). It writes your settings and the command to run, and shows a hand-picked sample of ten purchases with their answer-key labels, so you can see what the data looks like before installing anything.

## Try it

```bash
pip install -e ".[dev]"
txnforge generate --out out/demo            # ~120,000 messages, 14 days, 3,000 cards
txnforge validate out/demo/messages.jsonl   # findings, each with its rule and citation
pytest                                      # 88 tests
uvicorn txnforge.api:app                    # sandbox API on http://127.0.0.1:8000/docs
txnforge levers                             # every dial and its default
```

Output of one run (seed 7):

```
119,963 messages checked, 375 findings
  R04 [network/warning]   Technical fallback should stay at or below 5% ...      (Rulebook 4.1 variations)
  R06 [heuristic/error]   Some MCCs cannot be e-commerce ...                     (industry practice)
  R08 [heuristic/warning] Ticket size far above the usual range for the MCC ...
  R06 by acquirer: ACQ02=307
```

That last line is the point: one acquirer started sending pay-at-pump transactions with an e-commerce entry mode after a "release" on day 9. Nothing else in the feed is wrong, and the validator isolates it to one acquirer from the data alone.

## Levers: choose how many oddities

Every dial is a share or a count, set with `--set name=value` (repeatable), a JSON file (`--levers file.json`) or `levers={...}` in Python. Out-of-range values are rejected. The manifest records the levers used and `label_counts` shows what was actually injected.

```bash
txnforge generate --out out/noisy --set fraud_rate=0.02 --set fraud_mix=card_testing:3,geo_velocity:1 \
    --set duplicate_rate=0.01 --set missing_terminal_rate=0.02 --set late_clearing_rate=0.005
```

| Group | Lever (default) | Effect |
|---|---|---|
| Fraud | `fraud_rate` (.004) | Share of cards compromised |
| | `fraud_mix` (equal) | Relative weight of card_testing, skimmed_fallback, geo_velocity, electronics_ring |
| | `adjudicated_rate` (.8) | Share of fraud cases that return as 4837 chargebacks |
| Valid but odd | `base_decline_rate` (.06), `reversal_timeout_rate` (.01), `refund_rate` (.02), `dispute_rate` (.004) | Normal-looking noise a detector must not over-flag |
| | `fallback_rate` (.01), `broken_chip_merchants` (3), `broken_chip_fallback_rate` (.35) | Chip fallback, including bad readers that are benign look-alikes |
| Incidents | `incidents` (all five), `mapping_break_share` (.7) | Which of the five prepared operational incidents appear, and how much of the affected acquirer's traffic is wrong |
| | `custom_incidents` (none) | Your own incidents, placed anywhere: any of 12 types with a target, start day and hour, duration and severity (see below) |
| Data defects (all 0 by default) | `duplicate_rate` | Authorization requests sent twice |
| | `missing_terminal_rate` | Card-present requests without the terminal ID |
| | `amount_mismatch_rate` | Clearings larger than the authorization |
| | `late_clearing_rate` | Clearings after the 7-day window |
| | `orphan_clearing_rate` | Clearings whose authorization is absent |

## Scale

Cards are independent of each other, so a run is cut into groups of cards (about 250,000 messages each). Each group is generated, written to disk and freed; groups run in parallel and are merged into one time-ordered file, so memory stays small and the limit is disk.

```bash
txnforge generate --out out/big --target-messages 10000000 --days 30 --workers 4
```

Measured on a 4-core PC with 48 GB of RAM:

| Messages | Cards x days | Time | Disk | Peak memory |
|---|---|---|---|---|
| 1.0 million | 11,646 x 30 | 46 s | 385 MB | not measured |
| 10.06 million | 116,457 x 30 | 5.5 min | 3.7 GB | 1.7 GB (4 workers) |

The 10-million feed was checked afterwards: line count equals the manifest, timestamps are in order, and every message type is present. Real networks are far larger (one global network reported 159.4 billion switched transactions for 2024, about 437 million a day), so this is sized to exercise detection and analysis methods, not to match production volume. The same output for a seed is identical across worker counts. Without `--out`, `generate()` returns the feed in memory, which suits runs up to a few hundred thousand messages.

**Event builder.** Open `docs/index.html` in a browser, or use the hosted page linked above (no install, works offline) to set every lever with sliders. Each lever shows its default, a green band for a typical range where one exists, and a tag saying where the number comes from: the card network's published rules, a secondary source, or a modelling choice. It writes the `levers.json`, the command, or the Python call. Presets: default, clean baseline, industry-like, detector training, stress test. A test keeps the page's defaults in step with `levers.py`. Industry-reference figures are directional and dated 2026-11-04; check primary sources before quoting them.

**For a detector.** `docs/DATA-CONTRACT.md` describes the files, the answer-key labels and how to score a detector against them. `python tools/make_datasets.py` builds seven ready-made datasets (clean, default, long, noisy, industry, drill, holdout) in `out/datasets/`, with `--strip-sim` applied so the simulator-only fields are gone and labels map to messages by trace ID. The Event Builder also sets purchases per card (the `card_activity` multiplier, with reference points) and has a checkbox for stripping.

Defects are added after the base feed is built, using their own random stream, so changing a defect rate never reshuffles the underlying transactions. Each one is listed in `answer_key.jsonl` with its transaction, so a detector can be scored on precision and recall.

## Regional lookup, data residency and metrics (v0.2)

A second layer treats the feed the way a servicing or disputes team would: one trace ID in, the whole lifecycle out.

```bash
GET /lookup/{trace}?requester_region=NA&purpose=dispute     # events in order, status, redactions applied
GET /quality                                                # SQL metrics by region
```

- **Lifecycle view**: authorization, response, reversal, clearing and chargeback joined on the trace ID, with a status (declined, reversed, authorized not yet cleared, cleared, disputed).
- **Residency policy as data** (`lookup.POLICY`, illustrative, not legal advice): a requester from the data's own region sees everything; from another region the card number is masked; for EU data, merchant city and ZIP are also removed for dispute lookups, and analytics or servicing lookups are denied with a stated reason.
- **Chargebacks**: adjudicated fraud comes back as 1442 with reason 4837 after 15-60 days, and about 0.4% of ordinary sales are disputed (4853, 4834). The validator checks every chargeback points at a cleared sale (R12).
- **Metrics in plain SQL** (`lookup.METRICS`): lifecycle completeness (about 98% on the demo feed; the rest are authorizations not cleared yet when the window ends), orphan clearings, dispute rate by region, missing terminal ID on card-present, decline rate by region, latency p50/p95 by region.

## What is in the feed

| Message | Meaning |
|---|---|
| 0100 / 0110 | Authorization request / response |
| 0200 / 0210 | Single-message financial request / response (ATM cash) |
| 0400 / 0410 | Reversal request / response, referencing the original by trace ID and original data elements |
| 1240 | First presentment (clearing) |
| 1442 | Chargeback, reason in DE 25, referencing the cleared sale by trace ID |

Data elements used: 2 PAN, 3 processing code, 4 amount, 18 MCC, 22 POS entry mode, 32 acquirer, 37 retrieval reference, 39 response code, 41/42 terminal and merchant IDs, 43 merchant name and location, 48 (subelement 63 trace ID), 49 currency, 61 (subfield 7 transaction status), 90 original data elements, 95 replacement amount.

Lifecycles it knows: plain purchase, decline, refund, acquirer timeout then full reversal, hotel preauthorization with partial reversal and later clearing, pay-at-pump two-step authorization, ATM single message, chip fallback.

Realism levers: 21 merchant categories each with its own ticket-size distribution and entry-mode mix; fuel and ATM can never be e-commerce; ATM amounts are whole dollars; per-card spending level and activity rate; local-time peaks that differ by country (US evening, Spain late with a midday lull, UK evening); three currencies; a few merchants with genuinely broken chip readers (high fallback that is valid, labeled as a look-alike, not fraud).

## What gets injected (all in `answer_key.jsonl` and `incidents.json`)

| Kind | What it looks like in the feed |
|---|---|
| Card testing | A burst of small e-commerce authorizations, many declined, then one large one |
| Skimmed card | Chip fallback (entry mode 80) at retail and electronics, higher tickets |
| Geo velocity | The same card in the US and in Spain 70 minutes apart |
| Electronics ring | Large e-commerce electronics purchases in quick succession |
| Issuer outage | One issuer's authorizations decline with 91 for three hours |
| Latency spike | UK response latency about 6x for two hours |
| Storm | One US state's volume drops to 15%, then recovers over a day |
| Fraud rule change | Electronics e-commerce declines with 59 jump for two days |
| Mapping break | One acquirer sends the wrong entry mode for one merchant category |

### Your own incidents

The five prepared incidents have fixed times. `custom_incidents` places any of 12 types wherever you like, each as `{type, start_day, start_hour, duration_hours, target, severity}`; they apply even with the prepared ones off, and are written to `incidents.json` as `INC-C1`, `INC-C2`, and so on. The Event Builder has an editor for them and an `Incident drill` preset.

| Type | Target | Severity means | Visible as |
|---|---|---|---|
| issuer_outage | issuer | share declined | Declines with 91 for one issuer |
| network_outage | none | share declined | Declines with 91 everywhere |
| acquirer_outage | acquirer | share of purchases lost | One acquirer's volume vanishes |
| latency_spike | country | response-time multiplier | Slow approvals in one country |
| volume_drop | US state | share of volume that remains | A storm: volume falls, then recovers |
| holiday_surge | country or all | volume multiplier | Volume rises |
| fraud_rule_tightened | MCC | share of online purchases declined | Declines with 59 |
| decline_spike | issuer or all | extra share declined | Declines with 05 |
| mapping_break | acquirer | share with the wrong entry mode | Pay-at-pump purchases look online |
| chip_firmware_bug | acquirer | share falling back to the stripe | Fallback entry mode surges |
| clearing_delay | acquirer | extra days before clearing | Clearings arrive late, past the 7-day window |
| fraud_attack_wave | none | share of cards attacked | A burst of labeled fraud cases |

An empty target uses the type's default (for example ISS07, ACQ01, FL, GB) and `mapping_break` picks an acquirer that runs pumps. Surge and storm effects are applied per day, in proportion to how much of the day the incident covers, so a short incident has a small effect on daily volume.

## The rule pack

Every rule is tagged **network** (stated in the Transaction Processing Rules, with the section) or **heuristic** (industry operating knowledge, not a numbered rule). That distinction matters: an anomaly you can cite is a finding, one you cannot is a hypothesis.

| Id | Kind | Rule (short) |
|---|---|---|
| R01 | network | A reversal must point at a known original authorization |
| R02 | network | A reversal's replacement amount cannot exceed the approved amount |
| R03 | network | Clearing within 7 days of approval, 30 for a preauthorization |
| R04 | network | Technical fallback at or below 5% of chip transactions per location per month |
| R05 | network | Card number in the network's BIN ranges and Luhn-valid |
| R06 | heuristic | Some MCCs (pay-at-pump fuel, ATM cash) cannot be e-commerce |
| R07 | heuristic | ATM amounts are whole dollars |
| R08 | heuristic | Ticket size extremely high for the MCC |
| R09 | network | Clearing amount should not exceed the authorized amount (regional exceptions) |
| R11 | heuristic | Every clearing record traces back to an authorization |

## Sandbox API

`GET /scenarios`, `POST /scenarios/{name}`, `POST /validate`, `GET /rules`. One call returns a complete, valid lifecycle for a named situation, which is the fastest way for an integrator to see what a correct conversation looks like.

```bash
curl -X POST "http://127.0.0.1:8000/scenarios/fuel_two_step?seed=3"
```

## Known limits

- Field values for DE 22 other than 07 come from general knowledge of the network's interface specification, which was not available here: check them before relying on them.
- The message layout is JSON with numbered data elements, not a bitmap-encoded ISO 8583 message.
- The fuel pattern (small first authorization, then the real amount) is an observed industry behavior, not something a specific Rulebook section prescribes; the rules around preauthorization timing are.
- Chargebacks have no representment or arbitration stages and their timing is not checked against the real windows; no 3-D Secure, no tokens, no multi-currency conversion fees.
- Time zones use fixed offsets, no daylight saving.

## Layout

```
src/txnforge/rules.py        rules and catalogs as data, each with a source
src/txnforge/generator.py    population, lifecycles, fraud, incidents
src/txnforge/validator.py    rule checks with citations
src/txnforge/scenarios.py    single-transaction sandbox scenarios
src/txnforge/lookup.py       SQLite lookup, residency policy, SQL metrics
src/txnforge/api.py          FastAPI sandbox
docs/PRD.md                  product requirements for the sandbox
```

## Safety and licence

- Card numbers in the generated data are random, Luhn-valid numbers in network-style ranges. They are not issued to anyone by construction, but a random number can match a real one by chance. Use them only for testing, and never submit them to a real payment system.
- Reference figures in the Event Builder come from public secondary sources, are directional, and were gathered on 2026-11-04; check primary sources before quoting them.
- Released under the MIT licence (see `LICENSE`).
