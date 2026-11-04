# PRD: Transaction sandbox with a rule-citing validator

Status: draft v0.1. Owner: product. This is a self-directed product exercise; the persona details and numbers below are hypotheses to test, not measurements.

## 1. Problem

An engineer integrating with a payments network spends weeks learning, by trial and error, what a *correct* conversation looks like: which message follows which, which fields must agree, how long you have to clear, what a reversal must reference. Documentation describes rules one at a time; real conversations combine them. Test data in most sandboxes is a few happy-path examples, so the first real-world oddity (a pump pre-authorization, a timeout reversal, a chip fallback) is discovered in production.

On the receiving side, analysts and data teams cannot tell "unusual but valid" from "mapped wrong" without the rules in hand, and they rarely have labeled data to practice on.

## 2. Users and jobs

| User | Job to be done | Pain today |
|---|---|---|
| Integration engineer (bank, processor, fintech) | "Show me a valid lifecycle for my case, and tell me exactly what rule my message breaks." | Learning by rejection; unclear error causes |
| Product or solutions engineer | "Reproduce a customer's bad feed and point at the rule." | Hand-reading specs and logs |
| Fraud/ops analyst, data scientist | "Give me realistic, labeled data to test detection and monitoring." | Synthetic data that is random, or real data that cannot be shared |
| Technical product manager | "Know which rules cause the most rework so I can prioritise docs and tooling." | No telemetry on conformance |

## 3. Product

1. **Scenario API**: named situations return a complete valid lifecycle (`fuel_two_step`, `hotel_preauth`, `reversal_timeout`, `chip_fallback`, ...).
2. **Validator API**: post any message list, receive findings with rule id, kind (network or heuristic), severity and a section citation.
3. **Feed generator**: bulk data with injected, labeled fraud and incidents, answer key kept separate.
4. **Rule pack as data**: every rule has an id, a source, and tests; adding a rule is a data change plus a test.

Out of scope for v0: real network connectivity, real card data, wire-format encoding, certification claims.

## 4. Principles

- **Cite or label it a hypothesis.** A finding without a source is not shown as a rule violation.
- **Separate the answer key.** Consumers of the feed must not be able to see the labels.
- **Valid by construction, broken on purpose.** The generator is validated by the validator on a clean baseline; every injected defect has a test that proves the validator catches it.
- **Plain output.** Findings say what is wrong in a sentence an integrator can act on.

## 5. Success metrics (hypotheses)

| Metric | Target | Why |
|---|---|---|
| Time to first valid authorization for a new integrator | under 10 minutes | The sandbox's job is to shorten the first mile |
| Share of injected defects the validator catches | 100% of network-rule defects, tracked per release | Trust |
| Scenario coverage vs. support-ticket themes | each of the top 10 themes has a scenario | Keeps the sandbox relevant |
| Repeat findings per integrator after a first fix | trending down | Findings are understandable |
| False-positive rate on a clean baseline | 0 network-rule findings | Credibility |

### 5b. Regional lookup service (v0.2)

**Problem.** Disputes, servicing and analytics teams in different markets need the same transaction facts, but what each may see depends on where the data lives and why they ask. Legacy lookups usually return everything or nothing.

**Capability.** One trace ID returns the full lifecycle; a policy table decides masking or denial by data region, requester region and purpose. Every response states the policy applied.

**Success measures.** Lookup p95 latency by region; lifecycle completeness (share of approved authorizations with a clearing or reversal, excluding those still inside the clearing window); orphan clearings (target zero); dispute rate by region; share of lookups denied by policy (a high share means the policy or the consumers' requests are wrong); missing mandatory fields on card-present authorizations.

**Migration view.** Policy lives in data, not code, so a new region is a table row plus tests. The same lookup contract can sit in front of a legacy store during migration, so consumers move once and the back end changes later.

## 6. Risks and open questions

- **Spec drift.** Rules change; the pack needs a version and a change log. Open: who owns the rule-to-source mapping?
- **Fidelity vs. safety.** More realistic data raises the chance of being mistaken for real data. Mitigation: test-only BIN ranges and an explicit marker in every record.
- **Regional variation.** Many rules differ by region (the manual has regional sections). Open: model region as a parameter of every rule from v1.
- **Heuristic creep.** Operating knowledge is useful but can harden into "rules". Keep the kind label visible everywhere.
- **Licensing.** Specifications are proprietary; the pack stores paraphrases and section references only.

## 7. Roadmap

| Phase | Content |
|---|---|
| v0.1 (this repo) | Generator, 10 rules, 7 scenarios, sandbox API, tests |
| v0.2 (this repo) | Chargeback lifecycle, regional lookup with residency policy, SQL quality metrics |
| v0.2.1 | Real chargeback windows per reason code, region parameter on every rule |
| v0.3 | Multi-clearing and partial shipments, token and credential-on-file flows |
| v0.4 | Conformance report per integrator (history, repeat findings, time to green) |
| v0.5 | Rule-pack versioning with diff reports ("what changed for me?") |
