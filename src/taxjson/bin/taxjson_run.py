#!/usr/bin/env python3
"""taxjson — one-command orchestrator for the full tax pipeline.

Replaces the per-account shell scripts (curr.sh, sheltered.sh, margin.sh,
crypto.sh, export.sh, reports.sh) with a single command that reads a
short TOML config and walks a conventional inputs/ tree.

Directory layout:
    taxjson.toml          # config (year, country, base_currency, accounts)
    inputs/
      margin/             # account name = folder name
        *.csv             # broker auto-detected by CSV content
        *.tt              # optional starting-position files
        elections.json    # corp-actions election manifest (created by the
                          # pipeline on first election; COMMIT this file —
                          # elections are decisions, not rebuildable data)
      rrsp/  tfsa/  ...
    ticker.map            # optional — symbol rules (GLOBAL/TOBASE/JOURNAL/DELETE/
                          # DISTINCT/RENAME), lookups (QUOTE/CRYPTO/
                          # EXTRACT/T1135) and market-list entries
                          # (STABLE/SPLITSHARE/INDEXOPT/SECFUT/EVENING/
                          # MULT/VENUE)
    reports/              # all outputs land here, overwritten on re-run
    work/                 # intermediate JSON (rebuilt by every run)

Subcommands:
    tjs run                       # full pipeline, FULL rebuild (every
                                      # run — never serves stale/cached data)
    tjs run --account margin      # one account
    tjs sum                       # cross-account realized-gains summary
    tjs init [DIR] --country ca   # scaffold a new project directory
    tjs help [COMMAND]            # every other subcommand

The code is in taxjson.cli (the commands, the parser, the dispatcher) and
taxjson.pipeline (the run). This module is the entry point: `main`, the two
names taxjson_missing_history imports from here, and the two a published
taxjson-fetch imports. taxjson.lib imports nothing from here (step 8: the
checklist's steps get the run's readers from cli, lib/checklist.RunHelpers).
"""

from taxjson.cli.main import main
from taxjson.pipeline.config import input_files
from taxjson.pipeline.inputs import detect_broker
from taxjson.pipeline.stages import in_kind_taxable_legs, own_move_plan

__all__ = (
    "detect_broker", "in_kind_taxable_legs", "input_files", "main",
    "own_move_plan",
)

if __name__ == "__main__":
    main()
