"""Step 6c(6b2): the summaries, positions and plan views read rows through model/result views.

cli/summaries (`tjs div-estimate`'s and `tjs option-yield`'s holdings,
`_issuer_split`'s dividends and `_section_1256_of_docs`' gains, which
`tjs estimate` and `tjs plot-tax` reach), cli/positions
(`tjs shares`, `tjs list` and its dated holdings), cli/plan
(`_last_loss_by_class`, behind `tjs buy-check`, `tjs sell-check` and
`tjs rebuy`), cli/common `_capitalized_pil_shares` (`tjs dil-sum`,
`tjs divs`, `tjs dil`) and income_summary `capitalized_pil_shares`
read a row's units and money through `GainRow(e)` / `Holding(h)` views
over the rows' own dicts. Pinned as test_model_result_cli pins 6c(6b1):
each command runs in-process, text and `--json`, in every golden project
after its `tjs run`, with the views and with a raw reader in their
place, and the two print the same; the raw side must have read rows
where the goldens hold them. The files go through the row checks first
(`_load_json_or_die`, `_read_work_doc`; the share's payments in lieu
through `check_row_types` in income_summary `capitalized_pil_shares`); the
views' capitalized shares read with json.loads before, sum-income's rows
unchecked (backlog TJ-2941: TestTheFunnel). TestTheShares pins the shares' keys, which no golden
holds (no §263(h) payment in lieu); TestTheKeys a key no test held."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _golden
from _support.view_lock import RawRow
from _support.view_patch import raw_views
from taxjson.bin import taxjson_sum_income as SI
from taxjson.lib import income_summary
from taxjson.cli import common as CM
from taxjson.cli import listings as LI
from taxjson.cli import plan as PL
from taxjson.cli import positions as PO
from taxjson.cli import summaries as SU
from taxjson.cli import totals as TO
from taxjson.cli.main import main as tjs_main

BOOKS = ("demo", "style-canada", "style-usa", "options", "crypto")
# "@mid": the middle of the project's year (`tjs list`'s dated holdings).
COMMANDS = (("estimate",), ("plot-tax",), ("div-estimate",), ("option-yield",),
            ("shares",), ("list",), ("list", "@mid"), ("dil-sum",), ("divs",), ("dil",),
            ("rebuy",), ("buy-check", "ZZQ"), ("sell-check", "ZZQ"))
VIEWS = [(SU, "GainRow"), (SU, "Holding"), (PO, "Holding"), (PL, "GainRow"), (CM, "GainRow")]
# The Canadian books whose taxjson.toml names no province.
EXTRA = {("options", "estimate"): ["--province", "ON"], ("crypto", "estimate"): ["--province", "ON"],
         ("options", "plot-tax"): ["--province", "ON"], ("crypto", "plot-tax"): ["--province", "ON"]}
# No golden holds a row these read: TestTheShares and the commands' own tests pin them.
UNREAD = {"dil-sum", "divs", "dil"}


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


def _year(root):
    for p in sorted((root / "work").glob("*_gains.json")):
        y = (json.loads(p.read_text(encoding="utf-8")).get("summary") or {}).get("year")
        if y:
            return int(y)
    raise AssertionError(f"no gains file with a year under {root}")


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
        read = {c[0]: 0 for c in COMMANDS}
        for book, (root, env) in self.roots.items():
            mid = f"{_year(root)}-06-30"
            with mock.patch.dict(os.environ, env, clear=True):
                for cmd in COMMANDS:
                    base = [mid if a == "@mid" else a for a in cmd] + EXTRA.get((book, cmd[0]), [])
                    for argv in (base, base + ["--json"]):
                        with self.subTest(book=book, argv=argv):
                            new = _tjs(root, argv)
                            _Counted.made = 0
                            with contextlib.ExitStack() as st:
                                for mod, name in VIEWS:
                                    st.enter_context(mock.patch.object(mod, name, _Counted))
                                old = _tjs(root, argv)
                            read[cmd[0]] += _Counted.made
                            self.assertEqual(new, old)
        for cmd, n in read.items():
            if cmd not in UNREAD:
                with self.subTest(cmd=cmd):
                    self.assertGreater(n, 0, f"{cmd} read no rows in any project")


class TestTheCountry(unittest.TestCase):
    """dil-sum, divs and dil pass the share their income rules' country
    (TJ-3001): the US golden's views read the shares, the Canadian's
    do not."""

    def test_the_callers(self):
        for book, usa in (("style-usa", True), ("style-canada", False)):
            m = _golden.meta(book)
            with _golden.project_run(book, m) as (root, env, _r, _b), \
                    mock.patch.dict(os.environ, env, clear=True):
                for cmd in ("dil-sum", "divs", "dil"):
                    with self.subTest(book=book, cmd=cmd), \
                            mock.patch.object(CM, "_capitalized_pil_shares", return_value={}) as spy:
                        for mod in (LI, TO):
                            mock.patch.object(mod, "_capitalized_pil_shares", spy).start()
                        try:
                            _tjs(root / m.get("run_dir", "."), [cmd])
                        finally:
                            mock.patch.stopall()
                        self.assertEqual([c.args[2] for c in spy.call_args_list], [usa])


def _dil(**kw):
    return dict({"action": "DIVIDEND_IN_LIEU", "id": "99900001", "symbol": "ZZQ", "date": "2025-03-14",
                 "currency": "USD", "capitalized_263h": 30.0, "pil": -40.0}, **kw)


def _project(tmp, rows, country="usa", accounts=("margin",)):
    """A project whose accounts' gains files hold `rows` (work/<acct>_gains.json)."""
    root = Path(tmp)
    (root / "taxjson.toml").write_text(f'[settings]\ncountry = "{country}"\n', encoding="utf-8")
    (root / "work").mkdir()
    for a in accounts:
        (root / "work" / f"{a}_gains.json").write_text(json.dumps(
            {"transactions": rows, "inventory": [], "summary": {"year": 2025}}), encoding="utf-8")
    return root


class TestTheShares(unittest.TestCase):
    """The share of a payment in lieu in a cover's cost: capitalized /
    (capitalized - pil), at most 1; none without a capitalized part or
    for another action. A swapped key reads another share. The views
    call sum-income's function (TJ-2993)."""

    ROWS = [_dil(), _dil(id="99900002", capitalized_263h=50.0, pil=-10.0),
            _dil(id="99900003", capitalized_263h=0.0), _dil(id="99900004", action="DIVIDEND")]
    WANT = {"99900001": 30.0 / 70.0, "99900002": 50.0 / 60.0}

    def test_sum_income(self):
        self.assertEqual(income_summary.capitalized_pil_shares(self.ROWS), self.WANT)
        made = _Counted.made
        with raw_views((income_summary,), ["GainRow"], _Counted):
            self.assertEqual(income_summary.capitalized_pil_shares(self.ROWS), self.WANT)
        self.assertGreater(_Counted.made, made)

    def test_the_views(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, self.ROWS)
            self.assertEqual(CM._capitalized_pil_shares(root, {"margin"}, True),
                             {("margin", k): v for k, v in self.WANT.items()})
            self.assertEqual(CM._capitalized_pil_shares(root, set(), True), {})


class TestTheKeys(unittest.TestCase):
    """plan `_last_loss_by_class` reads a sale's economic raw_gain, so a
    loss the superficial-loss rule denied in full (gain 0) still shows;
    a sale without one reads its gain. No test held the first: a mutant
    reading gain for both survived the suite."""

    def test_the_last_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "margin_gains.json"
            p.write_text(json.dumps({"transactions": [
                {"symbol": "ZZQ", "date": "2025-03-03", "date_settle": "2025-03-04", "qty": -10,
                 "proceeds": 900.0, "cost": 1000.0, "gain": 0.0, "raw_gain": -100.0,
                 "disallowed_amount": 100.0},
                {"symbol": "ZZR", "date": "2025-02-03", "date_settle": "2025-02-04", "qty": -5,
                 "proceeds": 400.0, "cost": 450.0, "gain": -50.0}],
                "inventory": [], "summary": {"year": 2025}}), encoding="utf-8")
            got = PL._last_loss_by_class({"margin": p}, str.upper, {"margin"}, usa=False)
        self.assertEqual({k: (v["date"], v["gain"]) for k, v in got.items()},
                         {"ZZQ": ("2025-03-04", -100.0), "ZZR": ("2025-02-04", -50.0)})


class TestTheFunnel(unittest.TestCase):
    """A text capitalized_263h: the views read the gains file with
    json.loads and met it with float() naming no file; sum_income's
    `--gains` too, a traceback (TJ-2941). The payments in lieu go through
    the row checks now, naming the file, the row and the field: the views
    stop (exit 2), as `_load_json_or_die` does, since the share would be
    left in their figures (TJ-2988); sum-income stops with exit 2;
    lib/report_model's call is refused naming the account (TJ-2991).
    Only those rows' two fields are checked: a damaged sale, or another
    field of a payment in lieu, or a Canadian one (no capitalized part),
    fails neither (TJ-2992, TJ-2998, TJ-2999). A file that is not a gains
    file's shape stops the views too (TJ-2997). The views read the US
    projects' files of their own accounts alone (TJ-2989, TJ-2990), the
    country their income rules' (TJ-3001, TJ-3004)."""

    BAD = [_dil(capitalized_263h="abc")]

    def test_the_views(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp, self.BAD)
            err = io.StringIO()
            with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
                CM._capitalized_pil_shares(root, {"margin"}, True)
        msg = err.getvalue()
        self.assertEqual(cm.exception.code, 2, msg)
        self.assertIn("could not read work/margin_gains.json", msg)
        self.assertIn("capitalized_263h is 'abc', not a number", msg)
        self.assertIn("would show its payments in lieu whole", msg)
        self.assertEqual(msg.count("`tjs run`"), 1, msg)

    def test_a_file_of_no_gains_shape(self):
        for doc in ({}, {"transcations": [_dil()]}):
            with self.subTest(doc=doc), tempfile.TemporaryDirectory() as tmp:
                root = _project(tmp, [])
                (root / "work" / "margin_gains.json").write_text(json.dumps(doc), encoding="utf-8")
                err = io.StringIO()
                with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
                    CM._capitalized_pil_shares(root, {"margin"}, True)
                self.assertEqual(cm.exception.code, 2)
                self.assertIn("damaged", err.getvalue())

    def test_what_the_views_read(self):
        sale = {"symbol": "ZZQ", "date": "2025-03-03", "qty": -1, "proceeds": 9.0, "cost": 10.0,
                "gain": -1.0, "denied_contribution": "abc"}
        other = _dil(id="99900002", dividend="abc", qty="x", date=20250314)
        canadian = {"action": "DIVIDEND_IN_LIEU", "id": "99900003", "symbol": "ZZR.TO", "pil": "abc"}
        for usa, accounts, rows, want in (
                (True, ("margin", "ira"), self.BAD, {}),            # ira's file only, not margin's
                (False, ("ira",), self.BAD, {}),                    # no §263(h) in Canada
                (True, ("ira",), [sale, other, canadian], {("ira", "99900002"): 30.0 / 70.0})):
            with self.subTest(usa=usa, rows=len(rows)), tempfile.TemporaryDirectory() as tmp:
                root = _project(tmp, rows, "usa" if usa else "canada", accounts)
                if len(accounts) == 2:
                    (root / "work" / "ira_gains.json").write_text(json.dumps({"transactions": []}),
                                                                  encoding="utf-8")
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    self.assertEqual(CM._capitalized_pil_shares(root, {"ira"}, usa), want)
                self.assertEqual(err.getvalue(), "")

    def test_sum_income(self):
        sale = {"symbol": "ZZQ", "date": "2025-03-03", "qty": -1, "gain": -1.0, "denied_contribution": "abc"}
        canadian = {"action": "DIVIDEND_IN_LIEU", "id": "99900003", "symbol": "ZZR.TO", "pil": "abc"}
        for rows, code in ((self.BAD, 2), ([sale, _dil(dividend="abc"), canadian], 0)):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as tmp:
                root = _project(tmp, rows)
                p = root / "work" / "margin_gains.json"
                book = root / "work" / "margin_base.json"
                book.write_text(json.dumps({"transactions": []}), encoding="utf-8")
                err = io.StringIO()
                with mock.patch.object(sys, "argv", ["taxjson-sum-income", "--gains", str(p), str(book)]), \
                        contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                    try:
                        SI.main()
                        got = 0
                    except SystemExit as e:
                        got = e.code or 0
                self.assertEqual(got, code, err.getvalue())
                if code:
                    self.assertIn(p.name, err.getvalue())
                    self.assertIn("capitalized_263h is 'abc', not a number", err.getvalue())

    def test_the_report(self):
        # The report reads the gains file the run has just written; its
        # summarize_gains reads `pil` first (through the Result's views).
        # The share's check holds what only it reads: a capitalized_263h
        # too large for a float read as inf, and the share as 1.0.
        from taxjson.lib.json_input import InputFileError
        from taxjson.lib.account_report import build_account_report
        with self.assertRaises(InputFileError) as cm:
            build_account_report({"transactions": [_dil(capitalized_263h=10 ** 400)],
                                  "summary": {"year": 2025}}, "margin", basis="pre-wash")
        self.assertIn("margin's gains: \"transactions\" row 0 (ZZQ): capitalized_263h is an integer",
                      str(cm.exception))
        with self.assertRaises(InputFileError) as cm:
            income_summary.capitalized_pil_shares([{"symbol": "ZZR", "gain": "x"}, _dil(pil="abc")], "the rows")
        self.assertIn('the rows: "transactions" row 1 (ZZQ): pil is \'abc\'', str(cm.exception))


if __name__ == "__main__":
    unittest.main()
