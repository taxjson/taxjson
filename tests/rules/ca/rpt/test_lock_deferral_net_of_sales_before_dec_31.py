"""The tjs command audit of 2026-10-27, year-end records group: close-year,
handoff, carryover and check-filed agree with the run.

- TJ-0296 US `carryover` reads the filed locks before its gains run: a
  wash-sale basis add whose replacement was sold in a locked year is
  booked in the loss's year, as the pipeline books it (US-WASH-22);
- TJ-0323 US `carryover` names a crypto account's unknown-cost sales
  (warning, --json `excluded_unknown_cost` / `incomplete_years`) and
  counts its rows in `first_transaction_year`;
- TJ-0297 a US crypto account's close-year record (handoff.snapshot) is
  computed without the wash-sale rule, as the run computes it;
- TJ-0285 (TJ-0324) Canada: the record's Dec 31 `deferred` is what the
  sales before Dec 31 left of each superficial-loss addition;
- TJ-0298 close-year refuses a --filed-dispositions row of another year,
  and a replaced lock's kept row of another year, before any write;
- TJ-0274 (TJ-0336) a lock's `return_row` is checked for its shape like
  every other locked figure; check-filed's OK says it is not recomputed.

Synthetic data only (made-up tickers and accounts, amounts under a
thousand).

Formerly: test_fix_1027_yearend
"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R

from taxjson.model.transaction import TaxTransaction
from taxjson.project.settings import Settings


def _tx(acct, date, qty, net, action="BUYSELL", sym="ZZQ.US",
        currency="USD"):
    return TaxTransaction(action=action, date=date, date_settle=date,
                          symbol=sym, quantity=qty,
                          price=abs(net / qty) if qty else 0.0,
                          net_amount=net, currency=currency, account=acct)


def _book(path: Path, rows) -> Path:
    path.write_text(json.dumps(
        {"transactions": [r.to_dict() for r in rows]}))
    return path


# ------------------------------------------------- snapshots (handoff)
def _snapshot(rows, country, crypto, acct):
    """handoff.snapshot of one taxable account at 2025-12-31, as
    close-year records it."""
    from taxjson.cli import file as cli_file
    from taxjson.pipeline import run as pipeline_run
    from taxjson.lib import handoff
    us = country == "usa"
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        (p / "work").mkdir()
        cfg = {"settings": {"year": 2025, "country": country,
                            "base_currency": "USD" if us else "CAD",
                            "tax_date": "trade" if us else "settle"},
               "accounts": {acct: {"type": "taxable", "crypto": crypto}}}
        _book(p / "work" / f"{acct}_base.json", rows)
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            return handoff.snapshot(p / "work", cfg, "2025-12-31",
                                    pipeline_run._filed_run_gains,
                                    cli_file._handoff_gains_flags(Settings.from_table(cfg["settings"])))


@rule(R.CA_RPT_08)
class TestCaDeferredAtYearEnd(unittest.TestCase):
    """TJ-0285 / TJ-0324: the record's `deferred` summed every addition
    placed by Dec 31; a sale before Dec 31 that recovered part of it did
    not reduce it (250.00 for the 100.00 the engine's pool still holds).

    Formerly: test_fix_1027_yearend.TestCaDeferredAtYearEnd"""

    def test_deferred_is_what_the_later_sale_left(self):
        rows = [_tx("margin", "2025-03-10", 10, 1000, sym="ZZQ.US",
                    currency="CAD"),
                _tx("margin", "2025-04-01", -5, 250, sym="ZZQ.US",
                    currency="CAD"),
                _tx("margin", "2025-10-01", -3, 300, sym="ZZQ.US",
                    currency="CAD")]
        snap = _snapshot(rows, "canada", False, "margin")
        # 5 held at day 30 deny the 250.00 loss; 3 of those 5 sold in
        # October take 3/5 of it: 2 units, ACB 300.00, 100.00 deferred.
        self.assertEqual(snap["equity"]["ZZQ.US"],
                         {"qty": 2.0, "acb": 300.0, "deferred": 100.0})

    def test_engine_year_end_agrees(self):
        """The same figures as the engine's own year-end pools."""
        from taxjson.lib.pipeline import GainsRequest, run_gains
        rows = [_tx("margin", "2025-03-10", 10, 1000, sym="ZZQ.US",
                    currency="CAD"),
                _tx("margin", "2025-04-01", -5, 250, sym="ZZQ.US",
                    currency="CAD"),
                _tx("margin", "2025-10-01", -3, 300, sym="ZZQ.US",
                    currency="CAD")]
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            res = run_gains(list(rows), [], [], req=GainsRequest(
                country="canada", year=2025, taxable=True))
        (h,) = res["inventory_year_end"]
        snap = _snapshot(rows, "canada", False, "margin")["equity"]["ZZQ.US"]
        self.assertAlmostEqual(snap["acb"], h["total_cost"], places=2)
        self.assertAlmostEqual(snap["deferred"], h["deferred_wash"],
                               places=2)


if __name__ == "__main__":
    unittest.main()
