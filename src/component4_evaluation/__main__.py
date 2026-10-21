"""GUI by default; an optional offline-only CLI supports reproducible checks."""

import argparse
from dataclasses import asdict
import json

from .harness import SCENARIOS, EvaluationError, export_result, run_evaluation


def main():
    parser = argparse.ArgumentParser(description="Component 4 synthetic research evaluation")
    parser.add_argument("--headless", action="store_true", help="Run one offline rule scenario without Tk")
    parser.add_argument("--scenario", choices=SCENARIOS, default="normal")
    parser.add_argument("--export", help="Explicit JSON output on D:, outside the repository")
    args = parser.parse_args()
    if args.headless:
        try:
            result = run_evaluation(args.scenario)
            if args.export:
                export_result(result, args.export)
            print(json.dumps(asdict(result), indent=2, allow_nan=False))
            return 0 if result.assessment["verdict"] == "PASS" else 1
        except EvaluationError:
            print("Evaluation unavailable; no result asserted.")
            return 1
    if args.export:
        parser.error("--export requires --headless; the GUI has an Export JSON button")
    from .desktop import main as desktop_main
    desktop_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
