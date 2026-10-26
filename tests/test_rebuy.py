"""`taxjson rebuy`: every ticker sold at a loss whose repurchase window is
still open, and the first day it can be bought back (CA-PLAN-05 /
US-PLAN-06). The rows are the wash radar's own (`losses`, `rebuy_at`);
lib/rebuy groups and renders them. Synthetic books and projects only."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule, rule_absent

from _qa_project import project as qa_project, tj
from _style import assert_concise, assert_legend_first, assert_styled
from _style import project as style_project

_W = {"TAXJSON_WIDTH": "100"}


def _row(d, sym, qty, px, acct="margin", settle=None, currency="CAD"):
    return {"action": "BUYSELL", "date": d, "date_settle": settle or d,
            "time": "10:00:00", "symbol": sym, "quantity": qty,
            "price": px, "net_amount": -qty * px, "currency": currency,
            "account": acct, "commission": 0.0,
            "id": f"{acct}-{sym}-{d}-{qty}"}


def _radar(country, taxable, sheltered=None, as_of="2026-05-20",
           cleared=7):
    """The radar's --json document (in process), flattened per ticker."""
    from taxjson.bin import taxjson_wash_radar as R
    from taxjson.bin.taxjson_watch import flatten_radar
    with tempfile.TemporaryDirectory() as d:
        tp = Path(d) / "margin_base.json"
        tp.write_text(json.dumps({"transactions": taxable}))
        argv = ["taxjson-wash-radar", "--country", country, "--date",
                as_of, "--all", "--json", "--cleared-days", str(cleared),
                "--taxable", str(tp)]
        if sheltered:
            sp = Path(d) / "sheltered_base.json"
            sp.write_text(json.dumps({"transactions": sheltered}))
            argv += ["--sheltered", str(sp)]
        out, old = io.StringIO(), sys.argv
        sys.argv = argv
        try:
            with contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(io.StringIO()):
                R.main()
        finally:
            sys.argv = old
    return flatten_radar(json.loads(out.getvalue()))


def _build(country, taxable, sheltered=None, as_of="2026-05-20", **kw):
    from taxjson.lib import rebuy as RB
    from taxjson.lib.ticker_map import underlying_of
    radar = _radar(country, taxable, sheltered, as_of)
    return RB.build(radar, as_of, country,
                    underlying=lambda s: underlying_of(s, {}), **kw)


def _by(doc):
    return {r["ticker"]: r for r in doc["rows"]}


# A loss sale of 100 ZZQ.TO traded 2026-05-01, settled 2026-05-04.
_CA_LOSS = [_row("2026-03-02", "ZZQ.TO", 100, 50.0, settle="2026-03-03"),
            _row("2026-05-01", "ZZQ.TO", -100, 40.0, settle="2026-05-04")]


class TestRebuyRows(unittest.TestCase):
    @rule("CA-PLAN-05")
    def test_canada_date_is_the_radar_reentry_on_settlement(self):
        doc = _build("canada", _CA_LOSS)
        r = _by(doc)["ZZQ.TO"]
        # settlement 2026-05-04 + 30 is Wed 06-03, the window's last
        # day: a trade then settles Thu, after it (TJ-0186; it was
        # settlement + 31). The radar's own CLEARS date (wash-radar,
        # buy-check).
        self.assertEqual(r["rebuy_at"], "2026-06-03")
        self.assertEqual(r["days_left"], 14)
        self.assertEqual(r["sold_on"], "2026-05-01")
        self.assertEqual(r["accounts"], ["margin"])
        self.assertAlmostEqual(r["loss"], 1000.0)
        self.assertEqual(r["status"], "wait")
        radar = _radar("canada", _CA_LOSS)
        self.assertEqual(radar["ZZQ.TO"]["clears_at"], r["rebuy_at"])

    @rule("CA-PLAN-05")
    @rule_absent("CA-PLAN-05", country="usa")
    @rule("US-PLAN-06")
    @rule_absent("US-PLAN-06", country="canada")
    def test_same_book_settle_vs_trade_dates(self):
        """One book, two laws: Canada counts the window from the
        settlement date, the US from the trade date."""
        book = [dict(t, currency="USD", symbol="ZZQ.US") for t in _CA_LOSS]
        ca = _by(_build("canada", book))["ZZQ.US"]
        us = _by(_build("usa", book))["ZZQ.US"]
        self.assertEqual(ca["rebuy_at"], "2026-06-03")   # settles after
        self.assertEqual(us["rebuy_at"], "2026-06-01")   # trade + 31

    @rule("CA-PLAN-05")
    def test_option_series_under_underlying_with_own_date(self):
        opt = "ZZQ260619C00045000.TO"
        book = _CA_LOSS + [
            _row("2026-04-01", opt, 2, 3.0 * 100, settle="2026-04-02"),
            _row("2026-05-12", opt, -2, 1.0 * 100, settle="2026-05-13")]
        for t in book[2:]:
            t["net_amount"] = -t["quantity"] * t["price"]
        doc = _build("canada", book)
        tick = [r["ticker"] for r in doc["rows"]]
        self.assertEqual(tick, ["ZZQ.TO", opt])          # under its share
        rows = _by(doc)
        self.assertEqual(rows[opt]["underlying"], "ZZQ.TO")
        self.assertTrue(rows[opt]["is_option"])
        # Each series keeps its own window: 2026-05-13 + 30 is Fri
        # 06-12, and a trade that day settles after it.
        self.assertEqual(rows[opt]["rebuy_at"], "2026-06-12")
        self.assertEqual(rows["ZZQ.TO"]["rebuy_at"], "2026-06-03")

    @rule("CA-PLAN-05")
    def test_registered_rebuy_is_flagged_rebought(self):
        shl = [_row("2026-05-08", "ZZQ.TO", 100, 39.0, acct="tfsa",
                    settle="2026-05-11")]
        doc = _build("canada", _CA_LOSS, shl)
        r = _by(doc)["ZZQ.TO"]
        self.assertEqual(r["status"], "rebought")
        self.assertTrue(r["rescue_by"])
        from taxjson.lib import rebuy as RB
        text = RB.render(doc, "CAD").text()
        self.assertIn("! ZZQ.TO: already bought back; sell it by", text)
        self.assertIn("RRSP and TFSA included; there the loss is denied "
                      "for good", text)

    def test_cleared_tail_is_one_line(self):
        doc = _build("canada", _CA_LOSS, as_of="2026-06-07")
        self.assertEqual(doc["rows"], [])
        self.assertEqual([c["ticker"] for c in doc["cleared"]], ["ZZQ.TO"])
        self.assertEqual(doc["cleared"][0]["rebuy_at"], "2026-06-03")
        from taxjson.lib import rebuy as RB
        text = RB.render(doc, "CAD").text()
        self.assertIn("Cleared in the last 7 days: ZZQ.TO.", text)
        # Past the tail: gone.
        doc = _build("canada", _CA_LOSS, as_of="2026-06-20")
        self.assertEqual(doc["cleared"], [])

    @rule("US-PLAN-06")
    def test_us_fully_washed_loss_needs_no_wait(self):
        book = [_row("2026-03-02", "ZZW.US", 100, 50.0, currency="USD"),
                _row("2026-05-01", "ZZW.US", -100, 40.0, currency="USD"),
                _row("2026-05-05", "ZZW.US", 100, 41.0, currency="USD")]
        r = _by(_build("usa", book))["ZZW.US"]
        self.assertEqual(r["status"], "washed")
        self.assertIsNone(r["rebuy_at"])
        from taxjson.lib import rebuy as RB
        self.assertEqual(RB._when(r), "now (washed)")

    @rule("US-PLAN-06")
    def test_us_ira_counts(self):
        from taxjson.lib import rebuy as RB
        self.assertIn("IRAs included", RB.any_account_line("usa"))
        self.assertIn("lost for good", RB.any_account_line("usa"))

    def test_symbol_filter(self):
        book = _CA_LOSS + [
            _row("2026-03-02", "ZZR.TO", 10, 20.0),
            _row("2026-05-06", "ZZR.TO", -10, 10.0, settle="2026-05-07")]
        doc = _build("canada", book, want={"ZZR.TO"},
                     canon=lambda s: s)
        self.assertEqual([r["ticker"] for r in doc["rows"]], ["ZZR.TO"])

    def test_render_keeps_the_budget(self):
        from taxjson.lib import out
        from taxjson.lib import rebuy as RB
        book = _CA_LOSS + [
            _row("2026-03-02", "ZZR.TO", 10, 20.0),
            _row("2026-05-06", "ZZR.TO", -10, 10.0, settle="2026-05-07")]
        doc = _build("canada", book)
        text = RB.render(doc, "CAD").text()
        self.assertLessEqual(len(out.prose_lines(text)), 6, text)
        assert_legend_first(self, text, "REBUY ON: the first day")
        full = RB.render(doc, "CAD", details=True).text()
        self.assertIn("SALES BEHIND EACH ROW", full)
        self.assertIn("HOW THE DATE IS SET", full)
        self.assertIn("Scope: these verdicts cover", full)


class TestRebuyCli(unittest.TestCase):
    """End to end: a project with a cross-listing (ticker.map TOBASE),
    a loss on one listing, a query by the other."""

    @rule("CA-PLAN-05")
    def test_cross_listing_counts(self):
        tt = ("BUYSELL 2026-03-02 10:00:00 ZZX.US 100 USD 30.00 3000\n"
              "BUYSELL 2026-05-01 10:00:00 ZZX.US -100 USD 20.00 2000\n"
              "BUYSELL 2026-04-01 10:00:00 ZZY.TO 50 CAD 10.00 500\n"
              "BUYSELL 2026-05-04 10:00:00 ZZY.TO -50 CAD 8.00 400\n")
        with tempfile.TemporaryDirectory() as tmp:
            root = qa_project(tmp, "p", {"margin/m.tt": tt},
                              ticker_map="TOBASE ZZX.US ZZX.TO\n")
            text = (root / "taxjson.toml").read_text().replace(
                "year = 2024", "year = 2026").replace(
                "option_grant_timing_since = 2024",
                "option_grant_timing_since = 2026")
            (root / "taxjson.toml").write_text(text)
            tj(root, "run", "--no-input")
            j = json.loads(tj(root, "rebuy", "--date", "2026-05-20",
                              "--json").stdout)
            rows = {r["ticker"]: r for r in j["rows"]}
            self.assertEqual(sorted(rows), ["ZZX.TO", "ZZY.TO"])
            self.assertIn("ZZX.US", rows["ZZX.TO"]["also"])
            # A query by the other listing finds it (buy-check matching).
            q = json.loads(tj(root, "rebuy", "ZZX.US", "--date",
                              "2026-05-20", "--json").stdout)
            self.assertEqual([r["ticker"] for r in q["rows"]], ["ZZX.TO"])
            d = tj(root, "rebuy", "--date", "2026-05-20", "--details")
            flat = " ".join(d.stdout.split())
            self.assertIn("A buy of ZZX.US counts too", flat)


class TestRebuyStyle(unittest.TestCase):
    """The synthetic style projects (tests/fixtures/style)."""

    def test_default_view_budget_both_countries(self):
        for c in ("canada", "usa"):
            with self.subTest(country=c):
                r = style_project(c).run("rebuy", "--date", "2024-11-25",
                                         **_W)
                self.assertEqual(r.returncode, 0, r.stderr)
                assert_concise(self, r.stdout, r.stderr,
                               legend="REBUY ON: the first day")
                assert_styled(self, r.stdout)
                self.assertIn("NVDA.US", r.stdout)
                self.assertIn("A buy in ANY of your accounts counts",
                              r.stdout)
                self.assertIn("tjs rebuy --details",
                              r.stdout.strip().splitlines()[-1])

    def test_canada_rows_and_violation(self):
        r = style_project("canada").run("rebuy", "--date", "2024-11-25",
                                        "--json")
        doc = json.loads(r.stdout)
        rows = {x["ticker"]: x for x in doc["rows"]}
        # The window closes Sat 12-21: Fri 12-20 settles after it (it
        # was Sun 12-22, a day nothing trades — TJ-0186).
        self.assertEqual(rows["NVDA.US"]["rebuy_at"], "2024-12-20")
        self.assertEqual(rows["SAMPA.TO"]["status"], "rebought")
        self.assertEqual(rows["SAMPA.TO"]["rescue_by"], "2024-11-28")
        self.assertEqual(doc["country"], "canada")

    def test_details_view(self):
        r = style_project("usa").run("rebuy", "--date", "2024-12-25",
                                     "--details", **_W)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("CLEARED IN THE LAST 7 DAYS", r.stdout)
        self.assertIn("NVDA.US", r.stdout)
        self.assertIn("IRAs included", " ".join(r.stdout.split()))


if __name__ == "__main__":
    unittest.main()
