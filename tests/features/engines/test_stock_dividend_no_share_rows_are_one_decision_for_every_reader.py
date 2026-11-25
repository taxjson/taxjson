"""Whether a stock dividend row books no shares is decided once, from the
rows alone, and every reader of the rows follows it (backlog TJ-5282).

A stock dividend posted after the short that owes it was covered is that
short's obligation: no shares (tax-logic CA-STKDIV-02 / US-STKDIV-06).
The engines decided it each in its own walk and wrote the rows into the
gains files; a reader that runs before the gains (the `[[distributions]]`
sizing) or adds up the rows itself (`tjs wash-radar`, `tjs t1135`'s
units, the holdings report's trades) still counted the row: a
distribution of 1.00 a share was applied to 60 shares where the account
held 50 (357.00 for 347.00). `lib/stock_dividend_cover.no_share_rows` is
the one decision. The engines make it; a reader that runs after the gains
run reads their record from the gains files, and `[[distributions]]`
makes it on the book the gains run will walk, checked after the run.

Synthetic rows only (the made-up ticker SYQ).
"""
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R

from taxjson.lib.stock_dividend_cover import (booked_rows, entitlement_date,
                                              no_share_rows, no_share_rows_of,
                                              presize_disagreements, presized,
                                              without_no_share_rows)
from taxjson.model.transaction import TaxTransaction


def _row(i, date, qty, account='m', symbol='SYQ', **kw):
    return dict({'action': 'BUYSELL', 'date': date, 'date_settle': date,
                 'time': '10:00:00', 'symbol': symbol, 'quantity': qty,
                 'currency': 'USD', 'price': 0.0, 'net_amount': 0.0,
                 'account': account, 'id': i}, **kw)


def _div(date='2025-04-01', **kw):
    return _row('d', date, 10, type='stock_dividend', **kw)


SHORT = [_row('s', '2025-03-03', -100), _row('c', '2025-03-25', 100)]


def _gone(rows, **kw):
    return sorted(no_share_rows(rows, entitled_lots=kw.pop('entitled_lots', False),
                                **kw)[0])


class TestTheDecisionFromTheRowsAlone(unittest.TestCase):

    @rule(R.CA_STKDIV_02)
    def test_a_short_on_the_entitlement_date_bought_back_since(self):
        gone, near = no_share_rows(SHORT + [_div(ex_date='2025-03-20',
                                                 delivered_cost=45.0)],
                                   entitled_lots=False)
        self.assertEqual(near, {})
        self.assertEqual(gone['d'], {
            'symbol': 'SYQ', 'account': 'm', 'date': '2025-04-01',
            'time': '10:00:00', 'currency': 'USD', 'units': 10.0, 'cost': 45.0,
            'entitlement': '2025-03-20', 'short': 100.0, 'dated_by': 'ex',
            'covered': ['2025-03-25'], 'cover': 'c', 'cover_date': '2025-03-25',
            'cover_settle': '2025-03-25'})

    @rule(R.CA_STKDIV_02)
    def test_the_tests_in_their_order(self):
        dated = _div(ex_date='2025-03-20')
        for name, rows, want in (
                ("a short still open at the row",
                 [SHORT[0], _row('c', '2025-03-25', 60), dated], []),
                ("no entitlement date", SHORT + [_div()], []),
                ("not short on the date: covered the day before",
                 [SHORT[0], _row('c', '2025-03-19', 100), dated], []),
                ("not short on the date: sold short on it",
                 [_row('s', '2025-03-20', -100), SHORT[1], dated], []),
                ("shares held on the date",
                 [_row('b', '2025-01-06', 100), dated], []),
                ("covered on the date itself",
                 [SHORT[0], _row('c', '2025-03-20', 100), dated], ['d']),
                ("a flip buy that goes long",
                 [SHORT[0], _row('c', '2025-03-25', 150), dated], ['d']),
                ("a record date alone",
                 SHORT + [_div(record_date='2025-03-20')], ['d']),
                ("the rows in any order", [dated] + SHORT[::-1], ['d'])):
            with self.subTest(name):
                self.assertEqual(_gone(rows), want)

    @rule(R.CA_STKDIV_02)
    def test_an_undated_row_beside_a_cover_is_named_not_dropped(self):
        gone, near = no_share_rows(SHORT + [_div()], entitled_lots=False)
        self.assertEqual((gone, near['d']['covered']), ({}, ['2025-03-25']))
        self.assertEqual(no_share_rows(
            [_row('s', '2024-11-04', -100), _row('c', '2024-12-02', 100), _div()],
            entitled_lots=False), ({}, {}))

    @rule(R.CA_STKDIV_02)
    def test_each_accounts_own_position_decides_unless_pooled(self):
        other = [dict(r, account='L') for r in SHORT]
        dated = _div(ex_date='2025-03-20')
        self.assertEqual(_gone(other + [dated]), [])
        self.assertEqual(_gone(other + [dated], pooled=True), ['d'])
        # (a holder elsewhere changes nothing of the receiving account's)
        self.assertEqual(_gone(SHORT + [_row('bl', '2025-01-06', 100, 'L'), dated]),
                         ['d'])

    @rule(R.US_STKDIV_06)
    def test_units_that_carry_their_own_date_are_entitled_where_lots_keep_dates(self):
        dated = _div(ex_date='2025-03-20')
        opening = _row('o', '2025-03-28', 100, type='opening', lot_date='2024-01-08')
        late = dict(opening, lot_date='2025-03-26')
        move = _row('mi', '2025-03-28', 100, action='TRANSFER', type='lot_move')
        for name, extra, us in (("an opening lot dated before", opening, []),
                                ("an opening lot bought after", late, ['d']),
                                ("a lot moved in", move, [])):
            with self.subTest(name):
                self.assertEqual(_gone(SHORT + [extra, dated], entitled_lots=True), us)
                # (Canada's pool has no lot dates: the account's position alone)
                self.assertEqual(_gone(SHORT + [extra, dated]), ['d'])

    @rule(R.CA_STKDIV_02)
    def test_a_ticker_change_is_followed_and_rows_of_either_shape_are_read(self):
        rename = _row('r', '2025-03-27', 1.0, action='SPLIT', symbol_new='SYR')
        dated = dict(_div(ex_date='2025-03-20'), symbol='SYR')
        self.assertEqual(_gone(SHORT + [rename, dated]), ['d'])
        as_objects = [TaxTransaction(**r) for r in SHORT + [_div(ex_date='2025-03-20')]]
        self.assertEqual(_gone(as_objects), ['d'])
        self.assertEqual(entitlement_date(as_objects[-1]), '2025-03-20')

    @rule(R.CA_STKDIV_02)
    def test_the_book_without_the_rows(self):
        book = SHORT + [_div(ex_date='2025-03-20')]
        self.assertEqual([r['id'] for r in without_no_share_rows(book, 'canada')],
                         ['s', 'c'])
        self.assertEqual([r['id'] for r in without_no_share_rows(book, 'usa')],
                         ['s', 'c'])
        plain = SHORT + [_div()]
        self.assertEqual(without_no_share_rows(plain, 'canada'), plain)
        # (a book with no stock dividend is returned as it is)
        self.assertEqual(without_no_share_rows(SHORT, None), SHORT)


class TestTheReadersOfTheRows(unittest.TestCase):
    """The review's R1 against its control R1c: short 100, one buy of 150
    (long 50 at 4.40), the dated stock dividend with its delivered cost,
    and a distribution of 1.00 a share on a later record date."""

    BOOK = ["BUYSELL 2025-03-03 10:00:00 SYQ.US -100 USD 5 500 0",
            "BUYSELL 2025-03-25 10:00:00 SYQ.US 150 USD 4.4 660 0"]
    DIV = ("BUYSELL 2025-04-01 10:00:00 SYQ.US 10 USD 0 0 type=stock_dividend "
           "ex=2025-03-20 delivered_cost=45")
    DIST = ('\n[[distributions]]\nsymbol = "SYQ.US"\nrecord_date = 2025-05-01\n'
            'per_share = 1.0\n')

    def _project(self, lines, country, *commands):
        from _qa_project import console, flat, tj, toml
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "p"
            (root / "inputs/margin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(toml(country=country, year=2025)
                                               + self.DIST)
            (root / "ticker.map").write_text("")
            (root / "inputs/margin/book.tt").write_text("\n".join(lines) + "\n")
            run = tj(root, "run", "--no-input", check=False)
            self.assertEqual(run.returncode, 0, console(run))
            out = {" ".join(c): flat(tj(root, *c, check=False).stdout)
                   for c in commands}
            out["holdings"] = flat((root / "reports/margin_holdings.toml").read_text())
            out["adjust"] = [
                t["net_amount"] for t in json.loads(
                    (root / "work/margin_base.json").read_text())["transactions"]
                if str(t.get("id") or "").startswith("DIST-")]
            return out

    @rule(R.CA_STKDIV_02)
    def test_canada_a_distribution_is_sized_on_the_shares_held(self):
        # 50 shares x 1.00 USD at 1.35 on 297.00: 347.00. Base (and the
        # engines' own decision alone): 60 shares, 357.00.
        got = self._project(self.BOOK + [self.DIV], "canada",
                            ("list",), ("t1135",), ("wash-radar",))
        control = self._project(self.BOOK, "canada",
                                ("list",), ("t1135",), ("wash-radar",))
        self.assertIn("margin SYQ.US 50 347.00 6.94", got["list"])
        self.assertEqual(got["list"], control["list"])
        self.assertEqual(got["adjust"], control["adjust"])
        self.assertIn("347.00 347.00", got["t1135"])
        # The radar's units are the engine's (it said 60).
        self.assertIn("CLEAR SYQ.US 50 0", got["wash-radar"])
        self.assertEqual(got["wash-radar"], control["wash-radar"])
        # The holdings report lists the purchase alone (it listed the row
        # as a BUY of 10 at 0 beside a quantity of 50).
        self.assertIn('quantity = 50.0', got["holdings"])
        self.assertIn('trades = [ { date = 2025-03-25, action = "BUY", qty = 50.0, '
                      'price = 4.4 }, ]', got["holdings"])

    @rule(R.US_STKDIV_06)
    def test_the_us_a_distribution_is_sized_on_the_shares_held(self):
        got = self._project(self.BOOK + [self.DIV], "usa", ("list",), ("wash-radar",))
        control = self._project(self.BOOK, "usa", ("list",), ("wash-radar",))
        self.assertIn("margin SYQ.US 50 270.00 5.40", got["list"])
        self.assertEqual(got["list"], control["list"])
        self.assertEqual(got["adjust"], control["adjust"])
        self.assertEqual(got["wash-radar"], control["wash-radar"])
        self.assertIn('qty = 50.0, price = 4.4 }, ]', got["holdings"])


class TestTheReadersFollowTheEngine(unittest.TestCase):
    """The engine's answer is the one source of truth (the review's X1 and
    W1 against their controls, the same books with no ex date on the
    row). A reader that runs after the gains run reads the engine's key
    from the gains files (`booked_rows`); `[[distributions]]`, which runs
    before it, decides on the book the engine will walk (`presized`: the
    missing-history openings synthesized first), and the run checks the
    two against each other after the gains run."""

    AROUND = ["BUYSELL 2025-03-03 10:00:00 SYQ.US -100 USD 5 500 0",
              "BUYSELL 2025-03-25 10:00:00 SYQ.US 100 USD 4.4 440 0"]
    DIV = "BUYSELL 2025-04-01 10:00:00 SYQ.US 20 USD 0 0 type=stock_dividend"
    DIST = ('\n[[distributions]]\nsymbol = "SYQ.US"\nrecord_date = 2025-05-01\n'
            'per_share = 1.0\n')
    OTHER = ["BUYSELL 2025-01-06 10:00:00 QZB.US 10 USD 5 50 0"]

    def _project(self, books, country, commands, opening=None, tail="", details=()):
        from _qa_project import console, flat, tj, toml
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "p"
            for account, lines in books.items():
                (root / "inputs" / account).mkdir(parents=True)
                (root / "inputs" / account / "book.tt").write_text(
                    "\n".join(lines) + "\n")
            if opening:
                (root / "inputs/margin/missing_history.tt").write_text(opening + "\n")
            extra = "".join(f'[accounts.{a}]\ntype = "sheltered"\n'
                            for a in books if a != "margin")
            (root / "taxjson.toml").write_text(
                toml(country=country, year=2025, extra_accounts=extra) + tail)
            (root / "ticker.map").write_text("")
            run = tj(root, "run", "--no-input", *details, check=False)
            self.assertEqual(run.returncode, 0, console(run))
            out = {" ".join(c): flat(tj(root, *c, check=False).stdout)
                   for c in commands}
            out["run"] = flat(console(run))
            holder = "shelt" if "shelt" in books else "margin"
            out["holdings"] = flat(
                (root / f"reports/{holder}_holdings.toml").read_text())
            out["adjust"] = [
                t["net_amount"] for t in json.loads(
                    (root / "work/margin_base.json").read_text())["transactions"]
                if str(t.get("id") or "").startswith("DIST-")]
            return out

    def _missing_history(self, country, commands):
        # X1: 200 held before the files (an OPENING cost=unknown line), 100
        # sold and 100 bought around the ex date, the dated dividend of 20.
        # Read without the opening, the account looked short on the ex date.
        opening = "OPENING 2024-12-31 SYQ.US 200 cost=unknown"
        got = self._project({"margin": self.AROUND + [self.DIV + " ex=2025-03-20"]},
                            country, commands, opening, self.DIST)
        control = self._project({"margin": self.AROUND + [self.DIV]},
                                country, commands, opening, self.DIST)
        for name in [" ".join(c) for c in commands] + ["adjust"]:
            self.assertEqual(got[name], control[name], name)
        self.assertNotIn("ATTENTION: stock dividend:", got["run"])
        self.assertIn("quantity = 220.0", got["holdings"])
        self.assertIn("qty = 20.0", got["holdings"])
        return got

    @rule(R.CA_STKDIV_02)
    def test_canada_a_holder_before_the_files_keeps_the_row_in_every_reader(self):
        got = self._missing_history("canada", (("list",), ("t1135",), ("wash-radar",)))
        self.assertIn("margin SYQ.US 220", got["list"])
        self.assertIn("CLEAR SYQ.US 220 0", got["wash-radar"])
        # 220 shares x 1.00 a share (it was sized on 200: 200.00).
        self.assertEqual(got["adjust"], [220.0])

    @rule(R.US_STKDIV_06)
    def test_the_us_a_holder_before_the_files_keeps_the_row_in_every_reader(self):
        got = self._missing_history("usa", (("list",), ("wash-radar",)))
        self.assertIn("margin SYQ.US 220", got["list"])
        self.assertEqual(got["adjust"], [220.0])

    def _sheltered_transfer(self, country, commands):
        # W1: a sheltered account's plain transfer in of 200, then the same
        # trades and the dated dividend. The engine gets the transfer as
        # held units; a reader that left it out saw a covered short.
        book = ["TRANSFER 2025-02-03 10:00:00 SYQ.US 200 USD 5 1000"] + self.AROUND
        got = self._project({"margin": self.OTHER,
                             "shelt": book + [self.DIV + " ex=2025-03-20"]},
                            country, commands)
        control = self._project({"margin": self.OTHER, "shelt": book + [self.DIV]},
                                country, commands)
        for name in [" ".join(c) for c in commands]:
            self.assertEqual(got[name], control[name], name)
        self.assertIn("shelt SYQ.US 220", got["list"])
        self.assertIn("SYQ.US 0 220", got["wash-radar --details"])
        self.assertIn("quantity = 220.0", got["holdings"])
        self.assertIn("qty = 20.0", got["holdings"])

    @rule(R.CA_STKDIV_02)
    def test_canada_a_sheltered_transfer_in_keeps_the_row_in_every_reader(self):
        self._sheltered_transfer("canada", (("list",), ("t1135",),
                                            ("wash-radar", "--details")))

    @rule(R.US_STKDIV_06)
    def test_the_us_a_sheltered_transfer_in_keeps_the_row_in_every_reader(self):
        self._sheltered_transfer("usa", (("list",), ("wash-radar", "--details")))

    def _registered_short(self, country):
        # W2: a registered account with a sale of 100 no purchase in the
        # files backs, a purchase of 100, the dated dividend of 20. A
        # registered account is never short (the cause is missing history,
        # which the run says): the covered-short rule never applies there.
        commands = (("list",), ("wash-radar", "--details"), ("find-missing-history",))
        got = self._project({"margin": self.OTHER,
                             "shelt": self.AROUND + [self.DIV + " ex=2025-03-20"]},
                            country, commands, details=("--details",))
        control = self._project({"margin": self.OTHER,
                                 "shelt": self.AROUND + [self.DIV]},
                                country, commands, details=("--details",))
        for name in [" ".join(c) for c in commands]:
            self.assertEqual(got[name], control[name], name)
        self.assertIn("shelt SYQ.US 20 0.00 0.00", got["list"])
        self.assertIn("SYQ.US 0 20", got["wash-radar --details"])
        self.assertIn("quantity = 20.0", got["holdings"])
        self.assertIn('action = "BUY", qty = 20.0', got["holdings"])
        self.assertNotIn("booked as no shares", got["run"])
        self.assertIn("cannot be short", got["run"])
        self.assertIn("SYQ.US shelt", got["find-missing-history"])

    @rule(R.CA_STKDIV_02)
    def test_canada_a_registered_account_is_never_a_covered_short(self):
        self._registered_short("canada")

    @rule(R.US_STKDIV_06)
    def test_the_us_a_registered_account_is_never_a_covered_short(self):
        self._registered_short("usa")

    @rule(R.CA_STKDIV_02)
    def test_the_decision_is_never_made_in_a_registered_book(self):
        book = SHORT + [_div(ex_date='2025-03-20')]
        for country in ('canada', 'usa'):
            with self.subTest(country):
                self.assertEqual(sorted(no_share_rows_of(book, country)[0]), ['d'])
                self.assertEqual(no_share_rows_of(book, country, registered=True),
                                 ({}, {}))
        self.assertEqual(presized(book, None, 'canada', registered=True)[1], [])
        # The engines are told by the account's type (`tjs run` passes
        # taxjson-gains --registered-book for a sheltered account): never
        # by the absence of --taxable, which a taxable account's
        # native-currency books are run without too.
        from taxjson.lib.pipeline import GainsRequest, engine_options
        from taxjson.pipeline.stages import _registered_flags
        self.assertIs(engine_options(GainsRequest(country='canada', registered_book=True))
                      .get('registered_book'), True)
        self.assertNotIn('registered_book',
                         engine_options(GainsRequest(country='canada')))
        self.assertEqual(_registered_flags(False), ["--registered-book"])
        self.assertEqual(_registered_flags(True), [])

    @rule(R.CA_STKDIV_02)
    def test_the_decision_counts_a_plain_transfer_as_the_engines_do(self):
        dated = _div(ex_date='2025-03-20')
        arrived = _row('t', '2025-02-03', 200, action='TRANSFER', type='')
        for country in ('canada', 'usa'):
            with self.subTest(country):
                self.assertEqual(no_share_rows_of([arrived] + SHORT + [dated], country)[0],
                                 {})
                self.assertEqual(sorted(no_share_rows_of(SHORT + [dated], country)[0]),
                                 ['d'])
        # A transfer out leaves the account flat: a short sold after it is one.
        left = _row('u', '2025-02-10', -200, action='TRANSFER', type='')
        self.assertEqual(sorted(no_share_rows_of(
            [arrived, left] + SHORT + [dated], 'canada')[0]), ['d'])

    @rule(R.CA_STKDIV_02)
    def test_the_sizing_book_holds_the_openings_the_gains_run_synthesizes(self):
        from taxjson.lib.missing_history import MissingHistoryPairs
        dated = _div(ex_date='2025-03-20')
        book = SHORT + [dated, _row('DIST-1', '2025-05-01', 0, action='ADJUST')]
        rows, gone = presized(book, None, 'canada')
        self.assertEqual(([r['id'] for r in rows], gone), (['s', 'c'], ['d']))
        pairs = MissingHistoryPairs({('SYQ', 'm')})
        pairs.quantities[('SYQ', 'm')] = 200.0
        rows, gone = presized(book, pairs, 'canada')
        self.assertEqual(gone, [])
        self.assertEqual(sum(r['quantity'] for r in rows), 210.0)
        self.assertNotIn('DIST-1', [r['id'] for r in rows])

    @rule(R.CA_STKDIV_02)
    def test_a_reader_after_the_run_reads_the_gains_files_alone(self):
        book = SHORT + [_div(ex_date='2025-03-20')]
        with tempfile.TemporaryDirectory() as d:
            work = Path(d)
            # No gains file names the row: kept, whatever the rows say.
            self.assertEqual(booked_rows(book, []), book)
            self.assertEqual(booked_rows(book, [work / "m_gains.json"]), book)
            (work / "m_gains.json").write_text(json.dumps(
                {"transactions": [], "stock_dividends_after_cover": ["d"]}))
            named = [work / "m_gains.json", work / "none_gains.json"]
            self.assertEqual([r['id'] for r in booked_rows(book, named)], ['s', 'c'])
            objects = [TaxTransaction(**r) for r in book]
            self.assertEqual([t.id for t in booked_rows(objects, named + named)],
                             ['s', 'c'])

    @rule(R.CA_STKDIV_02)
    def test_a_disagreement_between_the_sizing_and_the_gains_run_is_said(self):
        book = SHORT + [_div(ex_date='2025-03-20')]
        self.assertEqual(presize_disagreements(book, ['d'], ['d']), [])
        self.assertEqual(presize_disagreements(book, [], []), [])
        self.assertEqual(presize_disagreements(book, ['d'], []), [
            "stock dividend: SYQ (m): the row of 10 share(s) on 2025-04-01 was read "
            "as no shares (a covered short's obligation) when [[distributions]] "
            "sized its record-date balances, and the gains run booked it as shares "
            "received. The books' reading stands; a [[distributions]] amount of SYQ "
            "with a record date after the row was sized on 10 share(s) too few: "
            "check its ADJUST row (`tjs list --details`), and report this "
            "(`tjs --version`, this line)."])
        (line,) = presize_disagreements(book, [], ['d'])
        self.assertIn("was read as shares received when [[distributions]] sized",
                      line)
        self.assertIn("booked it as no shares (a covered short's obligation)", line)
        self.assertIn("10 share(s) too many", line)

    @rule(R.CA_STKDIV_02)
    def test_the_run_says_a_disagreement_after_the_gains_run(self):
        import contextlib
        import io
        from taxjson.pipeline import stages
        book = SHORT + [_div(ex_date='2025-03-20')]
        with tempfile.TemporaryDirectory() as d:
            base, gains = Path(d) / "m_base.json", Path(d) / "m_gains.json"
            base.write_text(json.dumps({"transactions": book}))
            said = {}
            for name, key in (("agree", ["d"]), ("differ", [])):
                gains.write_text(json.dumps({"transactions": [],
                                             "stock_dividends_after_cover": key}))
                out = io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                    stages._say_presize_disagreements(base, gains, None, "canada")
                said[name] = " ".join(out.getvalue().split())
        self.assertEqual(said["agree"], "")
        self.assertIn("ATTENTION: stock dividend: SYQ (m): the row of 10 share(s) on "
                      "2025-04-01 was read as no shares", said["differ"])


class TestABookWithAnOwedRowAndARowAfterItsCover(unittest.TestCase):
    """The gains files carry two keys: `stock_dividends_owed` (a row posted
    while the short is open: the short grows) and
    `stock_dividends_after_cover` (a row posted after its short was
    covered: no shares). One book with both, against the same book with
    neither row and the owed shares written as the engine books them (5
    more sold short at no proceeds): every reader agrees with the
    engine, and no row is under both keys."""

    BOOK = ["BUYSELL 2025-03-03 10:00:00 SYQ.US -100 USD 5 500 0",
            "BUYSELL 2025-03-25 10:00:00 SYQ.US 100 USD 4.4 440 0",
            "BUYSELL 2025-04-01 10:00:00 SYQ.US 10 USD 0 0 type=stock_dividend "
            "ex=2025-03-20",
            "BUYSELL 2025-05-05 10:00:00 SYQ.US -50 USD 5 250 0",
            "BUYSELL 2025-06-02 10:00:00 SYQ.US 5 USD 0 0 type=stock_dividend "
            "ex=2025-05-20"]
    CONTROL = BOOK[:2] + [BOOK[3], "BUYSELL 2025-06-02 10:00:00 SYQ.US -5 USD 0 0"]
    HOLDER = ["BUYSELL 2025-01-06 10:00:00 SYQ.US 300 USD 5 1500 0"]

    def _project(self, books, country):
        from _qa_project import console, flat, tj, toml
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "p"
            for account, lines in books.items():
                (root / "inputs" / account).mkdir(parents=True)
                (root / "inputs" / account / "book.tt").write_text(
                    "\n".join(lines) + "\n")
            extra = "".join(f'[accounts.{a}]\ntype = "taxable"\n'
                            for a in books if a != "margin")
            (root / "taxjson.toml").write_text(
                toml(country=country, year=2025, extra_accounts=extra))
            (root / "ticker.map").write_text("")
            run = tj(root, "run", "--no-input", check=False)
            self.assertEqual(run.returncode, 0, console(run))
            out = {name: flat(tj(root, *cmd, check=False).stdout)
                   for name, cmd in (("list", ("list",)),
                                     ("radar", ("wash-radar", "--details")))}
            out["run"] = flat(console(run))
            out["trades"] = flat((root / "reports/margin_holdings.toml").read_text()
                                 ).split("trades = ")[1]
            out["keys"] = {
                p.name: (doc.get("stock_dividends_owed") or [],
                         doc.get("stock_dividends_after_cover") or [])
                for p in sorted((root / "work").glob("margin*gains*.json"))
                for doc in [json.loads(p.read_text())]}
            return out

    def _both_keys(self, country):
        got = self._project({"margin": self.BOOK}, country)
        control = self._project({"margin": self.CONTROL}, country)
        # Each gains file names one row under each key, never one under both.
        self.assertTrue(got["keys"])
        for name, (owed, gone) in got["keys"].items():
            self.assertEqual((len(owed), len(gone)), (1, 1), name)
            self.assertFalse(set(owed) & set(gone), name)
        self.assertIn("margin SYQ.US -55", got["list"])
        for name in ("list", "radar", "trades"):
            self.assertEqual(got[name], control[name], name)
        self.assertIn("SYQ.US -55 0", got["radar"])
        self.assertIn('{ date = 2025-06-02, action = "SELL", qty = 5.0, price = 0.0 }',
                      got["trades"])
        self.assertNotIn("2025-04-01", got["trades"])
        self.assertIn("booked as no shares", got["run"])
        self.assertNotIn("$0 cost", got["run"])
        # Pooled with a holder in another account: the engine's units too.
        pooled = self._project({"margin": self.BOOK, "holder": self.HOLDER}, country)
        self.assertIn("margin SYQ.US -55", pooled["list"])
        self.assertIn("SYQ.US 245 0", pooled["radar"])

    @rule(R.CA_STKDIV_02)
    def test_canada_every_reader_agrees_with_the_engine_on_both_rows(self):
        self._both_keys("canada")

    @rule(R.US_STKDIV_06)
    def test_the_us_every_reader_agrees_with_the_engine_on_both_rows(self):
        self._both_keys("usa")


class TestTheExampleLineNamesTheRowsOwnCurrency(unittest.TestCase):
    """The "booked as no shares" line shows the .tt line that carries the
    delivered cost. It names the currency the row was written in, not the
    base currency the books were converted to: pasted with the broker's
    US-dollar cost, a line that said CAD had the cost read as Canadian
    dollars (`tjs sum` 36.00 where the US-dollar line gives 20.25)."""

    def _run(self, country, symbol, currency, cost=None):
        import re
        from _qa_project import console, flat, tj, toml
        book = [f"BUYSELL 2025-03-03 10:00:00 {symbol} -100 {currency} 5 500 0",
                f"BUYSELL 2025-03-25 10:00:00 {symbol} 150 {currency} 4.4 660 0",
                f"BUYSELL 2025-04-01 10:00:00 {symbol} 10 {currency} 0 0 "
                f"type=stock_dividend ex=2025-03-20"]
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "p"
            (root / "inputs/margin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(toml(country=country, year=2025))
            (root / "ticker.map").write_text("")
            tt = root / "inputs/margin/book.tt"
            tt.write_text("\n".join(book) + "\n")
            run = tj(root, "run", "--no-input", "--details", check=False)
            self.assertEqual(run.returncode, 0, console(run))
            (line,) = set(re.findall(r"\(`(BUYSELL [^`]*delivered_cost=COST)`",
                                     flat(console(run))))
            if cost is None:
                return line, ""
            # The printed line, pasted with the cost in place of the broker's row.
            tt.write_text("\n".join(book[:2] + [line.replace("COST", cost)]) + "\n")
            again = tj(root, "run", "--no-input", check=False)
            self.assertEqual(again.returncode, 0, console(again))
            return line, flat(tj(root, "sum", check=False).stdout)

    @rule(R.CA_STKDIV_02)
    def test_canada_a_us_dollar_rows_line_says_usd_and_its_cost_is_read_in_usd(self):
        line, total = self._run("canada", "SYQ.US", "USD", cost="45")
        self.assertEqual(line, "BUYSELL 2025-04-01 10:00:00 SYQ.US 10 USD 0 0 "
                               "type=stock_dividend ex=2025-03-20 delivered_cost=COST")
        # 500 - 440 - 45 = 15.00 USD at 1.35.
        self.assertIn("20.25", total)
        self.assertNotIn("36.00", total)

    @rule(R.CA_STKDIV_02)
    def test_canada_a_canadian_dollar_rows_line_is_as_it_was(self):
        line, _ = self._run("canada", "SYQ.TO", "CAD")
        self.assertEqual(line, "BUYSELL 2025-04-01 10:00:00 SYQ.TO 10 CAD 0 0 "
                               "type=stock_dividend ex=2025-03-20 delivered_cost=COST")

    @rule(R.US_STKDIV_06)
    def test_the_us_a_canadian_dollar_rows_line_says_cad(self):
        line, _ = self._run("usa", "SYQ.TO", "CAD")
        self.assertEqual(line, "BUYSELL 2025-04-01 10:00:00 SYQ.TO 10 CAD 0 0 "
                               "type=stock_dividend ex=2025-03-20 delivered_cost=COST")
        line, _ = self._run("usa", "SYQ.US", "USD")
        self.assertIn(" SYQ.US 10 USD 0 0 ", line)

    @rule(R.CA_STKDIV_02)
    def test_the_decisions_facts_carry_the_currency_the_row_was_written_in(self):
        converted = dict(_div(ex_date='2025-03-20'), currency='CAD', trade_currency='USD')
        self.assertEqual(no_share_rows(SHORT + [converted], entitled_lots=False)[0]
                         ['d']['currency'], 'USD')
        plain = dict(_div(ex_date='2025-03-20'), currency='CAD')
        self.assertEqual(no_share_rows(SHORT + [plain], entitled_lots=False)[0]
                         ['d']['currency'], 'CAD')


if __name__ == "__main__":
    unittest.main()
