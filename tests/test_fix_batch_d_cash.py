"""Batch D (the FX-on-cash ledger v2, the Kraken cash reader, futures):
2026-10 audit findings 71, 72, 78, 86, 91, 102, 103. All data
synthetic."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from taxjson.lib import cash_events as CE
from taxjson.lib import fx_cash_v2 as V2
from taxjson.lib.brokerages import _crypto_common as CC


def _kraken(text):
    """kraken_cash_events over `text` as a ledger export, on Toronto
    time."""
    from taxjson.lib.brokerages.kraken import kraken_cash_events
    with tempfile.TemporaryDirectory() as tmp, \
            patch.object(CC, "local_tz_name",
                         return_value="America/Toronto"):
        p = Path(tmp) / "kraken_demo.csv"
        p.write_text(text)
        return kraken_cash_events(p, True)


def _bals(evs):
    return {(e["date"], e["currency"]): e["balance"] for e in evs
            if e["kind"] == "CASHBAL"}


_HEAD = "txid,refid,time,type,subtype,aclass,asset,amount,fee,balance\n"


class TestKrakenCashOrder(unittest.TestCase):
    """#71: a day's balance rows listed newest-first took the first row
    of the day as its end (CASHBAL 100, not 120)."""

    def test_same_day_rows_newest_first(self):
        data = ("L2,R2,2025-02-03 16:00:00,deposit,,currency,ZUSD,20,0,"
                "120\n"
                "L1,R1,2025-02-03 15:00:00,deposit,,currency,ZUSD,100,0,"
                "100\n")
        bals = _bals(_kraken(_HEAD + data))
        self.assertEqual(bals[("2024-12-31", "USD")], 0.0)
        self.assertEqual(bals[("2025-02-03", "USD")], 120.0)
        # Oldest-first: the same figures.
        rev = "".join(reversed(data.splitlines(True)))
        self.assertEqual(_bals(_kraken(_HEAD + rev)), bals)


class TestKrakenCashHeader(unittest.TestCase):
    """#72: an uppercased header (which the parser reads) gave no cash
    events; a missing column returned [] instead of refusing."""

    DATA = ("L1,R1,2025-02-03 15:00:00,deposit,,currency,ZUSD,100,0,100\n"
            "L2,R2,2025-03-03 15:00:00,withdrawal,,currency,ZUSD,-40,1,"
            "59\n")

    def test_uppercase_header_reads_the_same(self):
        lower = _kraken(_HEAD + self.DATA)
        upper = _kraken(_HEAD.upper() + self.DATA)
        self.assertTrue(lower)
        self.assertEqual(upper, lower)

    def test_missing_column_is_refused_naming_the_file(self):
        head = "txid,refid,time,type,subtype,aclass,asset,amount,balance\n"
        data = "L1,R1,2025-02-03 15:00:00,deposit,,currency,ZUSD,100,100\n"
        with self.assertRaises(ValueError) as cm:
            _kraken(head + data)
        msg = str(cm.exception)
        self.assertIn("kraken_demo.csv", msg)
        self.assertIn("required column(s) missing: fee", msg)

    def test_trades_export_has_no_cash_events(self):
        head = ("txid,ordertxid,pair,time,type,ordertype,price,cost,fee,"
                "vol\n")
        data = ("T1,O1,XBT/USD,2025-02-03 15:00:00,buy,market,100,100,0,"
                "1\n")
        self.assertEqual(_kraken(head + data), [])


class TestKrakenCashFeeCurrency(unittest.TestCase):
    """#78: a fee in another `feecurrency` was charged to the row's
    asset (FLOW USD -2, opening 1002 instead of EUR -2, opening 1000)."""

    HEAD = ("txid,refid,time,type,subtype,aclass,asset,amount,fee,"
            "feecurrency,balance\n")

    def test_fiat_fee_in_another_currency(self):
        evs = _kraken(self.HEAD + "L1,R1,2025-02-03 15:00:00,withdrawal,,"
                      "currency,ZUSD,-100,2,ZEUR,900\n")
        flows = [(e["currency"], e["amount"]) for e in evs
                 if e["kind"] == "FLOW"]
        self.assertEqual(flows, [("EUR", -2.0)])
        self.assertEqual(_bals(evs)[("2024-12-31", "USD")], 1000.0)

    def test_coin_fee_is_not_a_cash_flow(self):
        evs = _kraken(self.HEAD + "L1,R1,2025-02-03 15:00:00,withdrawal,,"
                      "currency,ZUSD,-100,0.0001,XXBT,900\n")
        self.assertEqual([e for e in evs if e["kind"] == "FLOW"], [])
        self.assertEqual(_bals(evs)[("2024-12-31", "USD")], 1000.0)

    def test_fee_in_the_asset_itself(self):
        evs = _kraken(self.HEAD + "L1,R1,2025-02-03 15:00:00,withdrawal,,"
                      "currency,ZUSD,-100,2,ZUSD,898\n")
        flows = [(e["currency"], e["amount"]) for e in evs
                 if e["kind"] == "FLOW"]
        self.assertEqual(flows, [("USD", -2.0)])
        self.assertEqual(_bals(evs)[("2024-12-31", "USD")], 1000.0)


class TestDedupSettleDisagreement(unittest.TestCase):
    """#86: one conversion in two files with different settlement dates
    was kept once, silently."""

    @staticmethod
    def _conv(file, settle):
        e = CE.conv("2024-12-30", "USD", 100, "CAD", 150, settle=settle,
                    where=file)
        e.update(file=file, book="margin/ib", label="margin")
        return e

    def test_kept_once_with_a_warning(self):
        warnings = []
        out = CE.dedup_across_files(
            [self._conv("a.csv", "2024-12-31"),
             self._conv("b.csv", "2025-01-02")], warnings)
        self.assertEqual(len(out), 1)
        self.assertEqual(len(warnings), 1)
        w = warnings[0]
        for part in ("margin/ib", "a.csv", "b.csv", "2024-12-31",
                     "2025-01-02"):
            self.assertIn(part, w)

    def test_same_settle_no_warning_and_one_file_keeps_two(self):
        warnings = []
        out = CE.dedup_across_files(
            [self._conv("a.csv", "2024-12-31"),
             self._conv("b.csv", "2024-12-31")], warnings)
        self.assertEqual((len(out), warnings), (1, []))
        out = CE.dedup_across_files(
            [self._conv("a.csv", "2024-12-31"),
             self._conv("a.csv", "2025-01-02")], warnings)
        self.assertEqual((len(out), warnings), (2, []))

    def test_build_shows_the_warning(self):
        cash = {"events": [], "lines": [], "problems": [],
                "warnings": ["margin/ib: one FXCONV ..."]}
        doc = V2.build([], cash, "CAD", 2025, lambda c, d: 1.5,
                       country="canada")
        self.assertEqual(doc["warnings"], ["margin/ib: one FXCONV ..."])
        text = V2.render(doc, None, "canada", width_=0)
        self.assertIn("warning: margin/ib: one FXCONV", text)


class TestUnreadableCashFile(unittest.TestCase):
    """#91: an unreadable .tt file was skipped as if empty; the ledger
    then refused for the wrong reason (no opening pool)."""

    def test_permission_denied_is_an_input_problem(self):
        from taxjson.lib import cli_diag
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            f = root / "inputs" / "margin" / "cash.tt"
            f.parent.mkdir(parents=True)
            f.write_text("CASHOPEN 2025-01-01 USD 100 140\n")
            err = PermissionError(13, "Permission denied", str(f))
            with patch.object(cli_diag, "read_text_utf8", side_effect=err):
                lines, problems = CE.read_project_lines(
                    root, {"margin": {"type": "taxable"}})
        self.assertEqual(lines, [])
        self.assertEqual(problems, ["inputs/margin/cash.tt: cannot be "
                                    "read (Permission denied)"])
        doc = V2.build([], {"events": [], "lines": lines,
                            "problems": problems},
                       "CAD", 2025, lambda c, d: 1.5, country="canada")
        self.assertEqual(doc["status"], "not_computed")
        self.assertEqual(doc["problems"][0]["kind"], "input")
        self.assertIn("cash.tt: cannot be read", doc["problems"][0]["text"])

    def test_not_utf8_names_no_full_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            f = root / "inputs" / "margin" / "cash.tt"
            f.parent.mkdir(parents=True)
            f.write_bytes(b"CASHOPEN 2025-01-01 USD 100 140 \xff\n")
            _lines, problems = CE.read_project_lines(
                root, {"margin": {"type": "taxable"}})
        self.assertEqual(len(problems), 1)
        self.assertTrue(problems[0].startswith(
            "inputs/margin/cash.tt: cannot be read (not UTF-8 text"))
        self.assertNotIn(tmp, problems[0])


class TestUsFuturesTradeDateFifo(unittest.TestCase):
    """#102: US FIFO consumed futures lots in settlement order (a later
    trade with an earlier settle date was the "first in")."""

    def test_fifo_by_trade_date(self):
        from taxjson.lib.core import TaxTransaction as T, USATaxRules
        from taxjson.lib.futures import settle_futures
        rows = [T(action="BUYSELL", date=d, date_settle=sd,
                  time="09:30:00", symbol="F:SAMP", quantity=q,
                  currency="USD", net_amount=n, price=n, account="margin")
                for d, sd, q, n in [("2025-01-02", "2025-01-06", 1, 100),
                                    ("2025-01-03", "2025-01-03", 1, 200),
                                    ("2025-01-04", "2025-01-04", -1, 300)]]
        out, _ = settle_futures(rows, "fifo")
        self.assertEqual([t.date_settle for t in out],
                         ["2025-01-06", "2025-01-03", "2025-01-04"])
        self.assertEqual(out[2].net_amount, 200.0)
        r = USATaxRules().compute_gains(out, detect_wash_sales=False)
        self.assertEqual(r["summary"]["total_gain"], 200.0)


class TestFuturesFeeDatingInV2(unittest.TestCase):
    """#103: a futures opening commission left the cash at the close's
    rate (USD gain 100); paid at the opening it is 90, the actual-cash
    reference."""

    B = "margin/ib"

    def _cash(self, extra=()):
        return {"events": [dict(kind="CASHBAL", book=self.B,
                                date="2025-12-31", currency="USD",
                                balance=0, where="synthetic")] +
                list(extra),
                "lines": [dict(kind="CASHOPEN", book=self.B,
                               date="2025-01-01", currency="USD",
                               units=100, cost=100, where="synthetic")],
                "problems": []}

    def _row(self, d, q, n, fee=0.0):
        return dict(action="BUYSELL", date=d, date_settle=d,
                    time="09:30:00", symbol="F:SAMP", quantity=q,
                    currency="USD", price=100 if q > 0 else 200,
                    net_amount=n, fee=fee, account="margin", _book=self.B)

    def test_opening_fee_at_the_opening_rate(self):
        def rate(c, d):
            return 1 if d < "2025-04-01" else 2
        spend = dict(action="BUYSELL", date="2025-05-01",
                     date_settle="2025-05-01", symbol="SAMP.US",
                     quantity=1, currency="USD", net_amount=190,
                     account="margin", _book=self.B)
        rows = [self._row("2025-02-03", 1, 110, 10),
                self._row("2025-04-01", -1, 200), spend]
        doc = V2.build(rows, self._cash(), "CAD", 2025, rate,
                       country="canada")
        self.assertEqual(doc["status"], "computed", doc["problems"])
        self.assertEqual(doc["per_currency"]["USD"]["gain"], 90.0)
        ref = self._cash([dict(kind="FLOW", book=self.B, date=d, settle=d,
                               currency="USD", amount=x, where="synthetic")
                          for d, x in [("2025-02-03", -10),
                                       ("2025-04-01", 100)]])
        ref_doc = V2.build([spend], ref, "CAD", 2025, rate,
                           country="canada")
        self.assertEqual(doc["per_currency"], ref_doc["per_currency"])

    def test_total_cash_unchanged(self):
        from taxjson.bin.taxjson_fx_cash import _cash_legs
        from taxjson.lib.futures import settle_futures_dicts
        # Long 2, add 1, close 1, sell 4 (closes 2, opens a short of
        # 2), cover 2: flat at the end.
        rows = [self._row("2025-02-03", 2, 210, 10),
                self._row("2025-02-04", 1, 103, 3),
                self._row("2025-03-03", -1, 195, 5),
                self._row("2025-04-01", -4, 780, 8),
                self._row("2025-05-01", 2, 330, 6)]
        for method in ("average", "fifo"):
            settled = settle_futures_dicts(rows, method, fee_basis=True)
            self.assertEqual(len(settled), 6)
            legs = sum(a for r in settled for _c, a in _cash_legs(r))
            nets = sum(r["net_amount"] for r in settled)
            self.assertAlmostEqual(legs, nets, places=6)
            # Every part pays its own commission as a leg of its own.
            for r in settled:
                self.assertIn(("USD", -r["fee"]), _cash_legs(r))


if __name__ == "__main__":
    unittest.main()
