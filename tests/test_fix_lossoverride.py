"""Filing positions against the loss rule: `.tt` ALLOWLOSS lines (owner
request, 2026-10-08; lib/loss_overrides, tax-logic CA-SL-18 / US-WASH-25).

The superficial-loss / wash-sale engines are the law's mechanical test.
`ALLOWLOSS <sale date> <symbol> [<qty>] reason="..."` in the taxable
account that sold takes a filing position against ONE denial: the loss
stays allowed, the replacement's ACB / basis is not raised by it, every
other sale's verdict is unchanged, and the position is listed everywhere
(the run's Warning, `sum`, `sum --json`, `wash-sales`, the checklist). A
line that names no denied sale, or two, stops the run.

Every fixture is SYNTHETIC: invented QZ* tickers, made-up amounts.
"""
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule, rule_absent
from tax_rules.dual import cli, gains_both, projects_both, tx

from taxjson.lib import loss_overrides as LO
from taxjson.lib.country import CANADA as _CA, USA as _US


def _ov(date, symbol="QZA.TO", qty=None, account="margin",
        reason="counted from the trade date it is outside the window"):
    return {"account": account, "date": date, "symbol": symbol,
            "qty": qty, "reason": reason, "where": "inputs/margin/x.tt:1",
            "line": f"ALLOWLOSS {date} {symbol} reason=\"{reason}\""}


def _one(country, book, *, sheltered=(), **req):
    """`run_gains` of the book under ONE country (a single-country test
    may run only its own engine)."""
    import contextlib
    import copy
    import io
    from taxjson.lib.pipeline import GainsRequest, run_gains
    with contextlib.redirect_stderr(io.StringIO()):
        return run_gains(copy.deepcopy(list(book)),
                         copy.deepcopy(list(sheltered)), (),
                         req=GainsRequest(country=country, taxable=True,
                                          **req))


def _gains(res):
    return [t for t in res["transactions"] if "gain" in t and "qty" in t]


def _cost(res, symbol="QZA.TO"):
    return sum(float(i["total_cost"]) for i in res["inventory"]
               if i["symbol"] == symbol and not i.get("account") in ("plan",))


# A loss sold 2025-12-19 (settled 12-22) and a replacement bought in the
# window; amounts in CAD for both engines (no conversion in the engine).
_BUY = tx("BUYSELL", "2025-11-03", "QZA.TO", 100, -5000, currency="CAD",
          settle="2025-11-04")
_SELL = tx("BUYSELL", "2025-12-19", "QZA.TO", -100, 4000, currency="CAD",
           settle="2025-12-22")


# ------------------------------------------------------------- the line

class TestLine(unittest.TestCase):

    def test_parse(self):
        p = LO.parse_line('ALLOWLOSS 2025-12-19 qza.to 1,000 '
                          'reason="bought #2: 32 days on trade dates"  # x')
        self.assertEqual((p["date"], p["symbol"], p["qty"], p["reason"]),
                         ("2025-12-19", "QZA.TO", 1000.0,
                          "bought #2: 32 days on trade dates"))
        p = LO.parse_line('ALLOWLOSS 2025-12-19 QZA.TO reason="r"')
        self.assertIsNone(p["qty"])
        self.assertIsNone(LO.parse_line("BUYSELL 2025-01-01 10:00:00 A"))
        self.assertIsNone(LO.parse_line("# ALLOWLOSS 2025-12-19 A"))

    def test_malformed_lines_name_the_form(self):
        for bad, word in (
                ("ALLOWLOSS 2025-12-19 QZA.TO", "needs a reason"),
                ('ALLOWLOSS 2025-12-19 QZA.TO reason=""', "needs a reason"),
                ('ALLOWLOSS QZA.TO 2025-12-19 reason="r"', "date first"),
                ('ALLOWLOSS 2025-13-19 QZA.TO reason="r"', "YYYY-MM-DD"),
                ('ALLOWLOSS 2025-12-19 QZA.TO ten reason="r"',
                 "not a number"),
                ('ALLOWLOSS 2025-12-19 QZA.TO 0 reason="r"',
                 "not a number"),
                ('ALLOWLOSS 2025-12-19 reason="r"', "expected"),
                ('ALLOWLOSS 2099-12-19 QZA.TO reason="r"', "future"),
                ('ALLOWLOSS 2025-12-19 QZA.TO reason="a" "b"',
                 "one quoted")):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError) as cm:
                    LO.parse_line(bad, "x.tt:3")
                self.assertIn("x.tt:3", str(cm.exception))
                self.assertIn(word, str(cm.exception))

    def test_convert_tt_skips_the_line(self):
        from taxjson.bin.taxjson_convert_tt import tt_to_json
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.tt"
            p.write_text("BUYSELL 2025-03-03 10:00:00 QZA.TO 100 CAD 10.00 "
                         "1000.00 0.00\n"
                         'ALLOWLOSS 2025-03-03 QZA.TO reason="r"\n')
            doc = tt_to_json(p, "margin")
        self.assertEqual([t["action"] for t in doc["transactions"]],
                         ["BUYSELL"])

    def test_read_project_refuses_a_sheltered_account_and_twins(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for acct in ("margin", "plan"):
                (root / "inputs" / acct).mkdir(parents=True)
            line = 'ALLOWLOSS 2025-12-19 QZA.TO reason="r"\n'
            (root / "inputs/margin/a.tt").write_text(line + line)
            (root / "inputs/plan/p.tt").write_text(line)
            with self.assertRaises(LO.LossOverrideError) as cm:
                LO.read_project(root, {"margin": {"type": "taxable"},
                                       "plan": {"type": "sheltered"}})
        msg = str(cm.exception)
        self.assertIn("inputs/margin/a.tt:2", msg)
        self.assertIn("named twice", msg)
        self.assertIn("inputs/plan/p.tt:1", msg)
        self.assertIn("sheltered", msg)


# ---------------------------------------------------------- the engines

class TestEngines(unittest.TestCase):

    @rule("CA-SL-18")
    def test_canada_deferred_denial_allowed_and_no_acb_bump(self):
        book = [_BUY, _SELL,
                tx("BUYSELL", "2026-01-05", "QZA.TO", 50, -1900,
                   currency="CAD", settle="2026-01-06")]
        base = _one(_CA, book)
        self.assertAlmostEqual(_gains(base)[0]["disallowed_amount"], 500.0)
        self.assertAlmostEqual(_cost(base), 2400.0)     # 1,900 + 500
        res = _one(_CA, book, loss_overrides=(_ov("2025-12-19"),))
        g = _gains(res)[0]
        self.assertAlmostEqual(g["gain"], -1000.0)
        self.assertEqual(g["disallowed_amount"], 0.0)
        self.assertAlmostEqual(_cost(res), 1900.0)       # no bump
        self.assertEqual(res["wash_sales"], [])
        it, = res["loss_overrides"]
        self.assertEqual(it["status"], LO.STATUS_APPLIED)
        s, = it["sales"]
        self.assertAlmostEqual(s["would_disallow"], 500.0)
        self.assertAlmostEqual(s["would_defer"], 500.0)
        self.assertEqual(s["would_permanent"], 0.0)
        self.assertEqual(g["loss_override"]["where"], "inputs/margin/x.tt:1")

    @rule("US-WASH-25")
    def test_usa_deferred_disallowance_allowed_no_basis_no_tacking(self):
        book = [_BUY, _SELL,
                tx("BUYSELL", "2026-01-05", "QZA.TO", 50, -1900,
                   currency="CAD", settle="2026-01-06")]
        base = _one(_US, book)
        self.assertAlmostEqual(_cost(base), 2400.0)
        res = _one(_US, book, loss_overrides=(_ov("2025-12-19"),))
        g = _gains(res)[0]
        self.assertAlmostEqual(g["gain"], -1000.0)
        self.assertEqual(g["disallowed_amount"], 0.0)
        self.assertAlmostEqual(_cost(res), 1900.0)
        inv, = [i for i in res["inventory"] if i["symbol"] == "QZA.TO"]
        self.assertEqual(inv["position_start_date"], "2026-01-05")
        self.assertEqual(res["wash_sales"], [])
        s, = res["loss_overrides"][0]["sales"]
        self.assertAlmostEqual(s["would_disallow"], 500.0)

    @rule("CA-SL-18")
    def test_canada_registered_call_denied_for_good_then_allowed(self):
        # The owner's case: a margin loss sold Dec 19 (settled Dec 22), a
        # call on the shares bought in the RRSP Jan 20 (settled Jan 21):
        # day 30 on settle dates, day 32 on trade dates.
        call = tx("BUYSELL", "2026-01-20", "QZA261218C00040000.TO", 1,
                  -300, currency="CAD", settle="2026-01-21", account="plan")
        base = _one(_CA, [_BUY, _SELL], sheltered=[call])
        g = _gains(base)[0]
        self.assertAlmostEqual(g["disallowed_amount"], 1000.0)
        self.assertAlmostEqual(g["permanently_disallowed"], 1000.0)
        res = _one(_CA, [_BUY, _SELL], sheltered=[call],
                   loss_overrides=(_ov("2025-12-19"),))
        g = _gains(res)[0]
        self.assertAlmostEqual(g["gain"], -1000.0)
        self.assertEqual(g["permanently_disallowed"], 0.0)
        s, = res["loss_overrides"][0]["sales"]
        self.assertAlmostEqual(s["would_permanent"], 1000.0)
        rp, = s["replacements"]
        self.assertEqual((rp["holder"], rp["days_settle"], rp["days_trade"]),
                         ("sheltered", 30, 32))
        text = " ".join(LO.describe(res["loss_overrides"][0]))
        self.assertIn("registered: denied for good", text)
        self.assertIn("30 day(s) after the sale on settle dates, 32 on "
                      "trade dates", text)

    @rule("CA-SL-18", "CA-SL-08")
    def test_canada_partial_denial_per_sale_others_unchanged(self):
        # One 50-unit rebuy inside the windows of two 100-unit losses:
        # CRA's formula per sale denies half of each (CA-SL-08). The
        # override on the first leaves the second's denial as it was and
        # its ACB addition alone on the rebuy.
        book = [tx("BUYSELL", "2025-10-01", "QZA.TO", 200, -10000,
                   currency="CAD", settle="2025-10-02"),
                tx("BUYSELL", "2025-12-01", "QZA.TO", -100, 4000,
                   currency="CAD", settle="2025-12-02"),
                tx("BUYSELL", "2025-12-05", "QZA.TO", -100, 3000,
                   currency="CAD", settle="2025-12-08"),
                tx("BUYSELL", "2025-12-10", "QZA.TO", 50, -1500,
                   currency="CAD", settle="2025-12-11")]
        base = _one(_CA, book)
        d = {g["date"]: g["disallowed_amount"] for g in _gains(base)}
        self.assertAlmostEqual(d["2025-12-01"], 500.0)
        self.assertAlmostEqual(d["2025-12-05"], 1000.0)
        self.assertAlmostEqual(_cost(base), 3000.0)      # 1,500 + both
        res = _one(_CA, book, loss_overrides=(_ov("2025-12-01"),))
        d = {g["date"]: g["disallowed_amount"] for g in _gains(res)}
        self.assertEqual(d["2025-12-01"], 0.0)
        self.assertAlmostEqual(d["2025-12-05"], 1000.0)
        self.assertAlmostEqual(_cost(res), 2500.0)       # only the second
        self.assertAlmostEqual(
            res["loss_overrides"][0]["sales"][0]["would_disallow"], 500.0)

    @rule("US-WASH-25", "US-WASH-20")
    def test_usa_override_keeps_its_replacement_shares(self):
        # §1091 matches the 50 shares to the earlier loss; the override
        # on it does not hand them to the later loss.
        book = [tx("BUYSELL", "2025-10-01", "QZA.TO", 200, -10000,
                   currency="CAD"),
                tx("BUYSELL", "2025-12-01", "QZA.TO", -100, 4000,
                   currency="CAD"),
                tx("BUYSELL", "2025-12-05", "QZA.TO", -100, 3000,
                   currency="CAD"),
                tx("BUYSELL", "2025-12-10", "QZA.TO", 50, -1500,
                   currency="CAD")]
        base = _one(_US, book)
        d = {g["date"]: g["disallowed_amount"] for g in _gains(base)}
        self.assertAlmostEqual(d["2025-12-01"], 500.0)
        self.assertEqual(d["2025-12-05"], 0.0)
        res = _one(_US, book, loss_overrides=(_ov("2025-12-01"),))
        d = {g["date"]: g["disallowed_amount"] for g in _gains(res)}
        self.assertEqual(d["2025-12-01"], 0.0)
        self.assertEqual(d["2025-12-05"], 0.0)
        self.assertAlmostEqual(_cost(res), 1500.0)

    @rule("CA-SL-18")
    @rule_absent("CA-SL-18", country="usa")
    @rule("US-WASH-12")
    def test_call_replacement_only_canada_has_a_denial_to_override(self):
        call = tx("BUYSELL", "2026-01-05", "QZA261218C00040000.TO", 1,
                  -300, currency="CAD", settle="2026-01-06")
        r = gains_both([_BUY, _SELL, call],
                       loss_overrides=(_ov("2025-12-19"),))
        self.assertEqual(r["canada"]["loss_overrides"][0]["status"],
                         LO.STATUS_APPLIED)
        self.assertEqual(r["usa"]["loss_overrides"][0]["status"],
                         LO.STATUS_UNMATCHED)

    @rule("US-WASH-25")
    @rule_absent("US-WASH-25", country="canada")
    @rule("CA-SL-02")
    def test_rebuy_sold_by_day_30_only_usa_has_one_to_override(self):
        book = [_BUY, _SELL,
                tx("BUYSELL", "2025-12-29", "QZA.TO", 100, -3900,
                   currency="CAD", settle="2025-12-30"),
                tx("BUYSELL", "2026-01-05", "QZA.TO", -100, 3950,
                   currency="CAD", settle="2026-01-06")]
        r = gains_both(book, loss_overrides=(_ov("2025-12-19"),))
        self.assertEqual(r["usa"]["loss_overrides"][0]["status"],
                         LO.STATUS_APPLIED)
        self.assertEqual(r["canada"]["loss_overrides"][0]["status"],
                         LO.STATUS_UNMATCHED)

    @rule("CA-SL-18")
    def test_unmatched_and_ambiguous(self):
        # Two denied sales of the symbol that day (a buy between them):
        # the line without units names both; with units, one.
        book = [tx("BUYSELL", "2025-10-01", "QZA.TO", 300, -15000,
                   currency="CAD"),
                tx("BUYSELL", "2025-12-01", "QZA.TO", -100, 4000,
                   currency="CAD", time="10:00:00"),
                tx("BUYSELL", "2025-12-01", "QZA.TO", 10, -400,
                   currency="CAD", time="11:00:00"),
                tx("BUYSELL", "2025-12-01", "QZA.TO", -60, 2400,
                   currency="CAD", time="12:00:00"),
                tx("BUYSELL", "2025-12-10", "QZA.TO", 200, -8000,
                   currency="CAD")]
        res = _one(_CA, book, loss_overrides=(
            _ov("2025-12-01"), _ov("2025-12-02"), _ov("2025-12-01", qty=60),
            _ov("2025-12-01", symbol="QZB.TO")))
        st = [it["status"] for it in res["loss_overrides"]]
        self.assertEqual(st, [LO.STATUS_AMBIGUOUS, LO.STATUS_UNMATCHED,
                              LO.STATUS_APPLIED, LO.STATUS_UNMATCHED])
        probs = LO.problems(res["loss_overrides"])
        self.assertEqual(len(probs), 3)
        self.assertIn("names 2 denied sales", probs[0])
        self.assertIn("add the units sold", probs[0])
        self.assertIn("no trade traded or settled that day", probs[1])
        self.assertIn("that day's trades in account margin: QZA.TO -100",
                      probs[2])

    @rule("CA-SL-01")
    @rule("US-WASH-01")
    @rule_absent("CA-SL-01", country="usa")
    def test_window_basis_ignores_tax_date(self):
        # Settle day 30, trade day 32: Canada counts settle dates whatever
        # tax_date says; the US counts trade dates whatever it says.
        book = [_BUY, _SELL,
                tx("BUYSELL", "2026-01-20", "QZA.TO", 100, -3900,
                   currency="CAD", settle="2026-01-21")]
        for td in ("settle", "trade"):
            r = gains_both(book, tax_date=td, year=2025)
            with self.subTest(tax_date=td):
                self.assertAlmostEqual(
                    r["canada"]["summary"]["total_disallowed"], 1000.0)
                self.assertEqual(
                    r["usa"]["summary"]["total_disallowed"], 0.0)

    def test_no_override_no_key(self):
        res = gains_both([_BUY, _SELL])
        for c in res:
            self.assertNotIn("loss_overrides", res[c])


# -------------------------------------------------------------- the run

_ACCTS = ('[accounts.margin]\ntype = "taxable"\n\n'
          '[accounts.plan]\ntype = "sheltered"\n')


def _files(country, line=None, call=True):
    sfx, cur = ("TO", "CAD") if country == "canada" else ("US", "USD")
    m = (f"BUYSELL 2025-11-03 10:00:00 QZA.{sfx} 100 {cur} 50.00 5000.00 "
         f"0.00\n"
         f"BUYSELL 2025-12-19 10:00:00 QZA.{sfx} -100 {cur} 40.00 4000.00 "
         f"0.00\n")
    if line:
        m += line.replace("SFX", sfx) + "\n"
    rebuy = (f"BUYSELL 2026-01-05 10:00:00 QZA.{sfx} 100 {cur} 39.00 "
             f"3900.00 0.00\n")
    return {"inputs/margin/m.tt": m, "inputs/plan/p.tt": rebuy}


class TestRun(unittest.TestCase):

    def _run(self, td, country, line):
        root = projects_both(td, accounts=_ACCTS,
                             files=_files(country, line))[country]
        return root, cli(root, "run", "--no-input")

    @rule("CA-SL-18")
    def test_canada_listed_everywhere(self):
        line = 'ALLOWLOSS 2025-12-19 QZA.SFX reason="my position"'
        with tempfile.TemporaryDirectory() as td:
            root, r = self._run(td, "canada", line)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            out = r.stdout + r.stderr
            self.assertEqual(out.count("filing position(s) taken against "
                                       "the superficial-loss rule"), 1)
            self.assertIn('Reason: "my position" (inputs/margin/m.tt:3)',
                          " ".join(out.split()))
            doc = json.loads(cli(root, "sum", "--json").stdout)
            self.assertAlmostEqual(doc["filing"]["totals"]["gain"], -1000.0)
            self.assertAlmostEqual(doc["filing"]["totals"]["denied"], 0.0)
            fp, = doc["filing_positions"]
            self.assertEqual((fp["date"], fp["symbol"], fp["reason"]),
                             ("2025-12-19", "QZA.TO", "my position"))
            self.assertAlmostEqual(fp["would_disallow"], 1000.0)
            self.assertAlmostEqual(fp["would_permanent"], 1000.0)
            self.assertTrue(fp["in_year"])
            text = " ".join(cli(root, "sum").stdout.split())
            self.assertIn("FILING POSITIONS — 1 loss(es) claimed against "
                          "the superficial-loss rule (s.54)", text)
            ws = cli(root, "wash-sales")
            self.assertIn("FILING POSITIONS", ws.stdout)
            self.assertIn("No superficial losses", ws.stdout)
            wj = json.loads(cli(root, "wash-sales", "--json").stdout)
            self.assertEqual(len(wj["filing_positions"]), 1)
            ck = json.loads(cli(root, "checklist", "--json",
                                "--quick").stdout)
            st, = [s for s in ck["steps"] if s["id"] == "filing-positions"]
            self.assertEqual(st["status"], "manual")
            self.assertIn("QZA.TO", st["detail"])

    @rule("US-WASH-25")
    def test_usa_listed(self):
        line = 'ALLOWLOSS 2025-12-19 QZA.SFX reason="my position"'
        with tempfile.TemporaryDirectory() as td:
            root, r = self._run(td, "usa", line)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("against the wash-sale rule (§1091)", r.stdout)
            doc = json.loads(cli(root, "sum", "--json").stdout)
            fp, = doc["filing_positions"]
            self.assertAlmostEqual(fp["would_permanent"], 1000.0)
            self.assertIn("an IRA", fp["why"])

    @rule("CA-SL-18")
    def test_unmatched_line_stops_the_run(self):
        line = 'ALLOWLOSS 2025-12-18 QZA.SFX reason="wrong day"'
        with tempfile.TemporaryDirectory() as td:
            _root, r = self._run(td, "canada", line)
            self.assertNotEqual(r.returncode, 0)
            out = " ".join((r.stdout + r.stderr).split())
            self.assertIn("name no single denied superficial loss", out)
            self.assertIn("inputs/margin/m.tt:3", out)

    def test_malformed_line_stops_before_the_books(self):
        line = "ALLOWLOSS 2025-12-19 QZA.SFX"
        with tempfile.TemporaryDirectory() as td:
            _root, r = self._run(td, "canada", line)
            self.assertNotEqual(r.returncode, 0)
            out = " ".join((r.stdout + r.stderr).split())
            self.assertIn("cannot be used", out)
            self.assertIn("needs a reason", out)

    def test_no_line_no_file_no_key(self):
        with tempfile.TemporaryDirectory() as td:
            root, r = self._run(td, "canada", None)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertFalse((root / "work" / LO.STATE).exists())
            doc = json.loads(cli(root, "sum", "--json").stdout)
            self.assertNotIn("filing_positions", doc)
            ck = json.loads(cli(root, "checklist", "--json",
                                "--quick").stdout)
            st, = [s for s in ck["steps"] if s["id"] == "filing-positions"]
            self.assertEqual(st["status"], "n/a")


if __name__ == "__main__":
    unittest.main()
