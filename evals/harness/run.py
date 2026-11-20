"""Run the text-mode evaluation: `python -m evals.harness.run --help`.

Examples:

    # the M1 guarantee check: an unreliable mock, every safety layer on, faults on
    python -m evals.harness.run --llm mock-reckless --safety full --faults on --k 3

    # the same calls with every safety layer off: the failures the layers prevent
    python -m evals.harness.run --llm mock-reckless --safety baseline --faults on --k 3

    # one scenario's transcript
    python -m evals.harness.run --llm mock-reckless --show interrupt-demo

    # a real model (needs GEMINI_API_KEY); no request is sent once the cost reaches the cap
    python -m evals.harness.run --llm gemini --model gemini-3.8-flash --k 1 --max-usd 1.0
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
from pathlib import Path
from typing import Any

from evals.graders.deterministic import grade
from evals.harness.report import summarise, transcript
from evals.harness.runner import LLMFactory, TrialRecord, gemini_factory, mock_factory, run_trial
from evals.schema import load_corpus
from voice_agent.llm.gemini import DEFAULT_MODEL, DEFAULT_THINKING
from voice_agent.llm.mock import PROFILES
from voice_agent.llm.pricing import PRICES, BudgetExceeded, CostMeter
from voice_agent.tools import SafetyConfig

MAX_ERRORS = 3  # provider errors before a real-model run gives up


def _positive(value: str) -> float:
    number = float(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be more than 0")
    return number


def _rate_limited(error: Exception) -> bool:
    return "429" in str(error) or "RESOURCE_EXHAUSTED" in str(error)


def main(argv: list[str] | None = None) -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")  # Devanagari on a Windows console
    parser = argparse.ArgumentParser(prog="python -m evals.harness.run")
    parser.add_argument("--corpus", default="v0")
    parser.add_argument("--llm", default="mock-reckless", choices=[*sorted(PROFILES), "gemini"])
    parser.add_argument("--model", help=f"the Gemini model id (default {DEFAULT_MODEL})")
    parser.add_argument(
        "--thinking",
        choices=["minimal", "low", "medium", "high"],
        help="Gemini thinking level (default low: a phone call can't wait for long thinking)",
    )
    parser.add_argument(
        "--max-usd",
        type=_positive,
        default=1.0,
        help="real models only: send no request once the list-price cost reaches this; one "
        "request can still carry the total past it, by that request's own cost (default 1.0)",
    )
    parser.add_argument("--safety", default="full", choices=["full", "baseline"])
    parser.add_argument("--faults", default="on", choices=["on", "scenario", "off"])
    parser.add_argument("--k", type=int, default=3, help="runs per scenario (seeds 0..k-1)")
    parser.add_argument("--out", type=Path, help="write the results JSON here")
    parser.add_argument("--show", metavar="SCENARIO", help="print one scenario's transcript")
    parser.add_argument(
        "--require-guarantee",
        action="store_true",
        help="exit 1 unless the run is complete with no duplicate write, false confirmation or "
        "false failure",
    )
    parser.add_argument(
        "--require-violations",
        action="store_true",
        help="exit 1 unless duplicates, false confirmations and false failures all occur "
        "(shows a baseline run is not vacuous)",
    )
    args = parser.parse_args(argv)
    if (args.model or args.thinking) and args.llm != "gemini":
        parser.error("--model and --thinking apply to --llm gemini only")

    corpus = load_corpus(args.corpus)
    safety = SafetyConfig() if args.safety == "full" else SafetyConfig.baseline()
    meter: CostMeter | None = None
    factory: LLMFactory
    if args.llm == "gemini":
        model = args.model or DEFAULT_MODEL
        if not os.environ.get("GEMINI_API_KEY"):
            print("Set GEMINI_API_KEY (a dedicated key) to run against Gemini.", file=sys.stderr)
            return 2
        price = PRICES.get(model)
        if price is None:
            print(f"No dated price for {model} in voice_agent/llm/pricing.py.", file=sys.stderr)
            return 2
        meter = CostMeter(price, cap_usd=args.max_usd)
        factory = gemini_factory(model, meter, args.thinking or DEFAULT_THINKING)
    else:
        factory = mock_factory(args.llm)

    if args.show:
        scenario = next(s for s in corpus.scenarios if s.id == args.show)
        record = run_trial(scenario, factory, safety=safety, faults=args.faults, seed_=0)
        print("\n".join(transcript(record)))
        result = grade(record)
        print(f"\npassed={result.passed} checks={result.checks}")
        return 0

    seeds = list(range(args.k))
    records: list[TrialRecord] = []
    errors: list[dict[str, Any]] = []
    stopped: str | None = None
    for scenario in corpus.scenarios:
        for seed_ in seeds:
            try:
                records.append(
                    run_trial(scenario, factory, safety=safety, faults=args.faults, seed_=seed_)
                )
            except BudgetExceeded as exc:
                stopped = str(exc)
            except Exception as exc:  # a provider error: record it, never lose the paid-for trials
                errors.append({"scenario": scenario.id, "seed": seed_, "error": repr(exc)[:300]})
                if _rate_limited(exc) or len(errors) >= MAX_ERRORS:
                    stopped = f"stopped after a provider error: {repr(exc)[:120]}"
            if stopped:
                break
        if stopped:
            break

    planned = len(corpus.scenarios) * len(seeds)
    print(
        f"{args.llm} · safety={safety.label} · faults={args.faults} · corpus {corpus.version} "
        f"({len(corpus.scenarios)} scenarios) x k={args.k}: {len(records)} of {planned} trials"
    )
    if not records:
        print(f"  no trial completed. {stopped or ''} errors={errors}", file=sys.stderr)
        return 1
    grades = [grade(record) for record in records]
    report = summarise(
        records,
        grades,
        corpus=corpus,
        k=args.k,
        seeds=seeds,
        price=meter.price if meter else None,
        meter=meter,
        planned_trials=planned,
        errors=errors,
        stopped=stopped,
    )
    if args.llm == "gemini":
        report["model"]["thinking"] = args.thinking or DEFAULT_THINKING
    metrics = report["metrics"]
    if not report["complete"]:
        print(f"  INCOMPLETE: {stopped or ''} {len(errors)} errored trial(s)")
    print(f"  pass@1     {metrics['pass_at_1']['value']:.3f}  ci95={metrics['pass_at_1']['ci95']}")
    hat_k = metrics["pass_hat_k"]
    if hat_k["value"] is not None:
        print(f"  pass^{args.k}     {hat_k['value']:.3f}  over {hat_k['scenarios']} scenarios")
    for name in ("duplicate_writes", "false_confirmations", "false_failures", "unexpected_writes"):
        entry = metrics[name]
        share = f"{entry['trials_with_any']}/{entry['trials']}"
        print(f"  {name:20} {entry['count']:3}  in {share} trials")
    print(f"  harness stuck        {metrics['harness_stuck']}")
    print(f"  gate                 {metrics['gate']}")
    if meter is not None:
        print(f"  cost                 ${report['cost']['usd']:.4f} at list price")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(  # LF on every platform, as .gitattributes asks
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        print(f"  wrote {args.out}")

    names = ("duplicate_writes", "false_confirmations", "false_failures")
    violations = [metrics[name]["count"] for name in names]
    if args.require_guarantee and (any(violations) or not report["complete"]):
        print("FAILED: the guarantee did not hold over a complete run", file=sys.stderr)
        return 1
    if args.require_violations and not all(violations):
        print("FAILED: the baseline did not show every kind of violation", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
