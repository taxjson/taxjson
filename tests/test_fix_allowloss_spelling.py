"""An ALLOWLOSS line that names a sale the books spell another way
(owner, 2026-10-09; lib/loss_overrides, CA-SL-18 / US-WASH-25).

The owner's case, in synthetic form: `ALLOWLOSS <day> QZL.TO` while the
books spell the sale QZL.US (no TOBASE line joins the two listings). The
"no denied loss matches" message listed that day's trades in the
account, but cut the list at twelve in the engine's order, so on a busy
day the QZL.US sale was not among them. Now:

(a) the day's trades of the same root, on any listing, come first and
    are never cut;
(b) a sale of the same root on another listing that day is named
    plainly: "the books spell this sale QZL.US — write `ALLOWLOSS <day>
    QZL.US ...`, or if the two listings are one security add `TOBASE
    QZL.US QZL.TO` to ticker.map" (a ticker.map rule that books the
    line's symbol under another one is named instead);
(c) the same for a sale of the root a few days off (trade and
    settlement dates apart by the settlement gap, a weekend).

Both countries keep the line (CA-SL-18 / US-WASH-25): the same message.
Every fixture is SYNTHETIC: invented QZ* tickers, made-up amounts.
"""
import contextlib
import copy
import io
import unittest

from tax_rules import rule
from tax_rules.dual import tx

from taxjson.lib import loss_overrides as LO
from taxjson.lib.country import CANADA as _CA, USA as _US


def _tobase(country):
    """The TOBASE line onto the project's base-currency listing."""
    return ("TOBASE QZL.US QZL.TO" if country == _CA
            else "TOBASE QZL.TO QZL.US")


def _ov(date, symbol, qty=None):
    return {"account": "margin", "date": date, "symbol": symbol,
            "qty": qty, "reason": "r", "where": "inputs/margin/x.tt:1",
            "line": f"ALLOWLOSS {date} {symbol} reason=\"r\""}


def _one(country, book, ov):
    from taxjson.lib.pipeline import GainsRequest, run_gains
    with contextlib.redirect_stderr(io.StringIO()):
        res = run_gains(copy.deepcopy(list(book)), (), (),
                        req=GainsRequest(country=country, taxable=True,
                                         loss_overrides=(ov,)))
    it, = res["loss_overrides"]
    return it


def _busy_day(sale_sym, day, settle):
    """A loss on `sale_sym` sold on `day` (bought back in the window),
    and fourteen other trades that day booked before it."""
    cur = "USD" if sale_sym.endswith(".US") else "CAD"
    book = [tx("BUYSELL", "2024-11-01", sale_sym, 100, -5000,
               currency=cur, settle="2024-11-04")]
    for k in range(14):
        sym = f"QZB{chr(65 + k)}.TO"
        book += [tx("BUYSELL", "2024-11-01", sym, 10, -100, currency="CAD",
                    settle="2024-11-04"),
                 tx("BUYSELL", day, sym, -10, 110, currency="CAD",
                    settle=settle, time="09:31:00")]
    book += [tx("BUYSELL", day, sale_sym, -100, 4000, currency=cur,
                settle=settle, time="15:59:00"),
             tx("BUYSELL", "2024-12-20", sale_sym, 100, -3900,
                currency=cur, settle="2024-12-23")]
    return book


class TestOtherListing(unittest.TestCase):

    def _check(self, country):
        it = _one(country, _busy_day("QZL.US", "2024-12-16", "2024-12-17"),
                  _ov("2024-12-16", "QZL.TO"))
        self.assertEqual(it["status"], LO.STATUS_UNMATCHED)
        # (a) the root's trade first, never cut away by the others.
        self.assertEqual(it["day_trades"][0]["symbol"], "QZL.US")
        self.assertEqual(len(it["day_trades"]), 12)
        prob, = LO.problems([it])
        self.assertIn("that day's trades in account margin: QZL.US -100,",
                      prob)
        # (b) said plainly, with both ways out.
        self.assertIn("the books spell this sale QZL.US — write "
                      "`ALLOWLOSS 2024-12-16 QZL.US ...`, or if the two "
                      f"listings are one security add `{_tobase(country)}` "
                      "to ticker.map", prob)

    @rule("CA-SL-18")
    def test_canada(self):
        self._check(_CA)

    @rule("US-WASH-25")
    def test_usa(self):
        self._check(_US)

    @rule("CA-SL-18")
    def test_units_are_kept_in_the_line_to_write(self):
        ov = dict(_ov("2024-12-16", "QZL.TO"), qty=100.0)
        it = _one(_CA, _busy_day("QZL.US", "2024-12-16", "2024-12-17"), ov)
        prob, = LO.problems([it])
        self.assertIn("write `ALLOWLOSS 2024-12-16 QZL.US 100 ...`", prob)

    @rule("CA-SL-18")
    def test_a_ticker_map_spelling_is_named(self):
        # The run passes ticker.map's renames: a rule that books the
        # line's symbol under another one is the reason, not a TOBASE.
        it = _one(_CA, _busy_day("QZN.US", "2024-12-16", "2024-12-17"),
                  _ov("2024-12-16", "QZO.US"))
        prob, = LO.problems([it], spellings=({"QZO.US": "QZN.US"},
                                             set()))
        self.assertIn("ticker.map books QZO.US as QZN.US — write "
                      "`ALLOWLOSS 2024-12-16 QZN.US ...`", prob)
        self.assertNotIn("TOBASE", prob)

    @rule("CA-SL-18")
    def test_a_distinct_pair_offers_no_tobase(self):
        it = _one(_CA, _busy_day("QZL.US", "2024-12-16", "2024-12-17"),
                  _ov("2024-12-16", "QZL.TO"))
        prob, = LO.problems([it], spellings=(
            {}, {frozenset(("QZL.US", "QZL.TO"))}))
        self.assertIn("the books spell this sale QZL.US — write "
                      "`ALLOWLOSS 2024-12-16 QZL.US ...`", prob)
        self.assertNotIn("TOBASE", prob)


class TestNearDate(unittest.TestCase):
    """(c) A Friday sale settled Monday, the line dated Tuesday."""

    def _book(self, sym):
        cur = "USD" if sym.endswith(".US") else "CAD"
        return [tx("BUYSELL", "2024-11-01", sym, 100, -5000, currency=cur,
                   settle="2024-11-04"),
                tx("BUYSELL", "2024-12-13", sym, -100, 4000, currency=cur,
                   settle="2024-12-16"),
                tx("BUYSELL", "2024-12-20", sym, 100, -3900, currency=cur,
                   settle="2024-12-23")]

    def _check(self, country):
        it = _one(country, self._book("QZL.TO"), _ov("2024-12-17", "QZL.TO"))
        prob, = LO.problems([it])
        self.assertIn("has no trade traded or settled that day", prob)
        self.assertIn("the books have a sale of QZL.TO traded 2024-12-13, "
                      "settled 2024-12-16 — write `ALLOWLOSS 2024-12-13 "
                      "QZL.TO ...`", prob)
        it = _one(country, self._book("QZL.US"), _ov("2024-12-17", "QZL.TO"))
        prob, = LO.problems([it])
        self.assertIn("the books have a sale of QZL.US traded 2024-12-13, "
                      "settled 2024-12-16 — write `ALLOWLOSS 2024-12-13 "
                      "QZL.US ...`, or if the two listings are one "
                      f"security add `{_tobase(country)}` to ticker.map",
                      prob)

    @rule("CA-SL-18")
    def test_canada(self):
        self._check(_CA)

    @rule("US-WASH-25")
    def test_usa(self):
        self._check(_US)

    @rule("CA-SL-18")
    def test_a_far_date_names_nothing(self):
        it = _one(_CA, self._book("QZL.TO"), _ov("2024-12-27", "QZL.TO"))
        prob, = LO.problems([it])
        self.assertNotIn("the books have a sale", prob)


if __name__ == "__main__":
    unittest.main()
