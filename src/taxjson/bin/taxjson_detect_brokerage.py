#!/usr/bin/env python3
"""
Detect which brokerage parser reads a CSV — by its content first.

Prints the parser id (the name taxjson_brokerage.py takes) on stdout and,
on stderr, the same per-file line `taxjson run` prints: how the file was
detected (generic mapping, content signature, or file name fallback).
The rules live in taxjson.lib.brokerages.detect.
"""

import sys
from pathlib import Path
from typing import Optional

from taxjson.lib import cli_diag
from taxjson.lib.brokerages.base import BrokerageParseError, decode_broker_text
# Re-exported: `taxjson fetch` and older callers import them from here.
from taxjson.lib.brokerages.detect import (  # noqa: F401
    IB_SECTIONS, SCAN_ROWS as _SCAN_ROWS, AmbiguousBroker,
    ambiguity_message, content_matches, csv_rows as _csv_rows, detect,
    looks_like_ib_rows, looks_like_ib_text)

PROG = "taxjson-detect-brokerage"


def detect_brokerage(file_path: Path) -> Optional[str]:
    """The parser id the CONTENT of `file_path` matches (ib, questrade,
    webull, rbc_direct, coinbase, kraken), or None. No file-name
    fallback and no generic mapping here (`detect` adds those). An
    undecodable file or a file matching two exports is said in one
    error line and returns None."""
    try:
        text = decode_broker_text(Path(file_path).read_bytes(),
                                  Path(file_path).name)
        hits, _near = content_matches(text)
        if len(hits) > 1:
            raise AmbiguousBroker(ambiguity_message(Path(file_path), hits))
        return hits[0][0] if hits else None
    except BrokerageParseError as e:
        cli_diag.error(PROG, str(e))
        return None
    except Exception as e:
        cli_diag.error(PROG, f"detecting brokerage: {e}")
        return None


def cannot_detect_message(det) -> str:
    """The advice for a CSV no rule routes (shared with `taxjson run`):
    the header first, then a generic mapping, then the rename
    fallback."""
    from taxjson.lib.brokerages.base import shown_name
    near = f" Closest: {det.hint}." if det.hint else ""
    # The file's name masked like every other diagnostic: a broker's
    # default download name carries the account id (security review
    # L4).
    name = shown_name(det.path)
    return (f"cannot detect broker for {name}.{near} Check the header "
            f"first: the file must carry its export's own header row "
            f"(Interactive Brokers, Questrade, Webull, RBC Direct, Coinbase "
            f"or Kraken — README \"How a file's broker is detected\"). "
            f"Another broker: add a generic column mapping, "
            f"{name}.toml (see examples/generic_wealthsimple.toml). "
            f"Last resort for a Coinbase or Kraken export whose header is "
            f"not recognised: rename it to start with cb_ or kr_.")


def main():
    import argparse
    ap = argparse.ArgumentParser(
        prog="taxjson-detect-brokerage",
        description="Print which brokerage parser a CSV resolves to "
                    "(stdout: the parser id; stderr: how it was "
                    "detected — a generic mapping, the content's header "
                    "signature, or the file-name fallback), by the same "
                    "rules `taxjson run` uses.")
    ap.add_argument("csv", type=Path, help="CSV file to identify")
    args = ap.parse_args()

    file_path = args.csv
    if not file_path.exists():
        print(f"taxjson-detect-brokerage: error: no such file: "
              f"{file_path}", file=sys.stderr)
        sys.exit(2)                 # a missing input (A2-0164)

    try:
        det = detect(file_path)
    except BrokerageParseError as e:
        cli_diag.error(PROG, str(e))
        sys.exit(1)
    if det.error and det.broker is None:
        cli_diag.error(PROG, det.error)
    if det.positions:
        # A positions report: no parser reads it as activity.
        print(f"positions:{det.positions}")
        print(det.line(), file=sys.stderr)
        sys.exit(0)
    if det.broker:
        print(det.broker)
        print(det.line(), file=sys.stderr)
        if det.note:
            print(f"note: {det.note}", file=sys.stderr)
    else:
        print("unknown", file=sys.stderr)
        if not det.error:
            print(cannot_detect_message(det), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
