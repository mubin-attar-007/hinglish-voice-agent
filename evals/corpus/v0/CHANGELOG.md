# Corpus v0 changelog

Corpus v0 is a development corpus. It may change until corpus v1 is frozen.

Every results file records the corpus sha256, which covers the YAML files only, not this changelog. So each result names the exact version it was graded on.

| sha256 (first 10) | Date | Change | Why |
|---|---|---|---|
| `28cff54cf2` | 2026-11-20 | First version: 20 scenarios | No results were published on it |
| `8a7d217052` | 2026-11-20 | `bad-wrong-id` rewritten in English | Code review found the English "order not found" path untested |
| `0ff676161a` | 2026-11-20 | Two changes | See below |

**The change in `0ff676161a`:**
- 11 scenarios now accept any slot that fits the caller's goal (`final_fits`), not only the one the mock books.
- `unavailable-full` gives the caller a fallback wish for "Sunday?".

**Why:** the first real-model run (`gemini-3.5-flash-lite`, graded on `8a7d217052`) exposed two gaps.
- The agent read "evening" as after 5 PM and booked 6 to 8, which the caller accepted. The scenario still demanded exactly 4 to 6.
- The scripted caller could not say yes to "Shall I check Sunday?". See [METHOD.md](../../../docs/METHOD.md#failures).
