# Decisions and verified facts

This file holds the project's technical decisions, and the versions and prices those decisions depend on.

- **Checked** means the fact was read from the named source on that date.
- Prices change, so re-check a price before relying on it.
- A fact that could not be confirmed is listed under [Open questions](#open-questions), not presented as true.

## Decisions

| Date | Decision | Why |
|---|---|---|
| 2026-11-20 | Public repo `mubin-attar-007/hinglish-voice-agent`, under the Apache-2.0 licence | The work is public from day one |
| 2026-11-20 | Use WebSocket audio first, with one host-agnostic Docker image | The connections pass through ordinary HTTPS proxies, so the server can run on any host. WebRTC comes later, if M2's latency numbers justify it (Pipecat's docs recommend SmallWebRTC for browsers) |
| 2026-11-20 | STT: `saaras:v4` in `codemix` mode | It is the current default model, and `codemix` writes Hindi in Devanagari and English in Latin script |
| 2026-11-20 | TTS: start with `bulbul:v4-flash`, and compare `bulbul:v3` on quality | Bulbul v2, the earlier plan, is no longer accepted by Sarvam's API. v4-flash is the cheapest current model |
| 2026-11-20 | Agent replies use Hindi in Devanagari and English in Latin script | Sarvam's TTS guidance says Romanised Hindi lowers quality. The convention also matches the STT `codemix` output |
| 2026-11-20 | LLM: in M2, compare a Gemini Flash-class model (`gemini-3.8-flash` or a Flash-Lite) with `sarvam-105b` or `sarvam-105b-conversations`, on task success, latency and cost | Both support tool calling. The choice is made by the evaluation, not by preference |
| 2026-11-20 | Gemini's free tier is used only for synthetic evaluation runs, never for the live demo | Content sent through the free tier may be used to improve Google's products, and human reviewers may read it |
| 2026-11-20 | Live-demo budget: Sarvam's free credit only, until the cap is raised | A demo-cost control. The demo shows a "resting" message when the cap is reached |
| 2026-11-20 | Re-decide the host before M3, and keep the server host-agnostic | Fly.io's region list no longer shows Mumbai (`bom`). See [Hosting](#hosting) |
| 2026-11-20 | The model never supplies the idempotency key. The tool adapter derives it from (call id, order id, slot id, order version) | A model cannot break idempotency by inventing or varying keys. Including the version keeps a genuine A→B→A change of mind distinct from a retry |
| 2026-11-20 | Every write carries a deadline and an expected order version. The order service refuses to commit after the deadline or on a version mismatch | The deadline makes the re-read after a timeout final, because no late commit can land after the agent has read the record. The version check stops parallel or stale writes |
| 2026-11-20 | Sentences about the outcome are rendered from the record by code, and the model's own outcome claims are always replaced | The model proposes the change, and the code confirms it |
| 2026-11-20 | The gate checks the reply one sentence at a time, before text-to-speech | The same function runs in text mode and inside Pipecat's `LLMTextProcessor` |
| 2026-11-20 | External reference: τ-bench 2.0.0. Novelty is claimed only for code-mixed Hinglish with fault-injected checks against the system of record | τ-bench 2.0.0 already covers Hindi, in text and in voice |
| 2026-11-20 | After a write, code speaks the outcome, and a model response that makes a change says nothing of its own | The held-out sentence bank shows the detector misses 28% of paraphrased claims, and a mock exploited that before its retry landed. This keeps the moment of a change independent of detection. See [METHOD.md](METHOD.md#false-confirmations) |
| 2026-11-20 | Mock models label every sentence with what it really claims, and graders use those labels as ground truth | A claim the detector misses still counts against the agent, so mock results can't flatter the detector |
| 2026-11-20 | "Faults on" adds a timeout after commit to the first reschedule of every scenario that has no reschedule fault of its own | Most writes in a faults-on run then lose their answer, which is the hardest case for the guarantee |
| 2026-11-20 | Gemini thinking level defaults to `low` (`--thinking`), with a 60 s server deadline | The first real requests at default thinking ran past the SDK's 30 s deadline, which it sends as `X-Server-Timeout`. A phone call can't wait for long thinking |
| 2026-11-20 | The M1 real-model run uses `gemini-3.5-flash-lite` | `gemini-3.8-flash` answered "503 UNAVAILABLE: high demand" that day. 3.8 Flash joins the M2 model comparison |

## Verified facts

### Pipecat

| Fact | Source | Checked |
|---|---|---|
| `pipecat-ai` 1.16.0 was released on 2026-11-12. It needs Python ≥3.11 | [PyPI](https://pypi.org/project/pipecat-ai/) · [releases](https://github.com/pipecat-ai/pipecat/releases) | 2026-11-20 |
| Extras: `websocket` (pulls in FastAPI), `sarvam`, `google` and `openai`. Silero VAD and Smart Turn v3 ship in core | PyPI metadata · source | 2026-11-20 |
| The transport is `FastAPIWebsocketTransport` with `FastAPIWebsocketParams`, from `pipecat.transports.websocket.fastapi`. The browser serializer is `ProtobufFrameSerializer`. The JS client is `@pipecat-ai/client-js` 1.16.0 with `@pipecat-ai/websocket-transport` 1.8.2 | [FastAPI WebSocket transport](https://docs.pipecat.ai/api-reference/server/services/transport/fastapi-websocket) · [JS WebSocket transport](https://docs.pipecat.ai/api-reference/client/js/transports/websocket) | 2026-11-20 |
| The docs describe WebSocket as being for telephony or prototyping, and recommend SmallWebRTC for browsers | [Choosing a transport](https://docs.pipecat.ai/client/concepts/choosing-a-transport) | 2026-11-20 |
| Interruptions are on by default, and `allow_interruptions` was removed in 1.0.0. VAD attaches through `LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer(), user_turn_strategies=…)`. The default turn-stop strategy uses Smart Turn v3 | [Interruptions](https://docs.pipecat.ai/pipecat/fundamentals/interruptions) · [User turn strategies](https://docs.pipecat.ai/api-reference/server/utilities/turn-management/user-turn-strategies) | 2026-11-20 |
| `PipelineTask` and `PipelineRunner` have been deprecated since 1.3.0, in favour of `PipelineWorker` and `WorkerRunner` | [CHANGELOG](https://github.com/pipecat-ai/pipecat/blob/main/CHANGELOG.md) | 2026-11-20 |
| Metrics come from `PipelineParams(enable_metrics=True, enable_usage_metrics=True)` and from `UserBotLatencyObserver`. `SpeakingObserver` timestamps when the user starts speaking, the interruption, and when the bot stops speaking. Barge-in stop latency is computed from those timestamps | [Metrics](https://docs.pipecat.ai/pipecat/fundamentals/metrics) · [User-bot latency observer](https://docs.pipecat.ai/api-reference/server/utilities/observers/user-bot-latency-observer) | 2026-11-20 |
| `LLMTextProcessor(text_transforms=…)` rewrites whole sentences before TTS, and its transforms are async. On interruption, the context keeps only the words that were spoken | [LLM text processor](https://docs.pipecat.ai/api-reference/server/utilities/frame/llm-text-processor) · [source](https://github.com/pipecat-ai/pipecat/blob/main/src/pipecat/processors/aggregators/llm_text_processor.py) | 2026-11-20 |
| `register_function(…, cancel_on_interruption=None, timeout_secs=None)` takes service-level defaults. `reschedule` will set `cancel_on_interruption=False` explicitly | [Function calling](https://docs.pipecat.ai/pipecat/learn/function-calling) · [source](https://github.com/pipecat-ai/pipecat/blob/main/src/pipecat/services/llm_service.py) | 2026-11-20 |
| Sarvam services: `SarvamSTTService` (WebSocket, default `saaras:v4`); `SarvamTTSService` (WebSocket, default `bulbul:v3`, with `bulbul:v4-flash` available since 1.13.0); `SarvamLLMService` (default `sarvam-105b`) | [Sarvam STT](https://docs.pipecat.ai/api-reference/server/services/stt/sarvam) · [example](https://github.com/pipecat-ai/pipecat/blob/main/examples/voice/voice-sarvam.py) | 2026-11-20 |

### Sarvam AI

| Fact | Source | Checked |
|---|---|---|
| The STT default is `saaras:v4`, and `saaras:v3` is still available. `saarika:v2.5` is deprecated. The modes are transcribe, translate, verbatim, translit and codemix; `codemix` writes Hindi in Devanagari and English in Latin script | [STT overview](https://docs.sarvam.ai/api/api-guides-tutorials/speech-to-text/overview) · [Saaras](https://docs.sarvam.ai/api/api-guides-tutorials/models/saaras) | 2026-11-20 |
| Streaming STT goes over the WebSocket API. Input is mono 8 or 16 kHz PCM. REST requests are limited to 30 s of audio | [Realtime streaming](https://docs.sarvam.ai/api/api-guides-tutorials/speech-to-text/realtime-streaming) | 2026-11-20 |
| STT costs ₹0.49 per minute on pay-as-you-go, billed per second (about ₹29.4 per hour) | [Pricing](https://docs.sarvam.ai/api-reference-docs/getting-started/pricing) | 2026-11-20 |
| TTS: `bulbul:v4-flash` costs ₹20 per 10,000 characters, and `bulbul:v3` (the API default) costs ₹30. Bulbul v2 is no longer accepted | [API pricing](https://www.sarvam.ai/api-pricing) · [Bulbul](https://docs.sarvam.ai/api/api-guides-tutorials/models/bulbul) | 2026-11-20 |
| For TTS, native script is recommended for Indic words, because Romanised Hindi lowers quality | [TTS best practices](https://docs.sarvam.ai/api/api-guides-tutorials/text-to-speech/best-practices) | 2026-11-20 |
| LLMs: `sarvam-105b` (128K context) and `sarvam-105b-conversations` (32K, no reasoning, aimed at voice). Both use OpenAI-compatible chat completions with `tools` | [Sarvam-105B](https://docs.sarvam.ai/api/token-factory/models/sarvam-105b) · [Chat completions](https://docs.sarvam.ai/api/token-factory/chat-completions) | 2026-11-20 |
| The free credit is ₹100 per verified user, cut from ₹1,000 in May 2026. Credits do not expire | [Changelog, May 2026](https://docs.sarvam.ai/changelog/2026/5/1) | 2026-11-20 |
| Data: content may be used for training unless you opt out. Default retention is 30 days, and zero retention is available for model APIs | [Privacy policy](https://www.sarvam.ai/privacy-policy) (updated 2026-07-29) · [Data retention](https://docs.sarvam.ai/api/platform/data-retention) | 2026-11-20 |

### Google Gemini

| Fact | Source | Checked |
|---|---|---|
| `gemini-3.8-flash` is stable and supports function calling. It costs $0.75 in and $3.75 out per 1M tokens to 2026-12-31, then $1.50 and $7.50 | [Pricing](https://aistudio.google.com/docs/pricing) · [Models](https://aistudio.google.com/docs/models) | 2026-11-20 |
| `gemini-3.5-flash-lite` costs $0.30 in and $2.50 out per 1M tokens, and `gemini-3.1-flash-lite` costs $0.25 and $1.50. `gemini-2.5-flash` is deprecated and is routed to 3.5 Flash-Lite from 2026-12-01 | [Pricing](https://aistudio.google.com/docs/pricing) · [Deprecations](https://aistudio.google.com/docs/deprecations) | 2026-11-20 |
| On the free tier, content may be used to improve Google's products, and human reviewers may read it. On paid tiers, prompts are not used to improve products | [Terms](https://aistudio.google.com/docs/terms) (effective 2026-03-23) | 2026-11-20 |
| The speech-to-speech baseline candidate is `gemini-3.8-live`, which supports function calling. Audio costs $3.00 in and $12.00 out per 1M tokens, about $0.005 and $0.018 a minute | [Pricing](https://aistudio.google.com/docs/pricing) | 2026-11-20 |
| `google-genai` 3.6.1 was released on 2026-11-18. Manual function calling uses `types.FunctionDeclaration(parameters_json_schema=…)` | [PyPI](https://pypi.org/project/google-genai/) · [README](https://github.com/googleapis/python-genai) | 2026-11-20 |
| In `google-genai` 3.6.1, `HttpOptions.timeout` (ms) is sent as the server deadline (`X-Server-Timeout`). Function responses go back with role `user` in the SDK's own chat loop, although its README example shows `tool`. `ThinkingConfig.thinking_level` takes MINIMAL, LOW, MEDIUM, HIGH or XHIGH | SDK source: `_api_client.py`, `chats.py` and `types.py` | 2026-11-20 |
| A one-word probe with thinking `low`, once per model, from this project's network, on 2026-11-20. `gemini-3.8-flash` returned 503 UNAVAILABLE ("high demand"). `gemini-3.5-flash-lite` answered in 6.0 s, and `gemini-3.1-flash-lite` in 2.4 s. These are single samples on a busy day, not latency measurements | Probe script, run once per model | 2026-11-20 |

### Hosting

| Fact | Source | Checked |
|---|---|---|
| Fly.io's region list and pricing table no longer show `bom` (Mumbai). In `sin`, a shared-cpu-1x machine with 1 GB costs about $8.50 a month. New organisations get no free tier. Fly Proxy does not close idle WebSockets | [Regions](https://docs.fly.io/reference/regions) · [Pricing](https://docs.fly.io/about/pricing) · [WebSockets](https://docs.fly.io/networking/websockets) | 2026-11-20 |
| Cloud Run supports WebSockets in `asia-south1` (Mumbai), with a request timeout of up to 60 minutes. Keeping one minimum instance warm costs about $13 a month (our arithmetic from list prices) | [WebSockets](https://docs.cloud.google.com/run/docs/triggering/websockets) · [Request timeout](https://docs.cloud.google.com/run/docs/configuring/request-timeout) · [Locations](https://docs.cloud.google.com/run/docs/locations) · [Pricing](https://cloud.google.com/run/pricing) | 2026-11-20 |
| Render's Starter plan costs $7 a month with 512 MB. It is offered in Singapore and supports WebSockets | [Pricing](https://render.com/pricing) · [Regions](https://render.com/docs/regions) · [WebSockets](https://render.com/docs/websocket) | 2026-11-20 |

### Benchmarks

| Fact | Source | Checked |
|---|---|---|
| τ-bench 2.0.0, "Multilingual and Voice" (2026-10-19), adds Hindi in text and in voice. Its scores are not comparable with 1.x | [tau2-bench releases](https://github.com/sierra-research/tau2-bench/releases) | 2026-11-20 |
| τ-Voice reports voice agents at 26–38% pass@1 in realistic audio, against 31–51% in clean audio, over 278 tasks | [arXiv 2603.13686](https://arxiv.org/abs/2603.13686) | 2026-11-20 |

### Tooling

| Fact | Source | Checked |
|---|---|---|
| `actions/checkout` v7.0.1 is pinned at `3d3c42e5aac5ba805825da76410c181273ba90b1` | [Release](https://github.com/actions/checkout/releases/tag/v7.0.1) | 2026-11-20 |
| `astral-sh/setup-uv` v10.3.3 is pinned at `68559e99cc807c1eb7e9238fd43a15bba460eec2` | [Release](https://github.com/astral-sh/setup-uv/releases/tag/v10.3.3) | 2026-11-20 |

## Open questions

These could not be confirmed. Each one is checked before the work that depends on it.

- **Gemini free-tier rate limits** are set per project, and the docs give no numbers. Checked before the M1 real-LLM run.
- **The price of `sarvam-105b`:** the model page shows introductory rates through 2026, but an August 2026 changelog entry lists higher rates. Re-checked before measuring cost in M2.
- **Whether Fly.io can still create Machines in `bom`:** run `fly platform regions` before the host decision in M3.
- **Hindi support in Gemini's Live (speech-to-speech) models** is unverified.
- **Whether Pipecat's JS client resamples playback audio** is unverified. Until it is, the server's output sample rate is matched to the client player's rate.
- **How to make spoken replies TTS-friendly** is an open question for M2.
  - In M1's real-model runs, `gemini-3.5-flash-lite` wrote Hindi in Roman script, although the prompt asked for Devanagari.
  - It also gave times in 24-hour form ("16:00 se 18:00").
  - Candidates are a firmer prompt, or a Pipecat text transform that renders times and script before TTS. M2 chooses between them by measuring.
- **Booking on an unclear reply:** in r2, the model booked the first of two offered slots when the caller's reply named neither. The prompt should require an explicit choice. To be fixed and re-measured in M2.
- **A choice made by a time limit:** the grader counts a choice only when the caller names the slot.
  - In r3, "6 baje ke baad" fitted exactly one offered slot, and the agent booked it. The grader failed the call. It is published as measured.
  - Before the next real-model run, the grader will count a limit that fits exactly one offered slot as a choice.
  - M2 decides whether the agent should confirm before booking.
