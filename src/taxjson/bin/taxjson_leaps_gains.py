#!/usr/bin/env python3
"""
taxjson_leaps_gains.py

Summarize LONG option gains (every tenor; `taxjson leaps-sum` is the
LEAPS-only view) by underlying from taxjson_gains.py output.
Ported from tt_leaps_gains.pl. The body is shared with taxjson-ccd-gains
(bin/_option_gains_report.py).
"""

from taxjson.lib.cli_diag import guard_main
from taxjson.bin._option_gains_report import main as _main, process_data as _process

PROG = "taxjson-leaps-gains"


def process_data(data, leaps_by_underlying):
    """LONG option rows of one gains document, grouped by underlying."""
    _process(data, leaps_by_underlying, direction='LONG', calls_only=False)


@guard_main("taxjson-leaps-gains")
def main():
    # Every LONG option close of any tenor — not only LEAPS (a buy placed
    # >3 months to expiry). The old "(LEAPS)" title disagreed with
    # `taxjson leaps-sum` from the same run by 42k (audit R1-173).
    _main(prog=PROG,
          description="Summarize LONG option gains (every tenor).",
          direction='LONG', calls_only=False,
          title="LONG OPTIONS", row_total_label="LONG OPTION GAIN",
          summary_title=("LONG OPTIONS SUMMARY (every long option close, "
                         "any tenor; `taxjson leaps-sum` is the LEAPS-only "
                         "view)"),
          summary_col="LONG_OPT_GAIN")


if __name__ == "__main__":
    main()
