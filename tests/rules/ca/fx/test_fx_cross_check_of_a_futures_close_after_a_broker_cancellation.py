"""Backlog TJ-5193: `tjs audit` re-derives a futures close's settled P/L
from the rows the run settled, a broker cancellation applied.

The run nets a `type=trade_cancel` row with the fill it reverses in its
merge (taxjson_merge2 `cancel_trade_pairs`, before the ticker map): a
whole one drops the pair, one of part of a fill books the fill reduced,
under a new id. The audit's re-settlement read only the parsed rows the
books hold by id, so after a partial cancellation the reduced buy (a new
id, no parsed row) was missing, the later sale had nothing to close, and
its P/L read 0: "FX cross-check FAILED: 0.00 USD x 1.35 = 0.00 but the
base book row carries 1350.00 CAD" (with separators), exit 1, on fresh books. The audit
now nets the cancellations of the settled symbols' parsed rows as the run
does, then settles: 1000.00 USD x 1.35 = 1350.00 CAD, exit 0. A
cancellation it cannot place (the books keep it as a trade of its own, or
drop one it finds no fill for) fails the account's settled rows of that
symbol, naming it.

The books here are built by the run's own steps (the merge's netting and
the conversion's settlement) from hand-written rows; the audit reads them
in process. Synthetic contracts (F:QWM26.US) and rates only."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R
from taxjson.model.transaction import TaxTransaction
from _support.audit_project import _write

SYM = "F:QWM26.US"
RATE = 1.35


def _fut(d, qty, price, net, **kw):
    """A fill of SYM (x100: the notional is the net), the buys written
    positive as the .tt and the IB parser write them."""
    return TaxTransaction(action="BUYSELL", date=d, date_settle=d, time="10:00:00",
                          symbol=SYM, quantity=qty, currency="USD", price=price,
                          fee=0.0, net_amount=net, gross_amount=abs(net),
                          account="margin", **kw)


BUY2 = ("2025-04-08", 2.0, 50.0, 10000.0)
CANCEL1 = ("2025-04-08", -1.0, 50.0, 5000.0)
SELL1 = ("2025-05-08", -1.0, 60.0, 6000.0)


def _books(raw, *, net=True):
    """The base book the run makes of `raw`: the merge's cancellation
    netting (unless `net` is False: books that kept the cancellation as a
    trade), then the conversion's futures settlement, at RATE."""
    from taxjson.bin.taxjson_convert_currency import (
        process_transactions, reset_fallback_tally)
    from taxjson.bin.taxjson_merge2 import cancel_trade_pairs
    rows = list(raw)
    if net:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rows = cancel_trade_pairs(rows)
    reset_fallback_tally()
    rates = {"USD": {d: Decimal(str(RATE)) for d in ("2025-04-08", "2025-04-09",
                                                    "2025-05-08")}}
    return process_transactions(rows, "CAD", rates, Decimal(str(RATE)),
                                country="canada")


def _indexes(raw, base):
    source_index = {r.id: [{"label": "hand.tt", "row": r.to_dict()}] for r in raw}
    return source_index, {r.id: r.to_dict() for r in base}


class _Audit:
    """The audit, in process, on a source file of `raw`, a base book of
    `base` and the engine's gains of it."""

    def __init__(self, tmp: Path, raw, base):
        self.source = _write(tmp, "src.json", [r.to_dict() for r in raw])
        self.base = _write(tmp, "base.json", [r.to_dict() for r in base])
        self.rates = tmp / "rates.csv"
        d, lines = date(2025, 1, 1), []
        while d < date(2025, 7, 1):
            lines.append(f"{d.isoformat()} 12:00:00 USD CAD {RATE}")
            d += timedelta(days=1)
        self.rates.write_text("\n".join(lines), encoding="utf-8")
        from taxjson.engine.by_country import get_tax_rules
        from taxjson.lib.tx_load import load_transactions
        res = get_tax_rules("canada").compute_gains(load_transactions(self.base))
        self.gains = tmp / "gains.json"
        self.gains.write_text(json.dumps({"transactions": res["transactions"]},
                                         default=str), encoding="utf-8")

    def run(self, *extra):
        from taxjson.bin.taxjson_audit import main as audit_main
        argv = ["--country", "canada", "--year", "2025", "--base", str(self.base),
                "--rates", str(self.rates), "--base-currency", "CAD",
                "--source", str(self.source), "--check", str(self.gains), *extra]
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = audit_main(argv)
        return rc, out.getvalue(), err.getvalue()


class FuturesNetsAfterACancellation(unittest.TestCase):

    @rule(R.CA_FX_04)
    def test_a_partial_cancellation_is_netted_before_the_close_is_settled(self):
        # The entry's repro: buy 2, cancel 1 of them, sell the 1 left at
        # 60 (P/L 1000.00 USD). It read 0.
        from taxjson.bin.taxjson_audit import futures_native_nets
        raw = [_fut(*BUY2), _fut(*CANCEL1, type="trade_cancel"), _fut(*SELL1)]
        base = _books(raw)
        src, idx = _indexes(raw, base)
        self.assertEqual(idx[raw[2].id]["type"], "futures_settlement")
        self.assertAlmostEqual(idx[raw[2].id]["net_amount"], 1000.0 * RATE, places=6)
        nets = futures_native_nets(src, idx, "canada")
        self.assertAlmostEqual(nets[raw[2].id], 1000.0, places=6)
        self.assertEqual(nets.unplaced, {})

    @rule(R.CA_FX_04)
    def test_a_whole_cancellation_and_a_rebuy_is_as_before(self):
        # Control: the pair dropped whole; the rebuy and the sale are the
        # books' own rows, read as before.
        from taxjson.bin.taxjson_audit import futures_native_nets
        raw = [_fut("2025-04-08", 2.0, 50.0, 10000.0),
               _fut("2025-04-08", -2.0, 50.0, 10000.0, type="trade_cancel"),
               _fut("2025-04-09", 1.0, 50.0, 5000.0), _fut(*SELL1)]
        nets = futures_native_nets(*_indexes(raw, _books(raw)), "canada")
        self.assertAlmostEqual(nets[raw[3].id], 1000.0, places=6)
        self.assertEqual(getattr(nets, "unplaced", {}), {})

    def test_a_cancellation_the_books_keep_as_a_trade_is_named(self):
        # Books that kept the cancellation as a selling fill of its own:
        # the audit nets it and cannot re-derive what the books settled.
        from taxjson.bin.taxjson_audit import futures_native_nets
        raw = [_fut(*BUY2), _fut(*CANCEL1, type="trade_cancel"), _fut(*SELL1)]
        nets = futures_native_nets(*_indexes(raw, _books(raw, net=False)), "canada")
        why = nets.unplaced[raw[2].id]
        self.assertIn("the broker cancellation (type=trade_cancel) of -1 F:QWM26.US "
                      "@ 50 on 2025-04-08 (hand.tt) is booked as a trade of its own, "
                      "though the parsed rows hold the fill it reverses", why)

    def test_a_cancellation_whose_fill_no_parsed_row_holds_is_named(self):
        # The books netted it; the parsed rows the audit reads lack the
        # fill (another statement not given to the audit).
        from taxjson.bin.taxjson_audit import futures_native_nets
        raw = [_fut(*BUY2), _fut(*CANCEL1, type="trade_cancel"), _fut(*SELL1)]
        src, idx = _indexes(raw, _books(raw))
        del src[raw[0].id]
        nets = futures_native_nets(src, idx, "canada")
        self.assertIn("is left out of the books, but none of the parsed rows is "
                      "the fill it reverses", nets.unplaced[raw[2].id])


class AuditOnACancelledFuturesFill(unittest.TestCase):

    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.tmp = Path(t.name)

    @rule(R.CA_FX_04)
    def test_the_repro_ties(self):
        raw = [_fut(*BUY2), _fut(*CANCEL1, type="trade_cancel"), _fut(*SELL1)]
        rc, out, err = _Audit(self.tmp, raw, _books(raw)).run()
        self.assertEqual(rc, 0, out + err)
        self.assertIn("1,000.00 USD × 1.35000 = 1,350.00 CAD  =  base book ✓", out)
        self.assertNotIn("FAILED", out + err)

    def test_a_cancellation_it_cannot_place_fails_naming_it(self):
        raw = [_fut(*BUY2), _fut(*CANCEL1, type="trade_cancel"), _fut(*SELL1)]
        rc, out, err = _Audit(self.tmp, raw, _books(raw, net=False)).run()
        self.assertEqual(rc, 1, out + err)
        self.assertIn("FAILED: FX cross-check FAILED: the settled P/L cannot be "
                      "re-derived: the broker cancellation (type=trade_cancel) of "
                      "-1 F:QWM26.US @ 50 on 2025-04-08", out)

    def test_a_round_trip_with_no_cancellation_ties_as_before(self):
        # Control.
        raw = [_fut("2025-04-08", 1.0, 50.0, 5000.0), _fut(*SELL1)]
        rc, out, err = _Audit(self.tmp, raw, _books(raw)).run()
        self.assertEqual(rc, 0, out + err)
        self.assertIn("1,000.00 USD × 1.35000 = 1,350.00 CAD  =  base book ✓", out)


if __name__ == "__main__":
    unittest.main()
