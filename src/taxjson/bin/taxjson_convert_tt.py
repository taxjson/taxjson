#!/usr/bin/env python3
"""
taxjson_convert_tt.py

Bi-directional conversion between TaxText (.tt) and taxjson (.json).

Usage:
    taxjson-convert-tt input.tt                # → JSON to stdout
    taxjson-convert-tt input.tt out.json       # → JSON to file
    taxjson-convert-tt input.json              # → tt to stdout
    taxjson-convert-tt input.json out.tt       # → tt to file

Direction is inferred from the input file extension. The `.tt` format
is a single space-separated line per transaction; the field order of
each action is in lib/tt_format.py (parse_tt_line / tx_to_tt_line, and
docs/settings.md, ".tt files"), which reads and writes the lines.
"""
from taxjson.lib.stage_msg import emit_line
import argparse
import json
import sys
from pathlib import Path

from taxjson.lib.cli_diag import guard_main
from taxjson.lib.tt_format import json_to_tt_lines, tt_to_json


def _write_atomic(path: Path, text: str) -> None:
    """Write `text` to `path` through a temp file in the same folder:
    the old contents stay until the new ones are complete."""
    from taxjson.lib.cli_diag import write_text_atomic
    write_text_atomic(path, text)


@guard_main("taxjson-convert-tt")
def main():
    try:
        _main()
    except ValueError as exc:
        # Malformed .tt lines raise deliberately-loud ValueErrors;
        # surface them as clean CLI errors, not tracebacks
        # (2026-09 audit).
        emit_line(f"taxjson-convert-tt: error: {exc}", file=sys.stderr)
        sys.exit(1)


def _main():
    parser = argparse.ArgumentParser(
        description="Bi-directional conversion between .tt and .json. "
                    "Direction is inferred from the input file's extension."
    )
    parser.add_argument("input", help="Input file (.tt or .json)")
    parser.add_argument(
        "output",
        nargs="?",
        help="Output file. Omit to write to stdout.",
    )
    parser.add_argument(
        "--account-name",
        metavar="NAME",
        default="default",
        help=(
            "Account label baked into each tx-id hash (tt→json only). "
            "E.g. 'Margin', 'RRSP', 'TFSA'. (default: 'default')"
        ),
    )
    parser.add_argument(
        "--equity",
        action="store_true",
        help=("tt→json: the account is not a crypto account (tjs run "
              "passes it), so a symbol without a market suffix is "
              "warned about; without it, read from the project's "
              "taxjson.toml above the file."),
    )
    parser.add_argument(
        "--date-basis",
        choices=("settle", "trade"),
        default=None,
        help=(
            "json->tt only: which date a .tt line (one date) carries — "
            "the settlement date or the trade date. Default: the "
            "project's tax_date when the input sits in a project's work/ "
            "(Canada: settle, US: trade); outside a project it is "
            "required when a row's trade and settle dates differ."
        ),
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        emit_line(f"taxjson-convert-tt: error: input file not found: {input_path}",
                  file=sys.stderr)
        sys.exit(2)                 # a missing input (A2-0164)

    suffix = input_path.suffix.lower()
    if suffix == '.json':
        # JSON → tt. The one date a .tt line keeps is the tax-year date
        # basis, which is the country's: never a silent Canadian settle
        # default (partition INPUTS-14 — a US book came back with its
        # Dec-31 sales in January).
        date_basis = args.date_basis
        if date_basis is None:
            from taxjson.lib.country import CountryError
            from taxjson.lib.missing_history import tax_date_near
            try:
                date_basis = tax_date_near(input_path)
            except CountryError as e:
                emit_line(f"taxjson-convert-tt: error: taxjson.toml: {e}",
                          file=sys.stderr)
                sys.exit(2)
        if date_basis is None:
            data = json.loads(input_path.read_text(encoding='utf-8'))
            rows = (data.get('transactions', data)
                    if isinstance(data, dict) else data)
            if any(isinstance(t, dict) and t.get('date_settle')
                   and t.get('date') and t['date_settle'] != t['date']
                   for t in rows or []):
                emit_line("taxjson-convert-tt: error: rows have a trade date "
                          "and a different settlement date, and a .tt line "
                          "keeps one: pass --date-basis settle (Canada, CRA) "
                          "or --date-basis trade (US, IRS) — no taxjson.toml "
                          "beside the input to read tax_date from.",
                          file=sys.stderr)
                sys.exit(2)
            date_basis = 'settle'       # every row has one date: moot
        # Every line first, then one write: a row that fails half-way
        # used to leave a partial file — and truncate whatever the
        # output held before (R1-133).
        text = "".join(line + "\n" for line in json_to_tt_lines(
            input_path, date_basis=date_basis))
        if args.output:
            _write_atomic(Path(args.output), text)
        else:
            sys.stdout.write(text)
    else:
        # tt → JSON  (default, also handles unknown extensions)
        result = tt_to_json(input_path, args.account_name,
                            equity=True if args.equity else None)
        if args.output:
            _write_atomic(Path(args.output),
                          json.dumps(result, indent=2, sort_keys=True))
        else:
            json.dump(result, sys.stdout, indent=2, sort_keys=True)


if __name__ == "__main__":
    main()
