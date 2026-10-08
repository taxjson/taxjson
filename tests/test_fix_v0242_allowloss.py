"""ALLOWLOSS filing positions seen everywhere (pre-release review of
v0.24.2, M7 and its lows; lib/loss_overrides, CA-SL-18 / US-WASH-25).

A `.tt` ALLOWLOSS line keeps a loss the rule would deny. The return
forms (form-export: text, --json, --csv, TXF), `audit`, `wash-sales
--explain`, the US `wash-radar`, `carryover` and `handoff` said nothing
of it: the row read as an ordinary loss, the audit as "disallowed 0.00",
the explain as "no matching gains found". Each now notes the position.
The line itself: a `reason=` inside a trailing comment is not the
reason; the units name a whole sale (never one fill of it); two lines
naming one sale in different spellings are refused.

Every fixture is SYNTHETIC: invented QZ* tickers, made-up amounts.
"""
import csv
import io
import json
import tempfile
import unittest

from tax_rules import rule
from tax_rules.dual import cli, projects_both, tx

from taxjson.lib import loss_overrides as LO
from taxjson.lib.country import CANADA as _CA, USA as _US


def _ov(date, symbol="QZA.TO", qty=None, where="inputs/margin/x.tt:1"):
    return {"account": "margin", "date": date, "symbol": symbol,
            "qty": qty, "reason": "r", "where": where,
            "line": f"ALLOWLOSS {date} {symbol} reason=\"r\""}


def _one(country, book, **req):
    import contextlib
    import copy
    from taxjson.lib.pipeline import GainsRequest, run_gains
    with contextlib.redirect_stderr(io.StringIO()):
        return run_gains(copy.deepcopy(list(book)), (), (),
                         req=GainsRequest(country=country, taxable=True,
                                          **req))


# ------------------------------------------------------------- the line

class TestLine(unittest.TestCase):

    def test_reason_inside_a_comment_is_not_the_reason(self):
        with self.assertRaises(ValueError) as cm:
            LO.parse_line('ALLOWLOSS 2025-12-01 QZA.TO  # reason="x"',
                          "m.tt:4")
        self.assertIn("needs a reason", str(cm.exception))
        p = LO.parse_line('ALLOWLOSS 2025-12-01 QZA.TO reason="a #1" '
                          '# reason="b"')
        self.assertEqual(p["reason"], "a #1")


# Two fills of one same-day sell-down (one sale) and a rebuy in the window.
_FILLS = [tx("BUYSELL", "2025-11-03", "QZA.TO", 100, -5000, currency="CAD",
             settle="2025-11-04"),
          tx("BUYSELL", "2025-12-01", "QZA.TO", -60, 2400, currency="CAD",
             settle="2025-12-02", time="10:00:00"),
          tx("BUYSELL", "2025-12-01", "QZA.TO", -40, 1600, currency="CAD",
             settle="2025-12-02", time="10:01:00"),
          tx("BUYSELL", "2025-12-10", "QZA.TO", 100, -3900, currency="CAD",
             settle="2025-12-11")]


class TestMatching(unittest.TestCase):

    @rule("CA-SL-18")
    def test_one_fills_units_name_no_sale_canada(self):
        res = _one(_CA, _FILLS, loss_overrides=(_ov("2025-12-01", qty=60),))
        it, = res["loss_overrides"]
        self.assertEqual(it["status"], LO.STATUS_UNMATCHED)
        self.assertTrue(all(g["disallowed_amount"] > 0
                            for g in res["transactions"]
                            if "gain" in g and "qty" in g))
        prob, = LO.problems(res["loss_overrides"])
        self.assertIn("60 units is one fill of the 100-unit sale", prob)
        self.assertIn("write its units, 100, or none", prob)
        res = _one(_CA, _FILLS, loss_overrides=(_ov("2025-12-01", qty=100),))
        self.assertEqual(res["loss_overrides"][0]["status"],
                         LO.STATUS_APPLIED)

    @rule("US-WASH-25")
    def test_one_fills_units_name_no_sale_usa(self):
        res = _one(_US, _FILLS, loss_overrides=(_ov("2025-12-01", qty=40),))
        self.assertEqual(res["loss_overrides"][0]["status"],
                         LO.STATUS_UNMATCHED)
        self.assertIn("one fill", LO.problems(res["loss_overrides"])[0])

    @rule("CA-SL-18")
    def test_the_same_sale_in_two_spellings_is_refused(self):
        for second in (_ov("2025-12-01", qty=100, where="m.tt:2"),
                       _ov("2025-12-02", where="m.tt:2")):   # settle date
            with self.subTest(second=second["date"]):
                res = _one(_CA, _FILLS, loss_overrides=(
                    _ov("2025-12-01", where="m.tt:1"), second))
                self.assertEqual([i["status"]
                                  for i in res["loss_overrides"]],
                                 [LO.STATUS_APPLIED] * 2)
                prob, = LO.problems(res["loss_overrides"])
                self.assertIn("m.tt:2", prob)
                self.assertIn("names the same sale as m.tt:1", prob)

    @rule("CA-SL-18")
    def test_each_fill_carries_its_own_share(self):
        res = _one(_CA, _FILLS, loss_overrides=(_ov("2025-12-01"),))
        notes = [g["loss_override"] for g in res["transactions"]
                 if g.get("loss_override")]
        self.assertEqual(len(notes), 2)
        self.assertAlmostEqual(sum(n["would_disallow"] for n in notes),
                               1000.0)
        self.assertAlmostEqual(notes[0]["sale_would_disallow"], 1000.0)


# -------------------------------------------------------------- the run

def _files(country, extra=""):
    sfx, cur = ("TO", "CAD") if country == "canada" else ("US", "USD")
    return {"inputs/margin/m.tt": (
        f"BUYSELL 2025-11-03 10:00:00 QZA.{sfx} 100 {cur} 50.00 5000.00 "
        f"0.00\n"
        f"BUYSELL 2025-12-01 10:00:00 QZA.{sfx} -100 {cur} 40.00 4000.00 "
        f"0.00\n"
        f"BUYSELL 2025-12-10 10:00:00 QZA.{sfx} 100 {cur} 39.00 3900.00 "
        f"0.00\n"
        f'ALLOWLOSS 2025-12-01 QZA.{sfx} reason="my position"\n' + extra)}


def _flat(text):
    return " ".join(text.split())


class TestOutputs(unittest.TestCase):
    """One project per country; every output names the position."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.roots = {}
        for c in ("canada", "usa"):
            root = projects_both(f"{cls._td.name}/{c}",
                                 files=_files(c))[c]
            r = cli(root, "run", "--no-input")
            assert r.returncode == 0, r.stdout + r.stderr
            cls.roots[c] = root

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    @rule("CA-SL-18")
    def test_schedule3_all_formats(self):
        root = self.roots["canada"]
        text = _flat(cli(root, "form-export").stdout)
        self.assertIn("QZA.TO: filing position: ALLOWLOSS "
                      "inputs/margin/m.tt:4, the superficial-loss rule "
                      "would deny 1,000.00; the loss is claimed in full. "
                      "Reason: \"my position\"", text)
        self.assertIn("A row with a filing position (your .tt ALLOWLOSS "
                      "line, CA-SL-18)", text)
        doc = json.loads(cli(root, "form-export", "--json").stdout)
        row, = doc["rows"]
        self.assertEqual(row["gain"], -1000.0)
        fp, = row["filing_positions"]
        self.assertEqual((fp["where"], fp["would_disallow"]),
                         ("inputs/margin/m.tt:4", 1000.0))
        self.assertIn("filing position", row["notes"])
        with tempfile.TemporaryDirectory() as td:
            out = f"{td}/s3.csv"
            self.assertEqual(cli(root, "form-export", "--csv",
                                 out).returncode, 0)
            with open(out, encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        self.assertIn("filing position: ALLOWLOSS", rows[0]["notes"])

    @rule("US-WASH-25")
    def test_form_8949_all_formats(self):
        root = self.roots["usa"]
        text = _flat(cli(root, "form-export").stdout)
        self.assertIn("100 QZA.US sold 2025-12-01: filing position: "
                      "ALLOWLOSS inputs/margin/m.tt:4, the wash-sale rule "
                      "would disallow 1,000.00", text)
        self.assertIn("box 1g", text)
        doc = json.loads(cli(root, "form-export", "--json").stdout)
        row, = doc["part_I"]
        self.assertEqual((row["code"], row["adjustment"], row["gain"]),
                         ("", 0.0, -1000.0))
        self.assertEqual(row["filing_position"]["would_disallow"], 1000.0)
        with tempfile.TemporaryDirectory() as td:
            out = f"{td}/f.csv"
            self.assertEqual(cli(root, "form-export", "--csv",
                                 out).returncode, 0)
            with open(out, encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        self.assertIn("filing position: ALLOWLOSS", rows[0]["note"])
        r = cli(root, "form-export", "--form", "txf")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("1 TXF record(s) carry a filing position",
                      _flat(r.stderr))
        self.assertNotIn("$1000.00\n^", r.stdout)       # no wash amount

    def test_audit_both(self):
        for c, word in (("canada", "superficial-loss rule would deny"),
                        ("usa", "wash-sale rule would disallow")):
            with self.subTest(country=c):
                root = self.roots[c]
                text = _flat(cli(root, "audit", "--date", "2025-12-01",
                                 "--no-trace").stdout)
                self.assertIn(f"POSITION filing position: ALLOWLOSS "
                              f"inputs/margin/m.tt:4, the {word} "
                              f"1,000.00", text)
                summ = cli(root, "audit", "--summary").stdout
                self.assertIn("ALLOWLOSS(rule: 1000.00)", summ)
                doc = json.loads(cli(root, "audit", "--json").stdout)
                ev, = [e for e in doc["events"]
                       if e["date"] == "2025-12-01"]
                self.assertEqual(ev["loss_override"]["where"],
                                 "inputs/margin/m.tt:4")

    def test_wash_sales_explain_both(self):
        for c in ("canada", "usa"):
            with self.subTest(country=c):
                r = cli(self.roots[c], "wash-sales", "--explain")
                self.assertNotIn("no matching gains found", r.stderr)
                self.assertIn("position: Filing position: ALLOWLOSS "
                              "inputs/margin/m.tt:4", _flat(r.stdout))

    @rule("US-WASH-25")
    def test_us_wash_radar_honours_the_position(self):
        r = cli(self.roots["usa"], "wash-radar", "--date", "2025-12-15")
        text = _flat(r.stdout)
        self.assertIn("claimed as your filing position against §1091 "
                      "(ALLOWLOSS inputs/margin/m.tt:4): the rule would "
                      "disallow $1000.00; the books allow it", text)
        self.assertNotIn("$1000.00 is added to the replacement's basis",
                         text)

    def test_carryover_both(self):
        for c, word in (("canada", "deny"), ("usa", "disallow")):
            with self.subTest(country=c):
                root = self.roots[c]
                text = _flat(cli(root, "carryover").stdout)
                self.assertIn("Includes 1 filing position(s)", text)
                self.assertIn(f"2025 QZA.{'TO' if c == 'canada' else 'US'}"
                              f" loss 1,000.00 claimed, the rule would "
                              f"{word} 1,000.00", text)
                doc = json.loads(cli(root, "carryover", "--json").stdout)
                self.assertEqual(len(doc["filing_positions"]), 1)

    def test_handoff_names_positions_up_to_the_closed_year(self):
        from taxjson.bin import taxjson_run as R
        for c in ("canada", "usa"):
            with self.subTest(country=c):
                root = self.roots[c]
                settings = R.load_config(root)["settings"]
                rep = {"notes": []}
                R._handoff_positions(rep, root / "work", 2025, settings)
                self.assertEqual(len(rep["filing_positions"]), 1)
                self.assertIn("project must hold the same line",
                              rep["notes"][0])
                rep = {"notes": []}
                R._handoff_positions(rep, root / "work", 2024, settings)
                self.assertNotIn("filing_positions", rep)


class TestSumOtherYear(unittest.TestCase):

    @rule("CA-SL-18")
    def test_a_position_on_last_years_sale_is_not_in_the_totals(self):
        with tempfile.TemporaryDirectory() as td:
            root = projects_both(td, year=2026, files=_files(
                "canada", "BUYSELL 2026-03-02 10:00:00 QZB.TO 10 CAD 5.00 "
                          "50.00 0.00\n"
                          "BUYSELL 2026-03-09 10:00:00 QZB.TO -10 CAD 6.00 "
                          "60.00 0.00\n"))["canada"]
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            text = _flat(cli(root, "sum").stdout)
        self.assertIn("FILING POSITIONS — 1 loss(es) claimed against the "
                      "superficial-loss rule (s.54) (.tt ALLOWLOSS lines); "
                      "sales of another year: not in the totals above",
                      text)
        self.assertNotIn("the totals above include them", text)


if __name__ == "__main__":
    unittest.main()
