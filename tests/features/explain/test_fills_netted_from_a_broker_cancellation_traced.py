"""Backlog TJ-5270: `tjs audit` traces a row the run netted from a parsed
fill and a broker cancellation (`type=trade_cancel`) to the two, and
checks its amount against them.

The run's merge nets a cancellation with the fill it reverses
(taxjson_merge2 `cancel_trade_pairs`): one of part of a fill books the fill
reduced, and a whole one whose commission the broker kept leaves a FEE row
of it; each such row carries the content id the merge gives it, which no
parsed file holds. `tjs audit --details` listed the reduced buy under
"With no parsed row (re-run, not traced)" as "a row of hand.tt that no
parsed file holds now — stale work/ files: re-run `tjs run`", on fresh
books, `pool trades` counted it "with no parsed row", its amount was
never compared, and a sale so reduced was "SOURCE (none found)" with a
warning of "synthesized row, or stale work/ artifacts". Now each is named
as what made it, with the file and line of the fill and of the
cancellation, its amount is compared with the two netted as the run nets
them (a futures fill's settled P/L too), and a book that differs fails.

Synthetic contracts and tickers (F:QWM26.US, QZRX.TO), the test HOME's
made-up rates (USD/CAD 1.35), amounts under 1,000 or the contract's
notional."""
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
from pathlib import Path

import _paths
from tax_rules import rule
from taxjson.rules import ids as R
from taxjson.lib.dispatch import python_module_argv
from taxjson.model.transaction import TaxTransaction

TOML = ('[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\n'
        'country = "canada"\nbase_currency = "CAD"\nsource_currencies = ["USD"]\n'
        '\n[accounts.margin]\ntype = "taxable"\n')
# The entry's repro (the TJ-5193 project): buy 2 contracts, the broker
# cancels 1 of them, the 1 left sold a month later.
FUTURES_TT = ("BUYSELL 2025-04-08 10:00:00 F:QWM26.US 2 USD 50 10000 0 x100\n"
              "BUYSELL 2025-04-08 10:00:00 F:QWM26.US -1 USD 50 5000 0 x100 "
              "type=trade_cancel\n"
              "BUYSELL 2025-05-08 10:00:00 F:QWM26.US -1 USD 60 6000 0 x100\n")
# Shares: a buy of 10 with 4 of it cancelled, a sale of 10 with 4 of it
# cancelled (the sale audited is the reduced one), and a buy cancelled in
# full whose commission the broker kept, rebought the same day.
SHARES_TT = ("BUYSELL 2025-03-03 10:00:00 QZRX.TO 10 CAD 20 200 1\n"
             "BUYSELL 2025-03-03 10:00:00 QZRX.TO -4 CAD 20 80 0 type=trade_cancel\n"
             "BUYSELL 2025-03-04 10:00:00 QZRX.TO 5 CAD 21 105 1\n"
             "BUYSELL 2025-03-04 10:00:00 QZRX.TO -5 CAD 21 105 0 type=trade_cancel\n"
             "BUYSELL 2025-03-04 11:00:00 QZRX.TO 5 CAD 21 105 1\n"
             "BUYSELL 2025-06-02 10:00:00 QZRX.TO -10 CAD 25 250 1\n"
             "BUYSELL 2025-06-02 10:00:00 QZRX.TO 4 CAD 25 100 0 type=trade_cancel\n")
STALE = "no parsed file holds now"
_HEAD = "a fill less the part the broker cancelled (type=trade_cancel), booked by the run"
_KEPT = ("the commission the broker kept on a trade it cancelled in full "
         "(type=trade_cancel), booked by the run")


def _tjs(root: Path, *args: str) -> subprocess.CompletedProcess:
    """`tjs -C root args...` from this tree's sources (width 0)."""
    env = dict(os.environ, PYTHONPATH=str(_paths.SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH="0")
    return subprocess.run(python_module_argv("taxjson.bin.taxjson_run")
                          + ["-C", str(root), *args],
                          capture_output=True, text=True, env=env,
                          stdin=subprocess.DEVNULL, timeout=600)


def _project(tmp: Path, name: str, tt: str) -> Path:
    root = tmp / name
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "taxjson.toml").write_text(TOML, encoding="utf-8")
    (root / "inputs" / "margin" / "hand.tt").write_text(tt, encoding="utf-8")
    r = _tjs(root, "run", "--no-input")
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    return root


def _tamper(root: Path, sym: str, qty: float, by: float) -> None:
    """The base book's row of `sym` and `qty` with its net amount moved."""
    p = root / "work" / "margin_base.json"
    doc = json.loads(p.read_text(encoding="utf-8"))
    rows = [r for r in doc["transactions"]
            if r.get("symbol") == sym and r.get("quantity") == qty]
    assert len(rows) == 1, rows
    rows[0]["net_amount"] = rows[0]["net_amount"] + by
    p.write_text(json.dumps(doc), encoding="utf-8")


class TheProjectsAudit(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.futures = _project(tmp, "futures", FUTURES_TT)
        cls.shares = _project(tmp, "shares", SHARES_TT)
        cls.f_text = _tjs(cls.futures, "audit", "--details")
        cls.f_json = _tjs(cls.futures, "audit", "--json", "--no-trace")
        cls.s_text = _tjs(cls.shares, "audit", "--details")
        cls.s_json = _tjs(cls.shares, "audit", "--json", "--no-trace")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    @rule(R.CA_FX_04)
    def test_the_repro_names_the_reduced_buy_and_checks_its_settled_net(self):
        # It read "2025-04-08 BUY 1 F:QWM26.US — a row of hand.tt that no
        # parsed file holds now — stale work/ files: re-run `tjs run`".
        r = self.f_text
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn(STALE, r.stdout)
        self.assertNotIn("With no parsed row", r.stdout)
        self.assertIn("  pool trades              1 tied, 0 mismatched, 0 with no "
                      "parsed row  ✓", r.stdout)
        self.assertIn("  Netted from a parsed fill and a broker cancellation (checked "
                      "against the two):\n    2025-04-08 BUY 1 F:QWM26.US — " + _HEAD
                      + ": the fill inputs/margin/hand.tt:1, the cancellation "
                      "inputs/margin/hand.tt:2  ✓", r.stdout)
        # The close after it, as TJ-5193 left it.
        self.assertIn("1,000.00 USD × 1.35000 = 1,350.00 CAD  =  base book ✓", r.stdout)

    def test_the_json_lists_the_reduced_fill_with_its_parts(self):
        r = self.f_json
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        pool = json.loads(r.stdout)["pool_trades"]
        self.assertEqual((pool["checked"], pool["tied"], pool["untraced"]), (1, 1, 0))
        self.assertEqual(pool["untraced_rows"], [])
        (red,) = pool["reduced_rows"]
        self.assertEqual((red["date"], red["quantity"], red["ties"]),
                         ("2025-04-08", 1.0, True))
        self.assertTrue(red["origin"].startswith(_HEAD))
        self.assertEqual(red["parts"], [
            {"source": "margin_tt_hand.json (hand.tt)", "role": "fill", "line": 1},
            {"source": "margin_tt_hand.json (hand.tt)", "role": "cancellation",
             "line": 2}])

    def test_a_reduced_sale_is_traced_to_its_fill_and_cancellation(self):
        r = self.s_text
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("  SOURCE       " + _HEAD + "\n"
                      "               the fill: inputs/margin/hand.tt\n"
                      "               2025-06-02 SELL 10 QZRX.TO @ 25.00 — net 250.00 CAD, "
                      "fee 1.00  [line 6]\n"
                      "               the cancellation: inputs/margin/hand.tt\n"
                      "               2025-06-02 BUY 4 QZRX.TO @ 25.00 — net 100.00 CAD  "
                      "[line 7]\n"
                      "  CURRENCY     native CAD — no conversion\n"
                      "               150.00 CAD  =  base book ✓", r.stdout)
        self.assertNotIn("none found", r.stdout)
        self.assertNotIn("stale work/ artifacts", r.stdout)
        self.assertIn("  source rows              1/1 traced to a parsed broker row  ✓",
                      r.stdout)
        ev = json.loads(self.s_json.stdout)["events"][0]
        self.assertEqual([(s["role"], s["row"]["source_line"]) for s in ev["sources"]],
                         [("fill", 6), ("cancellation", 7)])
        self.assertEqual(ev["origin"], _HEAD + ": the fill inputs/margin/hand.tt:6, "
                         "the cancellation inputs/margin/hand.tt:7")
        self.assertEqual(ev["warnings"], [])

    def test_a_kept_commission_is_named_and_checked(self):
        # The whole cancellation of the 03-04 buy whose 1.00 commission the
        # broker kept: a FEE row the run booked, read as stale work/ files.
        r = self.s_text
        self.assertNotIn(STALE, r.stdout)
        self.assertIn("    2025-03-03 BUY 6 QZRX.TO — " + _HEAD + ": the fill "
                      "inputs/margin/hand.tt:1, the cancellation inputs/margin/"
                      "hand.tt:2  ✓", r.stdout)
        self.assertIn("    2025-03-04 FEE QZRX.TO 1.00 — " + _KEPT + ": the trade "
                      "inputs/margin/hand.tt:3, the cancellation inputs/margin/"
                      "hand.tt:4  ✓", r.stdout)
        doc = json.loads(self.s_json.stdout)
        other = doc["pool_other_rows"]
        self.assertEqual((other["checked"], other["tied"], other["untraced"]), (1, 1, 0))
        (fee,) = other["rows"]
        self.assertEqual((fee["action"], fee["amount"], fee["ties"]), ("FEE", 1.0, True))
        self.assertEqual([p["line"] for p in fee["parts"]], [3, 4])
        self.assertEqual(doc["pool_trades"]["tied"], 2)     # the reduced and the rebuy

    def test_a_reduced_fill_the_books_hold_otherwise_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            shutil.copytree(self.shares, root)
            _tamper(root, "QZRX.TO", 6.0, 1.0)
            r = _tjs(root, "audit", "--details", "--no-trace")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("  pool trades              1 tied, 1 mismatched, 0 with no "
                      "parsed row  ✗", r.stdout)
        self.assertIn("AMOUNT cross-check FAILED: the netting of the parsed fill and "
                      "its broker cancellation carries 120.00 CAD but the base book row "
                      "carries 121.00 CAD", r.stdout)


# ------------------------------------------------------------------ in process

SYM = "F:QWM26.US"
RATE = 1.35


def _fut(d, qty, price, net, **kw):
    return TaxTransaction(action="BUYSELL", date=d, date_settle=d, time="10:00:00",
                          symbol=SYM, quantity=qty, currency="USD", price=price,
                          fee=0.0, net_amount=net, gross_amount=abs(net),
                          account="margin", multiplier=100.0, **kw)


def _books(raw):
    """The base book the run makes of `raw` (the merge's netting, then the
    conversion's futures settlement at RATE)."""
    from taxjson.bin.taxjson_convert_currency import (process_transactions,
                                                      reset_fallback_tally)
    from taxjson.bin.taxjson_merge2 import cancel_trade_pairs
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        rows = cancel_trade_pairs(list(raw))
    reset_fallback_tally()
    rates = {"USD": {d: Decimal(str(RATE)) for d in ("2025-04-08", "2025-05-08")}}
    return process_transactions(rows, "CAD", rates, Decimal(str(RATE)),
                                country="canada")


def _write(p: Path, rows, **meta) -> Path:
    p.write_text(json.dumps({"transactions": rows, "metadata": meta}),
                 encoding="utf-8")
    return p


def _audit(tmp: Path, raw, base, *, tamper=None):
    """taxjson-audit in process on a parsed file of `raw` and a base book of
    `base` (`tamper(rows)` edits the base rows first): (rc, stdout)."""
    from taxjson.bin.taxjson_audit import main as audit_main
    from taxjson.engine.by_country import get_tax_rules
    from taxjson.lib.tx_load import load_transactions
    rows = [r.to_dict() for r in base]
    if tamper:
        tamper(rows)
    src = _write(tmp / "src.json", [r.to_dict() for r in raw], input_files=["hand.tt"])
    b = _write(tmp / "base.json", rows)
    gains = tmp / "gains.json"
    res = get_tax_rules("canada").compute_gains(load_transactions(b))
    gains.write_text(json.dumps({"transactions": res["transactions"]}, default=str),
                     encoding="utf-8")
    rates = tmp / "rates.csv"
    rates.write_text("\n".join(f"2025-{m:02d}-{d:02d} 12:00:00 USD CAD {RATE}"
                               for m in (4, 5) for d in range(1, 31)), encoding="utf-8")
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(io.StringIO()):
        rc = audit_main(["--country", "canada", "--year", "2025", "--base", str(b),
                         "--rates", str(rates), "--base-currency", "CAD",
                         "--source", str(src), "--check", str(gains), "--no-trace"])
    return rc, out.getvalue()


class AReducedFuturesClose(unittest.TestCase):
    """Sell 2 to close, the broker cancels 1 of the sale: the close of 1 the
    books hold is the reduced fill, its settled P/L re-derived from the
    netted pair (it was "SOURCE (none found)", the P/L not compared)."""

    RAW = [_fut("2025-04-08", 1.0, 50.0, 5000.0),
           _fut("2025-05-08", -2.0, 60.0, 12000.0),
           _fut("2025-05-08", 1.0, 60.0, 6000.0, type="trade_cancel")]

    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.tmp = Path(t.name)

    @rule(R.CA_FX_04)
    def test_its_settled_pl_is_compared_with_the_netted_pair(self):
        rc, out = _audit(self.tmp, self.RAW, _books(self.RAW))
        self.assertEqual(rc, 0, out)
        self.assertIn("  SOURCE       " + _HEAD, out)
        self.assertIn("1,000.00 USD × 1.35000 = 1,350.00 CAD  =  base book ✓", out)
        self.assertIn("  source rows              1/1 traced", out)

    @rule(R.CA_FX_04)
    def test_a_settled_pl_the_books_hold_otherwise_fails(self):
        def _more(rows):
            (close,) = [r for r in rows if r["quantity"] == -1.0]
            close["net_amount"] = close["net_amount"] + 13.5
        rc, out = _audit(self.tmp, self.RAW, _books(self.RAW), tamper=_more)
        self.assertEqual(rc, 1, out)
        self.assertIn("FX cross-check FAILED: 1,000.00 USD x 1.35 = 1,350.00 but the "
                      "base book row carries 1,363.50 CAD.", out)


class TheNetting(unittest.TestCase):
    """lib/trade_cancel `rows_made_by_cancellations`: the rows the netting
    makes, each with the fill and the cancellations it was made from, under
    the id the merge gives it."""

    def _rows(self, *specs):
        return [TaxTransaction(action="BUYSELL", date="2025-03-03", time="10:00:00",
                               symbol="QZRX.TO", quantity=q, currency="CAD", price=20.0,
                               net_amount=abs(q) * 20.0, fee=f, account="margin", type=t)
                for q, f, t in specs]

    def test_two_partial_cancellations_name_the_order_and_both(self):
        from taxjson.bin.taxjson_merge2 import cancel_trade_pairs
        from taxjson.lib.trade_cancel import REDUCED_FILL, rows_made_by_cancellations
        rows = self._rows((10.0, 1.0, ""), (-2.0, 0.0, "trade_cancel"),
                          (-3.0, 0.0, "trade_cancel"))
        (made,) = rows_made_by_cancellations(rows)
        self.assertEqual(made.kind, REDUCED_FILL)
        self.assertIs(made.fill, rows[0])
        self.assertEqual(made.cancellations, (rows[1], rows[2]))
        self.assertEqual(made.row.quantity, 5.0)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            (booked,) = cancel_trade_pairs(list(rows))
        self.assertEqual(made.row.id, booked.id)

    def test_the_rest_cancelled_with_its_commission_kept(self):
        from taxjson.bin.taxjson_merge2 import cancel_trade_pairs
        from taxjson.lib.trade_cancel import KEPT_COMMISSION, rows_made_by_cancellations
        rows = self._rows((10.0, 1.0, ""), (-4.0, 0.0, "trade_cancel"),
                          (-6.0, 0.0, "trade_cancel"))
        (made,) = rows_made_by_cancellations(rows)
        self.assertEqual(made.kind, KEPT_COMMISSION)
        self.assertEqual((made.row.action, made.row.net_amount), ("FEE", 1.0))
        self.assertIs(made.fill, rows[0])
        self.assertEqual(made.cancellations, (rows[1], rows[2]))
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            (booked,) = cancel_trade_pairs(list(rows))
        self.assertEqual(made.row.id, booked.id)

    def test_a_whole_cancellation_refunded_makes_nothing(self):
        # The pair is dropped, no row is made.
        from taxjson.lib.trade_cancel import rows_made_by_cancellations
        rows = self._rows((10.0, 0.0, ""), (-10.0, 0.0, "trade_cancel"))
        self.assertEqual(rows_made_by_cancellations(rows), [])


class Controls(unittest.TestCase):
    """What must not change: a row no parsed file holds and the run did not
    net is still named stale, a book with no cancellation lists no reduced
    rows, and a whole cancellation refunded with a rebuy audits as before."""

    def test_a_row_no_parsed_file_holds_is_still_named_stale(self):
        from taxjson.bin.taxjson_audit import row_origin
        row = {"id": "q1", "action": "BUYSELL", "source": "hand.tt"}
        self.assertEqual(row_origin(row),
                         "a row of hand.tt that no parsed file holds now — stale "
                         "work/ files: re-run `tjs run`")

    def test_books_with_no_cancellation_list_no_reduced_rows(self):
        # The --json of a book without one is as before (no new key).
        from taxjson.bin.taxjson_audit import pool_trade_check
        raw = [_fut("2025-04-08", 1.0, 50.0, 5000.0)]
        idx = {r.id: r.to_dict() for r in raw}
        src = {r.id: [{"label": "hand.tt", "row": r.to_dict()}] for r in raw}
        events = [{"id": "x", "symbol": SYM, "date": "2025-05-08"}]
        out = pool_trade_check(events, idx, src, base_currency="CAD",
                               fx_history={}, default_rate=None)
        self.assertEqual(sorted(out), ["checked", "mismatched", "tied", "untraced",
                                       "untraced_rows"])

    def test_a_whole_cancellation_refunded_and_a_rebuy_audits_as_before(self):
        def _sh(d, t, q, net, **kw):
            return TaxTransaction(action="BUYSELL", date=d, date_settle=d, time=t,
                                  symbol="QZRX.TO", quantity=q, currency="CAD",
                                  price=abs(net / q), net_amount=net, account="margin",
                                  **kw)
        raw = [_sh("2025-04-08", "10:00:00", 10.0, 200.0),
               _sh("2025-04-08", "10:00:00", -10.0, 200.0, type="trade_cancel"),
               _sh("2025-04-08", "11:00:00", 10.0, 200.0),
               _sh("2025-05-08", "10:00:00", -10.0, 250.0)]
        with tempfile.TemporaryDirectory() as td:
            rc, out = _audit(Path(td), raw, _books(raw))
        self.assertEqual(rc, 0, out)
        self.assertIn("  pool trades              1 tied, 0 mismatched, 0 with no "
                      "parsed row  ✓", out)
        self.assertIn("  source rows              1/1 traced", out)
        self.assertNotIn("Netted from", out)


if __name__ == "__main__":
    unittest.main()
