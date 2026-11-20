"""Print the claim detector's scores on both splits: `python -m evals.bank`."""

from __future__ import annotations

import io
import json
import sys
from dataclasses import asdict

from evals.bank import load_bank, score


def main() -> None:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")  # Devanagari on a Windows console
    report = {}
    for split in ("dev", "heldout"):
        result = score(load_bank(split))
        entry = asdict(result)
        entry["misses"] = [{"kind": miss.kind.value, "text": miss.text} for miss in result.misses]
        report[split] = entry
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
