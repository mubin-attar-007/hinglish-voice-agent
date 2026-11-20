"""Scaffold smoke test: the three packages import and describe themselves."""

import evals
import orders
import voice_agent


def test_packages_import() -> None:
    assert voice_agent.__doc__
    assert orders.__doc__
    assert evals.__doc__
