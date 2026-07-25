"""Command line entry point: normalized TSV in, Markdown RCA out.

This is the Python half of `logsleuth analyze`; the shell driver runs the awk
and sed stages and pipes the result here. It can also be run on its own:

    logsleuth parse app.log | logsleuth normalize | python3 -m logsleuth
"""

import argparse
import sys
from typing import List, Optional

from . import __version__
from .analyze import Options, analyze
from .rca import diagnose_all
from .records import read_records
from .report import render
from .rules import load_rules


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python3 -m logsleuth",
        description="Group errors by signature, flag spikes and write an "
                    "RCA summary in Markdown.")
    p.add_argument("input", nargs="?", default="-",
                   help="normalized records (6-column TSV); default stdin")
    p.add_argument("-o", "--out", help="write the report here instead of stdout")
    p.add_argument("--source", action="append", default=[],
                   help="input label for the report header (repeatable)")
    p.add_argument("--width", type=int, metavar="SECONDS",
                   help="bucket width (default: chosen from the time span)")
    p.add_argument("-z", type=float, default=3.5, metavar="SIGMAS",
                   help="sigmas above the median to call a spike (default 3.5)")
    p.add_argument("--min-count", type=int, default=5, metavar="N",
                   help="ignore buckets with fewer events than this (default 5)")
    p.add_argument("--lead", type=int, default=600, metavar="SECONDS",
                   help="run-up before a spike to inspect (default 600)")
    p.add_argument("--tail", type=int, default=300, metavar="SECONDS",
                   help="time after a spike to inspect (default 300)")
    p.add_argument("--lookback", type=int, default=1800, metavar="SECONDS",
                   help="how far back to look for deploys/restarts (default 1800)")
    p.add_argument("--top", type=int, default=10, metavar="N",
                   help="rows per table (default 10)")
    p.add_argument("--rules", action="append", default=[], metavar="FILE",
                   help="extra rules JSON, merged over the bundled rules")
    p.add_argument("--version", action="version",
                   version=f"logsleuth {__version__}")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    stream = sys.stdin if args.input == "-" else open(args.input, encoding="utf-8",
                                                      errors="replace")
    with stream:
        records = list(read_records(stream))

    opts = Options(width=args.width, z=args.z, min_count=args.min_count,
                   lead=args.lead, tail=args.tail, lookback=args.lookback,
                   top=args.top)
    analysis = analyze(records, opts)
    rules = load_rules(*args.rules)
    sources = args.source or [args.input if args.input != "-" else "stdin"]
    text = render(analysis, diagnose_all(analysis, rules), sources, args.top)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
