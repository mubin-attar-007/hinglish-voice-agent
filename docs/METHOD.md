# Method

**Status: draft.** Nothing has been measured yet.

Each section below will describe how one published number is produced: its method, its sample, its date and its scope. The headings are stable link targets, so link to them rather than to line numbers.

## Corpus

The scenario corpus is a set of synthetic calls asking to change a delivery slot. They are in Hinglish (Roman and Devanagari script) and in English.

- Corpus v0 (20 scenarios, milestone M1) is for development.
- Corpus v1 (60–100 scenarios, milestone M3) will be frozen and versioned, and will have a held-out split.

## Grading

Grading is deterministic first:

- the final state of the order database;
- the number of committed writes;
- every statement about the outcome, checked against the record as it stood at the moment the statement was spoken.

An LLM judge is used only for soft criteria such as clarity, and only after it agrees with human labels on a sample.

## Task success

Not measured yet. This will report pass@1 and pass^k (k = 3–5).

## Duplicate bookings

Not measured yet. When none occur, the report gives the rule-of-three upper bound: 0 out of n means at most 3/n, at 95% confidence.

## False confirmations

Not measured yet.

## False failures

Not measured yet.

## Clarification

Not measured yet.

## Barge-in stop latency

Not measured yet. This is the time from the caller starting to speak until the agent's audio stops, reported as p50 and p95.

## Voice-to-voice latency

Not measured yet. This is the time from the end of the caller's speech to the agent's first audio, reported as p50 and p95.

## STT entity accuracy

Not measured yet. This is the speech-to-text accuracy on code-mixed dates, times and numbers.

## Cost per call

Not measured yet. It will be reported in ₹ and in US$, with the price table and the date it was checked.

## Baselines

These are planned:

- the same pipeline without the confirmation gate and without idempotency;
- optionally, one speech-to-speech model;
- τ-bench 2.0.0 as an external reference. Scores are never compared across τ-bench versions.

## Failures

The failures will be published here in detail, next to the wins.

## Audit

The whole corpus is re-run before any headline number is published. A number that fails an audit is retired, with the reason.
