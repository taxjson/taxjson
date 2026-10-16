"""`taxjson win-rates`: the win rate of every closed trade, grouped per
ticker (an option under its underlying), account, month or class
(CA-RPT-16 / US-RPT-12: the trades `stats` counts). Synthetic books
only."""
import json
import tempfile
import unittest

from tax_rules import rule

from taxjson.lib import trade_stats as TS
from _style import assert_legend_first, assert_styled
from _style import project as style_project
from test_cli_polish import _cli, _project

_W = {"TAXJSON_WIDTH": "100"}


def _t(date, symbol, pnl, cls="long-shares", account="margin"):
    return {"date": date, "symbol": symbol,
            "underlying": TS._underlying(symbol), "account": account,
            "cls": cls, "pnl": pnl, "kind": ""}


class TestGroup(unittest.TestCase):
    TRADES = [_t("2026-02-03", "ABC.TO", 100.0),
              _t("2026-03-03", "ABC.TO", -40.0),
              _t("2026-03-20", "ABC260320C00050000.TO", 60.0,
                 "written-options"),
              _t("2026-04-02", "XYZ.TO", 0.0, account="tfsa"),
              _t("2026-04-09", "XYZ.TO", 25.0, account="tfsa")]

    def test_by_symbol_puts_an_option_under_its_underlying(self):
        g = TS.group(self.TRADES, "symbol")
        self.assertEqual([r["key"] for r in g["rows"]], ["ABC.TO", "XYZ.TO"])
        abc = g["rows"][0]
        self.assertEqual((abc["trades"], abc["wins"], abc["losses"],
                          abc["net_pl"], abc["win_rate"]),
                         (3, 2, 1, 120.0, 0.6667))
        self.assertEqual(abc["classes"], {"long-shares": 2,
                                          "written-options": 1})
        # A break-even trade is neither a win nor a loss.
        xyz = g["rows"][1]
        self.assertEqual((xyz["trades"], xyz["wins"], xyz["losses"],
                          xyz["breakeven"], xyz["win_rate"]),
                         (2, 1, 0, 1, 0.5))
        self.assertEqual((g["total"]["trades"], g["total"]["wins"],
                          g["total"]["net_pl"]), (5, 3, 145.0))

    def test_other_groupings(self):
        by_acct = TS.group(self.TRADES, "account")
        self.assertEqual([r["key"] for r in by_acct["rows"]],
                         ["margin", "tfsa"])
        by_month = TS.group(self.TRADES, "month")
        self.assertEqual([r["key"] for r in by_month["rows"]],
                         ["2026-02", "2026-03", "2026-04"])
        self.assertEqual(by_month["rows"][1]["net_pl"], 20.0)
        by_cls = TS.group(self.TRADES, "class")
        self.assertEqual([r["key"] for r in by_cls["rows"]],
                         ["long-shares", "written-options"])
        with self.assertRaises(ValueError):
            TS.group(self.TRADES, "week")

    def test_most_trades_first_then_net_pl(self):
        trades = [_t("2026-01-05", "A.TO", 1.0), _t("2026-01-06", "B.TO", 5.0),
                  _t("2026-01-07", "C.TO", 2.0), _t("2026-01-08", "C.TO", 2.0)]
        self.assertEqual([r["key"] for r in TS.group(trades)["rows"]],
                         ["C.TO", "B.TO", "A.TO"])


class TestWinRatesCli(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = _project(cls._tmp.name, "wr",
                            extra="option_grant_timing_since = 2025\n")
        r = _cli("-C", str(cls.root), "run", "--no-input")
        assert r.returncode == 0, r.stderr[-3000:]

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _json(self, *args):
        r = _cli("-C", str(self.root), "win-rates", *args, "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    @rule("CA-RPT-16")
    def test_same_trades_as_stats(self):
        st = json.loads(_cli("-C", str(self.root), "stats",
                             "--json").stdout)
        doc = self._json()
        self.assertEqual(doc["by"], "symbol")
        self.assertEqual(doc["total"], st["total"])
        self.assertEqual(doc["denied_total"], st["denied_total"])
        self.assertEqual(doc["accounts"], ["coins", "margin"])
        by_cls = self._json("--by", "class")
        self.assertEqual({r["key"]: r["trades"] for r in by_cls["rows"]},
                         {c: s["trades"] for c, s in st["classes"].items()
                          if s["trades"]})
        # The covered calls count under ABC.TO with its shares.
        abc = next(r for r in doc["rows"] if r["key"] == "ABC.TO")
        self.assertEqual(abc["classes"]["written-options"], 5)
        self.assertEqual(abc["classes"]["long-shares"], 1)

    def test_filters_and_groupings(self):
        one = self._json("abc")
        self.assertEqual([r["key"] for r in one["rows"]], ["ABC.TO"])
        self.assertEqual(one["unmatched_symbols"], [])
        none = self._json("NOPE.TO", "2026")
        self.assertEqual(none["rows"], [])
        self.assertEqual(none["unmatched_symbols"], ["NOPE.TO"])
        month = self._json("--by", "month", "margin")
        self.assertTrue(all(len(r["key"]) == 7 for r in month["rows"]))
        self.assertEqual(month["accounts"], ["margin"])
        tfsa = self._json("tfsa")
        self.assertTrue(tfsa["sheltered"])
        self.assertEqual(tfsa["total"]["trades"], 1)
        bad = _cli("-C", str(self.root), "win-rates", "2026-13")
        self.assertNotEqual(bad.returncode, 0)
        both = _cli("-C", str(self.root), "win-rates", "2026",
                    "--all-history")
        self.assertNotEqual(both.returncode, 0)

    def test_text(self):
        r = _cli("-C", str(self.root), "win-rates")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("WIN RATES — tax year 2026", r.stdout)
        self.assertIn("TICKER", r.stdout)
        self.assertIn("ABC.TO", r.stdout)
        self.assertIn("TOTAL", r.stdout)
        self.assertIn("Denied by the superficial-loss rule", r.stdout)
        r = _cli("-C", str(self.root), "win-rates", "NOPE")
        self.assertIn("! NOPE: no closed trade on it in the window",
                      r.stdout)


class TestWinRatesStyle(unittest.TestCase):
    def test_style_both_countries(self):
        for c in ("canada", "usa"):
            for by in ("symbol", "month"):
                with self.subTest(country=c, by=by):
                    r = style_project(c).run("win-rates", "--by", by, **_W)
                    self.assertEqual(r.returncode, 0, r.stderr)
                    assert_legend_first(self, r.stdout, "Economic P/L in")
                    assert_styled(self, r.stdout)


if __name__ == "__main__":
    unittest.main()
