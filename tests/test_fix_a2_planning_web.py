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
  A2-0374            the wash-radar page shows the CA-PLAN-04 /
                     US-PLAN-04 scope note
  A2-0696 / A2-1187 / A2-1188
                     damaged artifact fields are an error banner, never a
                     500 or a silent fallback
  A2-1179            one stale-rate threshold shared with harvest
  A2-1185            freshness fallback reads the checklist's input set
"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule, rule_absent

try:
    from fastapi.testclient import TestClient  # noqa: F401
    _HAVE_WEB = True
except Exception:          # pragma: no cover - extra not installed
    _HAVE_WEB = False


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


# ------------------------------------------------- radar page (data layer)
def _sidecar(rows, **extra):
    doc = {"schema_version": 1, "as_of_date": "2026-09-01",
           "account": "margin", "sections": [{
               "category": "VIOLATION", "title": "VIOLATION",
               "rows": rows}]}
    doc.update(extra)
    return doc


def _radar_row(**kw):
    r = {"ticker": "DL.TO", "taxable_display": "100",
         "sheltered_display": "0", "clears_at": "2026-09-17",
         "advisory": "VIOLATION: ...", "category": "VIOLATION"}
    r.update(kw)
    return r


class TestRadarSidecarShape(unittest.TestCase):
    def _sections(self, doc, rpt=None):
        from taxjson.web import data
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {"margin": []})
            (root / "reports" / "wash_radar_margin.json").write_text(
                json.dumps(doc))
            if rpt:
                (root / "reports" / "wash_radar_margin.rpt").write_text(rpt)
            return data.wash_radar_sections(_ctx(root), "margin")

    def test_a2_0696_wrong_type_fields_raise_report_error(self):
        from taxjson.web.data import ReportArtifactError
        for bad in ({"clears_at": 20250101}, {"clears_at": ["2025-01-01"]},
                    {"advisory": 5}, {"advisory": None},
                    {"advisory": ["x"]}, {"ticker": {"a": 1}}):
            with self.subTest(bad=bad):
                with self.assertRaises(ReportArtifactError):
                    self._sections(_sidecar([_radar_row(**bad)]))

    def test_a2_1188_missing_sections_is_an_error_not_no_report(self):
        from taxjson.web.data import ReportArtifactError
        rpt = ("--- CLEAR ---\nTICKER | T | S | C | A\n"
               "SHOP.TO | 1 | 0 | - | CLEAR — safe to sell at a loss\n")
        for doc in ({"Sections": []}, {}):
            with self.subTest(doc=doc):
                with self.assertRaises(ReportArtifactError):
                    self._sections(doc, rpt=rpt)

    def test_valid_sidecar_still_renders(self):
        secs = self._sections(_sidecar([_radar_row(clears_at=None)]))
        self.assertEqual(secs[0]["rows"][0]["ticker"], "DL.TO")


class TestHoldingsFieldShape(unittest.TestCase):
    def test_a2_1187_trades_of_the_wrong_type_is_a_report_error(self):
        from taxjson.web import data
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {"margin": []})
            (root / "reports" / "margin_holdings.toml").write_text(
                '[[holding]]\nsymbol = "SHOP.TO"\nquantity = 1.0\n'
                'trades = 5\n')
            with self.assertRaises(data.ReportArtifactError):
                data.find_holding(_ctx(root), "margin", "SHOP.TO")


# ------------------------------------------------------------ A2-1179
class TestStaleRateThreshold(unittest.TestCase):
    def test_web_and_harvest_share_one_threshold(self):
        from taxjson.web import data
        from taxjson.bin.taxjson_harvest import _STALE_RATE_DAYS
        days = _STALE_RATE_DAYS
        rates = "".join(f"2026-09-{d:02d} 12:00:00 USD CAD 1.39000\n"
                        for d in range(1, 11))
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {"margin": []})
            (root / "work" / "to_base.csv").write_text(rates)
            ctx = _ctx(root)
            _p, _r, at = data._price_to_base(
                ctx, 1.0, "USD", f"2026-09-{10 + days:02d}")
            _p, _r, over = data._price_to_base(
                ctx, 1.0, "USD", f"2026-09-{11 + days:02d}")
        self.assertIsNone(at)
        self.assertIsNotNone(over)


# ------------------------------------------------------------ A2-1185
class TestFreshnessInputSet(unittest.TestCase):
    def test_finder_and_lock_files_follow_the_checklist_rule(self):
        import os
        import time
        from taxjson.web import data
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {"margin": []},
                            inputs={"margin/trades.csv": "a\n"})
            old = time.time() - 3600
            os.utime(root / "inputs" / "margin" / "trades.csv", (old, old))
            os.utime(root / "taxjson.toml", (old, old))
            (root / "reports" / "margin.sum").write_text("x\n")
            self.assertFalse(data.freshness(_ctx(root))["stale"])
            (root / "inputs" / "margin" / ".DS_Store").write_text("x")
            (root / "inputs" / "margin" / "notes.txt").write_text("x")
            future = time.time() + 60
            for n in (".DS_Store", "notes.txt"):
                os.utime(root / "inputs" / "margin" / n, (future, future))
            f = data.freshness(_ctx(root))
            self.assertFalse(f["stale"], f)


@unittest.skipUnless(_HAVE_WEB, "web extra not installed")
class TestRoutes(unittest.TestCase):
    def _client(self, root, **kw):
        from taxjson.web.app import create_app
        return TestClient(create_app(_ctx(root), **kw),
                          base_url="http://127.0.0.1")

    @rule("CA-PLAN-04")
    def test_a2_0374_radar_page_shows_scope_note_canada(self):
        from taxjson.lib.wash_scope import scope_note
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {"margin": []})
            (root / "reports" / "wash_radar_margin.rpt").write_text(
                "--- CLEAR ---\nTICKER | T | S | C | A\n"
                "SHOP.TO | 1 | 0 | - | CLEAR — safe to sell at a loss\n")
            page = self._client(root).get("/wash-radar?account=margin").text
        self.assertIn("affiliated", page)
        self.assertIn(scope_note("canada")[:40], page)

    @rule("US-PLAN-04")
    def test_a2_0374_radar_page_shows_scope_note_usa(self):
        from taxjson.lib.wash_scope import scope_note
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {"margin": []}, country="usa", base="USD")
            (root / "reports" / "wash_radar_margin.rpt").write_text(
                "--- CLEAR ---\nTICKER | T | S | C | A\n"
                "SHOP.US | 1 | 0 | - | CLEAR — safe to sell at a loss\n")
            page = self._client(root).get("/wash-radar?account=margin").text
        self.assertIn("§1091", page)
        self.assertNotIn("s.251.1", page)
        self.assertIn(scope_note("usa")[:40], page)

    def test_a2_1187_holding_detail_bad_trades_is_banner_not_500(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {"margin": []})
            (root / "reports" / "margin_holdings.toml").write_text(
                '[[holding]]\nsymbol = "SHOP.TO"\nquantity = 1.0\n'
                'trades = 5\n')
            r = self._client(root).get("/holdings/margin/SHOP.TO")
        self.assertNotEqual(r.status_code, 500)
        self.assertIn("re-run", r.text)


if __name__ == "__main__":
    unittest.main()
