"""Step 6c(6b1): the cli's gains views read rows through model/result views.

`tjs sum` and `tjs estimate` (cli/estimate `cmd_summary`: the RETURN
row's engine totals), `tjs leaps-sum`, `tjs ccd-sum`, `tjs winners` and
`tjs losers` (cli/totals), `tjs leaps` and `tjs gains` (cli/listings),
`tjs wash-sales` (cli/explain `cmd_wash_sales`) and common's
`_real_world_legs` read a gains row's units and money through
`GainRow(e)` / `Holding(h)` views over the rows' own dicts. Pinned as
6c(3) pinned the export group (test_model_result_export): each command
runs in-process, text and `--json`, in every golden project after its
`tjs run`, with the views and with a raw reader in their place, and the
two print the same. The raw side must have read rows (a command that
read none would compare nothing). The files go through the row checks
first (`_load_json_or_die`, `_read_work_doc`, form_export
`load_dispositions`), so a view never sees an integer too large for a
float (TestTheFunnel). Which key a site reads is held by the golden
views (sum, estimate, leaps-sum, ccd-sum, wash-sales), TestTheKeys and
the commands' own tests: a wrong-key mutant of each read is killed."""
import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

import _golden
from _support.view_lock import RawRow
from taxjson.cli import estimate as ES
from taxjson.cli import explain as EX
from taxjson.cli import listings as LI
from taxjson.cli import totals as TO
from taxjson.cli.main import main as tjs_main

BOOKS = ("demo", "style-canada", "style-usa", "options", "crypto")
COMMANDS = ("sum", "estimate", "leaps-sum", "ccd-sum", "winners", "losers", "leaps", "gains",
            "wash-sales")
# cli/common makes no view: its `_real_world_legs` reads its callers' (TJ-2985).
VIEWS = [(m, "GainRow") for m in (ES, EX, LI, TO)] + [(EX, "Holding")]
# The Canadian books whose taxjson.toml names no province: the estimate's.
EXTRA = {("options", "estimate"): ["--province", "ON"], ("crypto", "estimate"): ["--province", "ON"]}


class _Counted(RawRow):
    """RawRow, counting the rows it was made over."""
    __slots__ = ()
    made = 0

    def __init__(self, fields):
        _Counted.made += 1
        super().__init__(fields)


def _tjs(root, argv):
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with mock.patch.object(sys, "argv", ["tjs", "-C", str(root), *argv]), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            tjs_main()
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


class TestTheCommands(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._stack = contextlib.ExitStack()
        cls.roots = {}
        for book in BOOKS:
            m = _golden.meta(book)
            root, env, _r, _b = cls._stack.enter_context(_golden.project_run(book, m))
            cls.roots[book] = (root / m.get("run_dir", "."), env)

    @classmethod
    def tearDownClass(cls):
        cls._stack.close()

    def test_each_command_with_and_without_the_views(self):
        read = {c: 0 for c in COMMANDS}
        for book, (root, env) in self.roots.items():
            with mock.patch.dict(os.environ, env, clear=True):
                for cmd in COMMANDS:
                    for argv in ([cmd], [cmd, "--json"]):
                        argv = argv + EXTRA.get((book, cmd), [])
                        with self.subTest(book=book, argv=argv):
                            new = _tjs(root, argv)
                            _Counted.made = 0
                            with contextlib.ExitStack() as st:
                                for mod, name in VIEWS:
                                    st.enter_context(mock.patch.object(mod, name, _Counted))
                                old = _tjs(root, argv)
                            read[cmd] += _Counted.made
                            self.assertEqual(new, old)
        for cmd, n in read.items():
            with self.subTest(cmd=cmd):
                self.assertGreater(n, 0, f"{cmd} read no rows in any project")


class TestTheFunnel(unittest.TestCase):
    """A sale's `denied_contribution` that is not a number is refused in
    one line naming the file, the row and the field, before a view or a
    float() reads it: `tjs sum` and `tjs estimate` stopped with a
    ValueError traceback, form-export named no file (TJ-2967); and each
    says to re-run once (TJ-2968, TJ-2976, TJ-2977, TJ-2979)."""

    def test_a_text_denied_contribution(self):
        from taxjson.bin import taxjson_form_export as FE
        m = _golden.meta("style-canada")
        with _golden.project_run("style-canada", m) as (root, env, _r, _b):
            work = root / "work"
            p = next(q for q in sorted(work.glob("*_gains_wash.json")) if not q.name.startswith(".")
                     and any("gain" in t and not t.get("action")
                             for t in json.loads(q.read_text(encoding="utf-8"))["transactions"]))
            doc = json.loads(p.read_text(encoding="utf-8"))
            sale = next(t for t in doc["transactions"] if "gain" in t and not t.get("action"))
            sale["denied_contribution"] = "abc"
            p.write_text(json.dumps(doc), encoding="utf-8")
            want = "denied_contribution is 'abc', not a number"
            with mock.patch.dict(os.environ, env, clear=True):
                for argv in (["sum"], ["estimate", "--province", "ON"], ["wash-sales"], ["winners"]):
                    with self.subTest(argv=argv):
                        code, _out, err = _tjs(root, argv)
                        self.assertEqual(code, 2, err)
                        self.assertIn(f"could not read work/{p.name}", err)
                        self.assertIn(want, err)
                        self.assertEqual(err.count("`tjs run`"), 1, err)
                err = io.StringIO()
                with mock.patch.object(sys, "argv", ["taxjson-form-export", str(p), "--form", "schedule3",
                                                     "--country", "canada"]), \
                        contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(FE.main(), 2)
            self.assertIn(want, err.getvalue())
            self.assertEqual(err.getvalue().count("`tjs run`"), 1, err.getvalue())


class TestTheAdvice(unittest.TestCase):
    """json_input `rerun_advice`: the advice to re-run added unless the
    message gives it already, as the row checks' messages do; a message
    naming `tjs run` for another reason still gets it (TJ-2970, TJ-2980).
    corp_views, renames and harvest wrap the row checks with it (TJ-2978)."""

    def test_the_rule(self):
        from taxjson.lib.json_input import rerun_advice
        given = "x.json: damaged or hand-edited: fix it or re-run `tjs run`"
        self.assertEqual(rerun_advice(given), given)
        self.assertEqual(rerun_advice("x.json: Re-run `tjs run` first", " — again"), "x.json: Re-run `tjs run` first")
        self.assertEqual(rerun_advice("written by `tjs run --year 2023`", " — re-run `tjs run`"),
                         "written by `tjs run --year 2023` — re-run `tjs run`")

    def test_the_readers(self):
        from taxjson.bin import taxjson_harvest as HV
        from taxjson.lib import corp_views, renames
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "margin_gains.json"
            p.write_text(json.dumps({"transactions": [{"symbol": "ZZQ", "qty": "n/a"}]}), encoding="utf-8")
            for read, exc in ((corp_views._rows, corp_views.ViewError), (renames._load_rows, ValueError)):
                with self.subTest(read=read.__module__):
                    with self.assertRaises(exc) as cm:
                        read(p)
                    self.assertIn("qty is 'n/a'", str(cm.exception))
                    self.assertEqual(str(cm.exception).count("`tjs run`"), 1, cm.exception)
            p.write_text("{", encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
                HV._read_input(p, "the gains file")
            self.assertEqual(err.getvalue().count("`tjs run`"), 1, err.getvalue())


class TestTheKeys(unittest.TestCase):
    """The keys the views read where the lock cannot tell (it swaps the
    reader, not the key) and the golden books did not: `tjs leaps`' text
    columns and its taxable/sheltered split, and `tjs gains`' QTY."""

    def _leaps(self, root, as_json):
        from taxjson.cli.listings import cmd_leaps
        args = argparse.Namespace(dir=str(root), period="all", account=None, json=as_json)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            cmd_leaps(args)
        return out.getvalue()

    def test_leaps(self):
        from _support.view_leaps_project import LEAP, LEAP_GAIN, _project
        sale = dict(LEAP_GAIN, qty=-3, proceeds=2565.0, cost=1620.0, gain=900.0, raw_gain=945.0,
                    disallowed_amount=45.0)
        del sale["account"]          # the JSON row's account is the file's (TJ-2986)
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, gains=[sale])
            text = self._leaps(root, False)
            doc = json.loads(self._leaps(root, True))
        line = next(ln for ln in text.splitlines() if LEAP in ln)
        self.assertEqual(line.split()[1:6], [LEAP, "3", "2,565.00", "1,620.00", "900.00"], line)
        self.assertIn("900.00", text.split(line, 1)[1])          # the total
        self.assertEqual((doc["total_gain"], doc["taxable_gain"], doc["sheltered_gain"]),
                         (900.0, 900.0, 0.0))
        self.assertEqual([(r["account"], r["symbol"]) for r in doc["rows"]], [("margin", LEAP)])

    def test_the_gains_quantity(self):
        recent = (date.today() - timedelta(days=5)).isoformat()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "work").mkdir()
            (root / "taxjson.toml").write_text('[settings]\ncountry = "canada"\n', encoding="utf-8")
            (root / "work" / "margin_raw_gains.json").write_text(json.dumps({"transactions": [
                {"date": recent, "symbol": "ZZQ.TO", "qty": 7, "currency": "CAD",
                 "proceeds": 150.0, "cost": 100.0, "gain": 50.0, "raw_gain": 60.0,
                 "days_held": 30}]}), encoding="utf-8")
            code, out, err = _tjs(root, ["gains", "30d", "margin"])
        self.assertEqual(code, 0, err)
        self.assertIn(f"{recent} ZZQ.TO 7 CAD 150.00 100.00 50.00 30", " ".join(out.split()))


if __name__ == "__main__":
    unittest.main()
