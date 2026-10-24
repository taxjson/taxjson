"""`taxjson cash`: the cash events of the taxable accounts (conversions,
deposits, withdrawals, the .tt cash lines) as the .tt lines that declare
them, oldest first, with per-currency totals (lib/cash_events tt_line,
tt_comment, apply_declarations; bin/taxjson_run cmd_cash). The fixture
is the FX-on-cash ledger v2 CLI test's synthetic project."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib import cash_events as CE
from taxjson.lib.dispatch import python_module_argv

from test_fix_fx_cash_v2 import _ROWS_TT, _TOML, _rates

REPO_ROOT = Path(__file__).resolve().parent.parent


def _cli(root, *args):
    e = dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")
    cmd = python_module_argv("taxjson.bin.taxjson_run",
                             ["-C", str(root), *args])
    return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True,
                          text=True, env=e, stdin=subprocess.DEVNULL,
                          timeout=600)


def _move(amount, date="2025-01-15", decl=None, desc="Wire in",
          decl_where=None):
    ev = CE.move(date, "USD", amount, where="x.csv", desc=desc)
    ev.update(label="margin", book="margin/ib", broker="ib", file="x.csv")
    if decl is not None:
        ev["decl"] = decl
    if decl_where:
        ev["decl_where"] = decl_where
    return ev


class TestLines(unittest.TestCase):
    def test_each_kind_as_its_tt_line(self):
        conv = CE.conv("2025-03-05", "USD", 5000, "CAD", 6780,
                       desc="Forex USD.CAD")
        self.assertEqual(CE.tt_line(conv),
                         "FXCONV 2025-03-05 USD 5,000.00 CAD 6,780.00")
        conv["value"] = 6780.0
        self.assertTrue(CE.tt_line(conv).endswith(" value=6,780.00"))
        self.assertEqual(CE.tt_line(_move(2000.0)),
                         "CASHMOVE 2025-01-15 USD +2,000.00 ?")
        self.assertEqual(CE.tt_line(_move(-1500.0, decl={"how": "kept",
                                                         "value": None})),
                         "CASHMOVE 2025-01-15 USD -1,500.00 kept")
        self.assertEqual(CE.tt_line(_move(10000.0, decl={"how": "cost",
                                                         "value": 13000.0})),
                         "CASHMOVE 2025-01-15 USD +10,000.00 cost=13,000.00")
        opening = CE.parse_line("CASHOPEN 2025-01-01 USD 0 0", "a.tt:1")
        self.assertEqual(CE.tt_line(opening),
                         "CASHOPEN 2025-01-01 USD 0.00 0.00")
        fee = CE.flow("2025-03-05", "USD", -2.0, desc="Forex commission")
        self.assertEqual(CE.tt_line(fee), "#FEE 2025-03-05 USD -2.00")
        # A statement balance, a period, a note: not a line anyone writes.
        self.assertIsNone(CE.tt_line(CE.balance("2025-12-31", "USD", 1.0)))
        self.assertIsNone(CE.tt_line({"kind": "PERIOD", "date": "2025-01-01"}))
        self.assertIsNone(CE.tt_line({"kind": "NOTE", "text": "x"}))

    def test_a_declared_line_parses_back_as_the_same_event(self):
        for ev in (CE.conv("2025-03-05", "USD", 5000, "CAD", 6780.5),
                   _move(10000.0, decl={"how": "cost", "value": 13000.0}),
                   _move(-1500.0, decl={"how": "kept", "value": None}),
                   _move(-700.25, decl={"how": "spot", "value": None}),
                   CE.parse_line("CASHOPEN 2025-01-01 USD 1200 1600.5",
                                 "a.tt:1")):
            line = CE.tt_line(ev)
            back = CE.parse_line(line, "copied.tt:1")
            self.assertIsNotNone(back, line)
            self.assertEqual(back["kind"], ev["kind"])
            for k in ("from_ccy", "from_amt", "to_ccy", "to_amt",
                      "currency", "amount", "decl", "units", "cost"):
                if k in ev:
                    self.assertEqual(back[k], ev[k], (line, k))
        # An undeclared move is refused when copied: the `?` names what
        # to say.
        with self.assertRaises(CE.CashLineError) as cm:
            CE.parse_line(CE.tt_line(_move(2000.0)), "copied.tt:1")
        self.assertIn("say how the cash moved", str(cm.exception))
        # A #FEE line is a comment.
        self.assertIsNone(CE.parse_line(
            CE.tt_line(CE.flow("2025-03-05", "USD", -2.0)), "copied.tt:1"))

    def test_comment(self):
        self.assertEqual(CE.tt_comment(_move(2000.0)), "Wire in")
        self.assertEqual(CE.tt_comment(_move(2000.0, decl_where="inputs/margin/cash.tt:3")),
                         "Wire in; declared in inputs/margin/cash.tt:3")
        ln = CE.parse_line("CASHMOVE 2025-01-15 USD 10000 cost=13000",
                           "inputs/margin/cash.tt:2")
        self.assertEqual(CE.tt_comment(ln), "inputs/margin/cash.tt:2")

    def test_declarations_match_as_the_ledger_matches_them(self):
        m = _move(10000.0)
        other = _move(10000.0, date="2025-02-01")
        ln = CE.parse_line("CASHMOVE 2025-01-15 USD 10000 cost=13000",
                           "inputs/margin/cash.tt:2")
        ln.update(label="margin", file="cash.tt", book=None)
        loose = CE.parse_line("CASHMOVE 2025-06-01 USD -50 kept",
                              "inputs/margin/cash.tt:3")
        loose.update(label="margin", file="cash.tt", book=None)
        CE.apply_declarations([m, other], [ln, loose])
        self.assertEqual(m["decl"], {"how": "cost", "value": 13000.0})
        self.assertEqual(m["decl_where"], "inputs/margin/cash.tt:2")
        self.assertTrue(ln.get("matched"))
        self.assertIsNone(other["decl"])
        self.assertFalse(loose.get("matched"))        # a move of its own
        self.assertEqual(CE.tt_line(m),
                         "CASHMOVE 2025-01-15 USD +10,000.00 cost=13,000.00")


class TestCommand(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name) / "proj"
        (cls.root / "inputs" / "margin").mkdir(parents=True)
        (cls.root / "taxjson.toml").write_text(
            _TOML.format(extra="") + '\n[accounts.rrsp]\ntype = "sheltered"\n')
        (cls.root / "inputs" / "margin" / "rows.tt").write_text(_ROWS_TT)
        (cls.root / "work").mkdir()
        _rates(cls.root / "work" / "to_base.csv")
        r = _cli(cls.root, "run", "--no-input")
        assert r.returncode == 0, r.stderr + r.stdout

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_lists_the_tt_lines_oldest_first_with_totals(self):
        r = _cli(self.root, "cash")
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        # One taxable account: the lines as they are, no prefix.
        self.assertEqual(lines[0].split("  #")[0].split(),
                         ["CASHOPEN", "2025-01-01", "USD", "0.00", "0.00"])
        self.assertEqual(lines[1].split("  #")[0].split(),
                         ["CASHMOVE", "2025-01-15", "USD", "+10,000.00",
                          "cost=13,000.00"])
        self.assertTrue(lines[1].endswith("# inputs/margin/rows.tt:2"),
                        lines[1])
        self.assertNotIn("CASHBAL", r.stdout)       # a statement fact
        self.assertIn("TOTAL DEPOSITS: 10,000.00 USD", r.stdout)
        self.assertNotIn("TOTAL WITHDRAWALS", r.stdout)
        # The lines line up in columns (the events view's layout).
        self.assertEqual(lines[0].index("2025-01-01"),
                         lines[1].index("2025-01-15"))
        self.assertEqual(r.stdout, _cli(self.root, "cash", "margin").stdout)
        self.assertEqual(r.stdout,
                         _cli(self.root, "cash", "tax_year").stdout)

    def test_json(self):
        doc = json.loads(_cli(self.root, "cash", "--json").stdout)
        self.assertEqual(doc["scope"], "tax year 2025")
        self.assertEqual([(x["kind"], x["tt"]) for x in doc["rows"]],
                         [("CASHOPEN", "CASHOPEN 2025-01-01 USD 0.00 0.00"),
                          ("CASHMOVE", "CASHMOVE 2025-01-15 USD "
                                       "+10,000.00 cost=13,000.00")])
        self.assertEqual(doc["totals"]["deposits"], {"USD": 10000.0})
        self.assertEqual(doc["totals"]["withdrawals"], {})
        self.assertEqual(doc["problems"], [])

    def test_window_and_accounts(self):
        r = _cli(self.root, "cash", "2024")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "(no cash events in this window)")
        r = _cli(self.root, "cash", "bogus")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no account 'bogus'", r.stderr)
        r = _cli(self.root, "cash", "rrsp")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not a taxable account", r.stderr)

    def test_the_ledger_and_the_listing_agree(self):
        v2 = json.loads(_cli(self.root, "fx-cash", "--ledger", "v2",
                             "--json").stdout)
        self.assertEqual(v2["status"], "computed", v2["problems"])
        cash = json.loads(_cli(self.root, "cash", "--json").stdout)
        self.assertEqual(sum(1 for x in cash["rows"]
                             if x["kind"] == "CASHMOVE"),
                         len(v2["declared"]))

    def test_needs_a_run(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(_TOML.format(extra=""))
            (root / "inputs" / "margin" / "rows.tt").write_text(_ROWS_TT)
            r = _cli(root, "cash")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("taxjson cash:", r.stderr)
            self.assertIn("run `taxjson run` first", r.stderr)


if __name__ == "__main__":
    unittest.main()
