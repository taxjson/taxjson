"""A Questrade activity that comes back with its Symbol blank is the
same activity as its named copy, not a second trade (backlog TJ-4955).

A re-fetch returned an option sale with a blank `symbol`; the merge
(`_merge_csv_text`) and the cross-window identity (`qt_activities`) held
the Symbol in the row's identity, so the file kept both rows and the
parser booked the sale twice (the blank row's contract is read from its
description). Now: rows equal in everything but a blank Symbol are one
activity and the named copy is the one kept. Each symbol keeps the most
copies either side holds (two real fills stay two, two symbols stay two
rows, a named row is never dropped); the file's blank rows are kept only
up to the download's count of the activity, so one merge repairs a file
an older version wrote and the same download merged again changes
nothing. `tjs fetch` names the rows it dropped and the backup of the
file as it was; a blank row whose trade the download does not hold is
kept.

All data synthetic (the made-up ticker SYN); every broker response is
mocked.
"""
import _bootstrap   # first: it puts the plugin and the core on sys.path

import contextlib
import csv
import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from taxjson_fetch import api
from taxjson_fetch import command as fc

from _questrade_fetch import NUMBER, SESSION, _qt_request

PLUGIN_SRC = _bootstrap.PLUGIN_SRC     # the plugin these tests read
OPT = "SYN16Jan26C130.00"
OTHER = "SYN16Jan26C135.00"
_DAY = "2025-06-20T00:00:00.000000-04:00"


def _sale(symbol=OPT, **kw):
    """An option sale: 4 contracts, 896 net."""
    a = dict(tradeDate=_DAY, transactionDate=_DAY, settlementDate=_DAY,
             action="Sell", symbol=symbol, symbolId=77 if symbol else 0,
             description="CALL SYN 01/16/26 130 SYN CORP WE ACTED AS AGENT",
             quantity=-4, price=2.25, grossAmount=900, commission=-4,
             netAmount=896, currency="USD", type="Trades")
    a.update(kw)
    return a


def _fee(**kw):
    a = dict(tradeDate=_DAY, transactionDate=_DAY, settlementDate=_DAY,
             action="FCH", symbol="", symbolId=0, description="ADMIN FEE",
             quantity=0, price=0, grossAmount=0, commission=0, netAmount=-5,
             currency="CAD", type="Fees and rebates")
    a.update(kw)
    return a


def _buy(day="2025-06-10"):
    d = f"{day}T00:00:00.000000-04:00"
    return dict(tradeDate=d, transactionDate=d, settlementDate=d,
                action="Buy", symbol="SYN.TO", symbolId=5,
                description="SYNTHETIC CORP", quantity=10, price=10,
                grossAmount=-100, commission=0, netAmount=-100,
                currency="CAD", type="Trades")


def _csv(*acts):
    return api.qt_to_csv(list(acts), NUMBER)


def _symbols(text, action="Sell"):
    """The Symbol cells of the file's `action` rows, in file order."""
    return [r["Symbol"] for r in csv.DictReader(io.StringIO(text))
            if r["Action"] == action]


def _merge(existing, new):
    """(the sale rows' symbols, added, dropped, filled) of the merge."""
    m = fc._merge_csv_text(_csv(*existing), _csv(*new))
    text, added = m
    return _symbols(text), added, m.dropped, m.filled


class TestOneBlankCopy(unittest.TestCase):

    def test_named_then_blank(self):
        self.assertEqual(_merge([_sale()], [_sale("")]), ([OPT], 0, 0, 0))

    def test_blank_then_named(self):
        """The named copy takes the blank row's place: not a new row."""
        self.assertEqual(_merge([_sale("")], [_sale()]), ([OPT], 0, 0, 1))

    def test_an_unchanged_copy(self):
        self.assertEqual(_merge([_sale()], [_sale()]), ([OPT], 0, 0, 0))
        self.assertEqual(_merge([_sale("")], [_sale("")]), ([""], 0, 0, 0))

    def test_a_number_spelled_another_way(self):
        self.assertEqual(_merge([_sale()], [_sale("", quantity=-4.0)]),
                         ([OPT], 0, 0, 0))

    def test_a_blank_row_that_differs_elsewhere_is_another_row(self):
        syms, added, dropped, filled = _merge([_sale()],
                                              [_sale("", commission=-5)])
        self.assertEqual((sorted(syms), added, dropped, filled),
                         (["", OPT], 1, 0, 0))

    def test_the_other_rows_keep_their_places(self):
        m = fc._merge_csv_text(_csv(_buy(), _sale(), _fee()),
                               _csv(_buy(), _sale(""), _fee()))
        self.assertEqual(m[0], _csv(_buy(), _sale(), _fee()))
        m = fc._merge_csv_text(_csv(_buy(), _sale(""), _fee()),
                               _csv(_buy(), _sale(), _fee()))
        self.assertEqual(m[0], _csv(_buy(), _sale(), _fee()))


class TestRealFillsStay(unittest.TestCase):

    def test_two_equal_fills_on_each_side(self):
        self.assertEqual(_merge([_sale(), _sale()], [_sale(), _sale()]),
                         ([OPT, OPT], 0, 0, 0))

    def test_a_second_fill_the_file_did_not_hold(self):
        self.assertEqual(_merge([_sale()], [_sale(), _sale()]),
                         ([OPT, OPT], 1, 0, 0))

    def test_a_fill_the_download_no_longer_holds_is_kept(self):
        self.assertEqual(_merge([_sale(), _sale()], [_sale()]),
                         ([OPT, OPT], 0, 0, 0))

    def test_one_download_with_a_named_and_a_blank_row_is_two(self):
        """One download holds both: two activities, as it says."""
        syms, added, dropped, _f = _merge([_buy()], [_sale(), _sale("")])
        self.assertEqual((sorted(syms), added, dropped), (["", OPT], 2, 0))
        syms, added, dropped, _f = _merge([_sale(), _sale("")],
                                          [_sale(), _sale("")])
        self.assertEqual((sorted(syms), added, dropped), (["", OPT], 0, 0))

    def test_two_different_symbols_stay_two_rows(self):
        syms, added, dropped, filled = _merge([_sale()], [_sale(OTHER)])
        self.assertEqual((sorted(syms), added, dropped, filled),
                         ([OPT, OTHER], 1, 0, 0))

    def test_blank_rows_with_no_named_copy_as_before(self):
        """Fees, FX conversions, deposits: the larger side's count."""
        def fees(existing, new):
            m = fc._merge_csv_text(_csv(*existing), _csv(*new))
            return len(_symbols(m[0], "FCH")), m[1], m.dropped, m.filled
        self.assertEqual(fees([_fee()], [_fee()]), (1, 0, 0, 0))
        self.assertEqual(fees([_fee(), _fee()], [_fee()]), (2, 0, 0, 0))
        self.assertEqual(fees([_fee()], [_fee(), _fee()]), (2, 1, 0, 0))
        self.assertEqual(fees([_buy()], [_fee(), _fee()]), (2, 2, 0, 0))


class TestMixedCounts(unittest.TestCase):

    def test_named_and_blank_against_two_named_is_two_not_four(self):
        """Said as it may be: a second named row added, the blank row a
        copy of a named one (a twin, or the second fill's)."""
        self.assertEqual(_merge([_sale(), _sale("")], [_sale(), _sale()]),
                         ([OPT, OPT], 1, 1, 0))

    def test_two_named_against_named_and_blank_is_two(self):
        self.assertEqual(_merge([_sale(), _sale()], [_sale(), _sale("")]),
                         ([OPT, OPT], 0, 0, 0))

    def test_one_blank_against_two_named(self):
        self.assertEqual(_merge([_sale("")], [_sale(), _sale()]),
                         ([OPT, OPT], 1, 0, 1))

    def test_two_blank_against_one_named(self):
        """The download holds the trade once: one row, the named one."""
        self.assertEqual(_merge([_sale(""), _sale("")], [_sale()]),
                         ([OPT], 0, 1, 1))

    def test_a_blank_row_beside_another_symbol_is_dropped_not_replaced(self):
        """The file names one contract and a blank row, the download
        another contract: the blank row equals a named row kept either
        way, and which one is not known: one row added, one dropped."""
        syms, added, dropped, filled = _merge([_sale(), _sale("")],
                                              [_sale(OTHER)])
        self.assertEqual((sorted(syms), added, dropped, filled),
                         ([OPT, OTHER], 1, 1, 0))

    def test_another_symbol_and_a_blank_copy(self):
        """The file names one contract; the download another and a blank
        row: two activities, both named."""
        syms, added, dropped, filled = _merge([_sale()],
                                              [_sale(OTHER), _sale("")])
        self.assertEqual((sorted(syms), added, dropped, filled),
                         ([OPT, OTHER], 1, 0, 0))


class TestAFileThatHoldsTwinsIsRepaired(unittest.TestCase):
    """Existing: a named row and its blank twin, as an older version
    wrote them."""

    def test_the_download_holds_the_trade_once(self):
        self.assertEqual(_merge([_sale(), _sale("")], [_sale()]),
                         ([OPT], 0, 1, 0))

    def test_the_download_holds_it_once_blank(self):
        self.assertEqual(_merge([_sale(), _sale("")], [_sale("")]),
                         ([OPT], 0, 1, 0))

    def test_two_fills_each_with_a_twin(self):
        self.assertEqual(_merge([_sale(), _sale(""), _sale(), _sale("")],
                                [_sale(), _sale()]), ([OPT, OPT], 0, 2, 0))

    def test_two_blank_rows_beside_one_named(self):
        """The download's count decides: both blank rows go at once."""
        self.assertEqual(_merge([_sale(), _sale(""), _sale("")], [_sale()]),
                         ([OPT], 0, 2, 0))
        self.assertEqual(_merge([_sale(), _sale(""), _sale("")],
                                [_sale("")]), ([OPT], 0, 2, 0))

    def test_named_rows_are_never_dropped(self):
        self.assertEqual(_merge([_sale(), _sale(), _sale("")], [_sale("")]),
                         ([OPT, OPT], 0, 1, 0))

    def test_a_download_that_does_not_hold_the_trade_repairs_nothing(self):
        """Outside the fetched window the file alone cannot tell a twin
        from a second fill: kept (the parser's ATTENTION names it)."""
        syms, added, dropped, filled = _merge([_sale(), _sale("")],
                                              [_buy()])
        self.assertEqual((sorted(syms), added, dropped, filled),
                         (["", OPT], 1, 0, 0))

    def test_the_download_says_two(self):
        syms, _a, dropped, _f = _merge([_sale(), _sale("")],
                                       [_sale(), _sale("")])
        self.assertEqual((sorted(syms), dropped), (["", OPT], 0))


# (the file's sale rows, the download's): every case of the rule.
S, B, OTH = "S", "B", "O"
CASES = [
    ("S", "B"), ("B", "S"), ("S", "S"), ("B", "B"), ("SS", "SS"),
    ("S", "SS"), ("SS", "S"), ("", "SB"), ("SB", "SB"), ("S", "O"),
    ("SB", "SS"), ("SS", "SB"), ("B", "SS"), ("BB", "S"), ("S", "OB"),
    ("SB", "O"), ("SB", "S"), ("SB", "B"), ("SBSB", "SS"), ("SBB", "S"),
    ("SBB", "B"), ("SSB", "B"), ("SB", ""), ("BB", "B"), ("B", "BB"),
    ("BBB", "SB"), ("SOB", "B"), ("SOBB", "SO"), ("OBB", "SB"),
]


def _rows(letters):
    return [_buy()] + [{S: _sale(), B: _sale(""), OTH: _sale(OTHER)}[c]
                       for c in letters] + [_fee()]


class TestTheSameDownloadMergedAgainChangesNothing(unittest.TestCase):
    """A file the merge wrote is not read as an older version's: the
    second and the third merge of one download add, drop and replace
    nothing and write the same bytes."""

    def _settles(self, existing, new):
        first = fc._merge_csv_text(existing, new)
        text = first[0]
        for _again in range(2):
            m = fc._merge_csv_text(text, new)
            self.assertEqual((m[0], m[1], m.dropped, m.filled),
                             (text, 0, 0, 0))
        return first

    def test_every_case(self):
        for had, got in CASES:
            with self.subTest(file=had, download=got):
                first = self._settles(_csv(*_rows(had)), _csv(*_rows(got)))
                kept = _symbols(first[0])
                # A named row is never dropped; no row is invented.
                for sym in (OPT, OTHER):
                    letter = S if sym == OPT else OTH
                    self.assertEqual(kept.count(sym),
                                     max(had.count(letter),
                                         got.count(letter)))
                named = kept.count(OPT) + kept.count(OTHER)
                if not got:           # the download says nothing of it
                    self.assertEqual(len(kept), len(had))
                elif not named:       # no symbol named: as before
                    self.assertEqual(len(kept), max(len(had), len(got)))
                else:                 # the download's count decides
                    self.assertEqual(len(kept), max(len(got), named))

    def test_random_files_and_downloads(self):
        import random
        rnd = random.Random(4955)
        pool = [_sale(), _sale(""), _sale(OTHER), _fee(), _buy(),
                _sale("", commission=-5), _sale(commission=-5)]
        for _n in range(300):
            had = [rnd.choice(pool) for _ in range(rnd.randrange(7))]
            got = [rnd.choice(pool) for _ in range(rnd.randrange(1, 7))]
            with self.subTest(n=_n):
                first = self._settles(_csv(*had), _csv(*got))
                before = list(csv.reader(io.StringIO(_csv(*had))))[1:]
                after = list(csv.reader(io.StringIO(first[0])))[1:]
                named = [r for r in before if r[3]]
                for r in named:           # no named row is ever dropped
                    self.assertGreaterEqual(after.count(r), before.count(r))
                self.assertEqual(len(after), len(before) + first[1]
                                 - first.dropped)


class TestBlankCopiesRule(unittest.TestCase):
    """`qt_blank_copies`: never more blank rows than the larger side
    holds, never fewer activities than the download says."""

    def test_bounds_over_small_counts(self):
        from collections import Counter
        from itertools import product
        for kn, kb, nn, nb, exact in product(range(3), range(3), range(3),
                                             range(3), (True, False)):
            kept, new = Counter(S=kn), Counter(S=nn)
            got = api.qt_blank_copies(kept, kb, new, nb, kept_exact=exact)
            names = max(kn, nn)
            with self.subTest(kept=(kn, kb), new=(nn, nb), exact=exact):
                self.assertGreaterEqual(got, 0)
                self.assertLessEqual(got, max(kb, nb))
                if nn + nb == 0:
                    self.assertEqual(got, kb)
                    continue
                self.assertGreaterEqual(names + got, nn + nb)
                if exact:
                    self.assertGreaterEqual(names + got, kn + kb)
                elif kn or nn:
                    # The file's count does not decide: the download's.
                    self.assertEqual(names + got, max(names, nn + nb))
                if not kn and not nn:
                    self.assertEqual(got, max(kb, nb))


def _windows(*chunks):
    """`qt_activities` over mocked windows, one response each."""
    calls = []

    def http(url):
        calls.append(url)
        i = len(calls) - 1
        return json.dumps({"activities": [dict(a) for a in (
            chunks[i] if i < len(chunks) else [])]}).encode()
    acts = api.qt_activities(dict(SESSION), "1", date(2025, 6, 1),
                             date(2025, 7, 15),
                             http)
    assert len(calls) == 2, calls
    return [a["symbol"] for a in acts]


class TestTwoWindows(unittest.TestCase):
    """The boundary activity, returned by both windows of one fetch."""

    def test_named_then_blank(self):
        self.assertEqual(_windows([_sale()], [_sale("")]), [OPT])

    def test_blank_then_named_keeps_the_named_copy_in_its_place(self):
        self.assertEqual(_windows([_buy(), _sale(""), _fee()], [_sale()]),
                         ["SYN.TO", OPT, ""])

    def test_whatever_id_the_blank_copy_carries(self):
        """Equal in every other field: the id alone decides nothing."""
        for sid in (0, 77, 78, None):
            self.assertEqual(_windows([_sale()], [_sale("", symbolId=sid)]),
                             [OPT], sid)

    def test_an_unchanged_copy(self):
        self.assertEqual(_windows([_sale()], [_sale()]), [OPT])
        self.assertEqual(_windows([_fee()], [_fee()]), [""])

    def test_two_real_fills_in_both_windows(self):
        self.assertEqual(_windows([_sale(), _sale()], [_sale(), _sale()]),
                         [OPT, OPT])

    def test_a_second_fill_the_first_window_did_not_see(self):
        self.assertEqual(_windows([_sale()], [_sale(), _sale("")]),
                         [OPT, ""])
        self.assertEqual(_windows([_sale("")], [_sale(), _sale()]),
                         [OPT, OPT])

    def test_named_and_blank_then_two_named_is_two(self):
        self.assertEqual(_windows([_sale(), _sale("")], [_sale(), _sale()]),
                         [OPT, OPT])

    def test_one_window_with_a_named_and_a_blank_row_is_two(self):
        self.assertEqual(_windows([_sale(), _sale("")], [_sale()]),
                         [OPT, ""])

    def test_two_different_symbols_stay_two(self):
        self.assertEqual(_windows([_sale()], [_sale(OTHER, symbolId=78)]),
                         [OPT, OTHER])

    def test_one_symbol_under_two_ids_stays_two(self):
        """As before: nothing is matched across named rows but equals."""
        self.assertEqual(_windows([_sale()], [_sale(symbolId=78)]),
                         [OPT, OPT])


class TestTjsFetchSaysWhatItDropped(unittest.TestCase):

    def _fetch(self, existing, acts, **args):
        """(the account's summary, the file after, what was said, the
        other files in the folder: name -> text)."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            folder = root / "inputs" / "margin"
            folder.mkdir(parents=True)
            out = folder / "questrade_2025.csv"
            out.write_text(_csv(*existing))
            req, said = _qt_request(root, trim_overlap=False, **args)
            with patch.object(fc, "_qt_open_session",
                              return_value=(dict(SESSION), "x")), \
                    patch.object(api, "qt_activities", return_value=acts), \
                    patch.object(api, "qt_window",
                                 return_value=(date(2025, 6, 1),
                                               date(2025, 6, 30))), \
                    contextlib.redirect_stderr(io.StringIO()):
                res = fc.run(req)
            others = {p.name: p.read_text() for p in folder.iterdir()
                      if p != out}
            return res["margin"], out.read_text(), "\n".join(said), others

    def test_the_repair_names_the_row_the_file_and_the_backup(self):
        before = [_buy(), _sale(), _sale("")]
        res, text, said, others = self._fetch(before, [_buy(), _sale()])
        self.assertEqual(text, _csv(_buy(), _sale()))
        self.assertEqual((res["added"], res["dropped"],
                          res["symbol_filled"]), (0, 1, 0))
        self.assertIn("questrade_2025.csv: +0 new row(s) (2 downloaded)",
                      said)
        self.assertIn(
            "  questrade_2025.csv: dropped 1 row with a blank Symbol, "
            "equal in every other cell to a row kept and read as its copy "
            f"(the row kept, then the quantity): 2025-06-20 Sell {OPT} -4. "
            "The file as it was: questrade_2025.csv.bak. A dropped row "
            "that was booked counted its trade twice: run `tjs run` again "
            "and check the account's positions with `tjs sanity`.", said)
        self.assertEqual(others, {"questrade_2025.csv.bak": _csv(*before)})

    def test_the_rows_named_are_bounded(self):
        days = ["2025-06-%02d" % d for d in range(2, 7)]

        def on(day, symbol=OPT):
            d = f"{day}T00:00:00.000000-04:00"
            return _sale(symbol, tradeDate=d, transactionDate=d,
                         settlementDate=d)
        res, _text, said, _o = self._fetch(
            [x for d in days for x in (on(d), on(d, ""))],
            [on(d) for d in days])
        self.assertEqual(res["dropped"], 5)
        self.assertIn(
            "dropped 5 rows with a blank Symbol, each equal in every other "
            "cell to a row kept and read as its copy (the row kept, then "
            f"the quantity): 2025-06-02 Sell {OPT} -4; 2025-06-03 Sell "
            f"{OPT} -4; 2025-06-04 Sell {OPT} -4; and 2 more. The file as "
            "it was: questrade_2025.csv.bak.", said)

    def test_a_dry_run_says_it_and_writes_nothing(self):
        before = _csv(_buy(), _sale(), _sale(""))
        res, text, said, others = self._fetch(
            [_buy(), _sale(), _sale("")], [_buy(), _sale()], dry_run=True)
        self.assertEqual((text, others), (before, {}))
        self.assertEqual(res["dropped"], 1)
        self.assertIn("questrade_2025.csv: would drop 1 row with a blank "
                      f"Symbol, equal in every other cell to a row kept and "
                      f"read as its copy (the row kept, then the quantity): "
                      f"2025-06-20 Sell {OPT} -4.", said)
        self.assertNotIn(".bak", said)

    def test_a_second_fetch_says_and_keeps_nothing_more(self):
        once = _csv(_buy(), _sale())
        res, text, said, others = self._fetch([_buy(), _sale()],
                                              [_buy(), _sale()])
        self.assertEqual((text, others), (once, {}))
        self.assertEqual((res["added"], res["dropped"],
                          res["symbol_filled"]), (0, 0, 0))
        self.assertNotIn("blank Symbol", said)

    def test_a_blank_copy_downloaded_is_not_a_new_row(self):
        res, text, said, others = self._fetch([_buy(), _sale()],
                                              [_buy(), _sale("")])
        self.assertEqual((text, others), (_csv(_buy(), _sale()), {}))
        self.assertEqual((res["added"], res["dropped"]), (0, 0))
        self.assertNotIn("blank Symbol", said)

    def test_a_named_copy_replaces_the_blank_row(self):
        res, text, said, others = self._fetch([_buy(), _sale("")],
                                              [_buy(), _sale()])
        self.assertEqual((text, others), (_csv(_buy(), _sale()), {}))
        self.assertEqual((res["added"], res["dropped"],
                          res["symbol_filled"]), (0, 0, 1))
        self.assertIn("questrade_2025.csv: 1 row with a blank Symbol "
                      "replaced by the download's copy that names the "
                      "symbol (the same activity, not a new row).", said)
        self.assertNotIn("dropped", said)

    def test_a_clean_fetch_says_nothing_of_it(self):
        res, _text, said, others = self._fetch([_buy(), _fee()],
                                               [_buy(), _fee(), _sale()])
        self.assertEqual((res["added"], res["dropped"], others), (1, 0, {}))
        self.assertNotIn("blank Symbol", said)


class TestTheParserBooksTheRepairedFileOnce(unittest.TestCase):

    def test_four_contracts_not_eight(self):
        from taxjson.lib.brokerages.questrade import QuestradeBrokerage

        def sold(text):
            with tempfile.TemporaryDirectory() as td, \
                    contextlib.redirect_stderr(io.StringIO()) as err:
                p = Path(td) / "questrade_2025.csv"
                p.write_text(text)
                txs = QuestradeBrokerage().parse_file(p)
            return (sum(t["quantity"] for t in txs
                        if t.get("action") == "BUYSELL"
                        and t["quantity"] < 0), err.getvalue())
        twin = _csv(_buy(), _sale(), _sale(""))
        qty, err = sold(twin)
        self.assertEqual(qty, -8)
        self.assertIn("lines 3 and 4 differ only by a blank Symbol", err)
        merged, _added = fc._merge_csv_text(twin, _csv(_buy(), _sale()))
        qty, err = sold(merged)
        self.assertEqual(qty, -4)
        self.assertNotIn("blank Symbol", err)


if __name__ == "__main__":
    unittest.main()
