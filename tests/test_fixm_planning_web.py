"""Web UI findings, medium round (planning area).

  R1-149  what-if FX: latest rate on/before the date (with its age), a
          currency with no rates refused, currency case normalised
  R1-197  what-if sale settles like a real one (era/holiday aware) and
          states the tax year it falls in
  R1-258  what-if on a Canadian taxable account blends the s.47 pool
          across the project's taxable accounts, as the filing does
  S078-15 ProjectContext refuses non-boolean account flags; the app
          surfaces a config it cannot reload
  S079-00 what-if on a SHORT position: a negative qty buys to cover;
          a positive qty is refused with a reason that says so
  S079-06 VIOLATION rescue deadline day is "sell TODAY", not "passed"
  S079-08 TOBASE'd option whose root changes (KGC -> K) resolves
  S078-21 freshness banner sees project-root inputs and a partial run
"""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient  # noqa: F401
    _HAVE_WEB = True
except Exception:          # pragma: no cover - extra not installed
    _HAVE_WEB = False


def _row(symbol, qty, net, date_, settle=None, acct="margin", price=None):
    return {"action": "BUYSELL", "date": date_,
            "date_settle": settle or date_, "symbol": symbol,
            "quantity": qty,
            "price": price if price is not None else abs(net / qty),
            "net_amount": net, "currency": "CAD", "account": acct}


def _project(tmp, books, *, accounts=None, settings_extra="",
             ticker_map=None):
    """books: {account: [rows]}; accounts: {name: toml-body}."""
    root = Path(tmp)
    (root / "work").mkdir()
    (root / "reports").mkdir()
    accounts = accounts or {"margin": 'type = "taxable"\n'}
    cfg = ('[settings]\nyear = 2026\ncountry = "canada"\n'
           'base_currency = "CAD"\n' + settings_extra)
    for name, body in accounts.items():
        cfg += f"[accounts.{name}]\n{body}"
    (root / "taxjson.toml").write_text(cfg)
    for name, rows in books.items():
        (root / "work" / f"{name}_base.json").write_text(
            json.dumps({"transactions": rows}))
    if ticker_map is not None:
        (root / "ticker.map").write_text(ticker_map)
    return root


def _ctx(root):
    from taxjson.web.context import ProjectContext
    return ProjectContext.load(root)


# ----------------------------------------------------------------- R1-149
class TestWhatIfFx(unittest.TestCase):
    RATES = "".join(
        f"2026-09-{d:02d} 12:00:00 USD CAD 1.39000\n"
        f"2026-09-{d:02d} 12:00:00 GBP CAD 1.87000\n"
        for d in range(1, 21))

    def _fx(self, cur, on):
        from taxjson.web import data
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {"margin": []})
            (root / "work" / "to_base.csv").write_text(self.RATES)
            return data._price_to_base(_ctx(root), 100.0, cur, on)

    def test_stale_rates_use_the_latest_rate_not_a_constant(self):
        price, rate, note = self._fx("USD", "2026-10-15")
        self.assertAlmostEqual(rate, 1.39)
        self.assertAlmostEqual(price, 139.0)
        self.assertIn("2026-09-20", note)          # the rate's own date

    def test_other_currency_uses_its_own_rate(self):
        price, rate, _note = self._fx("GBP", "2026-09-29")
        self.assertAlmostEqual(rate, 1.87)

    def test_recent_rate_has_no_note_whatever_the_case(self):
        _p, rate, note = self._fx("usd", "2026-09-20")
        self.assertAlmostEqual(rate, 1.39)
        self.assertIsNone(note)

    def test_currency_without_rates_is_refused(self):
        from taxjson.web import data
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {"margin": [
                _row("AAA.TO", 10, 1000.0, "2026-01-05")]})
            (root / "work" / "to_base.csv").write_text(self.RATES)
            r = data.what_if_sell(_ctx(root), "margin", "AAA.TO", 10,
                                  90.0, on="2026-09-10",
                                  price_currency="EUR")
        self.assertFalse(r["ok"])
        self.assertIn("EUR", r["reason"])


# ----------------------------------------------------------------- R1-197
class TestWhatIfSettlement(unittest.TestCase):
    def test_year_end_sale_settles_in_the_next_tax_year(self):
        from taxjson.web import data
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {"margin": [
                _row("ABCD.TO", 100, 5000.0, "2025-06-02", "2025-06-03")]})
            r = data.what_if_sell(_ctx(root), "margin", "ABCD.TO", 100,
                                  40.0, on="2025-12-31")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["settle_date"], "2026-01-02")
        self.assertEqual(r["tax_year"], 2026)
        self.assertTrue(any("2026" in w and "tax year" in w
                            for w in r["warnings"]), r["warnings"])

    def test_window_runs_on_the_settle_date(self):
        # A buy settling 2025-12-01 is 32 days before the sale's real
        # settle (2026-01-02): outside the window, no denial.
        from taxjson.web import data
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {"margin": [
                _row("ABCD.TO", 100, 5000.0, "2025-06-02", "2025-06-03"),
                _row("ABCD.TO", 10, 450.0, "2025-11-28", "2025-12-01")]})
            r = data.what_if_sell(_ctx(root), "margin", "ABCD.TO", 100,
                                  40.0, on="2025-12-31")
        self.assertTrue(r["ok"], r)
        self.assertFalse(r["is_wash_sale"])
        self.assertAlmostEqual(r["disallowed_amount"], 0.0)

    def test_mid_year_sale_has_no_tax_year_warning(self):
        from taxjson.web import data
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {"margin": [
                _row("ABCD.TO", 100, 5000.0, "2026-01-05", "2026-01-06")]})
            r = data.what_if_sell(_ctx(root), "margin", "ABCD.TO", 100,
                                  40.0, on="2026-06-10")
        self.assertEqual(r["settle_date"], "2026-06-11")
        self.assertEqual(r["tax_year"], 2026)
        self.assertFalse(any("tax year" in w for w in r["warnings"]))


# ----------------------------------------------------------------- R1-258
class TestWhatIfBlend(unittest.TestCase):
    ACCTS = {"acctA": 'type = "taxable"\n', "acctB": 'type = "taxable"\n'}

    def _books(self):
        return {"acctA": [_row("QQQ.TO", 100, 3000.0, "2026-01-05",
                               "2026-01-06", acct="acctA")],
                "acctB": [_row("QQQ.TO", 100, 1000.0, "2026-01-05",
                               "2026-01-06", acct="acctB")]}

    def test_sale_uses_the_blended_pool(self):
        from taxjson.web import data
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, self._books(), accounts=self.ACCTS)
            ra = data.what_if_sell(_ctx(root), "acctA", "QQQ.TO", 100,
                                   20.0, on="2026-09-10")
            rb = data.what_if_sell(_ctx(root), "acctB", "QQQ.TO", 100,
                                   20.0, on="2026-09-10")
        for r in (ra, rb):
            self.assertTrue(r["ok"], r)
            self.assertAlmostEqual(r["cost_basis"], 2000.0)
            self.assertAlmostEqual(r["economic_gain"], 0.0)
            self.assertIn("blended", r["basis"])

    def test_oversell_still_checked_per_account(self):
        from taxjson.web import data
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, self._books(), accounts=self.ACCTS)
            r = data.what_if_sell(_ctx(root), "acctA", "QQQ.TO", 150,
                                  20.0, on="2026-09-10")
        self.assertFalse(r["ok"])

    def test_single_taxable_account_basis_is_unchanged(self):
        from taxjson.web import data
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {"margin": [
                _row("QQQ.TO", 100, 3000.0, "2026-01-05", "2026-01-06")]})
            r = data.what_if_sell(_ctx(root), "margin", "QQQ.TO", 100,
                                  20.0, on="2026-09-10")
        self.assertAlmostEqual(r["cost_basis"], 3000.0)


# ----------------------------------------------------------------- S078-15
class TestContextFlags(unittest.TestCase):
    def test_string_bool_flag_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {"margin": []}, accounts={
                "margin": 'type = "taxable"\ncrypto = "false"\n'})
            with self.assertRaises(ValueError) as cm:
                _ctx(root)
        self.assertIn("crypto", str(cm.exception))

    def test_mis_cased_type_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {"margin": []}, accounts={
                "margin": 'type = "Taxable"\n'})
            with self.assertRaises(ValueError):
                _ctx(root)

    @unittest.skipUnless(_HAVE_WEB, "web extra not installed")
    def test_app_surfaces_a_config_it_cannot_reload(self):
        from fastapi.testclient import TestClient
        from taxjson.web.app import create_app
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {"margin": []})
            c = TestClient(create_app(_ctx(root)), base_url="http://127.0.0.1")
            cfg = root / "taxjson.toml"
            cfg.write_text(cfg.read_text().replace('"taxable"',
                                                   '"Taxable"'))
            st = cfg.stat()
            os.utime(cfg, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
            r = c.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("taxjson.toml", r.text)
        self.assertIn("Taxable", r.text)


# ----------------------------------------------------------------- S079-00
class TestWhatIfShort(unittest.TestCase):
    SETTINGS = 'option_premium_timing = "close"\n'

    def _short_books(self):
        opt = "ZZZ261218C00025000.TO"
        return opt, {"margin": [
            _row(opt, -1, 190.0, "2026-08-03", "2026-08-04", price=1.9),
            _row("YYY.TO", -200, 2000.0, "2026-08-03", "2026-08-04")]}

    def test_positive_qty_on_a_short_is_refused_with_the_reason(self):
        from taxjson.web import data
        opt, books = self._short_books()
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, books)
            r = data.what_if_sell(_ctx(root), "margin", opt, 1, 0.5,
                                  on="2026-09-10")
            r2 = data.what_if_sell(_ctx(root), "margin", "YYY.TO", 200,
                                   8.0, on="2026-09-10")
        for x in (r, r2):
            self.assertFalse(x["ok"])
            self.assertIn("SHORT", x["reason"])
            self.assertIn("negative", x["reason"])

    def test_negative_qty_buys_to_cover(self):
        from taxjson.web import data
        opt, books = self._short_books()
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, books, settings_extra=self.SETTINGS)
            r = data.what_if_sell(_ctx(root), "margin", opt, -1, 0.5,
                                  on="2026-09-10")
            r2 = data.what_if_sell(_ctx(root), "margin", "YYY.TO", -200,
                                   8.0, on="2026-09-10")
            r3 = data.what_if_sell(_ctx(root), "margin", "YYY.TO", -300,
                                   8.0, on="2026-09-10")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["side"], "cover")
        self.assertAlmostEqual(r["economic_gain"], 140.0)   # 190 - 50
        self.assertTrue(r2["ok"], r2)
        self.assertAlmostEqual(r2["economic_gain"], 400.0)  # 2000 - 1600
        self.assertFalse(r3["ok"])                          # over-cover

    def test_grant_timing_cover_books_the_full_buy_back(self):
        # Grant timing: the premium was taxed at the write, so the
        # buy-back realizes its whole cost as the loss.
        from taxjson.web import data
        opt, books = self._short_books()
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, books, settings_extra=(
                'option_premium_timing = "grant"\n'
                'option_grant_timing_since = 2026\n'))
            r = data.what_if_sell(_ctx(root), "margin", opt, -1, 0.5,
                                  on="2026-09-10")
        self.assertTrue(r["ok"], r)
        self.assertAlmostEqual(r["economic_gain"], -50.0)

    def test_negative_qty_on_a_long_is_refused(self):
        from taxjson.web import data
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {"margin": [
                _row("AAA.TO", 10, 1000.0, "2026-01-05")]})
            r = data.what_if_sell(_ctx(root), "margin", "AAA.TO", -10,
                                  90.0, on="2026-09-10")
        self.assertFalse(r["ok"])


# ----------------------------------------------------------------- S079-06
class TestViolationDeadlineDay(unittest.TestCase):
    def _row(self, cat, clears, today):
        from datetime import date
        from taxjson.web import data
        sidecar = {"sections": [{"category": cat, "title": cat, "rows": [{
            "ticker": "XYZ.US", "taxable_display": "100",
            "sheltered_display": "0", "clears_at": clears,
            "advisory": f"{cat}: Sell 100 by {clears}", "category": cat}]}]}
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {"margin": []})
            (root / "reports" / "wash_radar_margin.json").write_text(
                json.dumps(sidecar))
            secs = data.wash_radar_sections(
                _ctx(root), "margin", today=date.fromisoformat(today))
        return secs[0]["rows"][0]

    def test_deadline_day_is_still_actionable(self):
        row = self._row("VIOLATION", "2026-09-29", "2026-09-29")
        self.assertIn("TODAY", row["clears_in"])
        self.assertNotIn("passed", row["clears_in"])
        self.assertNotIn("PASSED", row["advisory"])

    def test_day_after_is_passed(self):
        row = self._row("VIOLATION", "2026-09-29", "2026-09-30")
        self.assertEqual(row["clears_in"], "deadline passed — loss denied")


# ------------------------------------------------- S038-09 (web half)
class TestStaleRadarSidecar(unittest.TestCase):
    """After `run --account <sheltered>` rebuilds a base book but not
    the cross-account radar, the page must not present CLEAR as safe."""

    def _root(self, tmp):
        root = _project(tmp, {"margin": [], "tfsa": []}, accounts={
            "margin": 'type = "taxable"\n', "tfsa": 'type = "sheltered"\n'})
        sidecar = {"sections": [{"category": "CLEAR", "title": "CLEAR",
                                 "rows": [{"ticker": "XYZ.TO",
                                           "taxable_display": "200",
                                           "sheltered_display": "0",
                                           "clears_at": None,
                                           "advisory": "CLEAR: No recent "
                                           "buys. Safe to sell at a loss",
                                           "category": "CLEAR"}]}]}
        (root / "reports" / "wash_radar_margin.json").write_text(
            json.dumps(sidecar))
        old = time.time() - 1000
        for p in root.rglob("*"):
            if p.is_file():
                os.utime(p, (old, old))
        return root

    def test_fresh_radar_has_no_warning(self):
        from taxjson.web import data
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            self.assertIsNone(data.radar_staleness(_ctx(root), "margin"))
            row = data.wash_radar_sections(_ctx(root), "margin")[0]["rows"][0]
        self.assertNotIn("STALE", row["advisory"])

    def test_newer_sheltered_book_marks_the_radar_stale(self):
        from taxjson.web import data
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            for n in ("tfsa_base.json", "sheltered_base.json"):
                (root / "work" / n).write_text('{"transactions": []}')
            msg = data.radar_staleness(_ctx(root), "margin")
            row = data.wash_radar_sections(_ctx(root), "margin")[0]["rows"][0]
        self.assertIn("full `taxjson run`", msg)
        self.assertIn("STALE", row["advisory"])

    @unittest.skipUnless(_HAVE_WEB, "web extra not installed")
    def test_page_shows_the_banner(self):
        from fastapi.testclient import TestClient
        from taxjson.web.app import create_app
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            (root / "reports" / "wash_radar_margin.rpt").write_text("x\n")
            os.utime(root / "reports" / "wash_radar_margin.rpt",
                     (time.time() - 1000,) * 2)
            (root / "work" / "sheltered_base.json").write_text("{}")
            r = TestClient(create_app(_ctx(root)), base_url="http://127.0.0.1").get(
                "/wash-radar?account=margin")
        self.assertIn("older than the books", r.text)


# ----------------------------------------------------------------- S079-08
class TestWhatIfRootChangingTobase(unittest.TestCase):
    def test_kgc_option_resolves_to_the_k_pool(self):
        from taxjson.web import data
        held = "K270115C00012000.TO"
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, {"margin": [
                _row(held, 1, 136.64, "2026-03-02", price=1.3664)]},
                ticker_map="TOBASE KGC.US K.TO\n")
            r = data.what_if_sell(_ctx(root), "margin",
                                  "KGC270115C00012000.US", 1, 0.5,
                                  on="2026-06-30")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["symbol"], held)
        self.assertAlmostEqual(r["cost_basis"], 136.64)


# ----------------------------------------------------------------- S078-21
class TestFreshness(unittest.TestCase):
    def _run_state(self, tmp, fingerprint=True):
        root = _project(tmp, {"margin": [], "tfsa": []}, accounts={
            "margin": 'type = "taxable"\n', "tfsa": 'type = "sheltered"\n'})
        for a in ("margin", "tfsa"):
            (root / "inputs" / a).mkdir(parents=True)
            (root / "inputs" / a / "x.tt").write_text("# none\n")
        (root / "ticker.map").write_text("")
        old = time.time() - 1000
        for p in root.rglob("*"):
            if p.is_file():
                os.utime(p, (old, old))
        for a in ("margin", "tfsa"):
            (root / "reports" / f"{a}.sum").write_text("sum\n")
            os.utime(root / "reports" / f"{a}.sum", (old + 100, old + 100))
        if fingerprint:
            from taxjson.lib.tomlcompat import tomllib
            from taxjson.lib.checklist import record_input_fingerprint
            record_input_fingerprint(
                root, tomllib.loads((root / "taxjson.toml").read_text()))
        return root

    def test_clean_state_is_fresh(self):
        from taxjson.web import data
        with tempfile.TemporaryDirectory() as tmp:
            root = self._run_state(tmp)
            self.assertFalse(data.freshness(_ctx(root))["stale"])

    def test_root_inputs_count(self):
        from taxjson.web import data
        for name, body in (("distributions.map", "XYZ.TO 2024-07-01 -1\n"),
                           ("missing_history.json", "[]\n"),
                           ("ticker.map", "TOBASE A.US A.TO\n"),
                           ("ticker_extraction_overrides.txt", "x y\n"),
                           ("crypto_ticker.map", "XBT BTC\n")):
            for fp in (True, False):
                with self.subTest(name=name, fingerprint=fp), \
                        tempfile.TemporaryDirectory() as tmp:
                    root = self._run_state(tmp, fingerprint=fp)
                    (root / name).write_text(body)
                    self.assertTrue(data.freshness(_ctx(root))["stale"])

    def test_partial_run_does_not_hide_a_changed_input(self):
        # Edit margin's input, then a `run --account tfsa` rewrites only
        # tfsa's report: the OLDEST per-account report decides.
        from taxjson.web import data
        for fp in (True, False):
            with self.subTest(fingerprint=fp), \
                    tempfile.TemporaryDirectory() as tmp:
                root = self._run_state(tmp, fingerprint=fp)
                (root / "inputs" / "margin" / "x.tt").write_text("# edit\n")
                (root / "reports" / "tfsa.sum").write_text("new\n")
                self.assertTrue(data.freshness(_ctx(root))["stale"])


if __name__ == "__main__":
    unittest.main()
