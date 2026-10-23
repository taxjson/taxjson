"""Workflow audit 2026-10-22, items 8, 13 and 14 (item 7 is in
test_fix_batch_c_opening.TestSanityTomlDate):

* 8: `buy-check ZZH` (the bare root) of a never-held fund a ticker.map
  SIMILAR line names gives the same SAFE* caveat as `buy-check ZZH.TO`;
* 13: sell-check names a SIMILAR look-alike bought within 30 days in a
  registered account or IRA only (the radar files it CLEAR);
* 14: an s.86.1 spin-off's cash in lieu sold by hand in a .tt file, as
  the UNBOOKED warning says, ends the warning (and `run --strict` passes).

Every fixture is SYNTHETIC: made-up tickers ZZ*, account 99900001.
"""
import contextlib
import io
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from _qa_project import console, gains, project, tj, toml
from tax_rules import rule


def _day(n):
    return (date.today() + timedelta(days=n)).isoformat()


def _tt(day, sym, qty, cur, price, total, fee="0"):
    return f"BUYSELL {day} 09:30:00 {sym} {qty} {cur} {price} {total} {fee}\n"


def _flat(text):
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# 8: buy-check by the bare root of a never-held SIMILAR fund

class TestBuyCheckBareRootSimilar(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = project(
            cls.tmp.name, "pl",
            {"margin/trades.tt":
             _tt(_day(-120), "ZZG.TO", 50, "CAD", "40.00", "2004.95", "4.95")
             + _tt(_day(-10), "ZZG.TO", -50, "CAD", "30.00", "1495.05",
                   "4.95")},
            ticker_map="SIMILAR ZZG.TO ZZH.TO\n")
        (cls.root / "taxjson.toml").write_text(toml(year=date.today().year))
        tj(cls.root, "run", "--no-input")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @rule("CA-SL-20")
    def test_bare_root_gives_the_caveat(self):
        bare = tj(self.root, "buy-check", "ZZH", check=False)
        full = tj(self.root, "buy-check", "ZZH.TO", check=False)
        self.assertEqual(bare.returncode, 0, console(bare))
        self.assertIn("ZZH: SAFE*", bare.stdout)
        self.assertIn("ZZH.TO: SAFE*", full.stdout)
        self.assertIn("ZZG.TO: COOLING — a loss sold within the past 30 "
                      "days; ticker.map calls it similar to ZZH (SIMILAR).",
                      _flat(bare.stdout))

    @rule("CA-SL-20")
    def test_unrelated_root_stays_plain_safe(self):
        r = tj(self.root, "buy-check", "ZZQ", check=False)
        self.assertIn("ZZQ: SAFE", r.stdout)
        self.assertNotIn("SAFE*", r.stdout)
        self.assertNotIn("SIMILAR", r.stdout)


# ---------------------------------------------------------------------------
# 13: sell-check and a look-alike bought only in a registered account / IRA

class TestSellCheckShelteredSimilarBuy(unittest.TestCase):

    def _project(self, country, files, sheltered, ticker_map):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        extra = f'[accounts.{sheltered}]\ntype = "sheltered"\n'
        root = project(tmp.name, country, files, country=country,
                       ticker_map=ticker_map, extra_accounts=extra)
        (root / "taxjson.toml").write_text(
            toml(country, year=date.today().year, extra_accounts=extra))
        tj(root, "run", "--no-input")
        return root

    @rule("CA-SL-20")
    def test_canada_tfsa_buy(self):
        root = self._project(
            "canada",
            {"margin/trades.tt": _tt(_day(-200), "ZZB.TO", 100, "CAD", "30",
                                     "3009.95", "9.95"),
             "tfsa/trades.tt": _tt(_day(-7), "ZZK.TO", 40, "CAD", "10",
                                   "409.95", "9.95")},
            "tfsa", "SIMILAR ZZB.TO ZZK.TO\n")
        for q in ("ZZB", "ZZB.TO"):
            r = tj(root, "sell-check", q, check=False)
            self.assertEqual(r.returncode, 0, console(r))
            text = _flat(r.stdout)
            self.assertIn(f"{q}: SAFE*", text)
            self.assertIn(f"ZZK.TO: bought within the past 30 days in a "
                          f"registered account ('tfsa', {_day(-7)}); "
                          f"ticker.map calls it similar to {q} (SIMILAR).",
                          text)
            self.assertIn("identical property (s.54)", text)
        # The look-alike's own sell-check is unchanged.
        r = tj(root, "sell-check", "ZZK.TO", check=False)
        self.assertNotIn("SIMILAR", r.stdout)

    @rule("US-WASH-26")
    def test_usa_ira_buy(self):
        root = self._project(
            "usa",
            {"margin/trades.tt": _tt(_day(-200), "ZZX.US", 100, "USD", "50",
                                     "5001", "1"),
             "roth/trades.tt": _tt(_day(-9), "ZZV.US", 50, "USD", "30",
                                   "1501", "1")},
            "roth", "SIMILAR ZZX.US ZZV.US\n")
        r = tj(root, "sell-check", "ZZX", check=False)
        self.assertEqual(r.returncode, 0, console(r))
        text = _flat(r.stdout)
        self.assertIn("ZZX: SAFE*", text)
        self.assertIn(f"ZZV.US: bought within the past 30 days in an IRA "
                      f"('roth', {_day(-9)}); ticker.map calls it similar "
                      f"to ZZX (SIMILAR).", text)
        self.assertIn("substantially identical securities (§1091)", text)
        self.assertNotIn("s.54", text)

    @rule("CA-SL-20")
    def test_old_sheltered_buy_gives_no_caveat(self):
        root = self._project(
            "canada",
            {"margin/trades.tt": _tt(_day(-200), "ZZB.TO", 100, "CAD", "30",
                                     "3009.95", "9.95"),
             "tfsa/trades.tt": _tt(_day(-60), "ZZK.TO", 40, "CAD", "10",
                                   "409.95", "9.95")},
            "tfsa", "SIMILAR ZZB.TO ZZK.TO\n")
        r = tj(root, "sell-check", "ZZB", check=False)
        self.assertIn("ZZB: SAFE", r.stdout)
        self.assertNotIn("SAFE*", r.stdout)
        self.assertNotIn("ZZK.TO", r.stdout)


# ---------------------------------------------------------------------------
# 14: an s.86.1 spin-off's cash in lieu booked by hand in a .tt file

RBC_HEAD = ('"Date","Activity","Symbol","Symbol Description","Quantity",'
            '"Price","Settlement Date","Account","Value","Currency",'
            '"Description"\n')


def _rbc(day, act, sym, symdesc, qty, value, desc, price=""):
    cells = [day, act, sym, symdesc, qty, price, day, "99900001", value,
             "CAD", desc]
    return ",".join(f'"{c}"' for c in cells) + "\n"


RBC_SPIN = (RBC_HEAD
            + _rbc("2026-01-05", "Buy", "ZZP", "ZZP PARENT CORP", "105",
                   "-1050", "ZZP PARENT CORP", price="10")
            + _rbc("2026-06-15", "Reorganization", "ZZS", "ZZS SPINCO INC",
                   "10", "0", "DIS - ZZS SPINCO INC SPINOFF ON 105 SHS FROM "
                   "SEC# X123456 ZZP PARENT CORP REC 06/01/26 PAY 06/15/26")
            + _rbc("2026-06-18", "Reorganization", "ZZS", "ZZS SPINCO INC",
                   "0", "12.00",
                   "CIL - ZZS SPINCO INC CASH IN LIEU OF FRAC SHARES"))

FRACTION_TT = ("BUYSELL 2026-06-18 10:00:00 ZZS.TO 0.5 CAD 0 0\n"
               "BUYSELL 2026-06-18 10:00:01 ZZS.TO -0.5 CAD 24 12\n")


def _spin_event():
    from taxjson.lib.corp_actions import CorporateAction
    return CorporateAction(
        date="2026-06-15", time="", action_type="spinoff",
        source_symbol="ZZP.TO", source_isin="", target_symbol="ZZS.TO",
        target_isin="", ratio_new=10.0, ratio_old=105.0, qty_disposed=0.0,
        qty_received=10.0, fmv=0.0, currency="CAD", target_currency="CAD",
        account="acct", event_id="spin-zzp-zzs-test",
        cash_in_lieu=12.0, cash_in_lieu_currency="CAD",
        cash_in_lieu_date="2026-06-18")


def _warned(event, tt_sales):
    from taxjson.lib.corp_actions import warn_unsold_spinoff_cil
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        warn_unsold_spinoff_cil(event, "rollover_s_86_1", [], tt_sales)
    return buf.getvalue()


class TestSpinoffCilBookedByHand(unittest.TestCase):

    @rule("CA-CORP-05")
    def test_tt_sale_of_the_fraction_ends_the_warning(self):
        from taxjson.lib.corp_actions import read_tt_sales
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "fraction.tt"
            p.write_text("# the fraction, by hand\n" + FRACTION_TT)
            sales = read_tt_sales([p])
        self.assertEqual(sales, [{"date": "2026-06-18", "symbol": "ZZS.TO",
                                  "quantity": -0.5,
                                  "source": "fraction.tt:3"}])
        ev = _spin_event()
        text = _warned(ev, sales)
        self.assertNotIn("UNBOOKED", text)
        self.assertIn("cash in lieu is booked by hand (fraction.tt:3: a "
                      "sale of 0.5 ZZS.TO on 2026-06-18)", _flat(text))
        self.assertIn("UNBOOKED", _warned(ev, []))
        self.assertIn("dated from the spin-off to 2026-08-07",
                      _flat(_warned(ev, [])))

    @rule("CA-CORP-05")
    def test_other_sales_are_not_the_fraction(self):
        ev = _spin_event()
        for sale in ({"date": "2026-06-18", "symbol": "ZZS.TO",
                      "quantity": -10.0, "source": "x.tt:1"},     # whole
                     {"date": "2026-06-18", "symbol": "ZZP.TO",
                      "quantity": -0.5, "source": "x.tt:1"},      # parent
                     {"date": "2026-06-10", "symbol": "ZZS.TO",
                      "quantity": -0.5, "source": "x.tt:1"},      # before
                     {"date": "2026-09-30", "symbol": "ZZS.TO",
                      "quantity": -0.5, "source": "x.tt:1"}):     # late
            self.assertIn("UNBOOKED", _warned(ev, [sale]), sale)

    @rule("CA-CORP-05")
    def test_run_and_strict_after_the_fix(self):
        with tempfile.TemporaryDirectory() as d:
            root = project(d, "rbc", {"acct/rbc_2026.csv": RBC_SPIN})
            (root / "taxjson.toml").write_text(
                toml(year=2026).replace("[accounts.margin]",
                                        "[accounts.acct]"))
            tj(root, "run", "--no-input", check=False)
            tj(root, "elect", "acct", "--set",
               "20260615-zzp-zzs-9ee7=rollover_s_86_1",
               "--hint", "allocated_acb_cad=210")
            r = tj(root, "run", "--no-input")
            self.assertIn("UNBOOKED", console(r))
            (root / "inputs" / "acct" / "fraction.tt").write_text(
                FRACTION_TT)
            r = tj(root, "run", "--no-input", "--strict", check=False)
            self.assertEqual(r.returncode, 0, console(r))
            self.assertNotIn("UNBOOKED", console(r))
            sale = [g for g in gains(root, "acct")
                    if g.get("symbol") == "ZZS.TO"]
            self.assertEqual([(g["qty"], g["proceeds"]) for g in sale],
                             [(0.5, 12.0)])


if __name__ == "__main__":
    unittest.main()
