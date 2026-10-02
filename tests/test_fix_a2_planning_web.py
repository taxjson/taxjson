"""Re-audit-2 web findings (planning area: what-if, wash-radar page, serve).

  A2-0131 / A2-0384  what-if contract size: the size the book's rows
                     declare (x10 mini options, futures); plain futures
                     are refused, never priced at x1
  A2-0132            a plain future is never dated as an equity T+1 sale
  A2-0375 / A2-0437 / A2-1189
                     the simulated sale settles on the LISTING's market,
                     not the calendar of the typed price currency
  A2-0383            a sheltered account with inputs but no base book is
                     named in the warnings, never silently dropped
  A2-1175            Canada: a trust ROC with a record date is booked on
                     it before the simulated sale (as run_gains does)
  A2-0687            the engine's option-replacement flag reaches the
                     what-if warnings (US-WASH-12 note)
"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule, rule_absent


def _row(symbol, qty, net, date_, settle=None, acct="margin", price=None,
         currency="CAD", **kw):
    r = {"action": "BUYSELL", "date": date_,
         "date_settle": settle or date_, "symbol": symbol,
         "quantity": qty,
         "price": price if price is not None else abs(net / qty),
         "net_amount": net, "currency": currency, "account": acct}
    r.update(kw)
    return r


def _project(tmp, books, *, accounts=None, country="canada", year=2026,
             base="CAD", inputs=None):
    root = Path(tmp)
    (root / "work").mkdir()
    (root / "reports").mkdir()
    accounts = accounts or {"margin": 'type = "taxable"\n'}
    cfg = (f'[settings]\nyear = {year}\ncountry = "{country}"\n'
           f'base_currency = "{base}"\n')
    for name, body in accounts.items():
        cfg += f"[accounts.{name}]\n{body}"
    (root / "taxjson.toml").write_text(cfg)
    for name, rows in books.items():
        (root / "work" / f"{name}_base.json").write_text(
            json.dumps({"transactions": rows}))
    for rel, text in (inputs or {}).items():
        p = root / "inputs" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


def _ctx(root):
    from taxjson.web.context import ProjectContext
    return ProjectContext.load(root)


def _whatif(root, *a, **kw):
    from taxjson.web import data
    with contextlib.redirect_stderr(io.StringIO()):
        return data.what_if_sell(_ctx(root), *a, **kw)


# ------------------------------------------------- A2-0131 / A2-0384 size
class TestWhatIfContractSize(unittest.TestCase):
    OPT = "XYZ270115C00050000.TO"

    def _book(self, **kw):
        return {"margin": [_row(self.OPT, 1, 50.0, "2026-06-01",
                                price=5.0, **kw)]}

    @rule("CA-PLAN-03")
    def test_declared_x10_equity_option_is_priced_at_x10(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _whatif(_project(tmp, self._book(multiplier=10.0)),
                        "margin", self.OPT, 1, 4.0, on="2026-09-29")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["multiplier"], 10)
        self.assertAlmostEqual(r["proceeds"], 40.0)
        self.assertAlmostEqual(r["economic_gain"], -10.0)

    @rule("CA-PLAN-03")
    def test_undeclared_equity_option_keeps_x100(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = _whatif(_project(tmp, self._book()),
                        "margin", self.OPT, 1, 4.0, on="2026-09-29")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["multiplier"], 100)
        self.assertAlmostEqual(r["proceeds"], 400.0)

    @rule("US-PLAN-03")
    def test_declared_x10_equity_option_usa(self):
        opt = "XYZ270115C00050000.US"
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {"brk": [_row(
                opt, 1, 50.0, "2026-06-01", price=5.0, currency="USD",
                acct="brk", multiplier=10.0)]},
                accounts={"brk": 'type = "taxable"\n'}, country="usa",
                base="USD")
            r = _whatif(root, "brk", opt, 1, 4.0, on="2026-09-29")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["multiplier"], 10)
        self.assertAlmostEqual(r["economic_gain"], -10.0)

    @rule("CA-PLAN-03")
    def test_plain_future_is_refused_not_priced_at_x1(self):
        fut = _row("F:CLZ6.US", 1, 0.0, "2026-06-01", price=83.0,
                   multiplier=1000.0, type="futures_settlement")
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {"margin": [fut]})
            r = _whatif(root, "margin", "F:CLZ6.US", 1, 59.0,
                        on="2026-09-29")
            # A2-0132: and never dated as an equity T+1 sale either.
            r2 = _whatif(root, "margin", "F:CLZ6.US", 1, 59.0,
                         on="2025-12-31")
        for res in (r, r2):
            self.assertFalse(res["ok"], res)
            self.assertIn("plain future", res["reason"])
            self.assertNotIn("settle_date", res)


# ------------------------------------- A2-0375 / A2-0437 / A2-1189 market
class TestWhatIfListingMarket(unittest.TestCase):
    RATES = "".join(f"2025-12-{d:02d} 12:00:00 USD CAD 1.38000\n"
                    for d in range(1, 31))

    def _settle(self, sym, cur):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {"margin": [
                _row(sym, 100, 4000.0, "2025-06-02", price=40.0)]},
                year=2025)
            (root / "work" / "to_base.csv").write_text(self.RATES)
            r = _whatif(root, "margin", sym, 100, 50.0, on="2025-12-24",
                        price_currency=cur)
        self.assertTrue(r["ok"], r)
        return r["settle_date"]

    @rule("CA-PLAN-03")
    def test_settles_on_the_listing_market_whatever_the_price_currency(
            self):
        # 2025-12-26 is Boxing Day: CDS is closed, US markets open.
        for cur in (None, "CAD", "USD"):
            self.assertEqual(self._settle("AEM.US", cur), "2025-12-26", cur)
            self.assertEqual(self._settle("AEM.TO", cur), "2025-12-29", cur)
            self.assertEqual(self._settle("DLR.U.TO", cur), "2025-12-29",
                             cur)


# ------------------------------------------------------------ A2-0383
class TestWhatIfMissingShelteredBook(unittest.TestCase):
    @rule("CA-PLAN-03")
    def test_sheltered_account_with_inputs_but_no_book_is_named(self):
        accts = {"margin": 'type = "taxable"\n',
                 "lira": 'type = "sheltered"\n',
                 "rrsp": 'type = "sheltered"\n'}
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(
                tmp, {"margin": [_row("XYZ.TO", 200, 2000.0, "2026-05-01")]},
                accounts=accts,
                # lira has inputs (its book was deleted); rrsp has none.
                inputs={"lira/trades.tt": "x\n"})
            r = _whatif(root, "margin", "XYZ.TO", 200, 7.0, on="2026-09-29")
        self.assertTrue(r["ok"], r)
        joined = " ".join(r["warnings"])
        self.assertIn("lira", joined)
        self.assertNotIn("rrsp", joined)


# ------------------------------------------------------------ A2-1175
class TestWhatIfTrustRocRecordDate(unittest.TestCase):
    def _book(self, cur):
        return {"margin": [
            _row("REI.UN.TO", 100, 2000.0, "2025-03-03",
                 settle="2025-03-04", currency=cur),
            {"action": "ADJUST", "date": "2026-01-05",
             "date_settle": "2026-01-05", "symbol": "REI.UN.TO",
             "quantity": 0.0, "net_amount": -40.0, "type": "roc",
             "record_date": "2025-12-15", "currency": cur,
             "account": "margin",
             "description": "REI UNITS RETURN OF CAPITAL REC 12/15/25 "
                            "PAY 01/05/26"}]}

    @rule("CA-INC-DATE-ROC-TRUST")
    @rule_absent("CA-INC-DATE-ROC-TRUST", country="usa")
    @rule("US-INC-DATE-ROC")
    def test_trust_roc_moves_to_its_record_date_in_canada_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            ca = _whatif(_project(tmp, self._book("CAD"), year=2025),
                         "margin", "REI.UN.TO", 100, 25.0, on="2025-12-17")
        with tempfile.TemporaryDirectory() as tmp:
            us = _whatif(_project(tmp, self._book("USD"), year=2025,
                                  country="usa", base="USD"),
                         "margin", "REI.UN.TO", 100, 25.0, on="2025-12-17")
        self.assertTrue(ca["ok"], ca)
        self.assertAlmostEqual(ca["cost_basis"], 1960.0)
        self.assertAlmostEqual(ca["economic_gain"], 540.0)
        # US: the pay date (US-INC-DATE-ROC) — full basis at the sale.
        self.assertTrue(us["ok"], us)
        self.assertAlmostEqual(us["cost_basis"], 2000.0)


# ------------------------------------------------------------ A2-0687
class TestWhatIfOptionReplacementNote(unittest.TestCase):
    @rule("US-PLAN-02")
    def test_ira_call_in_window_is_noted_in_the_what_if(self):
        call = "XYZ261218C00015000.US"
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {
                "brk": [_row("XYZ.US", 100, 2000.0, "2026-01-05",
                             currency="USD", acct="brk")],
                "ira": [_row(call, 1, 300.0, "2026-09-23", price=3.0,
                             currency="USD", acct="ira")]},
                accounts={"brk": 'type = "taxable"\n',
                          "ira": 'type = "sheltered"\n'},
                country="usa", base="USD")
            r = _whatif(root, "brk", "XYZ.US", 100, 11.0, on="2026-10-01")
        self.assertTrue(r["ok"], r)
        joined = " ".join(r["warnings"])
        self.assertIn(call, joined)
        self.assertIn("call_vs_share_loss", joined)


if __name__ == "__main__":
    unittest.main()
