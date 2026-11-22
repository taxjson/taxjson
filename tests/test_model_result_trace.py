"""Step 6c(6c2): the trace, explain, filed and option-report readers read
gains rows through model/result views.

lib/trace_format (a trace block's figures, the wash explanation, the
per-symbol summary table, the report layout), bin/taxjson_explain
(`--list`'s one-line summaries, the trace's colour), lib/filed_locks
`aggregates_from_gains` (the figures close-year locks and check-filed and
lib/compare recompute) and bin/_option_gains_report (taxjson-ccd-gains,
taxjson-leaps-gains) read a gains row's units and money through
`GainRow(e)` views over the rows' own dicts. The golden projects pin the
trace files, ccd.rpt and leaps.rpt and close-year / check-filed against
the output from before the change. The rest is pinned as
test_model_result_audit pins 6c(6c1): `tjs wash-sales --explain`, the
explain tool's modes over each base book, the filed aggregates (and
lib/compare `aggregates`) and the two option reports run in-process in
every golden project after its `tjs run`, with the views and with a raw
reader in their place, and the two give the same; the raw side must have
read rows. aggregates_from_gains holds the rows to their types before a
view reads them (TestTheFunnel). TestTheKeys pins which key each figure
reads, which the comparison cannot see (both sides read the same key):
the 13 survivors of a 41-mutant wrong-key run, all killed."""
import argparse
import contextlib
import io
import json
import os
import sys
import unittest
from unittest import mock

import _golden
from _support.view_lock import RawRow
from taxjson.bin import _option_gains_report as OR
from taxjson.bin import taxjson_ccd_gains as CCD
from taxjson.bin import taxjson_explain as EXP
from taxjson.lib import filed_locks as FI
from taxjson.lib import once
from taxjson.bin import taxjson_leaps_gains as LEAPS
from taxjson.cli.main import main as tjs_main
from taxjson.lib import compare as CMP
from taxjson.lib import trace_format as TF
from taxjson.lib.json_input import InputFileError, read_work_doc
from taxjson.lib.report_model import resolve_gains_files
from taxjson.model.result import GainRow, money_keys
from taxjson.pipeline.config import _country, load_config
from taxjson.project.accounts import account_of, accounts_of
from taxjson.project.settings import settings_of

BOOKS = ("demo", "style-canada", "style-usa", "options", "crypto")
VIEWS = (TF, EXP, FI, OR)
# The explain tool's modes: the trace (plain, and in colour at a
# terminal: print_trace colours a block by its figures), `--list`, the
# report layout, and each over the wash sales alone.
MODES = ([], ["--color"], ["--list"], ["--layout", "report"], ["--wash-sales"],
         ["--wash-sales", "--layout", "report"])


class _Counted(RawRow):
    """RawRow, counting the rows it was made over."""
    __slots__ = ()
    made = 0

    def __init__(self, fields):
        _Counted.made += 1
        super().__init__(fields)


class _Tty(io.StringIO):
    def isatty(self):
        return True


def _old_raw_gain(row):
    """The traces' raw-gain read before the views (`g.get('raw_gain',
    gain)`), so the comparison does not run gain_before_denial on both
    sides (TJ-3036)."""
    return row.get("raw_gain", row.get("gain"))


@contextlib.contextmanager
def _raw():
    """The raw reader in each converted module's place."""
    with contextlib.ExitStack() as stack:
        for mod in VIEWS:
            stack.enter_context(mock.patch.object(mod, "GainRow", _Counted))
        stack.enter_context(mock.patch.object(TF, "gain_before_denial", _old_raw_gain))
        yield


def _both(run):
    """run() with the views, then with the raw reader: (new, old, rows
    the raw side read)."""
    new = run()
    _Counted.made = 0
    with _raw():
        old = run()
    return new, old, _Counted.made


def _call(main, argv, tty=False):
    # Each call as a process of its own (lib/once): a tool's once-only
    # note (a contract size ASSUMED) is said by both sides, not the first.
    out, err = (_Tty() if tty else io.StringIO()), io.StringIO()
    code = 0
    with mock.patch.object(sys, "argv", argv), once.own_process(), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            main()
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


def _bases(root):
    return [p for p in sorted((root / "work").glob("*_base.json"))
            if not p.name.startswith(".") and not p.name.endswith("_raw_base.json")]


class TestTheReaders(unittest.TestCase):
    """Each golden project run once; every reader compared in it."""

    @classmethod
    def setUpClass(cls):
        cls._stack = contextlib.ExitStack()
        cls.projects = {}
        try:
            for book in BOOKS:
                m = _golden.meta(book)
                root, env, _r, _b = cls._stack.enter_context(_golden.project_run(book, m))
                cls.projects[book] = (root / m.get("run_dir", "."), env)
        except BaseException:
            cls._stack.close()
            raise

    @classmethod
    def tearDownClass(cls):
        cls._stack.close()

    def _each(self):
        for book, (root, env) in self.projects.items():
            with mock.patch.dict(os.environ, env, clear=True):
                yield book, root

    def test_wash_sales_explain(self):
        read = 0
        for book, root in self._each():
            with self.subTest(book=book):
                new, old, n = _both(lambda: _call(tjs_main, ["tjs", "-C", str(root), "wash-sales", "--explain"]))
                read += n
                self.assertEqual(new, old)
                self.assertEqual(new[0], 0, new[2])
        self.assertGreater(read, 0, "no denial was explained")

    def test_the_explain_tool(self):
        for book, root in self._each():
            os.environ.pop("NO_COLOR", None)
            country = _country(settings_of(load_config(root)))
            read = 0
            for base in _bases(root):
                for mode in MODES:
                    with self.subTest(book=book, base=base.name, mode=mode):
                        argv = ["taxjson-explain", "--country", country, *mode, str(base)]
                        new, old, n = _both(lambda: _call(EXP.main, argv, tty="--color" in mode))
                        read += n
                        self.assertEqual(new, old)
                        self.assertEqual(new[0], 0, new[2])
                        if mode == ["--color"] and new[1]:
                            self.assertIn("\x1b[", new[1])
            with self.subTest(book=book):
                self.assertGreater(read, 0, f"the explain tool read no rows in {book}")

    def test_the_filed_aggregates(self):
        for book, root in self._each():
            cfg = load_config(root)
            year = settings_of(cfg).year
            accounts = accounts_of(cfg)
            read = 0
            for acct, path in resolve_gains_files(root / "work", warn_stale=False).items():
                doc = read_work_doc(path)
                crypto = bool(account_of(accounts, acct).crypto)
                for kw in ({}, {"crypto": crypto, "year": int(year)}, {"rounded": False},
                           {"account": acct}, {"account": "zz-none"}):
                    with self.subTest(book=book, account=acct, kw=kw):
                        new, old, n = _both(lambda: FI.aggregates_from_gains(doc, **kw))
                        read += n
                        self.assertEqual(json.dumps(new, sort_keys=True), json.dumps(old, sort_keys=True))
            with self.subTest(book=book, reader="compare"):
                new, old, n = _both(lambda: CMP.aggregates(root, cfg, warn_stale=False))
                read += n
                self.assertTrue(new, f"compare read no book in {book}")
                self.assertEqual(json.dumps(new, sort_keys=True), json.dumps(old, sort_keys=True))
            with self.subTest(book=book):
                self.assertGreater(read, 0, f"the aggregates read no rows in {book}")

    def test_the_option_reports(self):
        read = 0
        for book, root in self._each():
            files = [str(p) for p in resolve_gains_files(root / "work", warn_stale=False).values()]
            for prog, main in (("taxjson-ccd-gains", CCD.main), ("taxjson-leaps-gains", LEAPS.main)):
                with self.subTest(book=book, prog=prog):
                    new, old, n = _both(lambda: _call(main, [prog, *files]))
                    read += n
                    self.assertEqual(new, old)
                    self.assertEqual(new[0], 0, new[2])
        self.assertGreater(read, 0, "no option row was reported")


class TestTheFunnel(unittest.TestCase):
    """aggregates_from_gains refuses a row whose figure is not a number in
    one line naming the field, before a view reads it: lib/compare reads
    the books with load_report_json, past the row checks."""

    def test_a_text_gain(self):
        doc = {"transactions": [
            {"symbol": "ZZQ", "date": "2024-03-01", "date_settle": "2024-03-04", "account": "margin",
             "qty": 5.0, "proceeds": 50.0, "cost": 40.0, "gain": 10.0, "term": "LONG_TERM"},
            {"symbol": "ZZR", "date": "2024-04-01", "date_settle": "2024-04-03", "account": "margin",
             "qty": 2.0, "proceeds": 20.0, "cost": 30.0, "gain": "abc", "term": "LONG_TERM"}]}
        _Counted.made = 0
        with _raw(), self.assertRaises(InputFileError) as cm:
            FI.aggregates_from_gains(doc)
        self.assertIn("gains document", str(cm.exception))
        self.assertIn("gain is 'abc', not a number", str(cm.exception))
        self.assertEqual(_Counted.made, 0)          # no view over any row
        doc["transactions"][1]["gain"] = -10.0
        agg = FI.aggregates_from_gains(doc)
        self.assertEqual(agg["dispositions"], 2)

    def test_every_view_figure_is_checked(self):
        """A GainRow money field the row checks do not know would reach
        a view unchecked."""
        from taxjson.lib.json_input import _NUM_FIELDS
        self.assertEqual([k for k in money_keys(GainRow) if k not in _NUM_FIELDS], [])
        self.assertIn("denied_contribution", money_keys(GainRow))

    def test_the_rows_it_reads(self):
        """The checks hold this account's rows the aggregates read, and
        the figures a view reads (here and in form_lines' export
        builders): another account's row, a manual row's figures and a
        field no view reads do not stop it (TJ-2992, TJ-3023, TJ-3032,
        TJ-3034); its own sale's damaged figure does, before a view
        reads it."""
        doc = {"transactions": [
            {"symbol": "ZZQ", "date": "2024-03-01", "date_settle": "2024-03-04", "account": "margin",
             "qty": 5.0, "proceeds": 50.0, "cost": 40.0, "gain": 10.0, "price": "abc", "net_amount": "abc"},
            {"symbol": "ZZR", "date": "2024-04-01", "date_settle": "2024-04-03", "account": "cash",
             "qty": 2.0, "proceeds": 20.0, "cost": 30.0, "gain": -10.0, "denied_contribution": "abc"},
            {"symbol": "ZZS", "date": "2024-05-01", "date_settle": "2024-05-03", "account": "margin",
             "qty": 1.0, "proceeds": "abc", "tainted": True},
            {"symbol": "ZZT", "date": "2024-06-01", "date_settle": "2024-06-03", "account": "margin",
             "action": "DIVIDEND", "dividend": 4.0}]}
        agg = FI.aggregates_from_gains(doc, account="margin")
        self.assertEqual((agg["realized"], agg["proceeds"], agg["dividend"], agg["tainted"]), (10.0, 50.0, 4.0, 1))
        _Counted.made = 0
        with _raw(), self.assertRaises(InputFileError) as cm:
            FI.aggregates_from_gains(doc, account="cash")
        self.assertIn("row 1 (ZZR): denied_contribution is 'abc'", str(cm.exception))
        self.assertEqual(_Counted.made, 0)
        doc["transactions"][1]["denied_contribution"] = 0.0
        doc["transactions"].append({"symbol": "ZZU", "date": "2024-07-01", "date_settle": "2024-07-03",
                                    "account": "cash", "qty": 2.0, "proceeds": 20.0, "cost": 30.0})
        self.assertEqual(FI.aggregates_from_gains(doc, account="margin")["realized"], 10.0)
        with self.assertRaises(InputFileError) as cm:
            FI.aggregates_from_gains(doc, account="cash")
        self.assertIn('row 4 (ZZU 2024-07-01): a disposition without gain', str(cm.exception))
        doc["transactions"][3]["dividend"] = "abc"
        with self.assertRaises(InputFileError) as cm:
            FI.aggregates_from_gains(doc, account="margin")
        self.assertIn("row 3 (ZZT): dividend is 'abc'", str(cm.exception))


def _sale(**kw):
    """A denied sale whose figures all differ: a gain beside its raw
    loss, a denial part permanent, proceeds beside cost."""
    row = {"symbol": "ZZQ", "date": "2024-03-01", "account": "margin", "id": "99900001",
           "qty": 3.0, "proceeds": 30.0, "cost": 70.0, "gain": -10.0, "raw_gain": -40.0,
           "disallowed_amount": 30.0, "permanently_disallowed": 12.0, "term": "LONG_TERM",
           "direction": "LONG", "trace": ["# t"]}
    row.update(kw)
    return {k: v for k, v in row.items() if v is not None}


class TestTheKeys(unittest.TestCase):
    """Which key each figure reads, on rows where the keys differ: the
    view-against-raw comparison cannot see it (both sides read the same
    key), and in the goldens the gain and the raw gain, or the denial and
    its permanent part, agree on most rows (a wrong-key mutant run's
    survivors)."""

    def test_the_trace_block(self):
        self.assertEqual(TF.render_gain_block(_sale())[1],
                         "# ZZQ   2024-03-01   qty=3.0000   gain=-$10.00   "
                         "(raw -$40.00, disallowed +$30.00)   [LONG_TERM]")
        # A row with no raw gain: the gain is the raw figure.
        self.assertIn("(raw -$10.00, disallowed +$30.00)", TF.render_gain_block(_sale(raw_gain=None))[1])
        self.assertIn("qty=3.0000   proceeds=$30.00   MANUAL REPORTING",
                      TF.render_gain_block(_sale(), manual=True)[1])

    def test_the_report_block(self):
        out = TF.render_report_block(_sale(), width_=120)
        self.assertIn("  gain:      -10.00 (raw -40.00, denied 30.00)", out)
        self.assertIn("  quantity:  3", out)
        out = TF.render_report_block(_sale(raw_gain=None), width_=120)
        self.assertIn("  gain:      -10.00 (raw -10.00, denied 30.00)", out)
        self.assertIn("  proceeds:  30.00", TF.render_report_block(_sale(), width_=120, manual=True))

    def test_the_list_line(self):
        line = EXP.fmt_summary(_sale())
        self.assertIn("qty=    3.0000  cost=     70.0000  proc=     30.0000  gain=    -10.0000  WASH+30.00", line)
        line = EXP.fmt_summary(_sale(tainted=True))
        self.assertIn("qty=    3.0000  proc=     30.0000  MANUAL REPORTING", line)

    def test_the_trace_colour(self):
        """The header is coloured by the denial, else by the gain's sign
        (a gain of 5.00 beside a raw loss is a gain)."""
        args = argparse.Namespace(no_align=False)
        for row, key in ((_sale(permanently_disallowed=0.0), "wash"),
                         (_sale(gain=5.0, raw_gain=-5.0, disallowed_amount=0.0, permanently_disallowed=0.0), "gain")):
            with self.subTest(key=key):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    EXP.print_trace(row, args, True)
                header = out.getvalue().split("\n")[2]
                self.assertTrue(header.startswith(EXP.COLORS[key] + "# ZZQ"), repr(header))

    def test_the_wash_explanation(self):
        """The raw loss a denial explains is the header's: with no
        raw_gain key, the gain (TJ-3025); a row with no wash makes no
        view (TJ-3030)."""
        trigger = {"is_full_disallowance": True, "trigger_date": "2024-03-05", "trigger_qty": 3.0,
                   "trigger_price": 9.0, "trigger_account": "margin"}
        out = TF._render_wash_explanation(_sale(wash_trigger=trigger))
        self.assertIn("#   raw loss -$40.00 -> fully disallowed (+$30.00)", out)
        out = TF._render_wash_explanation(_sale(wash_trigger=trigger, raw_gain=None))
        self.assertIn("#   raw loss -$10.00 -> fully disallowed (+$30.00)", out)
        _Counted.made = 0
        with _raw():
            self.assertEqual(TF._render_wash_explanation(_sale(permanently_disallowed="abc")), [])
        self.assertEqual(_Counted.made, 0)

    def test_a_row_with_no_direction(self):
        """A legacy row's direction from its legs: a short's gain is its
        cost less its proceeds; a long's, its proceeds less its cost."""
        short = {"cost": 100.0, "proceeds": 40.0, "gain": 60.0}
        long_ = {"cost": 40.0, "proceeds": 100.0, "gain": 60.0}
        self.assertEqual(OR._direction(GainRow(short)), "SHORT")
        self.assertEqual(OR._direction(GainRow(long_)), "LONG")

if __name__ == "__main__":
    unittest.main()
