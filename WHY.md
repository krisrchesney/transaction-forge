# Why this exists

*Written November 2026. All data is synthetic. This is a learning project, not affiliated with any card network.*

## In 30 seconds

Transaction Forge makes fake card transactions that behave like real ones: a purchase is approved by the card's bank, sometimes cancelled, later settled between the banks, and sometimes refunded or disputed. You choose how much fraud, how many system incidents and how many data errors to mix in. The answer key is saved in a separate file, so whatever reads the data has to find the problems without seeing it.

## Why I built it

Two reasons. I wanted to learn how a card transaction actually moves through the system, and building a generator forces you to get the rules right. I also wanted a test feed for a fraud and incident detector, because most fake payments data is random rows that don't behave like payments, and real data can't be shared.

## What you are looking at on the page

The [Event Builder](https://krisrchesney.github.io/transaction-forge/) lets you set how much fraud, how many incidents and how many data errors to include. **Generate a preview** runs the real generator in your browser and shows a small sample, with each case's answer hidden until you reveal it. Nothing is uploaded. For a full-size run, the page writes the settings and the command to run on your own computer.

## How it works

1. **Build the cardholders and merchants**, each with realistic spending habits.
2. **Play out each purchase** through its normal steps: approval, decline, refund, cancellation, settlement.
3. **Mix in the problems you asked for**, such as fraud and outages, and record each one in the answer key.
4. **Check the result against the rules.** A validator flags anything that breaks a rule and says where the rule comes from.

## Choices I made, and why

- **Build from the rules, not random columns**, so the data behaves like payments.
- **Keep the answer key in a separate file.** If the labels sit in the data, any test of a detector is meaningless.
- **Include harmless look-alikes on purpose**, so a detector that flags them is shown to be wrong.
- **Same starting number, same data.** Runs can be repeated exactly.
- **Label every rule as stated in a rulebook or as industry practice.** An anomaly you can cite is a finding. One you can't is a guess.

## What I found

- **Speed:** 1 million messages (the records a payment creates) in 46 seconds, and 10 million in about 5.5 minutes, on a 4-core PC.
- **Checking:** with all problems switched off, the validator reports no rule breaks, and every error the generator can add has a test showing the validator catches it. [Details.](README.md)

**What not to trust:** the message layout is simplified, not the real wire format, and some field values are best guesses, marked as such. The fraud patterns are my own choices, not measured. Real payment feeds are much larger and messier.

## What's not finished

- Disputes stop at the first step, and security checks, wallet tokens and currency fees aren't modelled.
- It makes the test data and checks it against rules. Finding problems without the rules is a separate project.

## Where to look

[Event Builder](https://krisrchesney.github.io/transaction-forge/) · [README](README.md) · [Data contract](docs/DATA-CONTRACT.md) · [Product requirements](docs/PRD.md)
