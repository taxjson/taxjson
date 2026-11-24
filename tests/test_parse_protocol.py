"""The parse template (step 5h (2)): read, then per row classify and
build, then finish and the closing steps every parser shares; each
Outcome kind counted as the contract says, every appended row's line
noted, the expiries `build` keeps clamped and this file's alone."""
import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from taxjson.lib.brokerages.base import BaseBrokerage
from taxjson.parsers.protocol import KINDS, KNOWN_NONEVENT_PREFIX, REPORTED_PREFIX, Outcome, Row
from taxjson.parsers.settings import ParseSettings

_CONTRACT = "ZQQ 2025-12-19 C 50"


def _trade(line, settle):
    return {"action": "BUYSELL", "symbol": _CONTRACT, "account": "A",
            "date": "2025-12-19", "date_settle": settle, "from": line}


class _Row(Row):
    __slots__ = ("kind", "label")

    def __init__(self, line, kind, label=""):
        super().__init__({}, line)
        self.kind, self.label = kind, label


class _Stub(BaseBrokerage):
    """Rows that say their own outcome; a build is one tagged row, an
    expiry row kept for the clamp as a parser's build keeps it."""
    def __init__(self, rows):
        super().__init__()
        self.rows, self.finished = rows, None

    def read(self, path):
        return self.rows

    def classify(self, row):
        if row.kind == "consumed":
            return Outcome.consumed([{"action": "BUYSELL", "symbol": "ZQQ", "from": 4}])
        if row.kind in ("expiry", "noop"):
            return Outcome.build()
        return Outcome(row.kind, row.label)

    def build(self, row):
        if row.kind == "expiry":
            tx = {"action": "BUYSELL", "symbol": _CONTRACT, "account": "A",
                  "date": "2025-12-19", "date_settle": "2025-12-19", "from": row.line}
            self._expiries.append(tx)
            return [tx]
        return [_trade(row.line, "2025-12-22")]

    def finish(self, transactions, path):
        self.finished = (len(transactions), path.name)
        return transactions


def _parse(parser):
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "x.csv"
        f.write_text("a\n")
        with redirect_stderr(io.StringIO()) as err:
            out = parser.parse_file(f)
    return out, err.getvalue()


class TestTemplate(unittest.TestCase):
    def test_the_surface_is_frozen(self):
        # Every public member of the base, class or instance, is listed in
        # parsers.protocol.SURFACE once, and every name there is a member
        # (TJ-2321): a new hook or helper is a deliberate edit of the list.
        from taxjson.parsers.protocol import SURFACE
        listed = [n for names in SURFACE.values() for n in names]
        self.assertEqual(len(listed), len(set(listed)), "a name listed twice")
        # Configured, as every parse is: the settings and the account
        # context are the instance's then (TJ-2411).
        configured = BaseBrokerage()
        configured.configure(ParseSettings(), account_context=object())
        members = ({n for n in dir(BaseBrokerage) if not n.startswith("_")}
                   | {n for n in vars(configured) if not n.startswith("_")})
        self.assertEqual(sorted(members - set(listed)), [], "a member not in SURFACE")
        self.assertEqual(sorted(set(listed) - members), [], "a SURFACE name not a member")

    def test_a_build_that_counts_the_row_is_refused(self):
        # The template is the one counter: a builder that counts its row and
        # returns rows (or []) would count it twice (TJ-2374, TJ-2391).
        class Counts(_Stub):
            def build(self, row):
                self.count_nonevent("dust")
                return []
        with self.assertRaisesRegex(TypeError, "build counted the row itself"):
            _parse(Counts([_Row(2, "build")]))

        class Moves(_Stub):         # a move keeps the total: allowed
            def build(self, row):
                if row.line == 3:
                    self.recount(Outcome.nonevent("paired"))
                return super().build(row)
        out, _err = _parse(Moves([_Row(2, "build"), _Row(3, "build")]))
        self.assertEqual(len(out), 2)

    def test_each_outcome_kind_is_counted_and_every_row_lined(self):
        p = _Stub([_Row(2, "skip", "blank"), _Row(3, "nonevent", "a footer"),
                   _Row(4, "consumed"), _Row(5, "build"), _Row(6, "build")])
        out, err = _parse(p)
        self.assertEqual([t["from"] for t in out], [4, 5, 6])
        self.assertEqual(p._skip_counts,
                         {"blank": 1, KNOWN_NONEVENT_PREFIX + "a footer": 1})
        self.assertEqual((p._rows_seen, p._rows_consumed), (5, 3))
        self.assertEqual([p.row_line(t) for t in out], [4, 5, 6])
        self.assertEqual(p.finished, (3, "x.csv"))
        self.assertIn("skipped", err.lower())

    def test_a_parser_handing_its_own_list_builds_into_it(self):
        # 5h (8d): a row that undoes a row built before it (an IB `Ca`) works
        # on the parser's list; the template builds into that list, notes
        # the lines of what a build appends to it, and the parse returns it.
        own = []

        class _Own(_Stub):
            def new_transactions(self):
                return own

            def build(self, row):
                if row.kind == "noop":          # cancels the row before it
                    own.pop()
                    return Outcome.consumed()
                return super().build(row)

        p = _Own([_Row(2, "build"), _Row(3, "build"), _Row(4, "noop"), _Row(5, "build")])
        out, _ = _parse(p)
        self.assertIs(out, own)
        self.assertEqual([t["from"] for t in out], [2, 5])
        self.assertEqual([p.row_line(t) for t in out], [2, 5])
        self.assertEqual((p._rows_seen, p._rows_consumed), (4, 4))
        self.assertEqual(BaseBrokerage().new_transactions(), [])

    def test_a_row_that_removes_and_appends_has_its_rows_noted(self):
        # TJ-2481: a row that removes rows built before it and appends its own
        # (an IB `Ca` undoing a transfer, then booking): every appended row
        # carries the row's line. The old index slice missed one appended row
        # per row removed.
        own = []

        class _Swap(_Stub):
            def new_transactions(self):
                return own

            def classify(self, row):
                return Outcome.build() if row.kind.startswith("swap") else super().classify(row)

            def build(self, row):
                if row.kind.startswith("swap"):
                    n = int(row.kind[4:])
                    del own[-n:]                # undoes the n rows before it
                    return [_trade(row.line, "2025-12-22") for _ in range(n)]
                return super().build(row)

        p = _Swap([_Row(2, "build"), _Row(3, "build"), _Row(4, "swap1"),
                   _Row(5, "build"), _Row(6, "swap2"), _Row(7, "build")])
        out, _ = _parse(p)
        self.assertEqual([t["from"] for t in out], [2, 6, 6, 7])
        self.assertEqual([p.row_line(t) for t in out], [2, 6, 6, 7])

    def test_a_parser_handing_its_own_list_reads_whole(self):
        # TJ-2482 / TJ-2490: the hook is asked once read has returned, so a
        # parser that hands its own list reads its rows whole. A generator
        # read (its state not set up yet: an AttributeError, or the previous
        # file's list) is refused, whether the hook is the class's or set on
        # the instance; a list read hands its list as it is, uncopied.
        class _Lazy(_Stub):
            def read(self, path):
                self.own = []
                yield from self.rows

            def new_transactions(self):
                return self.own

        with self.assertRaisesRegex(TypeError, "reads its rows whole"):
            _parse(_Lazy([_Row(2, "build")]))

        class _LazyPlain(_Stub):
            def read(self, path):
                yield from self.rows

        p = _LazyPlain([_Row(2, "build")])
        own = []
        p.new_transactions = lambda: own
        with self.assertRaisesRegex(TypeError, "reads its rows whole"):
            _parse(p)
        out, _ = _parse(_LazyPlain([_Row(2, "build"), _Row(3, "build")]))
        self.assertEqual([t["from"] for t in out], [2, 3])  # the default list: lazy is fine

        class _Counted(list):
            iterated = 0

            def __iter__(self):
                type(self).iterated += 1
                return super().__iter__()

        class _Eager(_Stub):
            def read(self, path):
                return self.rows

            def new_transactions(self):
                return own

        _parse(_Eager(_Counted([_Row(2, "build")])))
        self.assertEqual(_Counted.iterated, 1)      # iterated by the loop alone

    def test_a_new_row_before_a_seen_one_is_noted(self):
        # TJ-2491: a build that answers a new row and then one already built
        # (or a parser that moves an earlier row to its list's end) leaves the
        # new row before a seen one: it is noted all the same.
        class _Again(_Stub):
            def build(self, row):
                if row.kind == "again":
                    return [_trade(row.line, "2025-12-22"), self.first]
                tx = _trade(row.line, "2025-12-22")
                self.first = getattr(self, "first", tx)
                return [tx]

            def classify(self, row):
                return Outcome.build() if row.kind == "again" else super().classify(row)

        p = _Again([_Row(2, "build"), _Row(3, "again")])
        out, _ = _parse(p)
        self.assertEqual([t["from"] for t in out], [2, 3, 2])
        self.assertEqual([p.row_line(t) for t in out], [2, 3, 2])

    def test_the_expiries_build_keeps_clamp_this_file_and_no_other(self):
        p = _Stub([_Row(2, "build"), _Row(3, "expiry")])
        out, _ = _parse(p)
        # The 0DTE trade's settle is clamped to the expiry; the expiry row
        # is remembered for the account-wide clamp; nothing is left for the
        # next file.
        self.assertEqual(out[0]["date_settle"], "2025-12-19")
        self.assertEqual([e["from"] for e in p.expiry_rows], [3])
        self.assertEqual(p._expiries, [])
        p.rows = [_Row(2, "build")]                 # a file with no expiry row
        out, _ = _parse(p)
        self.assertEqual(out[0]["date_settle"], "2025-12-22")

    def test_a_failed_parse_leaves_no_expiries_behind_and_releases(self):
        class Failing(_Stub):
            released = 0

            def finish(self, transactions, path):
                raise RuntimeError("after build")

            def release(self):
                self.released += 1
        p = Failing([_Row(2, "expiry")])
        with self.assertRaises(RuntimeError):
            _parse(p)
        self.assertEqual((p._expiries, p.released), ([], 1))

    def test_a_non_event_found_building_is_counted_by_the_template(self):
        class Finding(_Stub):
            def build(self, row):
                if row.kind == "noop":
                    return Outcome.nonevent("a 1-for-1 with nothing to book")
                return super().build(row)
        p = Finding([_Row(2, "build"), _Row(3, "noop")])
        out, _ = _parse(p)
        self.assertEqual([t["from"] for t in out], [2])
        self.assertEqual(p._rows_consumed, 1)
        self.assertEqual(p._skip_counts,
                         {KNOWN_NONEVENT_PREFIX + "a 1-for-1 with nothing to book": 1})

    def test_a_file_that_is_not_the_parsers_export_has_no_row_count(self):
        class Foreign(_Stub):
            def read(self, path):
                self._rows_seen = None              # not my export
                return []
        p = Foreign([])
        out, err = _parse(p)
        self.assertEqual((out, p._rows_seen, err), ([], None, ""))

    def test_a_consumed_row_found_a_non_event_in_finish_moves_its_count(self):
        class Pairing(_Stub):
            def finish(self, transactions, path):
                self.recount(Outcome.nonevent("leg booked with its counter-leg elsewhere"))
                return transactions
        p = Pairing([_Row(2, "consumed"), _Row(3, "build")])
        _parse(p)
        self.assertEqual((p._rows_seen, p._rows_consumed), (2, 1))
        self.assertEqual(p._skip_counts,
                         {KNOWN_NONEVENT_PREFIX + "leg booked with its counter-leg elsewhere": 1})

    def test_rows_read_pre_counted_add_to_the_rows_seen(self):
        class Footered(_Stub):
            def read(self, path):
                self._rows_seen = 1                 # a footer dropped here
                self.count_nonevent("footer")
                return super().read(path)
        p = Footered([_Row(2, "build"), _Row(3, "skip", "blank")])
        _parse(p)
        self.assertEqual(p._rows_seen, 3)
        self.assertEqual(p._rows_seen, p._rows_consumed + sum(p._skip_counts.values()))

    def test_parse_rows_stops_at_the_parsers_finish(self):
        # What a parser reading another file for its hints gets: the rows
        # built and finished, the expiries still there, no summary, no
        # release.
        class Keeping(_Stub):
            released = 0

            def release(self):
                self.released += 1
        p = Keeping([_Row(2, "build"), _Row(3, "expiry"), _Row(4, "skip", "blank")])
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "x.csv"
            f.write_text("a\n")
            with redirect_stderr(io.StringIO()) as err:
                out = p.parse_rows(f)
        self.assertEqual([t["from"] for t in out], [2, 3])
        self.assertEqual([e["from"] for e in p._expiries], [3])
        self.assertEqual((p.finished, p.released, err.getvalue()), ((2, "x.csv"), 0, ""))

    def test_a_row_with_its_own_id_takes_no_fill_mark(self):
        class WithIds(_Stub):
            def build(self, row):
                out = super().build(row)
                if row.line == 3:
                    out[0]["id"] = "tx-3"
                return out
        p = WithIds([_Row(2, "build"), _Row(3, "build"), _Row(4, "build")])
        out, _ = _parse(p)
        # Three identical rows: the second carries its own id and is left
        # alone; the third is the second fill among the id-less rows.
        self.assertEqual([t.get("description", "") for t in out], ["", "", "[fill #2]"])

    def test_only_rows_alike_in_every_field_of_the_id_are_split_fills(self):
        # TJ-5038: rows one field of the id already tells apart get two
        # ids; marked, the copy an overlapping export holds was a third.
        from taxjson.lib.brokerages.base import FILL_IDENTITY_FIELDS
        base = {"action": "BUYSELL", "date": "2025-03-03", "symbol": "ZQQ.TO",
                "quantity": 10, "price": 10.0, "net_amount": -100.0}
        other = {"action": "DIVIDEND", "date": "2025-03-04", "time": "10:00:00",
                 "symbol": "ZQR.TO", "quantity": 11, "currency": "USD",
                 "price": 11.0, "net_amount": -110.0, "gross_amount": 100.0,
                 "account": "B", "type": "roc", "date_settle": "2025-03-05",
                 "description": "second fill"}
        self.assertEqual([n for n, _ in FILL_IDENTITY_FIELDS], list(other))
        for name in other:
            with self.subTest(field=name):
                rows = [dict(base), dict(base, **{name: other[name]})]
                BaseBrokerage.disambiguate_split_fills(rows)
                self.assertEqual(rows[1].get("description", ""),
                                 other[name] if name == "description" else "")
        # Alike in all of them (10 and 10.0 are one quantity; a field left
        # out is its default): the second is marked, whatever else differs.
        rows = [dict(base), dict(base, quantity=10.0, time="09:30:00",
                                 account="PORTFOLIO", commission=1.0)]
        BaseBrokerage.disambiguate_split_fills(rows)
        self.assertEqual(rows[1]["description"], "[fill #2]")

    def test_the_fill_identity_is_the_fields_the_id_hashes(self):
        # A field the id reads and the marks did not would collapse two
        # rows; one the marks read and the id does not would mark none.
        import dataclasses
        from taxjson.lib.brokerages.base import FILL_IDENTITY_FIELDS, fill_identity
        from taxjson.model.transaction import TaxTransaction
        base = {"action": "BUYSELL", "date": "2025-03-03"}
        for f in dataclasses.fields(TaxTransaction):
            if f.name == "id" or not isinstance(f.default, (str, float)):
                continue
            changed = dict(base, **{f.name: "x" if isinstance(f.default, str) else 7.0})
            with self.subTest(field=f.name):
                self.assertEqual(
                    TaxTransaction(**base).id != TaxTransaction(**changed).id,
                    fill_identity(base) != fill_identity(changed))
        for name, default in FILL_IDENTITY_FIELDS:
            if name not in base:
                self.assertEqual(getattr(TaxTransaction(**base), name), default, name)

    def test_a_row_keeps_the_builder_classify_chose(self):
        row = _Row(2, "build")
        self.assertIsNone(row.builder)
        outcome = row.build(len)
        self.assertEqual((outcome.kind, row.builder), ("build", len))

    def test_a_reported_skip_is_counted_and_not_listed(self):
        class Reporting(_Stub):
            def classify(self, row):
                if row.kind == "skip":
                    return Outcome.reported("action DIV")
                if row.kind == "bare":
                    return Outcome.skip("action XYZ")
                return super().classify(row)
        p = Reporting([_Row(2, "build"), _Row(3, "skip", "x"), _Row(4, "nonevent", "footer"),
                       _Row(5, "bare")])
        _out, err = _parse(p)
        self.assertEqual(Outcome.reported("x"), Outcome("reported", "x"))
        self.assertEqual(p.skip_classes(),
                         ({"footer": 1}, {"action DIV": 1}, {"action XYZ": 1}))
        self.assertEqual(p._rows_seen, p._rows_consumed + sum(p._skip_counts.values()))
        self.assertIn("footer", err)
        self.assertIn("action XYZ", err)
        self.assertNotIn("DIV", err)

    def test_moving_more_rows_than_consumed_is_a_parser_bug(self):
        class Over(_Stub):
            def finish(self, transactions, path):
                self.recount(Outcome.nonevent("leg"), 2)         # one row was consumed
                return transactions
        with self.assertRaises(TypeError):
            _parse(Over([_Row(2, "consumed")]))

    def test_a_skipped_row_a_later_pass_pairs_is_recounted_consumed(self):
        # The move the other way (Interactive Brokers' cross-statement pairing):
        # a row counted as reported is consumed after all; the tally holds.
        class Paired(_Stub):
            def finish(self, transactions, path):
                self.recount(Outcome.consumed(), was=Outcome.reported("unpaired leg"))
                return transactions
        p = Paired([_Row(2, "reported", "unpaired leg")])
        _parse(p)
        self.assertEqual((p._rows_consumed, p._skip_counts), (1, {}))
        self.assertEqual(p.row_counts().unaccounted, 0)

        class Wrong(_Stub):
            def finish(self, transactions, path):
                self.recount(Outcome.build())       # not a counted kind
                return transactions
        with self.assertRaises(TypeError):
            _parse(Wrong([_Row(2, "consumed")]))

    def test_a_recount_carries_no_rows(self):
        # Rows reach the books through the parse's answer; a recount that
        # carried one would count it consumed and drop it (TJ-2413).
        class Carry(_Stub):
            def finish(self, transactions, path):
                self.recount(Outcome.consumed([{"action": "buy"}]),
                             was=Outcome.reported("unpaired leg"))
                return transactions
        with self.assertRaisesRegex(TypeError, "carry no rows"):
            _parse(Carry([_Row(2, "reported", "unpaired leg")]))

    def test_a_skip_label_with_a_class_prefix_is_refused(self):
        # A bare skip labelled like a non-event or a reported row would be
        # read back in that class, calming a dropped row (TJ-2414), whether
        # an Outcome says it or the public writer (TJ-2420).
        for prefix in (KNOWN_NONEVENT_PREFIX, REPORTED_PREFIX):
            with self.subTest(prefix=prefix):
                with self.assertRaisesRegex(TypeError, "class's prefix"):
                    _parse(_Stub([_Row(2, "skip", prefix + "a footer")]))
                with self.assertRaisesRegex(TypeError, "class's prefix"):
                    BaseBrokerage().count_skip(prefix + "a footer")
        p = BaseBrokerage()
        p.count_nonevent("a footer")
        p.count_skip("blank")
        self.assertEqual(p.skip_classes(), ({"a footer": 1}, {}, {"blank": 1}))

    def test_a_consumed_row_found_a_skip_in_finish_moves_its_count(self):
        class Orphan(_Stub):
            def finish(self, transactions, path):
                self.recount(Outcome.skip("orphan leg"), 1)
                return transactions
        p = Orphan([_Row(2, "consumed")])
        _parse(p)
        self.assertEqual((p._rows_consumed, p._skip_counts), (0, {"orphan leg": 1}))

    def test_build_answering_build_is_refused(self):
        class Wrong(_Stub):
            def build(self, row):
                return Outcome.build()
        with self.assertRaises(TypeError):
            _parse(Wrong([_Row(2, "build")]))

    def test_an_unknown_kind_is_refused_when_built(self):
        with self.assertRaises(TypeError) as cm:
            Outcome("skipped", "typo")
        self.assertIn("skipped", str(cm.exception))
        self.assertEqual(KINDS, ("skip", "nonevent", "reported", "consumed", "build"))

    def test_a_row_carries_its_line_as_its_attribute(self):
        # The row contract: a row object's `line`; the reader's bare dict
        # (its keys the export's columns) is not a row.
        p = BaseBrokerage()
        self.assertEqual(p.row_line_of(_Row(8, "build")), 8)
        self.assertIsNone(p.row_line_of({"line": 7}))
        self.assertIsNone(p.row_line_of(("a", "b")))

    def test_the_step_after_the_marks_sees_them(self):
        class Reversing(_Stub):
            def after_marks(self, transactions):
                return transactions[::-1]
        p = Reversing([_Row(2, "build"), _Row(3, "build")])
        out, _ = _parse(p)
        # Two identical rows: the first as `finish` left them keeps its
        # stable id (no mark); a step after the marks moves them with it.
        self.assertEqual([t["from"] for t in out], [3, 2])
        self.assertEqual([t.get("description", "") for t in out], ["[fill #2]", ""])


class _Cash(_Stub):
    """A stub whose "cash" rows are noted (a non-event, as a broker's cash
    row is) and whose finish builds one event per row noted, or raises
    building them when `bad` (a cash cell the parse never reads); a
    `refuse` file is refused in read."""
    bad = refuse = False

    def read(self, path):
        if self.refuse:
            raise ValueError("not this parser's export")
        return self.rows

    def classify(self, row):
        if row.kind == "cash":
            self.note_cash(row)
            return Outcome.nonevent("a cash row")
        return super().classify(row)

    def finish(self, transactions, path):
        def build():
            if self.bad:
                raise ValueError(f"{path.name}:{self._cash_rows[0].line}: unreadable")
            return [{"from": r.line} for r in self._cash_rows]
        self._cash_events_from(build)
        return super().finish(transactions, path)


class TestTheCashEventsOfEachFile(unittest.TestCase):
    """PLAN step 16 commits 7 and 8: the rows noted (`note_cash`), the events
    the finish builds of them (`cash_events`) and why it could not
    (`cash_problem`) are one file's, whatever the parser parsed before."""

    def test_the_finish_builds_the_rows_noted_in_order(self):
        p = _Cash([_Row(2, "cash"), _Row(3, "build"), _Row(4, "cash")])
        out, _ = _parse(p)
        self.assertEqual([t["from"] for t in out], [3])
        self.assertEqual((p.cash_events, p.cash_problem), ([{"from": 2}, {"from": 4}], ""))

    def test_a_cash_cell_the_finish_cannot_read_is_the_files_problem(self):
        # Never a refused parse: the rows are booked, the events none.
        p = _Cash([_Row(2, "cash"), _Row(3, "build")])
        p.bad = True
        out, _ = _parse(p)
        self.assertEqual([t["from"] for t in out], [3])
        self.assertEqual((p.cash_events, p.cash_problem), ([], "ValueError: x.csv:2: unreadable"))

    def test_a_refused_file_after_a_parsed_one_holds_none_of_its_cash(self):
        for bad in (False, True):
            with self.subTest(bad=bad):
                p = _Cash([_Row(2, "cash")])
                p.bad = bad
                _parse(p)
                self.assertEqual(bool(p.cash_events), not bad)
                self.assertEqual(bool(p.cash_problem), bad)
                p.refuse = True
                with self.assertRaises(ValueError):
                    _parse(p)
                self.assertEqual((p.cash_events, p.cash_problem), (None, ""))

    def test_parse_rows_twice_holds_the_second_files_rows_alone(self):
        # A parser reading another file for its hints (parse_rows, no
        # parse_file to clear what it kept).
        p = _Cash([_Row(2, "cash"), _Row(3, "cash")])
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "x.csv"
            f.write_text("a\n")
            p.parse_rows(f)
            p.rows = [_Row(5, "cash")]
            p.parse_rows(f)
        self.assertEqual([r.line for r in p._cash_rows], [5])
        self.assertEqual(p.cash_events, [{"from": 5}])

    def test_a_parse_keeps_no_cash_rows_after_it(self):
        p = _Cash([_Row(2, "cash"), _Row(3, "build")])
        _parse(p)
        self.assertEqual(p._cash_rows, [])
        self.assertEqual(p.cash_events, [{"from": 2}])


class TestTheConvertedSetIsStated(unittest.TestCase):
    """TJ-2357 (TJ-2325 in part): which parsers still override `parse_file` is
    read from the code, and the two sentences that state it (the protocol's
    docstring, the map) must say the same: none, since Interactive Brokers'
    conversion (5h (8d))."""

    def test_the_parsers_overriding_parse_file_are_the_ones_the_docs_name(self):
        import re as _re
        from taxjson.parsers import protocol
        pkg = Path(protocol.__file__).resolve().parents[1]           # src/taxjson
        overriding = sorted(p.stem for p in (pkg / "lib" / "brokerages").glob("*.py")
                            if p.stem != "base"
                            and _re.search(r"^\s+def parse_file\(", p.read_text(encoding="utf-8"), _re.M))
        self.assertEqual(overriding, [])
        self.assertIn("none overrides `parse_file`", protocol.__doc__)
        repo = pkg.parents[1]
        amap = (repo / "docs" / "architecture-map.md").read_text(encoding="utf-8")
        self.assertIn("no parser overrides it", amap)


if __name__ == "__main__":
    unittest.main()
