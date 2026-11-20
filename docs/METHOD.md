# Method

**Status: milestone M1 (text mode).** The results so far come from two sources:
- **Mocked models:** rule-based test doubles, some of them unreliable on purpose. They measure the safety layers around a model.
- **[One real model](#real-model):** `gemini-3.5-flash-lite`, run twice on the same corpus.

Voice and latency results come in M2–M3.

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

## Real model

**What ran (20 Nov 2026):**
- **Model:** `gemini-3.5-flash-lite`, with thinking set to `low`.
- **Setup:** text mode, corpus v0, faults on, every safety layer on, each scenario once (k = 1).
- **Why this model:** `gemini-3.8-flash` answered 503 "high demand" that day, so it joins the M2 comparison instead.
- **Pacing:** r1 hit the free tier's rate limit, so r2 sent at most one request every 5 s.

| Run | Corpus | Code | Calls | Passed | Duplicate writes | False confirmations | False failures |
|---|---|---|---|---|---|---|---|
| r1 | `8a7d217052` | `70c2ec5` | 19 of 20 (rate limit) | 15 | 0 | 0 | 0 |
| r2 | `0ff676161a` | `8bdd16f` | 20 of 20 | 19 | 0 | 0 | 0 |
| **r3 (headline)** | `0ff676161a` | `b9eb4fc` | 20 of 20 | **19** | **0** | **0** | **0** |

**r3 is the audit run.** It is the whole corpus, run on the final code. That code includes a stricter grader, under which the booked slot must be the one the caller chose.
- r3's result is the headline.
- r1 and r2 are kept as history, with what they exposed.
- r3 also had 0 unexpected writes.

**Scope:**
- One model, one day, and 20 calls run once each.
- 0 in 20 puts a rate at 15% or less, at 95% confidence (rule of three).
- pass^k is not measured.
- This is text mode only, so there are no speech-recognition errors yet.

**What the gate did:**
- **r1:** 8 model sentences were rewritten from the record. In 1 of them, the model's own claim was wrong.
- **r2:** 6 were rewritten, and all of them agreed with the record. Code spoke 24 outcomes after writes.
- **r3:** 8 were rewritten, and all of them agreed with the record. Code spoke 23 outcomes after writes.

**r1's failures: 3 were the harness, 1 was the scenario, and none was the guarantee.**
- **Questions missed:** the scripted caller did not recognise "When would you like to reschedule it to?" or "Aap kis date aur time par…?" as questions.
- **No way to accept another day:** the caller couldn't say yes to "check Sunday?".
- **An over-strict scenario:** it demanded exactly 4–6 PM. The agent offered 6–8 PM for "evening", and the caller accepted.
- **What followed:** the harness and the corpus were fixed (see the [changelog](../evals/corpus/v0/CHANGELOG.md)), and the whole corpus was re-run as r2.

**r2's one failure, `mind-cancel`: mostly the harness, partly the agent.**
- **The harness gap:** after the caller cancelled, the agent truthfully said the delivery stays on Friday and asked "Kya aapko kuch aur madad chahiye?". The scripted caller didn't recognise that, and asked for Saturday again.
- **The agent weakness:** offered two Saturday slots, the agent then booked the first on an unclear "Saturday shaam ko", instead of asking which one. This is a real weakness, to fix in the prompt.
- **Status:** the harness gap was fixed after r2.
- **Why there was a re-run:** the brief requires a re-run on the final code before any headline is published. So the whole corpus was re-run once, as r3.
- **Not tuned to pass:** the corpus was not re-run until it passed, and r3 is published as it came out.

**r3's one failure, `interrupt-time`: a gap in the grader, published as measured.**
- **What happened:** the caller interrupted with "Nahi nahi, 6 baje ke baad". Exactly one offered slot started after 6. The agent booked it, and code confirmed it correctly from the record.
- **Why it failed:** the stricter grader needs the caller to have named the slot it chose. A time limit that fits exactly one slot does not count yet.
- **What it would change:** counting it would make r3 20 of 20. The number stands as measured, and the grader is fixed before the next real-model run.
- **Open question for M2:** whether the agent should confirm before booking in this case is a prompt question.

**Seen in passing, for M2 (not measured):**
- **Script:** the model writes Hindi in Roman script, although the prompt asks for Devanagari, and Sarvam's TTS is better with Devanagari.
- **Times:** it says times in 24-hour form ("16:00 se 18:00"), which sounds awkward when spoken.
- **An unlisted offer:** in the smoke trial before r1, it once offered slots before it had called `list_slots`. It couldn't have booked them, because the adapter only accepts listed slots.

**Results files:**
- [r1](../results/text/v0/2026-11-20_gemini-3.5-flash-lite_full_faults-on_k1.json): marked incomplete. Its "-dirty" mark came from a docs edit made during the run; the code was exactly `70c2ec5`.
- [r2](../results/text/v0/2026-11-20_gemini-3.5-flash-lite_full_faults-on_k1_r2.json).
- [r3, the headline](../results/text/v0/2026-11-20_gemini-3.5-flash-lite_full_faults-on_k1_r3.json).

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

- **Mocked runs:** cost nothing.
- **Real-model runs:** each cost about US$0.06 at list price. In r3 that was 151,644 input tokens and 5,775 output tokens, thinking included. The rate-limit error suggests the key is on the free tier, so nothing may have been charged.
- **Prices:** dated, in [DECISIONS.md](DECISIONS.md).
- **Still to come:** cost per call in ₹, at a dated exchange rate, comes with M2's measured voice calls.

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
- **Real model, r1** (20 Nov 2026): 4 of 19 calls failed, from 3 harness gaps and 1 over-strict scenario. None was a safety failure. See [Real model](#real-model).
- **Real model, r2:** 1 of 20 failed (`mind-cancel`). It was a harness gap, plus the agent booking without an explicit choice. See [Real model](#real-model).
- **Real model, r3 (the headline):** 1 of 20 failed (`interrupt-time`). It was a gap in the grader, which does not yet count a time limit that fits exactly one slot as the caller's choice. It is published as measured. See [Real model](#real-model).

## Audit

Every published number can be re-run from a clean checkout. The commands:

```sh
uv run python -m evals.harness.run --llm mock-reckless --safety full --faults on --k 3
uv run python -m evals.harness.run --llm mock-reckless --safety baseline --faults on --k 3
uv run python -m evals.bank
```

CI runs the first two on every pull request (`--require-guarantee` and `--require-violations`). The whole corpus is re-run before any headline number is published. A number that fails an audit is retired, with the reason.
