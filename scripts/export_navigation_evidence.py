"""Export opt-in offline evidence with an explicit source timeline; no inference."""

import argparse
import json
from pathlib import Path

from venom_module_rider.navigation_archive import export_evidence


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--timeline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-mib", type=float, default=128)
    args = parser.parse_args(argv)
    summary = export_evidence(
        args.report,
        args.artifacts,
        args.timeline,
        args.output,
        max_mib=args.max_mib,
    )
    print(json.dumps(summary, indent=2, allow_nan=False))
    return 0 if summary["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
