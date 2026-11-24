"""The year hand-off's Dec-31 rows and closed sales (the outside audit of
2026-11-23, its probes R23 to R25):

- TJ-5065 a boundary row's amount is compared to the cent as the broker
  stated it: a correction under the 0.5% tolerance is reported for
  amendment, a converted amount that moved only with the rate is not; a
  record written before the change (no native amounts; a lock closed by
  v1.1.0, tests/fixtures/handoff) is compared as before, with one note
  naming what refreshes it;
- TJ-5066 the row's income amount (a dividend's gross, as the income
  summary counts it) is recorded and compared, so a gross / withholding
  split corrected after the close is reported;
- TJ-5180 each amount is compared in the currency it was stated in, from
  the decimal as written: a CAD withholding on a USD dividend in CAD,
  never a net the run took at a rate;
- TJ-5181 close-year refuses when a converted row's native books are
  missing; the rows compared only within 0.5% are counted in a note;
- TJ-5183 a row the closed year counted that this project's books no
  longer hold is reported where the account's broker exports reach its
  day; a .tt file, an OPENING line or books that start later say nothing;
- TJ-5186 a row re-dated, re-counted or re-spelled since is one item;
  re-dated past Dec 31, an item to amend, never "needs no change";
- TJ-5185 a day's TAX rows two dividends share are compared once;
- two rows alike each take their own record row, unchanged pairs first;
- TJ-5067 a sale another of this project's accounts recorded is a
  distinct sale, never this account's sale reported again: no "double";
  one account's own duplicate still is one.

Synthetic data only (made-up tickers and accounts, amounts under a
thousand)."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import _hermetic
from _paths import FIXTURES
from _qa_project import tj
from tax_rules import rule
from taxjson.model import money as M
from taxjson.rules import ids as R

_SETTINGS = {"country": "canada", "year": 2026, "base_currency": "CAD"}
_CFG = {"settings": _SETTINGS, "accounts": {"aa": {"type": "taxable"}}}
_CFG25 = {"settings": dict(_SETTINGS, year=2025),
          "accounts": {"aa": {"type": "taxable"}}}
_OLDER_NOTE = "predates the native amounts"
_USDCAD = float(_hermetic.SYNTHETIC_RATES["USDCAD"])


def _tmp(test) -> Path:
    td = tempfile.TemporaryDirectory()
    test.addCleanup(td.cleanup)
    return Path(td.name)


def _div(net, gross=None, **extra):
    """A Dec 31 dividend of account aa (the audit's probe row)."""
    r = {"action": "DIVIDEND", "date": "2025-12-31",
         "date_settle": "2025-12-31", "symbol": "ZZH.TO", "quantity": 100,
         "net_amount": net, "currency": "CAD", "account": "aa"}
    if gross is not None:
        r["gross_amount"] = gross
    r.update(extra)
    return r


def _usd_div(base_net, native_net):
    """The same dividend paid in USD: the base books' row converted
    (relabelled CAD, `trade_currency` USD) and its native books' row."""
    base = _div(base_net, id="d1", trade_currency="USD", symbol="ZZH.US")
    native = _div(native_net, id="d1", currency="USD", symbol="ZZH.US")
    return base, native


def _sale(net):
    """A Dec 31 sale of 40 ZZD.TO in account aa, settled that day."""
    return {"action": "BUYSELL", "date": "2025-12-31",
            "date_settle": "2025-12-31", "symbol": "ZZD.TO",
            "quantity": -40, "net_amount": net, "currency": "CAD",
            "account": "aa"}


def _tax(net, cur="CAD", symbol="ZZV.US"):
    """A Dec 31 TAX row (tax withheld) of account aa."""
    return {"id": f"t-{symbol}", "action": "TAX", "type": "tax",
            "date": "2025-12-31", "date_settle": "2025-12-31",
            "symbol": symbol, "quantity": 100, "net_amount": net,
            "gross_amount": net, "currency": cur, "account": "aa"}


def _cross(rate, tax=7.31):
    """A USD dividend of 37.00 gross with a same-day CAD TAX row of `tax`,
    read at USDCAD `rate` (the audit's e/e2): the base books' rows, and
    the native books', whose dividend net the run took at that rate."""
    gross = M.shown(37.0 * rate, 2)
    base = [_div(M.shown(gross - tax, 2), gross, id="d1",
                 trade_currency="USD", symbol="ZZV.US"), _tax(tax)]
    native = [_div(round(37.0 - tax / rate, 8), 37.0, id="d1",
                   currency="USD", symbol="ZZV.US"), _tax(tax)]
    return base, native


def _books(test, rows, native=None) -> Path:
    """A work/ folder holding account aa's base books (and its native
    books, `native`)."""
    work = _tmp(test) / "work"
    work.mkdir()
    (work / "aa_base.json").write_text(json.dumps({"transactions": rows}))
    if native is not None:
        (work / "aa_raw.json").write_text(json.dumps(
            {"transactions": native}))
    return work


def _record(test, rows, native=None):
    """The 2025 record as close-year writes its Dec-31 rows."""
    from taxjson.lib import handoff as H
    return {"year": 2025, "boundary_rows": H.boundary_rows(
        _books(test, rows, native), _CFG25, 2025)}


def _issues(test, rec, rows, native=None, notes=None):
    from taxjson.lib import handoff as H
    return H._boundary_issues(_books(test, rows, native), _CFG, rec,
                              "trade", set(), notes)


# ------------------------------------------------------------- TJ-5065
class TestCorrectionToTheCent(unittest.TestCase):
    """TJ-5065: once the row is identified (the 0.5% tolerance), its
    amount is compared to the cent."""

    @rule(R.CA_RPT_08)
    def test_the_audit_probe_502_and_503_are_both_reported(self):
        rec = _record(self, [_div(500.0, 500.0)])
        for amount, diff in ((502.0, 2.0), (503.0, 3.0)):
            out = _issues(self, rec, [_div(amount, amount)])
            self.assertEqual(len(out), 1, out)
            it = out[0]
            self.assertEqual((it["amount"], it["recorded_amount"],
                              it["difference"]), (amount, 500.0, diff))
            why = " ".join(it["why"].split())
            self.assertIn(f"at 500.00; this project reads it at "
                          f"{amount:.2f}", why)
            self.assertIn(f"reported {diff:.2f} less", why)
            self.assertIn("amend 2025 for the difference", why)

    @rule(R.CA_RPT_08)
    def test_a_cent_is_a_correction(self):
        rec = _record(self, [_div(500.0, 500.0)])
        out = _issues(self, rec, [_div(500.01, 500.01)])
        self.assertEqual(len(out), 1, out)
        self.assertEqual(out[0]["difference"], 0.01)
        self.assertEqual(_issues(self, rec, [_div(500.0, 500.0)]), [])

    @rule(R.CA_RPT_08)
    def test_the_record_keeps_the_native_amounts(self):
        base, native = _usd_div(675.0, 500.0)
        rows = _record(self, [base, _div(10.0, symbol="ZZK.TO")],
                       [native])["boundary_rows"]
        usd = next(x for x in rows if x["symbol"] == "ZZH.US")
        self.assertEqual(
            {k: usd[k] for k in ("net", "income", "native_currency",
                                 "native_net", "native_income")},
            {"net": 675.0, "income": 675.0, "native_currency": "USD",
             "native_net": 500.0, "native_income": 500.0})
        cad = next(x for x in rows if x["symbol"] == "ZZK.TO")
        self.assertEqual((cad["native_currency"], cad["native_net"],
                          cad["native_income"]), ("CAD", 10.0, 10.0))
        # The native books holding the id twice: the row of its action,
        # day and symbol, never another (the review's probe d); two
        # alike in all: none, compared within the tolerance.
        other = _div(7.0, id="d1", currency="USD", symbol="OTHER.US")
        for native_rows, want in (([other, native], 500.0),
                                  ([native, other], 500.0),
                                  ([native, dict(native, net_amount=7.0)],
                                   None)):
            row = _record(self, [base], native_rows)["boundary_rows"][0]
            self.assertEqual(row.get("native_net"), want, native_rows)
        # A converted row whose native books are missing: close-year
        # refuses, nothing is written (TJ-5181).
        from taxjson.lib import handoff as H
        with self.assertRaises(H.BooksError) as e:
            _record(self, [base])
        self.assertIn("aa_raw.json is missing", str(e.exception))
        self.assertIn("re-run `tjs run`", str(e.exception))

    @rule(R.CA_RPT_08)
    def test_a_rate_that_moved_is_no_correction(self):
        """The USD dividend of 500 read at 1.35 when the year closed and
        at 1.38 now: 2.2% apart in CAD, the same 500 USD: no issue."""
        base, native = _usd_div(675.0, 500.0)
        rec = _record(self, [base], [native])
        base, native = _usd_div(690.0, 500.0)
        self.assertEqual(_issues(self, rec, [base], [native]), [])

    @rule(R.CA_RPT_08)
    def test_a_native_cent_is_reported_in_its_currency(self):
        base, native = _usd_div(675.0, 500.0)
        rec = _record(self, [base], [native])
        base, native = _usd_div(675.01, 500.01)
        out = _issues(self, rec, [base], [native])
        self.assertEqual(len(out), 1, out)
        it = out[0]
        self.assertEqual((it["currency"], it["native_amount"],
                          it["recorded_native_amount"]),
                         ("USD", 500.01, 500.0))
        why = " ".join(it["why"].split())
        self.assertIn("at 500.00 USD; this project reads it at 500.01 USD",
                      why)
        self.assertIn("reported 0.01 USD less", why)


# ------------------------------------------------------------- TJ-5180
class TestAmountsAsStated(unittest.TestCase):
    """TJ-5180: each amount in the currency the broker stated it in, to
    the cent from the decimal as written."""

    @rule(R.CA_RPT_08)
    def test_a_withholding_in_another_currency_is_compared_in_its_own(self):
        rec = _record(self, *_cross(1.35))
        row = rec["boundary_rows"][0]
        self.assertEqual((row["native_currency"], row["native_income"],
                          row["native_withheld"]),
                         ("USD", 37.0, {"CAD": 7.31}))
        self.assertNotIn("native_net", row)      # the run's, at a rate
        for rate in (1.37, 1.40):
            self.assertEqual(_issues(self, rec, *_cross(rate)), [], rate)
        out = _issues(self, rec, *_cross(1.35, tax=7.50))
        self.assertEqual(len(out), 1, out)
        it = out[0]
        self.assertEqual((it["withheld"], it["recorded_withheld"],
                          it["withheld_difference"],
                          it["native_income_difference"]),
                         ({"CAD": 7.5}, {"CAD": 7.31}, {"CAD": 0.19}, 0.0))
        why = " ".join(it["why"].split())
        self.assertIn("at 37.00 USD gross, 7.31 CAD withheld; this project "
                      "reads it at 37.00 USD gross, 7.50 CAD withheld", why)
        self.assertIn("the same income but 0.19 CAD less tax withheld", why)

    @rule(R.CA_RPT_08)
    def test_two_dividends_of_a_day_compare_its_tax_rows_once(self):
        """TJ-5185 (the review's probe4 t2 to t4): the day's TAX rows of a
        security two dividends share are recorded and compared as one
        figure per currency: a change is one item; a gross corrected with
        the TAX rows unchanged is no change of tax withheld. A TAX row
        with no currency is in the dividend's."""
        def day(tax, g1=100.0, g2=50.0, t1=15.0):
            base = [_div(g1 - 10, g1, id="d1", trade_currency="USD",
                         symbol="ZZV.US"),
                    _div(g2 - 5, g2, id="d2", trade_currency="USD",
                         symbol="ZZV.US")]
            native = [_div(g1 - 10, g1, id="d1", currency="USD",
                           symbol="ZZV.US"),
                      _div(g2 - 5, g2, id="d2", currency="USD",
                           symbol="ZZV.US"),
                      _tax(t1, "USD"), _tax(tax, "")]
            return base, native
        rec = _record(self, *day(7.5))
        rows = rec["boundary_rows"]
        self.assertEqual([r["native_withheld_day"] for r in rows],
                         [{"USD": 22.5}, {"USD": 22.5}])
        self.assertFalse(any("native_withheld" in r or "native_net" in r
                             for r in rows))
        self.assertEqual(_issues(self, rec, *day(7.5)), [])
        for tax, t1, want in ((9.0, 15.0, 1.5), (7.51, 15.0, 0.01),
                              (7.5, 15.01, 0.01)):
            out = _issues(self, rec, *day(tax, t1=t1))
            self.assertEqual([i["withheld_difference"] for i in out],
                             [{"USD": want}], out)
        why = " ".join(out[0]["why"].split())
        self.assertIn("counted the tax withheld on the 2025-12-31 dividends "
                      "of ZZV.US in aa at 22.50 USD; this project reads "
                      "22.51 USD", why)
        out = _issues(self, rec, *day(7.5, g1=110.0))
        self.assertEqual([(i["native_income_difference"], "withheld" in i)
                          for i in out], [(10.0, False)], out)

    @rule(R.CA_RPT_08)
    def test_a_decimal_a_computation_left_is_no_change(self):
        """Cents as cents, half up from the decimal as written (the
        review's probe1 h1, probe4 h3): 57.375, 57.37499999 and 57.38
        are all 57.38."""
        rec = _record(self, [_div(57.375, 67.5)])
        for net in (57.37499999, 57.38, 57.379):
            self.assertEqual(_issues(self, rec, [_div(net, 67.5)]), [], net)
        self.assertEqual(len(_issues(self, rec, [_div(57.365, 67.5)])), 1)


# ------------------------------------------------------------- TJ-5181
_CFGC = {"settings": _SETTINGS,
         "accounts": {"cc": {"type": "taxable", "crypto": True}}}
_CFGC25 = {"settings": dict(_SETTINGS, year=2025),
           "accounts": {"cc": {"type": "taxable", "crypto": True}}}


def _crypto_books(test, priced):
    """Crypto account cc's books: a staking reward the fill stage priced
    at `priced` USD (its parse holds no amount) and a CAD sale as
    parsed."""
    reward = {"id": "s1", "action": "INTEREST", "date": "2025-12-31",
              "date_settle": "2025-12-31", "symbol": "ZZCOIN",
              "quantity": 0.01, "account": "cc"}
    sale = {"id": "s2", "action": "BUYSELL", "date": "2025-12-31",
            "date_settle": "2025-12-31", "symbol": "ZZCOIN",
            "quantity": -0.5, "net_amount": 90.0, "gross_amount": 90.0,
            "currency": "CAD", "account": "cc"}
    work = _tmp(test) / "work"
    work.mkdir()
    for name, rows in (
            ("cc_sorted.json", [dict(reward, net_amount=0.0, currency="USD"),
                                sale]),
            ("cc_filled.json", [dict(reward, net_amount=priced,
                                     currency="USD"), sale]),
            ("cc_base.json", [dict(reward, net_amount=M.shown(
                priced * _USDCAD, 2), currency="CAD", trade_currency="USD"),
                sale])):
        (work / name).write_text(json.dumps({"transactions": rows}))
    return work


class TestComparedWithinTheTolerance(unittest.TestCase):
    """TJ-5181: a converted row's native books missing at the close is a
    refusal (TestCorrectionToTheCent); a row compared only within 0.5%
    at the hand-off is counted in a note, with why."""

    @staticmethod
    def _section(work, cfg, rec):
        from taxjson.lib import handoff as H
        notes = []
        out = H._boundary_section(work, cfg, rec, "trade", set(), notes)
        return out, [n for n in notes if "not to the cent" in n]

    @rule(R.CA_RPT_08)
    def test_without_this_projects_native_books(self):
        base, native = _usd_div(675.0, 500.0)
        rec = _record(self, [base], [native])
        base, native = _usd_div(676.0, 500.74)
        out, said = self._section(_books(self, [base]), _CFG, rec)
        self.assertEqual(out, [])
        self.assertEqual(len(said), 1, said)
        self.assertIn("1 row: work/aa_raw.json is missing (re-run `tjs "
                      "run`", said[0])
        out, said = self._section(_books(self, [base], [native]), _CFG, rec)
        self.assertEqual(([i["native_difference"] for i in out], said),
                         ([0.74], []))

    @rule(R.CA_RPT_08)
    def test_a_crypto_row_taxjson_priced(self):
        from taxjson.lib import handoff as H
        rows = H.boundary_rows(_crypto_books(self, 30.0), _CFGC25, 2025)
        got = {r["action"]: r for r in rows}
        self.assertNotIn("native_currency", got["INTEREST"])
        self.assertEqual(got["BUYSELL"]["native_net"], 90.0)
        out, said = self._section(_crypto_books(self, 30.1), _CFGC,
                                  {"year": 2025, "boundary_rows": rows})
        self.assertEqual(out, [])
        self.assertEqual(len(said), 1, said)
        self.assertIn("1 row: its amounts are taxjson's (a crypto row "
                      "priced from the price cache)", said[0])


# ------------------------------------------------------------- TJ-5183
class TestRowsTheBooksNoLongerHold(unittest.TestCase):
    """TJ-5183: a record row the closed year counted (a sale or income
    from Dec 30) that this project's books of its account no longer
    hold, where the account's broker exports reach its day (they hold a
    row of any security dated on or before it): never a .tt file's rows,
    an OPENING line or a row taxjson made. These books' rows name no
    file (`source`), as an export's; a .tt row names its file."""

    @rule(R.CA_RPT_08)
    def test_a_row_the_books_reach_but_no_longer_hold(self):
        rec = _record(self, [_div(500.0, 500.0, id="A"),
                             _div(500.0, 500.0, id="B")])
        out = _issues(self, rec, [_div(500.0, 500.0, id="A")])
        self.assertEqual(len(out), 1, out)
        it = out[0]
        self.assertEqual((it["amount"], it["recorded_amount"],
                          it["difference"]), (0.0, 500.0, -500.0))
        why = " ".join(it["why"].split())
        self.assertIn("this project's books of aa, whose broker exports "
                      "reach that day (they start 2025-12-31), no longer "
                      "have it: the broker removed or reversed it after the "
                      "close, or the export that held it is not in this "
                      "project", why)
        self.assertEqual((it["income"], it["recorded_income"],
                          it["income_difference"]), (0.0, 500.0, -500.0))
        self.assertIn("amend 2025 for it; if it does, add the export", why)
        buy = dict(_sale(-100.0), date="2025-06-02", date_settle="2025-06-03",
                   quantity=100)
        rec = _record(self, [buy, _sale(240.0), _div(500.0, 500.0)])
        out = _issues(self, rec, [buy, _div(500.0, 500.0)])
        self.assertEqual([(i["action"], i["recorded_amount"]) for i in out],
                         [("BUYSELL", 240.0)])

    @rule(R.CA_RPT_08)
    def test_books_that_start_after_its_day_say_nothing(self):
        rec = _record(self, [_div(500.0, 500.0)])
        later = {"action": "BUYSELL", "date": "2026-01-05",
                 "date_settle": "2026-01-06", "symbol": "ZZH.TO",
                 "quantity": 10, "net_amount": -50.0, "currency": "CAD",
                 "account": "aa"}
        self.assertEqual(_issues(self, rec, [later]), [])

    @rule(R.CA_RPT_08)
    def test_a_removed_row_in_its_own_currency(self):
        """The item carries the row's amounts in its currency."""
        base, native = _usd_div(675.0, 500.0)
        other = _div(10.0, 10.0, symbol="ZZK.TO", id="k1")
        rec = _record(self, [base, other], [native, other])
        out = _issues(self, rec, [other], [other])
        self.assertEqual([(i["currency"], i["native_income"],
                           i["recorded_native_income"],
                           i["native_income_difference"],
                           i["recorded_native_amount"]) for i in out],
                         [("USD", 0.0, 500.0, -500.0, 500.0)], out)
        self.assertIn("at 500.00 USD, but", " ".join(out[0]["why"].split()))

    @rule(R.CA_RPT_08)
    def test_an_opening_position_file_says_nothing(self):
        """The next year's opening file holds the lots still held, dated
        at their purchase days, the sold symbol's too: a Dec 30 sale it
        rightly lacks is not removed. An OPENING line, the same."""
        sale = dict(_sale(240.0), date="2025-12-30", date_settle="2025-12-30")
        rec = _record(self, [sale])
        lot = dict(_sale(-500.0), date="2025-06-03", date_settle="2025-06-04",
                   quantity=60, source="aa_start.tt")
        jan = dict(_sale(300.0), date="2026-01-12", date_settle="2026-01-13",
                   source="aa_start.tt")
        self.assertEqual(_issues(self, rec, [lot, jan]), [])
        opening = dict(lot, type="opening", source="aa_2026.tt")
        self.assertEqual(_issues(self, rec, [opening]), [])
        self.assertEqual(_issues(self, rec, [dict(opening, source="")]), [])

    @rule(R.CA_RPT_08)
    def test_a_dividend_with_only_other_symbols_before_it(self):
        """A broker export reaching its day with another security's rows
        only: the dividend it no longer holds is reported. A .tt file's
        rows, whatever they hold, are no evidence: an account of .tt
        files only reports no removed row."""
        rec = _record(self, [_div(500.0, 500.0)])
        buy = dict(_sale(-100.0), symbol="ZZA.TO", date="2025-06-02",
                   date_settle="2025-06-03", quantity=10)
        out = _issues(self, rec, [dict(buy, source="aa_2025.csv")])
        self.assertEqual([(i["symbol"], i["recorded_amount"]) for i in out],
                         [("ZZH.TO", 500.0)])
        self.assertIn("(they start 2025-06-02)",
                      " ".join(out[0]["why"].split()))
        tt = [dict(buy, source="rows.tt"),
              _div(20.0, 20.0, symbol="ZZA.TO", date="2025-09-30",
                   date_settle="2025-09-30", source="rows.tt"),
              dict(_sale(60.0), date="2025-11-14", date_settle="2025-11-14",
                   quantity=-5, source="rows.tt")]
        self.assertEqual(_issues(self, rec, tt), [])

    @rule(R.CA_RPT_08)
    def test_a_row_re_dated_re_counted_or_re_spelled(self):
        """TJ-5186 (the review's probe5 r10 to r12): one item saying what
        changed, never "never had it" beside "no longer have it"."""
        buy = dict(_sale(-1000.0), symbol="ZZH.TO", date="2025-03-03",
                   date_settle="2025-03-04", quantity=100)
        rec = _record(self, [buy, _div(500.0, 500.0)])
        for change, now in (
                ("date", _div(500.0, 500.0, date="2025-12-30",
                              date_settle="2025-12-30")),
                ("quantity", _div(500.0, 500.0, quantity=110)),
                ("symbol", _div(500.0, 500.0, symbol="ZZH"))):
            out = _issues(self, rec, [buy, now])
            self.assertEqual([(i["changed"], i["difference"]) for i in out],
                             [([change], 0.0)], out)
            why = " ".join(out[0]["why"].split())
            self.assertIn("this project has the same income at the same "
                          "amount, re-", why)
            self.assertIn("so the 2025 return needs no change for it", why)
        out = _issues(self, rec, [buy, _div(500.0, 500.0, date="2025-12-30",
                                            date_settle="2025-12-30")])
        self.assertIn("re-dated from 2025-12-31 to 2025-12-30",
                      out[0]["why"])

    def _crossed(self, out, change, year=2026):
        self.assertEqual([(i["changed"], i.get("crossed_year_end"))
                          for i in out], [(change, True)], out)
        why = " ".join(out[0]["why"].split())
        self.assertNotIn("needs no change", why)
        self.assertNotIn("never had it", why)
        self.assertNotIn("no longer have it", why)
        return why

    @rule(R.CA_RPT_08)
    def test_a_dividend_re_dated_into_the_next_year(self):
        """A Dec 31 dividend the corrected export dates Jan 2: the 2025
        return reported it and this project counts it in 2026, so it is
        an item to amend, never "re-dated, needs no change"; in an
        account of .tt files too (the row is here); a dividend of the
        same amount in March is another row."""
        rec = _record(self, [_div(500.0, 500.0)])
        moved = _div(500.0, 500.0, date="2026-01-02",
                     date_settle="2026-01-02")
        for src in ("", "rows.tt"):
            why = self._crossed(_issues(self, rec, [dict(moved, source=src)]),
                                ["date"])
            self.assertIn("re-dated from 2025-12-31 to 2026-01-02 (the export "
                          "was corrected after the close), which counts in "
                          "2026: the 2025 return reported it and this project "
                          "counts it in 2026 too", why)
            self.assertIn("amend 2025 to take it out", why)
        march = _div(500.0, 500.0, date="2026-03-31", date_settle="2026-03-31")
        buy = dict(_sale(-1000.0), symbol="ZZH.TO", date="2025-03-03",
                   date_settle="2025-03-04", quantity=100)
        out = _issues(self, rec, [buy, march])
        self.assertEqual([("crossed_year_end" in i, i["recorded_amount"])
                          for i in out], [(False, 500.0)], out)
        self.assertIn("no longer have it", out[0]["why"])

    @rule(R.CA_RPT_08)
    def test_a_sale_re_dated_across_the_year_end(self):
        """A Dec 31 sale whose trade, settlement or tax date the corrected
        export moves past Dec 31 is an item to amend; one re-dated within
        2025 stays the one no-change item."""
        from taxjson.lib import handoff as H
        rec = _record(self, [_sale(240.0)])
        jan = dict(_sale(240.0), date="2026-01-02", date_settle="2026-01-05")
        why = self._crossed(_issues(self, rec, [jan]), ["date"])
        self.assertIn("which counts in 2026", why)
        early = dict(_sale(240.0), date="2025-12-30", date_settle="2025-12-30")
        rec2 = _record(self, [early])
        late = dict(_sale(240.0), date_settle="2026-01-02")
        out = _issues(self, rec2, [late])
        self.assertEqual(out[0]["date_settle"], "2026-01-02")
        why = self._crossed(out, ["date"])
        self.assertIn("settling 2026-01-02", why)
        self.assertIn("this project still counts it in 2025 on its trade "
                      "date", why)
        out = H._boundary_issues(_books(self, [late]), _CFG, rec2, "settle",
                                 set())
        self.assertIn("which counts in 2026", self._crossed(out, ["date"]))
        within = dict(_sale(240.0), date="2025-12-30", date_settle="2025-12-30")
        out = _issues(self, rec, [within])
        self.assertEqual([(i["changed"], "crossed_year_end" in i)
                          for i in out], [(["date"], False)], out)
        self.assertIn("so the 2025 return needs no change for it",
                      " ".join(out[0]["why"].split()))


# ---------------------------------------------------- rows alike, a sale
class TestRowsAlikeAndASale(unittest.TestCase):
    """Two record rows a day alike: each row here takes its own, the
    unchanged pairs first, whatever the order; a sale corrected by a cent
    is reported as income is."""

    @rule(R.CA_RPT_08)
    def test_two_rows_alike_each_take_their_own(self):
        rec = _record(self, [_div(500.0, 500.0, id="A"),
                             _div(502.0, 502.0, id="B")])
        for here in ([_div(501.0, 501.0, id="B"), _div(500.0, 500.0, id="A")],
                     [_div(500.0, 500.0, id="A"), _div(501.0, 501.0, id="B")]):
            out = _issues(self, rec, here)
            self.assertEqual([(i["recorded_amount"], i["amount"])
                              for i in out], [(502.0, 501.0)], out)

    @rule(R.CA_RPT_08)
    def test_a_sale_corrected_by_a_cent(self):
        rec = _record(self, [_sale(240.0)])
        self.assertEqual(_issues(self, rec, [_sale(240.0)]), [])
        out = _issues(self, rec, [_sale(240.01)])
        self.assertEqual([(i["action"], i["difference"]) for i in out],
                         [("BUYSELL", 0.01)])
        why = " ".join(out[0]["why"].split())
        self.assertIn("counted this sale in 2025 at 240.00; this project "
                      "reads it at 240.01", why)


_TOML = ('[settings]\nyear = {year}\ncountry = "canada"\n'
         'base_currency = "CAD"\nsource_currencies = ["USD"]\n'
         'province = "ON"\n{prior}\n[accounts.a]\ntype = "taxable"\n')
_ROWS = ("# made-up rows\n"
         "BUYSELL 2025-06-02 10:00:00 ZZU.US 100 USD 10.00 1000.00\n"
         "DIVIDEND 2025-12-31 09:30:00 ZZU.US 100 USD 0 50.00 {net}\n")
_TAX_ROWS = ("# made-up rows\n"
             "BUYSELL 2025-06-02 10:00:00 ZZU.US 100 USD 10.00 1000.00\n"
             "BUYSELL 2025-06-02 10:01:00 ZZV.US 100 USD 10.00 1000.00\n"
             "DIVIDEND 2025-12-31 09:30:00 ZZU.US 100 USD 0.30 30.00\n"
             "TAX 2025-12-31 09:30:00 ZZU.US 100 USD 0 {tax}\n"
             "DIVIDEND 2025-12-31 09:30:00 ZZV.US 100 USD 0.37 37.00\n"
             "TAX 2025-12-31 09:30:00 ZZV.US 100 CAD 0.0731 7.31\n")
# The rows the fixture lock was closed from (TestALockClosedByV110).
_FIXTURE_ROWS = ("# made-up rows\n"
                 "BUYSELL 2025-06-02 10:00:00 ZZU.US 100 USD 10.00 1000.00\n"
                 "BUYSELL 2025-06-02 10:01:00 ZZC.TO 100 CAD 10.00 1000.00\n"
                 "DIVIDEND 2025-12-31 09:30:00 ZZU.US 100 USD 0 50.00 {usd}\n"
                 "DIVIDEND 2025-12-31 09:30:00 ZZC.TO 100 CAD 5.00 {cad}\n"
                 "BUYSELL 2025-12-31 10:01:00 ZZC.TO -40 CAD 6.00 240.00\n")


class TestNativeAmountsThroughTheRun(unittest.TestCase):
    """TJ-5065 through `tjs run`, `close-year` and `handoff`: a USD
    dividend's native amounts are recorded from the account's native
    books (its row found by id), and a correction of a cent in USD is
    reported, though its CAD amount is within the tolerance."""

    def _year(self, top, year, net, rows=None):
        root = top / str(year)
        (root / "inputs" / "a").mkdir(parents=True)
        prior = ('prior_year_record = "../2025/filed/2025.json"\n'
                 if year == 2026 else "")
        (root / "taxjson.toml").write_text(_TOML.format(year=year,
                                                         prior=prior))
        (root / "ticker.map").write_text("")
        (root / "inputs" / "a" / "rows.tt").write_text(
            rows if rows is not None else _ROWS.format(net=net))
        return root

    @rule(R.CA_RPT_08)
    def test_a_usd_cent_reported_through_the_run(self):
        top = _tmp(self)
        old = self._year(top, 2025, "42.50")
        tj(old, "run", "--no-input")
        tj(old, "close-year", "--yes", "--force")
        row = json.loads((old / "filed" / "2025.json").read_text())[
            "boundary_rows"][0]
        self.assertEqual((row["native_currency"], row["native_net"],
                          row["native_income"], row["income"]),
                         ("USD", 42.5, 50.0, M.shown(50.0 * _USDCAD, 2)))
        new = self._year(top, 2026, "42.50")
        tj(new, "run", "--no-input")
        rep = json.loads(tj(new, "handoff", "--json").stdout)
        self.assertEqual((rep["problems"], rep["boundary"]), (0, []), rep)
        (new / "inputs" / "a" / "rows.tt").write_text(_ROWS.format(
            net="42.51"))
        tj(new, "run", "--no-input")
        r = tj(new, "handoff", "--json", check=False)
        rep = json.loads(r.stdout)
        self.assertEqual((r.returncode, len(rep["boundary"])), (1, 1), rep)
        it = rep["boundary"][0]
        self.assertEqual((it["currency"], it["native_amount"],
                          it["recorded_native_amount"]), ("USD", 42.51, 42.5))
        self.assertIn("0.01 USD more tax withheld",
                      " ".join(it["why"].split()))

    @rule(R.CA_RPT_08)
    def test_close_year_refuses_without_the_native_books(self):
        """TJ-5181 (the audit's e/rob): nothing is written."""
        old = self._year(_tmp(self), 2025, "42.50")
        tj(old, "run", "--no-input")
        (old / "work" / "a_raw.json").unlink()
        r = tj(old, "close-year", "--yes", "--force", check=False)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertFalse((old / "filed" / "2025.json").exists())
        said = " ".join((r.stdout + r.stderr).split())
        self.assertIn("work/a_raw.json is missing", said)
        self.assertIn("re-run `tjs run`", said)

    @rule(R.CA_RPT_08)
    def test_same_day_tax_rows_through_the_run(self):
        """TJ-5180 (the audit's e/e2v4): a USD dividend's USD TAX row and
        another's CAD TAX row, recorded as stated; the USD one corrected
        from 4.50 to 7.50 is reported, in USD."""
        top = _tmp(self)
        old = self._year(top, 2025, "42.50", rows=_TAX_ROWS.format(tax="4.50"))
        tj(old, "run", "--no-input")
        tj(old, "close-year", "--yes", "--force")
        rows = {r["symbol"]: r for r in json.loads(
            (old / "filed" / "2025.json").read_text())["boundary_rows"]}
        self.assertEqual(
            [(rows[s]["native_income"], rows[s]["native_withheld"])
             for s in ("ZZU.US", "ZZV.US")],
            [(30.0, {"USD": 4.5}), (37.0, {"CAD": 7.31})])
        new = self._year(top, 2026, "42.50", rows=_TAX_ROWS.format(tax="4.50"))
        tj(new, "run", "--no-input")
        rep = json.loads(tj(new, "handoff", "--json").stdout)
        self.assertEqual(rep["boundary"], [], rep)
        (new / "inputs" / "a" / "rows.tt").write_text(
            _TAX_ROWS.format(tax="7.50"))
        tj(new, "run", "--no-input")
        rep = json.loads(tj(new, "handoff", "--json", check=False).stdout)
        self.assertEqual([(i["symbol"], i["withheld_difference"])
                          for i in rep["boundary"]],
                         [("ZZU.US", {"USD": 3.0})], rep)
        self.assertIn("the same income but 3.00 USD less tax withheld",
                      " ".join(rep["boundary"][0]["why"].split()))


# A broker export (Questrade's layout, made-up rows and account).
_QT = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
       "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
       "Account #,Activity Type,Account Type\n")


def _qt(trade, settle, action, sym, qty, price):
    gross = abs(qty) * price
    net = -gross if action == "Buy" else gross
    return (f"{trade} 09:30:00 AM,{settle} 12:00:00 AM,{action},{sym},D,"
            f"{qty},{price:.2f},{gross:.2f},0.00,{net:.2f},CAD,99900099,"
            f"Trades,Individual\n")


class TestRemovedThroughABrokerExport(unittest.TestCase):
    """TJ-5183 through `tjs run`, `close-year` and `handoff`: a Dec 30
    sale the 2025 export held and the corrected export, which reaches
    that day, no longer has is reported; the same project's rows as a
    .tt file report nothing."""

    @rule(R.CA_RPT_08)
    def test_a_sale_the_corrected_export_no_longer_has(self):
        rows = [_qt("2025-03-03", "2025-03-04", "Buy", "ZZX.TO", 100, 10.0),
                _qt("2025-06-02", "2025-06-03", "Buy", "ZZB.TO", 50, 20.0),
                _qt("2025-12-29", "2025-12-30", "Sell", "ZZB.TO", -50, 22.0)]
        top = _tmp(self)
        for year, body in ((2025, rows), (2026, rows[:2])):
            root = top / str(year)
            (root / "inputs" / "a").mkdir(parents=True)
            prior = ('prior_year_record = "../2025/filed/2025.json"\n'
                     if year == 2026 else "")
            (root / "taxjson.toml").write_text(
                _TOML.format(year=year, prior=prior))
            (root / "ticker.map").write_text("")
            (root / "inputs" / "a" / "questrade.csv").write_text(
                _QT + "".join(body))
            tj(root, "run", "--no-input")
            if year == 2025:
                tj(root, "close-year", "--yes", "--force")
        rep = json.loads(tj(top / "2026", "handoff", "--json",
                            check=False).stdout)
        self.assertEqual([(i["symbol"], i["action"], i["recorded_amount"])
                          for i in rep["boundary"]],
                         [("ZZB.TO", "BUYSELL", 1100.0)], rep)
        self.assertIn("whose broker exports reach that day",
                      " ".join(rep["boundary"][0]["why"].split()))


# ------------------------------------------------- a lock closed earlier
class TestALockClosedByV110(unittest.TestCase):
    """TJ-5065: tests/fixtures/handoff/lock_closed_by_v1_1_0.json is the
    lock `tjs run` and `close-year` wrote for _FIXTURE_ROWS at 9988cf485
    (taxjson 1.1.0's record, before TJ-5065): its rows are compared as
    before, at the converted net within 0.5%, with one note."""

    @rule(R.CA_RPT_08)
    def test_compared_as_before_with_one_note(self):
        top = _tmp(self)
        (top / "2025" / "filed").mkdir(parents=True)
        shutil.copy(FIXTURES / "handoff" / "lock_closed_by_v1_1_0.json",
                    top / "2025" / "filed" / "2025.json")
        new = TestNativeAmountsThroughTheRun._year(
            self, top, 2026, "", rows=_FIXTURE_ROWS.format(cad="500.00",
                                                           usd="42.50"))
        tj(new, "run", "--no-input")
        rep = json.loads(tj(new, "handoff", "--json").stdout)
        self.assertEqual((rep["problems"], rep["boundary"]), (0, []), rep)
        self.assertEqual(len([n for n in rep["notes"] if _OLDER_NOTE in n]),
                         1, rep["notes"])
        # 3.00 on 500.00 CAD (0.6%) is seen; 0.01 USD on 42.50 is not.
        (new / "inputs" / "a" / "rows.tt").write_text(
            _FIXTURE_ROWS.format(cad="503.00", usd="42.51"))
        tj(new, "run", "--no-input")
        rep = json.loads(tj(new, "handoff", "--json", check=False).stdout)
        self.assertEqual([(i["symbol"], i["difference"])
                          for i in rep["boundary"]], [("ZZC.TO", 3.0)], rep)
        self.assertEqual(len([n for n in rep["notes"] if _OLDER_NOTE in n]),
                         1, rep["notes"])


# ------------------------------------------------------------- TJ-5066
class TestIncomeAmount(unittest.TestCase):
    """TJ-5066: the row's income amount, a dividend's gross, is the one
    the income summary taxes, and a corrected split is reported."""

    @rule(R.CA_RPT_08)
    def test_the_audit_probe_gross_600_net_500(self):
        from taxjson.lib.income_summary import income_amount, summarize_income
        before, after = _div(500.0, 500.0), _div(500.0, 600.0)
        rec = _record(self, [before])
        self.assertEqual(rec["boundary_rows"][0]["income"], 500.0)
        out = _issues(self, rec, [after])
        self.assertEqual(len(out), 1, out)
        it = out[0]
        self.assertEqual((it["amount"], it["recorded_amount"], it["income"],
                          it["recorded_income"], it["income_difference"],
                          it["difference"]),
                         (500.0, 500.0, 600.0, 500.0, 100.0, 0.0))
        self.assertEqual((it["withheld"], it["recorded_withheld"],
                          it["withheld_difference"]),
                         ({"CAD": 100.0}, {"CAD": 0.0}, {"CAD": 100.0}))
        why = " ".join(it["why"].split())
        self.assertIn("at 500.00 gross, 0.00 withheld; this project reads it "
                      "at 600.00 gross, 100.00 withheld", why)
        self.assertIn("reported 100.00 less, and 100.00 less tax withheld",
                      why)
        # The amount recorded is the income summary's.
        for row in (before, after):
            got = summarize_income([row], 2025)["ticker_stats"]["ZZH.TO"]
            self.assertEqual(got["CAD"]["div"], income_amount(row))

    @rule(R.CA_RPT_08)
    def test_a_withholding_split_anew_is_reported(self):
        rec = _record(self, [_div(510.0, 600.0)])
        out = _issues(self, rec, [_div(500.0, 600.0)])
        self.assertEqual(len(out), 1, out)
        why = " ".join(out[0]["why"].split())
        self.assertIn("the same income but 10.00 less tax withheld", why)
        self.assertIn("if it says 100.00 withheld", why)


# ------------------------------------------------- a record closed earlier
class TestOlderRecord(unittest.TestCase):
    """TJ-5065: a record written before the native amounts is compared as
    before (the converted net within 0.5%), and one note says so."""

    def _section(self, rec, rows):
        from taxjson.lib import handoff as H
        notes = []
        out = H._boundary_section(_books(self, rows), _CFG, rec, "trade",
                                  set(), notes)
        return out, [n for n in notes if _OLDER_NOTE in n]

    @staticmethod
    def _older():
        return {"year": 2025, "boundary_rows": [
            {"account": "aa", "action": a, "symbol": s, "date": "2025-12-31",
             "date_settle": "2025-12-31", "quantity": q, "net": n,
             "tax_date": "2025-12-31"}
            for a, s, q, n in (("DIVIDEND", "ZZH.TO", 100, 500.0),
                               ("INTEREST", "CASH", 0, 10.0))]}

    @rule(R.CA_RPT_08)
    def test_compared_as_before_with_one_note(self):
        rows = [_div(502.0, 600.0),
                _div(10.0, action="INTEREST", symbol="CASH", quantity=0)]
        out, said = self._section(self._older(), rows)
        self.assertEqual(out, [])
        self.assertEqual(len(said), 1, said)
        self.assertIn("close-year --force", said[0])
        rows[0] = _div(503.0, 503.0)
        out, said = self._section(self._older(), rows)
        self.assertEqual([i["difference"] for i in out], [3.0])
        self.assertEqual(len(said), 1, said)

    @rule(R.CA_RPT_08)
    def test_a_current_record_has_no_such_note(self):
        rec = _record(self, [_div(500.0, 500.0)])
        self.assertEqual(self._section(rec, [_div(500.0, 500.0)]), ([], []))


# ------------------------------------------------------------- TJ-5067
_CFG2 = {"settings": _SETTINGS,
         "accounts": {"aa": {"type": "taxable"}, "bb": {"type": "taxable"}}}


def _bs(d, ds, q, net, acct):
    return {"action": "BUYSELL", "date": d, "date_settle": ds,
            "time": "10:00:00", "symbol": "ZZQ.TO", "quantity": q,
            "price": abs(net / q), "net_amount": net, "currency": "CAD",
            "account": acct}


def _opening(acct):
    return {"action": "OPENING_BALANCE", "date": "2025-06-02",
            "date_settle": "2025-06-02", "symbol": "ZZQ.TO", "quantity": 10,
            "net_amount": -50.0, "currency": "CAD", "account": acct}


def _jan_sale(acct):
    """This project's January 2 sale of 10 for 100, in `acct`."""
    return {"symbol": "ZZQ.TO", "account": acct, "date": "2026-01-02",
            "date_settle": "2026-01-05", "qty": -10, "proceeds": 100.0,
            "cost": 50.0, "gain": 50.0, "currency": "CAD"}


def _sold(acct):
    """The record's December 30 sale of 10 for 100, in `acct`."""
    return {"account": acct, "symbol": "ZZQ.TO", "date": "2025-12-30",
            "date_settle": "2025-12-31", "qty": -10.0, "proceeds": 100.0,
            "cost": 50.0, "gain": 50.0, "denied": 0.0}


def _boundary(c):
    """The record's Dec-31 row of its sale `c`."""
    return {"group": "equity", "account": c["account"], "action": "BUYSELL",
            "symbol": c["symbol"], "date": c["date"],
            "date_settle": c["date_settle"], "quantity": c["qty"],
            "net": c["proceeds"], "income": c["proceeds"],
            "native_currency": "CAD", "native_net": c["proceeds"],
            "native_income": c["proceeds"], "tax_date": c["date_settle"]}


class TestSalesOfAnotherAccount(unittest.TestCase):
    """TJ-5067: the audit's probe (account aa's Dec 30 sale in the record,
    account bb's own ten shares sold January 2) and its controls."""

    def _check(self, books, gains, sold):
        from taxjson.lib import handoff as H
        root = _tmp(self)
        work = root / "work"
        work.mkdir()
        for acct in ("aa", "bb"):
            (work / f"{acct}_base.json").write_text(json.dumps(
                {"transactions": books.get(acct, [])}))
            (work / f"{acct}_gains_wash.json").write_text(json.dumps(
                {"transactions": gains.get(acct, []), "inventory": []}))
        record = {"year": 2025, "date_basis": "settle",
                  "dispositions": sold,
                  "year_end": {"equity": {"ZZQ.TO": {
                      "qty": 10.0, "acb": 50.0, "deferred": 0.0}}},
                  "boundary_rows": [_boundary(c) for c in sold],
                  "settle_next_year": [],
                  "carryforwards": {}}
        opening = {"equity": {"ZZQ.TO": {"qty": 10.0, "acb": 50.0,
                                         "deferred": 0.0}}}
        return H.check(root, _CFG2, record, opening)

    def _bb_sells_in_january(self):
        return [_opening("bb"), _bs("2026-01-02", "2026-01-05", -10, 100.0,
                                    "bb")]

    @rule(R.CA_RPT_08)
    def test_the_audit_probe_another_accounts_sale_is_no_double(self):
        rep = self._check({"bb": self._bb_sells_in_january()},
                          {"bb": [_jan_sale("bb")]}, [_sold("aa")])
        self.assertEqual((rep["double"], rep["problems"]), ([], 0), rep)
        # With aa's own history in the books, as before.
        aa = [_bs("2025-03-03", "2025-03-04", 10, -50.0, "aa"),
              _bs("2025-12-30", "2025-12-31", -10, 100.0, "aa")]
        rep = self._check({"aa": aa, "bb": self._bb_sells_in_january()},
                          {"bb": [_jan_sale("bb")]}, [_sold("aa")])
        self.assertEqual((rep["double"], rep["problems"]), ([], 0), rep)

    @rule(R.CA_RPT_08)
    def test_one_accounts_own_duplicate_is_still_a_double(self):
        rep = self._check({"bb": self._bb_sells_in_january()},
                          {"bb": [_jan_sale("bb")]}, [_sold("bb")])
        self.assertEqual(len(rep["double"]), 1, rep)
        self.assertIn("Report it in one year only", rep["double"][0]["why"])

    @rule(R.CA_RPT_08)
    def test_another_accounts_closed_sale_is_not_this_ones_own_row(self):
        """aa's January sale, the record holding aa's and bb's Dec 30
        sales: bb's own Dec 30 sale in this project's books is not aa's
        closed sale, so aa's January sale is reported as the double."""
        bb = [_bs("2025-03-03", "2025-03-04", 10, -50.0, "bb"),
              _bs("2025-12-30", "2025-12-31", -10, 100.0, "bb")]
        aa = [_opening("aa"), _bs("2026-01-02", "2026-01-05", -10, 100.0,
                                  "aa")]
        rep = self._check({"aa": aa, "bb": bb}, {"aa": [_jan_sale("aa")]},
                          [_sold("aa"), _sold("bb")])
        self.assertEqual([d["symbol"] for d in rep["double"]], ["ZZQ.TO"],
                         rep)


if __name__ == "__main__":
    unittest.main()
