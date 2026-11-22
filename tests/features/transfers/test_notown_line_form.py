"""Backlog TJ-0075 (decided 2026-10-26): a .tt NOTOWN line undoes one
own-move pairing.

The run pairs a transfer-out and a transfer-in of the same security and
quantity in your taxable accounts as one move of your own — within 10
days (exact), or 11 to transfer_in.SLOW_MOVE_DAYS (kept at 45) days
apart when neither leg has another candidate (slow) — so an unrelated
departure and arrival paired too: the arrival took the departure's cost
and nothing let the user undo it. Now

    NOTOWN <date> <symbol> <qty> <from-account> <from-date>

in the receiving account's .tt files undoes that one pairing, exact or
slow, and never makes one: the transfer-in is then an arrival from
outside the books (booked at the broker's stated book value, or kept
out with no cost) and the transfer-out an ordinary one; in a US project
no lots move. Every other leg pairs as without the line. The slow
pairing's Info line names the line to add; a line that undoes no
pairing is a Warning naming it; `tjs check-tt` knows the keyword.
Figures change only when a user writes the line.

Every fixture is synthetic: invented tickers, amounts and account ids.

Formerly: test_decisions_2026_10_26_notown
"""
import os
import tempfile
import unittest
from pathlib import Path

from tax_rules.dual import settings_for
from _style import CapturedWidth

from taxjson.lib import transfer_in as TI
from taxjson.lib import tt_format as TTF
_WIDTH = CapturedWidth()


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


class TestNotOwnLine(unittest.TestCase):
    """TJ-0075: the line's form, as the .tt readers take it.

    Formerly: test_decisions_2026_10_26_notown.TestNotOwnLine"""

    def test_tj0075_the_line_parses_and_round_trips(self):
        p = TTF.parse_not_own_line(
            "NOTOWN 2024-03-16 zzq.to 100 qa 2024-03-01  # not one move")
        self.assertEqual(
            {k: p[k] for k in ("date", "symbol", "quantity", "from_account",
                               "from_date")},
            {"date": "2024-03-16", "symbol": "ZZQ.TO", "quantity": 100.0,
             "from_account": "qa", "from_date": "2024-03-01"})
        self.assertIsNone(TTF.parse_not_own_line(
            "BUYSELL 2024-01-10 09:30:00 ZZQ.TO 100 CAD 2.00 200.00"))
        self.assertIsNone(TTF.parse_not_own_line("# NOTOWN in a comment"))
        text = TI.not_own_text("2024-03-16", "ZZQ.TO", 1250000.5, "qa",
                               "2024-03-01")
        self.assertEqual(text,
                         "NOTOWN 2024-03-16 ZZQ.TO 1250000.5 qa 2024-03-01")
        self.assertEqual(TTF.parse_not_own_line(text)["quantity"], 1250000.5)
        m = TI.SlowMove(key="ZZQ.TO", symbol="ZZQ.TO", quantity=100.0,
                        out_account="qa", out_date="2024-03-01",
                        in_account="qb", in_date="2024-03-16")
        self.assertEqual(m.not_own_line(),
                         "NOTOWN 2024-03-16 ZZQ.TO 100 qa 2024-03-01")

    def test_tj0075_a_malformed_line_is_refused_naming_the_form(self):
        for bad in ("NOTOWN ZZQ.TO 2024-03-16 100 qa 2024-03-01",
                    "NOTOWN 2024-03-16 ZZQ.TO 100 qa",
                    "NOTOWN 2024-03-16 ZZQ.TO -100 qa 2024-03-01",
                    "NOTOWN 2024-03-16 ZZQ.TO 0 qa 2024-03-01",
                    "NOTOWN 2024-03-16 ZZQ.TO 1,5 qa 2024-03-01",
                    "NOTOWN 2024-02-30 ZZQ.TO 100 qa 2024-03-01",
                    "NOTOWN 2024-03-16 ZZQ.TO 100 qa 2024-03-01 extra"):
            with self.assertRaises(ValueError, msg=bad) as cm:
                TTF.parse_not_own_line(bad, "inputs/qb/x.tt:3")
            self.assertIn("inputs/qb/x.tt:3", str(cm.exception))
            self.assertIn(TTF.NOTOWN_FORM, str(cm.exception))

    def test_tj0075_the_converter_and_check_tt_read_it_as_a_declaration(self):
        from taxjson.lib.tt_format import tt_to_json
        from taxjson.lib import check_tt as CK
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.tt"
            p.write_text("NOTOWN 2024-03-16 ZZQ.TO 100 qa 2024-03-01\n")
            # Was "unknown .tt action 'NOTOWN'": the run stopped.
            self.assertEqual(tt_to_json(p, "qb", equity=True)
                             ["transactions"], [])
            rep = CK.check_file(p, file="x.tt", account="qb", crypto=False,
                                equity=True)
            self.assertEqual((rep.events, rep.rows, rep.problems),
                             (1, 0, []))
            p.write_text("NOTOWN 2024-03-16 ZZQ.TO 100 qa\n")
            rep = CK.check_file(p, file="x.tt", account="qb", crypto=False,
                                equity=True)
            self.assertEqual([(x.severity, x.where) for x in rep.problems],
                             [(CK.ERROR, "x.tt:1")])

    def test_tj0075_check_tt_names_a_line_no_pairing_can_match(self):
        from taxjson.lib import check_tt as CK
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                settings_for("canada", year=2024, source_currencies=[])
                + '[accounts.qb]\ntype = "taxable"\n'
                + '[accounts.tfsa]\ntype = "sheltered"\n')
            for acct, text in (
                    ("qb", "NOTOWN 2024-03-16 ZZQ.TO 100 qa 2024-03-01\n"
                           "NOTOWN 2024-03-16 ZZQ.TO 100 qb 2024-03-10\n"),
                    ("tfsa", "NOTOWN 2024-03-16 ZZQ.TO 100 qb 2024-03-01\n")):
                (root / "inputs" / acct).mkdir(parents=True)
                (root / "inputs" / acct / "x.tt").write_text(text)
            accounts = {"qb": {"type": "taxable"},
                        "tfsa": {"type": "sheltered"}}
            doc = CK.check_project(root, accounts)
            got = sorted((p["severity"], p["where"]) for p in doc["problems"])
            self.assertEqual(got, [(CK.WARNING, "inputs/qb/x.tt:1"),
                                   (CK.WARNING, "inputs/tfsa/x.tt:1")])
            msgs = " ".join(p["message"] for p in doc["problems"])
            self.assertIn("'qa', which taxjson.toml does not declare", msgs)
            self.assertIn("account tfsa is not a taxable account", msgs)
            self.assertEqual(doc["errors"], 0)


if __name__ == "__main__":
    unittest.main()
