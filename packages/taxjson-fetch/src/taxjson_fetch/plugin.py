"""The taxjson-fetch plugin object the core's `taxjson fetch` loads.

Registered in pyproject.toml under the entry-point group
`taxjson.fetchers` (see taxjson.lib.fetchers for the contract). Kept
import-light: the HTTP clients load only when a fetch runs.
"""
import argparse
from typing import Any, Dict


class BrokerFetcher:
    """Questrade REST API and IBKR Flex Web Service."""

    brokerages = ("questrade", "ibkr_flex")
    description = ("Questrade REST API (activity, live positions) and "
                   "IBKR Flex Web Service (statements)")
    # The keys it reads under [accounts.<name>] (the core accepts these
    # three whether or not the plugin is installed).
    account_keys = ("brokerage", "account", "query_id")
    setup_hint = ("Add to taxjson.toml:\n"
                  "  [accounts.margin]\n"
                  "  type = \"taxable\"\n"
                  "  brokerage = \"questrade\"\n"
                  "  account = \"12345678\"\n"
                  "Credentials: $QUESTRADE_REFRESH_TOKEN (first run) / "
                  "$IBKR_FLEX_TOKEN.")

    def add_arguments(self, p: argparse.ArgumentParser) -> None:
        p.add_argument("--year", type=int, default=None, metavar="N",
                       help="Questrade: backfill a PAST tax year — "
                            "fetch its whole window (Dec 1 of N-1 "
                            "through Jan 31 of N+1) into "
                            "questrade_N.csv. Not combinable with "
                            "--from/--days")
        p.add_argument("--days", type=int, default=None, metavar="N",
                       help="Questrade: fetch only the last N days "
                            "(default: the whole tax-year window "
                            "Dec 1 of the prior year to Jan 31 "
                            "of the next; "
                            "trailing 90 days when the config has "
                            "no year)")
        p.add_argument("--from", dest="from_date", default=None,
                       metavar="YYYY-MM-DD",
                       help="Questrade: fetch from this date")
        p.add_argument("--refresh-token", default=None,
                       help="Questrade refresh token (first run; "
                            "rotations are cached in "
                            "~/.questrade_token, shared machine-"
                            "wide)")
        p.add_argument("--flex-token", default=None,
                       help="IBKR Flex Web Service token (else "
                            "$IBKR_FLEX_TOKEN)")
        p.add_argument("--positions", action="store_true",
                       help="Also snapshot LIVE holdings per "
                            "Questrade account into "
                            "work/<account>_live_holdings.toml "
                            "(cross-check with `taxjson sanity`)")
        p.add_argument("--trim-overlap", action="store_true",
                       help="Trim rows inside the fetched window "
                            "from manually exported Questrade CSVs "
                            "in the same folder (originals kept as "
                            ".bak) — the two sources round "
                            "price/gross differently, so their "
                            "copies of the same trade never dedup")

    def fetch(self, request) -> Dict[str, Any]:
        from taxjson_fetch.command import run
        return run(request)
