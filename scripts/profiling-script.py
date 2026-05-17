#!/usr/bin/env python3
from __future__ import annotations

import json

from profiling.config import build_parser, normalize_output_paths
from profiling.runner import Profiler


def main() -> int:
    args = normalize_output_paths(build_parser().parse_args())
    summary = Profiler(args).run()
    print("\n=== Recommendation ===")
    print(json.dumps(summary["recommendation"], indent=2))
    if summary["memory_revalidation"] is not None:
        print("\n=== Memory Revalidation ===")
        print(json.dumps(summary["memory_revalidation"], indent=2))
    print(f"\nWrote {args.results_file}")
    print(f"Wrote {args.summary_file}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        import sys

        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
