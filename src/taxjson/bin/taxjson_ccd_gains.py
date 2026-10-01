#!/usr/bin/env python3
"""
taxjson_ccd_gains.py

Summarize Covered Call (Short Option) gains by underlying from taxjson_gains.py output.
Ported from tt_ccd_gains.pl. The body is shared with taxjson-leaps-gains
(bin/_option_gains_report.py).
"""

from taxjson.lib.cli_diag import guard_main
from taxjson.bin._option_gains_report import main as _main, process_data as _process

PROG = "taxjson-ccd-gains"


def process_data(data, ccd_by_underlying):
    """SHORT call rows of one gains document, grouped by underlying."""
    _process(data, ccd_by_underlying, direction='SHORT', calls_only=True)


@guard_main("taxjson-ccd-gains")
def main():
    _main(prog=PROG,
          description="Summarize Covered Call (Short Option) gains.",
          direction='SHORT', calls_only=True,
          title="COVERED CALLS", row_total_label="COVERED CALL GAIN",
          summary_title="COVERED CALLS SUMMARY", summary_col="CCD_GAIN")


if __name__ == "__main__":
    main()
