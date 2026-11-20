# Hinglish voice agent

> **Status: milestone M1 of M4 (text mode).**
> - There is no live demo yet.
> - The results so far come from mocked models and from one real model in text mode, and are labelled that way.
> - Every number this project publishes comes with its method, sample, date and scope.

A voice agent that changes a delivery slot over a live call in Hinglish. It never double-books and never falsely confirms the change, even when the booking service times out after saving.

*Illustrative order system · synthetic data · fictional retailer.*

## What it does

**The problem.** A customer calls to move a delivery, and the agent books the new slot. The booking service saves the change, but its reply times out. An agent that trusts the model, or the error message, can now do one of three wrong things:

1. **False failure:** it tells the customer the change failed, when it was saved.
2. **Duplicate booking:** it retries, and books twice.
3. **False confirmation:** it says "done" without checking, or confirms something the order system does not show.

**What this agent does instead.** Before it says anything about the outcome, it re-reads the order from the system of record. Deterministic code, not the language model, decides what the agent may confirm, and the confirmation itself is generated from the record. That is the guarantee outside the model.

**What to try in the demo** (planned for milestone M3):

1. Start a call about a synthetic order. While the agent is speaking, interrupt it: *"Friday nahi, Saturday chaar baje ke baad"* ("Not Friday. Saturday, after four").
2. Pick one of the Saturday slots it offers. The booking service is set to save your change and then time out.
3. Watch the order-history panel beside the call. The agent should confirm exactly one Saturday delivery, and the panel lets you check that claim against the record.

A text box will let you type instead, if you can't or would rather not use a microphone.

## Demo

Not live yet. A public demo and a captioned recording are planned for milestones M3 and M4.

## Results

**So far: milestone M1, text mode, mocked models only.**
- A mocked model is a rule-based test double.
- `mock-reckless` misbehaves on purpose. It says "done" too early, retries blindly, says "it failed" when it didn't, names the wrong slot, and sends two writes at once.
- These numbers show what the safety layers stop. They say nothing about how good a real model is.

**Sample:** corpus v0 (20 synthetic scenarios), faults on, each scenario run 3 times, so 60 calls per row. Checked 20 Nov 2026. [Method](docs/METHOD.md#false-confirmations).

| Mock model | Safety layers | Calls passed | Duplicate writes | False confirmations | False failures |
|---|---|---|---|---|---|
| `mock-reckless` | on | 60 / 60 | 0 | 0 | 0 |
| `mock-reckless` | off | 3 / 60 | 13 | 92 | 58 |
| `mock-competent` | off | 57 / 60 | 3 | 0 | 0 |

- **What 0 means here:** with the layers on, 0 in 60 calls puts each rate at 5% or less, at 95% confidence (rule of three), for this mock.
- **The well-behaved mock still double-books without the layers.** A write that lands late turns its good-faith retry into a second booking. ([Why](docs/METHOD.md#duplicate-bookings).)
- **The claim detector** catches 72% of outcome claims in held-out paraphrases. The design doesn't rely on it at the moment of a change. ([Details and misses](docs/METHOD.md#the-claim-detector).)
- **The raw results** are in [results/text/v0/](results/text/v0/).
**A real model.** `gemini-3.5-flash-lite` was run on the same 20 scenarios, once each, with every safety layer on, on 20 Nov 2026. This was the audit run on the final code.
- **19 of 20 calls passed.**
- There were 0 duplicate bookings, 0 false confirmations and 0 false failures. From 20 calls, that puts each rate at 15% or less, at 95% confidence.
- The one failure is a gap in the grader, not in the agent, and it is published as measured.
- The details, and two earlier runs that exposed gaps in the test harness, are in [METHOD.md](docs/METHOD.md#real-model).

**Coming next:** voice latency and cost per call, in M2.

## Architecture

```text
Browser client ──WebSocket audio──▶ Voice server (Pipecat)
  mic, live captions,                 VAD + turn detection (Silero), interruptions
  order-history panel,                STT: Sarvam (code-mixed)
  "type instead" box                  LLM with tool calling
       ▲                              TTS: Sarvam Bulbul
       │ events                            │ tool calls
       └──────────── Order service (FastAPI + SQLite) ◀─┘
                     orders, slots, append-only event log
                     idempotency keys, fault injection
                     confirmation gate (the guarantee outside the model)
```

- **Order service:** the system of record.
  - Every reschedule carries an idempotency key. Code derives it, never the model, so a retry cannot write twice.
  - Faults can be switched on for each call: a timeout after the commit, an error before the commit, a slow response, and a slot taken by someone else during the call.
- **Confirmation gate:** each sentence the agent is about to say is checked before it is spoken.
  - The agent states an outcome only from a fresh read of the record.
  - After a timeout, it re-reads the record before it says anything about the result.
- **Evaluation:** a versioned corpus of scripted calls.
  - It runs in text mode and in audio mode.
  - It is graded deterministically, on the final state of the order database.

Design decisions, and the versions and prices they depend on, are in [docs/DECISIONS.md](docs/DECISIONS.md).

## Run it locally

You need Python 3.12 and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run pytest                                   # unit tests, plus the M1 smoke run
uv run python -m evals.harness.run --llm mock-reckless --safety full     # the guarantee
uv run python -m evals.harness.run --llm mock-reckless --safety baseline # without it
uv run python -m evals.harness.run --show interrupt-demo                  # one transcript
uv run python -m evals.bank                     # the claim detector on the sentence bank
```

None of these needs an API key or the network.

**Against a real model.** Set `GEMINI_API_KEY` as an environment variable first, using a key made for this project only. This can cost money unless your key is on the free tier.
- No request is sent once the cost at list prices reaches `--max-usd`. The last request can carry the total past the cap by its own cost, which is a few thousand tokens.
- A run cut short, or one with provider errors, is marked incomplete in its results file.
- Only synthetic data is sent.

```sh
uv run python -m evals.harness.run --llm gemini --model gemini-3.8-flash --k 1 --max-usd 1.0 \
  --out results/text/v0/<date>_gemini-3.8-flash_full_faults-on_k1.json
```

The same run is also available as a manual GitHub workflow, "Live eval". It never runs on pull requests.

The voice pipeline arrives in milestone M2.

## Limitations and failures

Nothing has been measured yet. This section will list the measured failures and the known limits of the design, including the cases the gate misses.

## Privacy and costs

- No audio is stored. Transcripts are held in memory only for the length of a call, and only anonymous aggregate metrics are logged. See [docs/privacy.md](docs/privacy.md).
- All order data is synthetic.
- The cost per call will be measured in milestone M2, and published here with its method.

## Licence

[Apache-2.0](LICENSE). Copyright 2026 Mubin Attar.
