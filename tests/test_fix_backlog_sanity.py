"""Backlog group `sanity`: `tjs sanity`, the holdings check at the end
of `tjs run` and the checklist's sanity step (TJ-0010, TJ-0020,
TJ-0064, TJ-0065, TJ-0066, TJ-0082 = TJ-0094, TJ-0135, TJ-0136,
TJ-0160). The projects are built from synthetic work/ files (no
pipeline run): made-up tickers, fake account ids."""
import contextlib
import io
import json
import os
import re
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path

from _style import CapturedWidth

from taxjson.lib.dispatch import python_module_argv

_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _tjs(root: Path, *argv: str, env=None):
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run")
        + ["-C", str(root), *argv],
        check=False, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, env=dict(os.environ, **(env or {})))


def _toml_value(v) -> str:
    return json.dumps(v)


def _project(root: Path, accounts, snapshots=()) -> Path:
    """A project with books in work/ and holdings/ snapshots.

    accounts: {name: {"type", "account", "broker_accounts", "holdings",
    "rows": [(day, symbol, quantity)]}}; snapshots: [(file name, {meta
    key: value}, [(symbol, quantity, extra TOML lines)])]."""
    cfg = ['[settings]', 'year = 2024', 'country = "canada"',
           'base_currency = "CAD"', 'source_currencies = []']
    (root / "work").mkdir(parents=True)
    for name, a in accounts.items():
        cfg.append(f"[accounts.{name}]")
        cfg.append(f'type = "{a.get("type", "taxable")}"')
        for key in ("account", "broker_accounts", "holdings"):
            if a.get(key) is not None:
                cfg.append(f"{key} = {_toml_value(a[key])}")
        rows, inv = [], {}
        for day, sym, q in a.get("rows", ()):
            rows.append({"action": "BUYSELL", "date": day,
                         "date_settle": day, "time": "10:00:00",
                         "symbol": sym, "quantity": q, "account": name,
                         "currency": "CAD"})
            inv[sym] = inv.get(sym, 0) + q
        (root / "work" / f"{name}_base.json").write_text(json.dumps(
            {"metadata": {}, "transactions": rows}))
        (root / "work" / f"{name}_gains.json").write_text(json.dumps({
            "inventory": [{"symbol": s, "qty": q, "total_cost": 10.0 * q,
                           "currency": "CAD"}
                          for s, q in inv.items() if q],
            "summary": {"year": "2024"}}))
    (root / "taxjson.toml").write_text("\n".join(cfg) + "\n")
    if snapshots:
        (root / "holdings").mkdir(exist_ok=True)
    for fname, meta, hrows in snapshots:
        text = "[meta]\n" + "".join(f"{k} = {_toml_value(v)}\n"
                                    for k, v in meta.items())
        for sym, q, extra in hrows:
            text += (f'\n[[holding]]\nsymbol = "{sym}"\nquantity = {q}\n'
                     f'currency = "CAD"\n{extra}')
        (root / "holdings" / fname).write_text(text)
    return root


def _ctx(root: Path):
    from taxjson.bin.taxjson_run import load_config
    from taxjson.lib import checklist as cl
    return cl.Ctx(root=root, cfg=load_config(root), year=2024,
                  today=date(2025, 3, 1), run_sub=cl.default_run_sub(root))


class TestBaseBookShape(unittest.TestCase):

    def test_tj0010_empty_object_or_bare_list_is_not_an_empty_history(self):
        """TJ-0010: a work/<account>_base.json of `{}` (or `[]`, or a
        books document with no transactions) was read as an empty
        history, so a wrong dated snapshot was compared with the latest
        books and certified OK. Refused like the other unreadable
        shapes."""
        from taxjson.lib import positions_check as PC
        with tempfile.TemporaryDirectory() as td:
            # ZZSA.TO held on 06-30, sold, then ZZSH.TO bought; the
            # snapshot of 06-30 wrongly says ZZSH.TO.
            root = _project(Path(td) / "p", {"margin": {
                "account": "99900001",
                "rows": [("2024-03-01", "ZZSA.TO", 10),
                         ("2024-08-01", "ZZSA.TO", -10),
                         ("2024-09-02", "ZZSH.TO", 30)]}},
                [("margin_2024-06-30.toml",
                  {"broker_account": "99900001", "as_of": "2024-06-30"},
                  [("ZZSH.TO", 30, "")])])
            r = _tjs(root, "sanity")
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("2024-06-30", r.stdout)
            self.assertIn("ZZSA.TO", r.stdout)
            base = root / "work" / "margin_base.json"
            for text in ("{}", "[]", '{"metadata": {}}', '[{"x": 1}]'):
                with self.subTest(base=text):
                    base.write_text(text)
                    for argv in (("sanity",), ("sanity", "--json")):
                        r = _tjs(root, *argv)
                        # (exit 2, an input that cannot be read:
                        # backlog TJ-0353)
                        self.assertEqual(r.returncode, 2,
                                         r.stdout + r.stderr)
                        self.assertNotIn("OK", r.stdout)
                        self.assertNotIn('"clean"', r.stdout)
                        self.assertIn("the books of 'margin' cannot be "
                                      "read", r.stderr)
                        self.assertIn("not a books document", r.stderr)
                    with self.assertRaises(PC.BaseBookError):
                        PC.load_base_rows(root / "work", "margin",
                                          strict=True)
                    # The lenient read stays what it was.
                    PC.load_base_rows(root / "work", "margin")
            base.write_text('{"transactions": []}')
            self.assertEqual(PC.load_base_rows(root / "work", "margin",
                                               strict=True), [])


def _san1(td: str, margin_rows=(("2024-02-01", "ZZA.TO", 10),)) -> Path:
    """TJ-0020's project: margin (99900001, no holdings setting),
    margin2 (99900003, holdings = ["m2_holdings.toml"], present and
    matching), holdings/margin_holdings.toml naming 99900003."""
    root = _project(Path(td) / "p", {
        "margin": {"account": "99900001", "rows": list(margin_rows)},
        "margin2": {"account": "99900003",
                    "holdings": ["m2_holdings.toml"],
                    "rows": [("2024-02-01", "ZZC.TO", 5)]}},
        [("margin_holdings.toml",
          {"broker_account": "99900003", "as_of": "2024-12-31"},
          [("ZZC.TO", 5, "")])])
    (root / "m2_holdings.toml").write_text(
        '[meta]\nbroker_account = "99900003"\nas_of = "2024-12-31"\n'
        '[[holding]]\nsymbol = "ZZC.TO"\nquantity = 5\ncurrency = "CAD"\n')
    return root


class TestIncompleteLine(unittest.TestCase):

    def test_tj0020_snapshot_of_an_account_with_its_own_setting(self):
        """TJ-0020: the only note is the A13 one (a holdings/ snapshot of
        margin2, whose own `holdings` setting names its files): no
        INCOMPLETE line telling to fix the paths. margin, which holds
        positions and has no file, is the UNCHECKED one."""
        with tempfile.TemporaryDirectory() as td:
            root = _san1(td)
            r = _tjs(root, "sanity", "--details")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("is margin2's, whose own `holdings = [...]`",
                          r.stderr)
            self.assertNotIn("INCOMPLETE", r.stdout)
            self.assertIn("UNCHECKED: 1 account(s) with positions and no "
                          "holdings file: margin", r.stdout)
            doc = json.loads(_tjs(root, "sanity", "--json").stdout)
            self.assertFalse(doc["complete"])          # margin unchecked
            self.assertEqual(doc["uncovered_accounts"], ["margin"])
            self.assertEqual(doc["accounts_not_checked"], [])
            self.assertTrue(doc["notes"])
            from taxjson.lib import checklist as cl
            res = cl.d_sanity(_ctx(root))
            self.assertEqual(res.status, "attention")
            self.assertIn("UNCHECKED", res.detail)
        with tempfile.TemporaryDirectory() as td:
            # margin holds nothing: every account with positions is
            # checked, and the note alone leaves nothing unchecked.
            root = _san1(td, margin_rows=())
            r = _tjs(root, "sanity")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn("INCOMPLETE", r.stdout)
            self.assertIn("in every group.", r.stdout)
            self.assertTrue(json.loads(
                _tjs(root, "sanity", "--json").stdout)["complete"])
            # The run's check says it as a note, not "incomplete".
            from taxjson.bin import taxjson_run as tr
            kinds = {}
            notes = tr._sanity_items_from_config(
                tr.load_config(root)["accounts"], root, 2024,
                kinds=kinds)[1]
            self.assertEqual(kinds["info"], notes)
            self.assertEqual(kinds["accounts"], [])

    def test_tj0020_unclaimed_snapshot_is_still_incomplete(self):
        """A holdings/ snapshot no account claims was not compared: still
        INCOMPLETE, said as a snapshot, not as a path in taxjson.toml."""
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", {
                "margin": {"account": "99900001",
                           "rows": [("2024-02-01", "ZZA.TO", 10)]}},
                [("margin_holdings.toml", {"broker_account": "99900001",
                                           "as_of": "2024-12-31"},
                  [("ZZA.TO", 10, "")]),
                 ("other.toml", {"as_of": "2024-12-31"},
                  [("ZZB.TO", 3, "")])])
            r = _tjs(root, "sanity")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("no account claims it", r.stderr)
            self.assertIn("INCOMPLETE: 1 snapshot(s) in holdings/ not "
                          "compared", r.stdout)
            self.assertNotIn("taxjson.toml not checked", r.stdout)
            self.assertFalse(json.loads(
                _tjs(root, "sanity", "--json").stdout)["complete"])


class TestRunCheckMissingFile(unittest.TestCase):

    def test_tj0064_missing_holdings_file_is_not_every_checked_account(self):
        """TJ-0064: margin's `holdings = [...]` file is missing and margin
        holds nothing; margin2 is checked from holdings/. The run's check
        said "positions match ... (every checked account)": margin is
        named unchecked."""
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", {
                "margin": {"account": "99900001",
                           "holdings": ["snapshots/margin_missing.toml"]},
                "margin2": {"account": "99900003",
                            "rows": [("2024-02-01", "ZZC.TO", 20)]}},
                [("margin2_holdings.toml", {"broker_account": "99900003",
                                            "as_of": "2024-12-31"},
                  [("ZZC.TO", 20, "")])])
            from taxjson.bin import taxjson_run as tr
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), \
                    contextlib.redirect_stderr(io.StringIO()):
                tr._sanity_console(root)
            text = buf.getvalue()
            self.assertIn("positions match the broker's holdings files",
                          text)
            self.assertNotIn("every checked account", text)
            self.assertIn("checked accounts only; 1 unchecked: margin",
                          text)
            doc = json.loads(_tjs(root, "sanity", "--json").stdout)
            self.assertEqual(doc["accounts_not_checked"], ["margin"])
            self.assertFalse(doc["complete"])


class TestDatedSnapshots(unittest.TestCase):

    def _two_brokers(self, td: str, snapshots) -> Path:
        return _project(Path(td) / "p", {"margin": {
            "broker_accounts": ["99900001", "99900005"],
            "rows": [("2024-02-01", "ZZA.TO", 50),
                     ("2024-02-01", "ZZC.TO", 20),
                     ("2024-09-03", "ZZA.TO", 10)]}}, snapshots)

    def test_tj0065_refusal_advice_can_be_followed(self):
        """TJ-0065: the B17 refusal said to pair each date's files in a
        group of their own (ACCOUNT=FILE), but a repeated left-hand side
        merges into one group: the advice gave the same Error. It now
        names a command, one date per run, that compares."""
        x = {"broker_account": "99900001"}
        y = {"broker_account": "99900005"}
        with tempfile.TemporaryDirectory() as td:
            root = self._two_brokers(td, [
                ("margin_x_jun.toml", dict(x, as_of="2024-06-30"),
                 [("ZZA.TO", 50, "")]),
                ("margin_x_dec.toml", dict(x, as_of="2024-12-31"),
                 [("ZZA.TO", 60, "")]),
                ("margin_y_dec.toml", dict(y, as_of="2024-12-31"),
                 [("ZZC.TO", 20, "")])])
            r = _tjs(root, "sanity")
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            err = " ".join(r.stderr.split())
            self.assertIn("snapshots of one broker account at different "
                          "dates", err)
            self.assertNotIn("in a group of their own", err)
            m = re.search(r"`(tjs sanity [^`]+)`", err)
            self.assertIsNotNone(m, err)
            argv = m.group(1).split()[2:]
            self.assertEqual(argv, [("margin=holdings/margin_x_dec.toml"
                                     "+holdings/margin_y_dec.toml")])
            r = _tjs(root, "sanity", *argv)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("OK", r.stdout)

    def test_tj0066_different_brokers_at_different_dates_are_said(self):
        """TJ-0066 (design note): different broker accounts' snapshots at
        different dates are summed and compared with the latest books;
        a June snapshot then shows a September trade as a difference.
        The compare is unchanged; a note says so, naming the symbol."""
        with tempfile.TemporaryDirectory() as td:
            root = self._two_brokers(td, [
                ("margin_x_jun.toml", {"broker_account": "99900001",
                                       "as_of": "2024-06-30"},
                 [("ZZA.TO", 50, "")]),
                ("margin_y_dec.toml", {"broker_account": "99900005",
                                       "as_of": "2024-12-31"},
                 [("ZZC.TO", 20, "")])])
            r = _tjs(root, "sanity", "--details")
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            err = " ".join(r.stderr.split())
            self.assertIn("margin: ZZA.TO changed after 2024-06-30, the "
                          "date of margin_x_jun.toml: compared with the "
                          "latest books — snapshots of different broker "
                          "accounts at different dates", err)
            # The default view's one line keeps the point.
            r = _tjs(root, "sanity", env={"TAXJSON_WIDTH": "120"})
            line = next(ln for ln in r.stderr.splitlines()
                        if "ZZA.TO changed" in ln)
            self.assertIn("compared with the latest books", line)
            self.assertLessEqual(len(line), 120)
        with tempfile.TemporaryDirectory() as td:
            # Both after the books' last trade: the sum is exact, no note.
            root = self._two_brokers(td, [
                ("margin_x_dec.toml", {"broker_account": "99900001",
                                       "as_of": "2024-12-30"},
                 [("ZZA.TO", 60, "")]),
                ("margin_y_dec.toml", {"broker_account": "99900005",
                                       "as_of": "2024-12-31"},
                 [("ZZC.TO", 20, "")])])
            r = _tjs(root, "sanity")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn("different broker accounts", r.stderr)


class TestChecklistStep(unittest.TestCase):

    def test_tj0082_tj0094_step_names_the_quantity_differences(self):
        """TJ-0082 / TJ-0094 (one defect): the checklist's sanity step
        showed the last line of `sanity --details` — the informational
        COST section — as its finding. It names the differences."""
        cost = "total_cost = 500\n"
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", {
                "margin": {"account": "99900001",
                           "rows": [("2024-02-01", "ZZA.TO", 50),
                                    ("2024-02-01", "ZZB.TO", 100)]},
                "margin2": {"account": "99900003",
                            "rows": [("2024-02-01", "ZZB.TO", 50),
                                     ("2024-02-01", "ZZC.TO", 20)]}},
                [("margin_holdings.toml", {"broker_account": "99900001",
                                           "as_of": "2024-12-31"},
                  [("ZZA.TO", 50, cost), ("ZZB.TO", 50, cost)]),
                 ("margin2_holdings.toml", {"broker_account": "99900003",
                                            "as_of": "2024-12-31"},
                  [("ZZC.TO", 20, "")])])
            r = _tjs(root, "sanity", "--details")
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("COST", r.stdout.strip().splitlines()[-1]
                          + r.stdout)
            from taxjson.lib import checklist as cl
            res = cl.d_sanity(_ctx(root))
            self.assertEqual(res.status, "attention")
            self.assertEqual(res.detail,
                             "2 differ: ZZB.TO (margin), ZZB.TO (margin2)")
        # The parser alone: the legend line is no row; a fourth row is
        # counted, not named.
        out = ("ISSUE: MISSING_IN_HOLDINGS = not in the broker's file; "
               "MISSING_IN_TAXJSON = not in the books.\n"
               "SYMBOL  ISSUE  TAXJSON  HOLDINGS  DIFF\n"
               "------\n"
               + "".join(f"ZZ{c}.TO  QTY_MISMATCH  2  1  +1\n"
                         for c in "ABCD")
               + "\n4 discrepancy(ies).\n\nCOST — books vs ...\n")
        self.assertEqual(cl._sanity_differences(out),
                         "4 differ: ZZA.TO, ZZB.TO, ZZC.TO +1 more")
        self.assertEqual(cl._sanity_differences("7 discrepancy(ies).\n"),
                         "7 discrepancy(ies).")

    def test_tj0136_no_holdings_at_all_is_attention_not_confirm(self):
        """TJ-0136: with no holdings file at all the step was `manual`
        ([m], closed by `--done sanity` with nothing compared), with one
        file and other accounts unchecked `attention`. Attention in both
        while an account with positions is unchecked."""
        from taxjson.lib import checklist as cl
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", {
                "margin": {"rows": [("2024-02-01", "ZZA.TO", 10)]},
                "tfsa": {"type": "sheltered",
                         "rows": [("2024-02-01", "ZZB.TO", 5)]}})
            res = cl.d_sanity(_ctx(root))
            self.assertEqual(res.status, "attention", res.detail)
            self.assertIn("UNCHECKED: 2 account(s) with positions", res.detail)
        with tempfile.TemporaryDirectory() as td:
            # Nothing held, nothing to compare: still a confirm step.
            root = _project(Path(td) / "p", {"margin": {}})
            res = cl.d_sanity(_ctx(root))
            self.assertEqual(res.status, "manual", res.detail)
            self.assertIn("no positions snapshot in holdings/", res.detail)


class TestNoHoldingsFiles(unittest.TestCase):

    def test_tj0135_json_every_account_and_no_readme_claim(self):
        """TJ-0135: with no holdings file `sanity --json` printed text,
        the hint claimed `tjs init` wrote holdings/README.txt (an
        `init --single` project has no holdings/), and UNCHECKED counted
        the taxable accounts only (the run counts every account with
        positions); --details did not list them."""
        accounts = {f"t{i}": {"rows": [("2024-02-01", "ZZA.TO", 1)]}
                    for i in range(3)}
        accounts.update({f"s{i}": {"type": "sheltered",
                                   "rows": [("2024-02-01", "ZZB.TO", 2)]}
                         for i in range(2)})
        accounts["empty"] = {}
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", accounts)
            r = _tjs(root, "sanity", "--json")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            doc = json.loads(r.stdout)
            self.assertEqual(doc["uncovered_accounts"],
                             ["s0", "s1", "t0", "t1", "t2"])
            self.assertFalse(doc["complete"])
            self.assertTrue(doc["clean"])
            self.assertEqual(doc["discrepancies"], [])
            r = _tjs(root, "sanity")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("UNCHECKED: 5 account(s) with positions and no "
                          "holdings file: s0, s1, t0 +2 more", r.stdout)
            self.assertIn("nothing to compare with", r.stdout)
            self.assertNotIn("README", r.stdout)
            self.assertIn("positions .toml in holdings/", r.stdout)
            r = _tjs(root, "sanity", "--details")
            for a in ("s0", "s1", "t0", "t1", "t2"):
                self.assertIn(f"account {a} not included", r.stdout)
            self.assertNotIn("account empty", r.stdout)
            # A holdings/ folder with its README (as `init` writes it).
            (root / "holdings").mkdir()
            (root / "holdings" / "README.txt").write_text("x\n")
            r = _tjs(root, "sanity")
            self.assertIn("its README.txt says how", r.stdout)


class TestCostNoteOneLine(unittest.TestCase):

    def test_tj0160_default_view_keeps_why_the_cost_is_not_compared(self):
        """TJ-0160: the default view cut the 'cost not compared' note to
        its file and row ('Info: margin_holdings.toml: [[holding]] #1
        (ZZS.TO)'), with no reason. The one line keeps the point."""
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", {"margin": {
                "account": "99900001",
                "rows": [("2024-02-01", "ZZS.TO", 30)]}},
                [("margin_holdings.toml", {"broker_account": "99900001",
                                           "as_of": "2024-12-31"},
                  [("ZZS.TO", 30, 'total_cost = "n/a"\n')])])
            r = _tjs(root, "sanity", env={"TAXJSON_WIDTH": "120"})
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            line = next(ln for ln in r.stderr.splitlines()
                        if "#1 (ZZS.TO)" in ln)
            self.assertIn("cost is not compared: total_cost 'n/a' is not "
                          "a number", line)
            self.assertLessEqual(len(line), 120)
            r = _tjs(root, "sanity", "--details")
            self.assertIn("cost is not compared: total_cost 'n/a' is not a "
                          "number — its quantities are.", r.stderr)


if __name__ == "__main__":
    unittest.main()
