"""The evaluation CLI's guards: bad caps, misplaced options, a real model without a key."""

from __future__ import annotations

import pytest

from evals.harness.run import main


def test_a_cap_of_zero_or_less_is_rejected() -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--llm", "mock-reckless", "--max-usd", "0"])
    assert exit_info.value.code == 2


def test_model_is_only_for_gemini() -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--llm", "mock-reckless", "--model", "gemini-3.8-flash"])
    assert exit_info.value.code == 2


def test_gemini_without_a_key_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert main(["--llm", "gemini", "--k", "1"]) == 2


def test_a_complete_mock_run_passes_the_guarantee_check() -> None:
    assert main(["--llm", "mock-reckless", "--k", "1", "--require-guarantee"]) == 0
