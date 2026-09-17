"""Tests for the web UI's pure-Python layer (context + data + what-if).
Runs WITHOUT the [web] extra — no FastAPI import here."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _project(tmp, transactions, *, holdings_toml=None, wash_rpt=None):
    """Build a minimal project dir: taxjson.toml + work/margin_base.json."""
    root = Path(tmp)
    (root / "work").mkdir()
    (root / "reports").mkdir()
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2026\ncountry = "canada"\nbase_currency = "CAD"\n'
        '[accounts.margin]\ntype = "taxable"\n'
        '[accounts.rrsp]\ntype = "sheltered"\n')
    (root / "work" / "margin_base.json").write_text(
        json.dumps({"transactions": transactions}))
    (root / "work" / "rrsp_base.json").write_text(
        json.dumps({"transactions": []}))
    if holdings_toml is not None:
        (root / "reports" / "margin_holdings.toml").write_text(holdings_toml)
    if wash_rpt is not None:
        (root / "reports" / "wash_radar_margin.rpt").write_text(wash_rpt)
    return root


class TestContext(unittest.TestCase):
    def test_load_and_classify_accounts(self):
        from taxjson.web.context import ProjectContext
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, [])
            ctx = ProjectContext.load(root)
        self.assertEqual(ctx.country, "canada")
        self.assertEqual(ctx.base_currency, "CAD")
        self.assertEqual([a.name for a in ctx.taxable()], ["margin"])
        self.assertEqual([a.name for a in ctx.sheltered()], ["rrsp"])


class TestWhatIfSell(unittest.TestCase):
    def test_plain_gain(self):
        from taxjson.web.context import ProjectContext
        from taxjson.web import data
        txs = [{"action": "BUYSELL", "date": "2025-01-02", "symbol": "AAA.TO",
                "quantity": 100, "price": 10.0, "net_amount": 1000.0,
                "currency": "CAD", "account": "margin"}]
        with tempfile.TemporaryDirectory() as tmp:
            ctx = ProjectContext.load(_project(tmp, txs))
            r = data.what_if_sell(ctx, "margin", "AAA.TO", 100, 15.0,
                                  on="2026-06-30")
        self.assertTrue(r["ok"])
        self.assertAlmostEqual(r["proceeds"], 1500.0)
        self.assertAlmostEqual(r["economic_gain"], 500.0)   # 1500 - 1000
        self.assertFalse(r["is_loss"])
        self.assertFalse(r["is_wash_sale"])

    def test_oversell_rejected(self):
        # Selling more than held must be rejected, not reported as a partial
        # gain (would otherwise sell into a phantom short).
        from taxjson.web.context import ProjectContext
        from taxjson.web import data
        txs = [{"action": "BUYSELL", "date": "2025-01-02", "symbol": "AAA.TO",
                "quantity": 100, "price": 10.0, "net_amount": 1000.0,
                "currency": "CAD", "account": "margin"}]
        with tempfile.TemporaryDirectory() as tmp:
            ctx = ProjectContext.load(_project(tmp, txs))
            r = data.what_if_sell(ctx, "margin", "AAA.TO", 500, 12.0,
                                  on="2026-06-30")
        self.assertFalse(r["ok"])
        self.assertIn("exceeds holding", r["reason"])

    def test_unheld_symbol_rejected(self):
        from taxjson.web.context import ProjectContext
        from taxjson.web import data
        with tempfile.TemporaryDirectory() as tmp:
            ctx = ProjectContext.load(_project(tmp, []))
            r = data.what_if_sell(ctx, "margin", "NONE.TO", 10, 5.0,
                                  on="2026-06-30")
        self.assertFalse(r["ok"])

    def test_native_currency_price_converts_to_base(self):
        # A USD price is converted to the base (CAD) at the sale-date FX before
        # the engine (which works on the CAD base.json) computes the gain.
        from taxjson.web.context import ProjectContext
        from taxjson.web import data
        txs = [{"action": "BUYSELL", "date": "2025-01-02", "symbol": "AEM.US",
                "quantity": 10, "price": 100.0, "net_amount": 1000.0,
                "currency": "CAD", "account": "margin"}]
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, txs)
            (root / "work" / "to_base.csv").write_text(
                "2026-06-30 12:00:00 USD CAD 1.40000\n")
            ctx = ProjectContext.load(root)
            r = data.what_if_sell(ctx, "margin", "AEM.US", 10, 50.0,
                                  on="2026-06-30", price_currency="USD")
        self.assertTrue(r["ok"])
        self.assertEqual(r["price_currency"], "USD")
        self.assertAlmostEqual(r["fx_rate"], 1.40)
        self.assertAlmostEqual(r["price_base"], 70.0)       # 50 USD × 1.40
        self.assertAlmostEqual(r["proceeds"], 700.0)        # base CAD
        self.assertAlmostEqual(r["economic_gain"], -300.0)  # 700 − 1000 cost

    def test_superficial_loss_flagged(self):
        # Loss sale with a repurchase inside the 30-day window → superficial
        # loss: economic loss partly disallowed, deductible loss smaller.
        from taxjson.web.context import ProjectContext
        from taxjson.web import data
        txs = [
            {"action": "BUYSELL", "date": "2025-01-02", "symbol": "BBB.TO",
             "quantity": 100, "price": 20.0, "net_amount": 2000.0,
             "currency": "CAD", "account": "margin"},
            {"action": "BUYSELL", "date": "2026-06-20", "symbol": "BBB.TO",
             "quantity": 100, "price": 12.0, "net_amount": 1200.0,
             "currency": "CAD", "account": "margin"},   # repurchase in window
        ]
        with tempfile.TemporaryDirectory() as tmp:
            ctx = ProjectContext.load(_project(tmp, txs))
            r = data.what_if_sell(ctx, "margin", "BBB.TO", 100, 10.0,
                                  on="2026-06-30")
        self.assertTrue(r["ok"])
        self.assertTrue(r["is_loss"])
        self.assertTrue(r["is_wash_sale"])
        self.assertGreater(r["disallowed_amount"], 0)
        # deductible loss is smaller in magnitude than the economic loss
        self.assertGreaterEqual(r["allowed_gain"], r["economic_gain"])


class TestWashRadarParse(unittest.TestCase):
    def test_sections_parsed(self):
        from taxjson.web.context import ProjectContext
        from taxjson.web import data
        rpt = ("--- LOCKED (1) ---------\n"
               "AAA.TO | 100.0000 | 0.0000 | 5d | LOCKED: recent buy\n"
               "--- CLEAR (1) ----------\n"
               "BBB.TO | 50.0000 | 0.0000 | - | CLEAR: safe\n")
        with tempfile.TemporaryDirectory() as tmp:
            ctx = ProjectContext.load(_project(tmp, [], wash_rpt=rpt))
            secs = data.wash_radar_sections(ctx, "margin")
        self.assertEqual([s["title"] for s in secs], ["LOCKED (1)", "CLEAR (1)"])
        self.assertEqual(secs[0]["rows"][0]["ticker"], "AAA.TO")


class TestServeCliWithoutExtra(unittest.TestCase):
    def test_serve_reports_missing_extra_cleanly(self):
        # `taxjson serve` must fail gracefully (not traceback) if FastAPI/uvicorn
        # aren't installed. We can't assert which branch runs in every env, so
        # just require a clean non-crash exit with a helpful stream.
        try:
            import uvicorn  # noqa: F401
            self.skipTest("web extra installed; missing-extra path not exercised")
        except ModuleNotFoundError:
            pass
        from taxjson.web.server import serve
        rc = serve(".", port=0)
        self.assertEqual(rc, 1)


try:
    import fastapi  # noqa: F401
    from fastapi.testclient import TestClient
    _HAVE_WEB = True
except Exception:
    _HAVE_WEB = False


@unittest.skipUnless(_HAVE_WEB, "web extra not installed")
class TestRoutes(unittest.TestCase):
    """Smoke-test the FastAPI routes render (guards the Starlette
    TemplateResponse request-first regression)."""
    def _client(self, tmp):
        from taxjson.web.context import ProjectContext
        from taxjson.web.app import create_app
        holdings = ('schema_version = "1.2"\n[[holding]]\nsymbol = "AAA.TO"\n'
                    'quantity = 100.0\ncurrency = "CAD"\ntotal_cost = 1000.0\n'
                    'cost_per_share = 10.0\n')
        txs = [{"action": "BUYSELL", "date": "2025-01-02", "symbol": "AAA.TO",
                "quantity": 100, "price": 10.0, "net_amount": 1000.0,
                "currency": "CAD", "account": "margin"}]
        root = _project(tmp, txs, holdings_toml=holdings)
        return TestClient(create_app(ProjectContext.load(root)))

    def test_pages_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = self._client(tmp)
            for url in ("/healthz", "/", "/holdings", "/holdings?account=margin",
                        "/wash-radar", "/holdings/margin/AAA.TO"):
                self.assertEqual(c.get(url).status_code, 200, url)

    def test_whatif_post_form(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = self._client(tmp)
            r = c.post("/whatif", data={"account": "margin", "symbol": "AAA.TO",
                                        "qty": "100", "price": "15"})
            self.assertEqual(r.status_code, 200)
            self.assertIn("Economic gain", r.text)

    def test_pages_carry_content_not_just_200(self):
        # A 200 with an empty/error-shaped body must not pass as "renders".
        with tempfile.TemporaryDirectory() as tmp:
            c = self._client(tmp)
            self.assertIn("margin", c.get("/").text)
            self.assertIn("AAA.TO", c.get("/holdings?account=margin").text)
            self.assertIn("AAA.TO", c.get("/holdings/margin/AAA.TO").text)

    def test_api_holdings_and_whatif(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = self._client(tmp)
            r = c.get("/api/holdings", params={"account": "margin"})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()[0]["symbol"], "AAA.TO")
            r = c.get("/api/whatif", params={
                "account": "margin", "symbol": "AAA.TO",
                "qty": 100, "price": 15})
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.json()["ok"])

    def test_unknown_account_is_404_not_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = self._client(tmp)
            r = c.get("/api/holdings", params={"account": "nope"})
            self.assertEqual(r.status_code, 404)
            self.assertFalse(r.json()["ok"])

    def test_traversal_account_is_404(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = self._client(tmp)
            r = c.get("/api/holdings",
                      params={"account": "../../outside/margin"})
            self.assertEqual(r.status_code, 404)

    def test_corrupt_holdings_toml_renders_error_not_500(self):
        from taxjson.web.context import ProjectContext
        from taxjson.web.app import create_app
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, [], holdings_toml="[[holding\nnot toml")
            c = TestClient(create_app(ProjectContext.load(root)))
            # Dashboard and holdings page render an error banner, not 500.
            r = c.get("/")
            self.assertEqual(r.status_code, 200)
            self.assertIn("could not be read", r.text)
            r = c.get("/holdings?account=margin")
            self.assertEqual(r.status_code, 200)
            self.assertIn("could not be read", r.text)
            # API surfaces a handled JSON error.
            r = c.get("/api/holdings", params={"account": "margin"})
            self.assertEqual(r.status_code, 500)
            self.assertFalse(r.json()["ok"])

    def test_untrusted_host_refused(self):
        # DNS-rebinding guard: a request whose Host header is not a
        # local name must be refused outright.
        with tempfile.TemporaryDirectory() as tmp:
            c = self._client(tmp)
            r = c.get("/api/holdings", params={"account": "margin"},
                      headers={"host": "evil.example.com"})
            self.assertEqual(r.status_code, 400)


class TestDataValidation(unittest.TestCase):
    """Account names come off the query string — they must be validated
    against taxjson.toml before touching the filesystem (closes the
    ?account=../../x traversal), and a corrupt artifact must raise a
    typed error the routes can render (not 500 the dashboard)."""

    def test_unknown_account_raises(self):
        from taxjson.web.context import ProjectContext
        from taxjson.web import data
        with tempfile.TemporaryDirectory() as tmp:
            ctx = ProjectContext.load(_project(tmp, []))
            with self.assertRaises(data.UnknownAccountError):
                data.load_holdings(ctx, "nope")
            with self.assertRaises(data.UnknownAccountError):
                data.what_if_sell(ctx, "nope", "AAA.TO", 1, 1.0,
                                  on="2026-06-30")
            with self.assertRaises(data.UnknownAccountError):
                data.wash_radar_sections(ctx, "nope")

    def test_traversal_account_rejected(self):
        from taxjson.web.context import ProjectContext
        from taxjson.web import data
        with tempfile.TemporaryDirectory() as tmp:
            ctx = ProjectContext.load(_project(tmp, []))
            with self.assertRaises(data.UnknownAccountError):
                data.load_holdings(ctx, "../../outside/margin")

    def test_corrupt_holdings_toml_raises_typed_error(self):
        from taxjson.web.context import ProjectContext
        from taxjson.web import data
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, [], holdings_toml="[[holding\nnot toml")
            ctx = ProjectContext.load(root)
            with self.assertRaises(data.ReportArtifactError):
                data.load_holdings(ctx, "margin")


class TestWhatIfWashPolicy(unittest.TestCase):
    """what_if_sell must follow the CLI's wash policy
    (GainsRequest.effective_detect_wash + the usa-crypto carve-out), not
    hard-set detect_wash_sales=True — and must not load the simulated
    account into its own sheltered pool (its own buys acted as their own
    wash triggers)."""

    _LOSS_WITH_WINDOW_BUY = [
        {"action": "BUYSELL", "date": "2025-01-02", "symbol": "BBB.TO",
         "quantity": 100, "price": 20.0, "net_amount": 2000.0,
         "currency": "CAD", "account": "rrsp"},
        {"action": "BUYSELL", "date": "2026-06-20", "symbol": "BBB.TO",
         "quantity": 100, "price": 12.0, "net_amount": 1200.0,
         "currency": "CAD", "account": "rrsp"},     # buy inside the window
    ]

    def test_sheltered_account_gets_no_wash_detection(self):
        from taxjson.web.context import ProjectContext
        from taxjson.web import data
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "work").mkdir()
            (root / "reports").mkdir()
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2026\ncountry = "canada"\n'
                'base_currency = "CAD"\n'
                '[accounts.rrsp]\ntype = "sheltered"\n')
            (root / "work" / "rrsp_base.json").write_text(
                json.dumps({"transactions": self._LOSS_WITH_WINDOW_BUY}))
            ctx = ProjectContext.load(root)
            r = data.what_if_sell(ctx, "rrsp", "BBB.TO", 100, 10.0,
                                  on="2026-06-30")
        self.assertTrue(r["ok"])
        self.assertTrue(r["is_loss"])
        # Sheltered gains aren't reported — no superficial-loss check,
        # exactly like the CLI (effective_detect_wash = taxable and ...).
        self.assertFalse(r["is_wash_sale"])
        self.assertFalse(r.get("disallowed_amount"))

    def _usa_project(self, tmp, account_toml, account, symbol):
        from taxjson.web.context import ProjectContext
        root = Path(tmp)
        (root / "work").mkdir()
        (root / "reports").mkdir()
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2026\ncountry = "usa"\n'
            'base_currency = "USD"\n' + account_toml)
        txs = [
            {"action": "BUYSELL", "date": "2025-01-02", "symbol": symbol,
             "quantity": 100, "price": 20.0, "net_amount": 2000.0,
             "currency": "USD", "account": account},
            {"action": "BUYSELL", "date": "2026-06-20", "symbol": symbol,
             "quantity": 100, "price": 12.0, "net_amount": 1200.0,
             "currency": "USD", "account": account},
        ]
        (root / "work" / f"{account}_base.json").write_text(
            json.dumps({"transactions": txs}))
        return ProjectContext.load(root)

    def test_usa_crypto_not_wash_checked(self):
        from taxjson.web import data
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._usa_project(
                tmp, '[accounts.crypto]\ntype = "taxable"\ncrypto = true\n',
                "crypto", "BTC")
            r = data.what_if_sell(ctx, "crypto", "BTC", 100, 10.0,
                                  on="2026-06-30")
        self.assertTrue(r["ok"])
        self.assertTrue(r["is_loss"])
        self.assertFalse(r["is_wash_sale"])     # IRS: crypto is property

    def test_usa_equity_still_wash_checked(self):
        from taxjson.web import data
        with tempfile.TemporaryDirectory() as tmp:
            ctx = self._usa_project(
                tmp, '[accounts.margin]\ntype = "taxable"\n',
                "margin", "AAPL.US")
            r = data.what_if_sell(ctx, "margin", "AAPL.US", 100, 10.0,
                                  on="2026-06-30")
        self.assertTrue(r["ok"])
        self.assertTrue(r["is_wash_sale"])


class TestViolationDeadlinePassed(unittest.TestCase):
    """A VIOLATION's clears_at is the rescue DEADLINE (last trade date
    for the full exit). Rendering a passed one as "cleared" — with the
    generic "window has CLEARED" suffix — said the opposite of the
    truth: the loss is now denied."""

    def _sidecar(self, cat, clears):
        return {"schema_version": 1, "as_of_date": "2026-09-01",
                "account": "margin", "include_all": False,
                "sections": [{"category": cat, "title": cat, "rows": [{
                    "ticker": "DL.TO", "taxable_qty": 100.0,
                    "taxable_display": "100", "sheltered_qty": 0.0,
                    "sheltered_display": "0", "clears_at": clears,
                    "clears_in_at_generation": "x",
                    "advisory": f"{cat}: ...", "category": cat}]}]}

    def _row(self, cat, clears, today):
        from datetime import date
        from taxjson.web.context import ProjectContext
        from taxjson.web import data
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, [])
            (root / "reports" / "wash_radar_margin.json").write_text(
                json.dumps(self._sidecar(cat, clears)))
            ctx = ProjectContext.load(root)
            secs = data.wash_radar_sections(
                ctx, "margin", today=date.fromisoformat(today))
        return secs[0]["rows"][0]

    def test_passed_violation_deadline_is_not_cleared(self):
        row = self._row("VIOLATION", "2026-09-17", "2026-09-20")
        self.assertEqual(row["clears_in"], "deadline passed — loss denied")
        self.assertIn("denied", row["advisory"])
        self.assertNotIn("CLEARED", row["advisory"])

    def test_future_violation_deadline_and_passed_cooling_unchanged(self):
        row = self._row("VIOLATION", "2026-09-17", "2026-09-10")
        self.assertEqual(row["clears_in"], "2026-09-17 (7d)")
        row = self._row("COOLING", "2026-09-17", "2026-09-20")
        self.assertEqual(row["clears_in"], "cleared")
        self.assertIn("CLEARED", row["advisory"])


_MIXED_HOLDINGS = (
    'schema_version = "1.2"\n'
    '[[holding]]\nsymbol = "AAA.TO"\nquantity = 100.0\ncurrency = "CAD"\n'
    'total_cost = 1000.0\ncost_per_share = 10.0\n'
    # A JOURNAL-folded cross-listing: no single native cost, only the
    # per-currency components plus the base figures (taxjson-export).
    '[[holding]]\nsymbol = "MIX.TO"\nquantity = 200.0\n'
    'mixed_currency = true\ntotal_cost_cad = 500.0\ntotal_cost_usd = 300.0\n'
    'base_currency = "CAD"\nbase_total_cost = 910.0\n'
    'base_cost_per_share = 4.55\n')


@unittest.skipUnless(_HAVE_WEB, "web extra not installed")
class TestMixedCurrencyHoldingsRender(unittest.TestCase):
    """2026-09 audit: holdings.toml omits total_cost/cost_per_share for a
    mixed-currency bucket (carrying total_cost_<cur> instead), and the
    pages rendered blank cost cells. Show the per-currency figures with
    the same MIXED marker the .sum uses."""

    def _client(self, tmp):
        from taxjson.web.context import ProjectContext
        from taxjson.web.app import create_app
        txs = [{"action": "BUYSELL", "date": "2025-01-02", "symbol": "MIX.TO",
                "quantity": 200, "price": 4.55, "net_amount": 910.0,
                "currency": "CAD", "account": "margin"}]
        root = _project(tmp, txs, holdings_toml=_MIXED_HOLDINGS)
        return TestClient(create_app(ProjectContext.load(root)))

    def test_list_and_detail_show_per_currency_cost(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = self._client(tmp)
            page = c.get("/holdings?account=margin").text
            self.assertIn("MIXED", page)
            self.assertIn("500.0 CAD", page)
            self.assertIn("300.0 USD", page)
            self.assertIn("4.55 CAD", page)               # base cost/sh
            detail = c.get("/holdings/margin/MIX.TO").text
            self.assertIn("MIXED", detail)
            self.assertIn("500.0 CAD", detail)
            self.assertIn("300.0 USD", detail)
            # The plain holding is untouched.
            self.assertIn("10.0 CAD", page)


class TestWhatIfShelteredNotDeductible(unittest.TestCase):
    """2026-09 audit: a what-if loss in an RRSP was labeled 'Deductible
    now' — nothing in a registered account is deductible."""

    _TXS = [{"action": "BUYSELL", "date": "2025-01-02", "symbol": "BBB.TO",
             "quantity": 100, "price": 20.0, "net_amount": 2000.0,
             "currency": "CAD", "account": "rrsp"}]

    def _root(self, tmp):
        root = Path(tmp)
        (root / "work").mkdir()
        (root / "reports").mkdir()
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2026\ncountry = "canada"\n'
            'base_currency = "CAD"\n'
            '[accounts.margin]\ntype = "taxable"\n'
            '[accounts.rrsp]\ntype = "sheltered"\n')
        (root / "work" / "rrsp_base.json").write_text(
            json.dumps({"transactions": self._TXS}))
        (root / "work" / "margin_base.json").write_text(
            json.dumps({"transactions": []}))
        return root

    def test_data_flags_sheltered_loss(self):
        from taxjson.web.context import ProjectContext
        from taxjson.web import data
        with tempfile.TemporaryDirectory() as tmp:
            ctx = ProjectContext.load(self._root(tmp))
            r = data.what_if_sell(ctx, "rrsp", "BBB.TO", 100, 10.0,
                                  on="2026-06-30")
        self.assertTrue(r["ok"])
        self.assertTrue(r["is_loss"])
        self.assertTrue(r["sheltered"])
        self.assertEqual(r["account_type"], "sheltered")
        self.assertTrue(any("not deductible" in w for w in r["warnings"]))

    @unittest.skipUnless(_HAVE_WEB, "web extra not installed")
    def test_page_labels_not_deductible(self):
        from taxjson.web.context import ProjectContext
        from taxjson.web.app import create_app
        with tempfile.TemporaryDirectory() as tmp:
            c = TestClient(create_app(ProjectContext.load(self._root(tmp))))
            r = c.post("/whatif", data={"account": "rrsp", "symbol": "BBB.TO",
                                        "qty": "100", "price": "10"})
            self.assertEqual(r.status_code, 200)
            self.assertIn("not deductible (registered account)", r.text)
            # Taxable stays numeric.
            root = Path(tmp)
            (root / "work" / "margin_base.json").write_text(json.dumps(
                {"transactions": [dict(self._TXS[0], account="margin")]}))
            c = TestClient(create_app(ProjectContext.load(root)))
            r = c.post("/whatif", data={"account": "margin",
                                        "symbol": "BBB.TO",
                                        "qty": "100", "price": "10"})
            self.assertNotIn("not deductible (registered", r.text)


if __name__ == "__main__":
    unittest.main()
