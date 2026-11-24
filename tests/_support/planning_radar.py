"""Shared test helpers: the wash radar and the planning checks.

Shared from: test_fix_backlog_radar
"""
import contextlib
import io
import json
import sys
import tempfile
from argparse import Namespace
from pathlib import Path

from _run_patch import patch_run
from _support.planning_radar_row import base_rows


def _row(d, sym, qty, px, acct="margin", settle=None, currency="CAD"):
    return {"action": "BUYSELL", "date": d, "date_settle": settle or d,
            "time": "10:00:00", "symbol": sym, "quantity": qty,
            "price": px, "net_amount": -qty * px, "currency": currency,
            "account": acct, "commission": 0.0,
            "id": f"{acct}-{sym}-{d}-{qty}"}


def _radar_main(country, taxable, sheltered=None, as_of="2026-10-25",
                extra=()):
    """The radar run in process: (stdout, the --json document or None)."""
    from taxjson.bin import taxjson_wash_radar as R
    with tempfile.TemporaryDirectory() as d:
        tp = Path(d) / "margin_base.json"
        tp.write_text(json.dumps({"transactions": base_rows(taxable, country)}))
        argv = ["taxjson-wash-radar", "--country", country, "--date",
                as_of, "--taxable", str(tp), *extra]
        if sheltered:
            sp = Path(d) / "sheltered_base.json"
            sp.write_text(json.dumps({"transactions": base_rows(sheltered,
                                                                country)}))
            argv += ["--sheltered", str(sp)]
        out, old = io.StringIO(), sys.argv
        sys.argv = argv
        try:
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(io.StringIO()):
                R.main()
        finally:
            sys.argv = old
    return out.getvalue()


def _radar(country, taxable, sheltered=None, as_of="2026-10-25",
           cleared=7):
    """The radar's --json document, flattened per ticker."""
    from taxjson.bin.taxjson_watch import flatten_radar
    return flatten_radar(json.loads(_radar_main(
        country, taxable, sheltered, as_of,
        ("--all", "--json", "--cleared-days", str(cleared)))))


def _check(cmd_name, radar, symbols, usa=False, last_loss=None,
           details=False):
    """buy-check / sell-check against a radar built above."""
    from taxjson.cli import plan
    from taxjson.lib.option_symbols import is_option_symbol, parse_option_underlying

    def canon(t):
        t = t.strip().upper()
        if is_option_symbol(t):
            return parse_option_underlying(t) or t
        return t
    buf = io.StringIO()
    code = 0
    with patch_run("_wash_class_context",
                      return_value=(radar, canon, last_loss or {})), \
            patch_run("_radar_country_is_usa", return_value=usa):
        try:
            with contextlib.redirect_stdout(buf):
                getattr(plan, cmd_name)(Namespace(
                    dir=".", symbol=list(symbols), json=False,
                    details=details))
        except SystemExit as e:
            code = e.code or 0
    return buf.getvalue(), code


def _rebuy(radar, as_of, country="canada"):
    from taxjson.lib import rebuy as RB
    from taxjson.lib.ticker_map import underlying_of
    return RB.build(radar, as_of, country,
                    underlying=lambda s: underlying_of(s, {}))
