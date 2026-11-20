"""Run the text-mode evaluation: `python -m evals.harness.run --help`.

Examples:

    # the M1 guarantee check: an unreliable mock, every safety layer on, faults on
    python -m evals.harness.run --llm mock-reckless --safety full --faults on --k 3

    # the same calls with every safety layer off: the failures the layers prevent
    python -m evals.harness.run --llm mock-reckless --safety baseline --faults on --k 3

    # one scenario's transcript
    python -m evals.harness.run --llm mock-reckless --show interrupt-demo
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

from evals.graders.deterministic import grade
from evals.harness.report import summarise, transcript
from evals.harness.runner import mock_factory, run_corpus, run_trial
from evals.schema import load_corpus
from voice_agent.llm.mock import PROFILES
from voice_agent.tools import SafetyConfig


def main(argv: list[str] | None = None) -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")  # Devanagari on a Windows console
    parser = argparse.ArgumentParser(prog="python -m evals.harness.run")
    parser.add_argument("--corpus", default="v0")
    parser.add_argument("--llm", default="mock-reckless", choices=sorted(PROFILES))
    parser.add_argument("--safety", default="full", choices=["full", "baseline"])
    parser.add_argument("--faults", default="on", choices=["on", "scenario", "off"])
    parser.add_argument("--k", type=int, default=3, help="runs per scenario (seeds 0..k-1)")
    parser.add_argument("--out", type=Path, help="write the results JSON here")
    parser.add_argument("--show", metavar="SCENARIO", help="print one scenario's transcript")
    parser.add_argument(
        "--require-guarantee",
        action="store_true",
        help="exit 1 if any duplicate write, false confirmation or false failure occurs",
    )
    parser.add_argument(
        "--require-violations",
        action="store_true",
        help="exit 1 unless duplicates, false confirmations and false failures all occur "
        "(shows a baseline run is not vacuous)",
    )
    args = parser.parse_args(argv)

    corpus = load_corpus(args.corpus)
    safety = SafetyConfig() if args.safety == "full" else SafetyConfig.baseline()
    factory = mock_factory(args.llm)

    if args.show:
        scenario = next(s for s in corpus.scenarios if s.id == args.show)
        record = run_trial(scenario, factory, safety=safety, faults=args.faults, seed_=0)
        print("\n".join(transcript(record)))
        result = grade(record)
        print(f"\npassed={result.passed} checks={result.checks}")
        return 0

    seeds = list(range(args.k))
    records = run_corpus(corpus.scenarios, factory, safety=safety, faults=args.faults, seeds=seeds)
    grades = [grade(record) for record in records]
    report = summarise(records, grades, corpus=corpus, k=args.k, seeds=seeds)
    metrics = report["metrics"]
    print(
        f"{args.llm} · safety={safety.label} · faults={args.faults} · corpus {corpus.version} "
        f"({len(corpus.scenarios)} scenarios) x k={args.k} = {len(grades)} trials"
    )
    print(f"  pass@1     {metrics['pass_at_1']['value']:.3f}  ci95={metrics['pass_at_1']['ci95']}")
    print(f"  pass^{args.k}     {metrics['pass_hat_k']['value']:.3f}")
    for name in ("duplicate_writes", "false_confirmations", "false_failures", "unexpected_writes"):
        entry = metrics[name]
        share = f"{entry['trials_with_any']}/{entry['trials']}"
        print(f"  {name:20} {entry['count']:3}  in {share} trials")
    print(f"  harness stuck        {metrics['harness_stuck']}")
    print(f"  gate                 {metrics['gate']}")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(  # LF on every platform, as .gitattributes asks
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        print(f"  wrote {args.out}")

    violations = [
        metrics[n]["count"] for n in ("duplicate_writes", "false_confirmations", "false_failures")
    ]
    if args.require_guarantee and any(violations):
        print("FAILED: the guarantee did not hold", file=sys.stderr)
        return 1
    if args.require_violations and not all(violations):
        print("FAILED: the baseline did not show every kind of violation", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
