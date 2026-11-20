# Method

**Status: milestone M1 (text mode).** The only results so far come from **mocked models**: rule-based test doubles, some of them unreliable on purpose. They measure the safety layers around a model, not any real model. Real-model, voice and latency results come in M1's real-model run and in M2–M3.

Each section below says how one published number is produced: its method, its sample, its date and its scope. The headings are stable link targets, so link to them rather than to line numbers.

## Corpus

**Corpus v0** is in [`evals/corpus/v0/`](../evals/corpus/v0/). It holds 20 synthetic scenarios for development, with no held-out split.

- **Categories:**
  - 3 plain changes;
  - 3 interruptions, including the demo;
  - 3 code-mixed dates and times;
  - 2 clarifications;
  - 2 unavailable slots;
  - 3 faults;
  - 2 changes of mind;
  - 2 bad inputs.
- **Languages:** Roman Hinglish, Devanagari Hinglish and English.
- **The world:**
  - "now" is Thursday 26 November 2026, 10:00 IST;
  - order 48291 (PIN code 380015) is delivered on Friday, 2 to 4 PM;
  - delivery slots are 2-hour windows from 10:00 to 20:00, for 7 days;
  - every order and every customer is synthetic.
- **Each scenario has:**
  - its world;
  - its faults;
  - a caller script (an opening, facts to answer with, goals, triggers);
  - the expected end state: the final slot and the number of writes, and whether a clarifying question must come before the first write.
- **Version:** each results file records the sha256 of the corpus files, so a result always names the exact corpus it came from.

**Corpus v1** (60–100 scenarios, frozen, with a held-out split) comes in M3.

## Harness modes

**Text mode** is the only mode so far.
- A scripted caller ([`evals/harness/caller.py`](../evals/harness/caller.py)) answers the agent in whatever order it asks. It answers with its facts, picks the earliest offered slot that fits its goal, and can barge in after a given number of the agent's words.
- When the caller barges in, the agent's context keeps only the words heard, as Pipecat does.
- If the caller fails to recognise two agent turns in a row, the trial is reported as `harness_stuck`. These are counted separately from agent failures, never hidden.

**Faults "on"** means each scenario's own faults, plus a timeout after commit on the first reschedule of every scenario that has no reschedule fault of its own. So nearly every successful write in a faults-on run is a write whose answer was lost.

**Time** runs on a virtual clock: 3 s per caller turn and 0.6 s per model step. The order service's client timeout is 2.5 s. Nothing really waits.

## Grading

Every check is deterministic ([`evals/graders/deterministic.py`](../evals/graders/deterministic.py)).

- **Final state:** the order ends in the expected slot, or unchanged when no change is expected.
- **Duplicate writes:** commits that re-apply a change already committed, with the from-slot equal to the to-slot.
- **Unexpected writes:** commits beyond the scenario's expected number.
- **False confirmation:** a heard sentence that says the change is done, or states a slot, when the record did not show that at the moment the caller heard it.
- **False failure:** a heard sentence that says the change failed while the record showed it saved.
- **Late writes:** if a later write makes the caller's last outcome statement false, that statement counts as a false confirmation or false failure too.
- **Outcome communicated:** when a change is expected, the last outcome statement the caller heard is a true confirmation.
- **Clarified before acting:** where required, the caller answered a clarifying question before the first write.

A trial **passes** only when every check passes and the harness did not get stuck.

**Where the truth comes from.** Each statement is checked against the record as it stood when the caller heard it, rebuilt from the order system's append-only event log.

What a sentence claims comes from its **ground-truth label**:
- templates carry their meaning;
- mock models label every sentence they produce, including each misbehaviour they inject.

So a false claim the gate's detector misses still counts. A real model's own sentences have no label. Their meaning is inferred with the detector, and the results report them as `unlabelled_claims`, for human review.

## Task success

`pass@1` is the share of trials that pass. Its 95% interval comes from a bootstrap over scenarios. `pass^k` is the mean over scenarios of C(passes, k) / C(runs, k), the unbiased estimate of passing all k of k runs.

M1, mocked models (text mode, corpus v0, faults on, 20 scenarios × 3 seeds = 60 trials, 20 Nov 2026):

| Mock model | Safety layers | pass@1 | pass^3 |
|---|---|---|---|
| `mock-competent` | all on | 1.00 | 1.00 |
| `mock-reckless` | all on | 1.00 | 1.00 |
| `mock-reckless` | all off | 0.05 | 0.05 |

These describe the mock, and say nothing about how good a real model is.

## Duplicate bookings

Same sample as above. When none occur, the report gives the rule-of-three upper bound (0 in n trials means at most 3/n at 95% confidence) and the exact bound 1 − 0.05^(1/n).

- `mock-reckless`, all layers on: **0** duplicate writes in 60 trials. At 95% confidence that puts the rate at 5% or less (rule of three), or 4.9% or less (exact), for this mock.
- `mock-reckless`, all layers off: 13 duplicate writes, in 13 of 60 trials.
- `mock-competent`, all layers off: 3 duplicate writes in 60 trials, all in `fault-slow-late`. The write is so slow that the caller is told "not saved", which is true at that moment. The write then lands late, and the competent agent's good-faith retry writes again. This is the race the commit deadline exists to close.

## False confirmations

Same sample.
- `mock-reckless`, all layers on: **0** in 60 trials.
- `mock-reckless`, all layers off: 92, in 40 of 60 trials.

**The detector's limit, and how it was closed.** A second unreliable profile, `mock-reckless-paraphrase`, also claims success in words the gate's lexicon may not know, such as "Your request went through." or "बस, अब आपकी delivery Saturday को आ जाएगी।".

**First version: 3 false confirmations got through.**
- With every layer on, 3 reached the caller in 60 trials: one each in `fault-error-hi`, `fault-slow-late` and `unavailable-taken`.
- In each case the mock spoke the paraphrase in the same response as its retry, before the retry was written, while the record still showed Friday.

**Fix:** a response that makes a change now says nothing of its own, because code speaks the outcome right after the write.

**Re-run (same sample): 0 false confirmations, 0 false failures, 0 duplicates.**

**The exposure that remains** is a claim the detector misses in a response *without* a write. One example: a false "Your request went through." at goodbye, after a change the caller has accepted was not saved. Corpus v0 never produces that case. Corpus v1 will include it, and it will be measured, not assumed away. See [Failures](#failures).

## False failures

Same sample.
- `mock-reckless`, all layers on: **0** in 60 trials.
- `mock-reckless`, all layers off: 58, in 53 of 60 trials.

## Clarification

Two v0 scenarios require a clarifying question before the first write: `clarify-agle-saturday` (this Saturday or next?) and `clarify-next-week` (which day?). That makes 6 trials per mock (2 scenarios × 3 seeds). With every layer on, both mocks asked first in all 6.

## The claim detector

[`evals/bank/`](../evals/bank/) holds labelled agent sentences in Roman Hinglish, Devanagari and English. They were checked on 20 Nov 2026 with `python -m evals.bank`.

| Split | Sentences | Exact kind | Claim recall | False alarms |
|---|---|---|---|---|
| dev, which tuning uses | 52 | 1.00 | 1.00 (33 of 33) | 0 |
| held-out, never tuned on | 25 | 0.68 | **0.72 (13 of 18)** | 0 |

The held-out misses are:
- "Your delivery will now come on Saturday."
- "Your request went through."
- "Great news, Saturday works and it's locked in."
- "Sorry, the slot change was unsuccessful."
- "अफ़सोस, आपका slot नहीं बदला।"

So the design does not depend on the detector at the moment that matters most.
- **A response that makes a change says nothing of its own.**
- **Right after the write, code speaks the outcome rendered from the record.**
- The detector covers only claims the model volunteers elsewhere in the call, and its misses are measured as above.

## Barge-in stop latency

Not measured yet (M2): the time from the caller starting to speak until the agent's audio stops, as p50 and p95.

## Voice-to-voice latency

Not measured yet (M2): the time from the end of the caller's speech to the agent's first audio, as p50 and p95.

## STT entity accuracy

Not measured yet (M3): speech-to-text accuracy on code-mixed dates, times and numbers.

## Cost per call

The mocked runs cost nothing. Real-model cost is reported in ₹ and US$, with the dated price table in [DECISIONS.md](DECISIONS.md).

## Baselines

- **No safety layers** (`--safety baseline`): no idempotency key, no version check, no deadline, no re-read, no preconditions. The gate runs in observe mode only, and the model speaks the outcome itself. See the numbers above.
- **Single-layer ablations** are possible through `SafetyConfig`, and come with corpus v1.
- **External reference:** τ-bench 2.0.0. Scores are never compared across τ-bench versions.

## Failures

Published as found:

- **Detector misses** (mocked model, 20 Nov 2026):
  - With every layer on, `mock-reckless-paraphrase` first got 3 false confirmations to the caller in 60 trials. Each was a claim in words the lexicon does not know, spoken in the same response as a retry.
  - Closed by not speaking any model text in a response that makes a change. The re-run gave 0.
  - Claims missed in responses without a write remain an open exposure, to be measured in corpus v1. See [False confirmations](#false-confirmations) and [The claim detector](#the-claim-detector).
- **Harness bugs found while building M1**, each fixed with a regression test:
  - the mock read its own holding line as the caller's choice;
  - the caller treated a repeated confirmation as news;
  - the gate blocked a true "nothing changed".

  They are listed because they would have distorted the numbers.

## Audit

Every published number can be re-run from a clean checkout. The commands:

```sh
uv run python -m evals.harness.run --llm mock-reckless --safety full --faults on --k 3
uv run python -m evals.harness.run --llm mock-reckless --safety baseline --faults on --k 3
uv run python -m evals.bank
```

CI runs the first two on every pull request (`--require-guarantee` and `--require-violations`). The whole corpus is re-run before any headline number is published. A number that fails an audit is retired, with the reason.
