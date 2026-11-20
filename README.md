# Hinglish voice agent

> **Status: early build (milestone M0 of M4).** There is no demo and there are no results yet. Every number this project publishes will come with its method, sample, date and scope.

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

None yet. This section will hold one table with these measures:

- task success (pass@1 and pass^k);
- duplicate bookings, false confirmations and false failures;
- barge-in stop latency and voice-to-voice latency;
- accuracy on code-mixed dates and times;
- cost per call.

Each number will link to its method in [docs/METHOD.md](docs/METHOD.md), with its sample, date and scope. The failures will be published next to the wins.

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
uv run pytest
```

The text-mode agent and the evaluation harness arrive in milestone M1, and the voice pipeline in M2.

## Limitations and failures

Nothing has been measured yet. This section will list the measured failures and the known limits of the design, including the cases the gate misses.

## Privacy and costs

- No audio is stored. Transcripts are held in memory only for the length of a call, and only anonymous aggregate metrics are logged. See [docs/privacy.md](docs/privacy.md).
- All order data is synthetic.
- The cost per call will be measured in milestone M2, and published here with its method.

## Licence

[Apache-2.0](LICENSE). Copyright 2026 Mubin Attar.
