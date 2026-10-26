"""Audit 2026-10-25, group sanity: `taxjson sanity` against the broker's
holdings files. brokers-inputs#1, robustness#2 and first-project#3 are
one defect (a holdings TOML whose `[meta] holdings_count` disagrees with
its tables, or whose contract size is 0 or negative, compared "OK");
first-project#2 is two dated snapshots of one account summed. Synthetic
data and fake ids only."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from _style import CapturedWidth

from taxjson.lib.dispatch import python_module_argv

# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the stderr phrases asserted below are then never wrapped.
_WIDTH = CapturedWidth()
_BUILT = None       # a tiny project with built books, copied per test

OPT = "ZZQ270115C00010000.US"


def _tjs(root: Path, *argv: str, env=None):
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run")
        + ["-C", str(root), *argv],
        capture_output=True, text=True,
        env=dict(os.environ, **(env or {})))


def setUpModule():
    global _BUILT
    _WIDTH.start()
    _BUILT = tempfile.TemporaryDirectory()
    root = Path(_BUILT.name) / "p"
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        '[settings]\ncountry = "canada"\nyear = 2024\n'
        'tax_date = "settle"\nbase_currency = "CAD"\n'
        'source_currencies = ["USD"]\n'
        '[accounts.margin]\ntype = "taxable"\n')
    # 30 ZZQ.TO bought 2024-02-01, 10 ZZR.TO on 2024-03-01.
    (root / "inputs" / "margin" / "t.tt").write_text(
        "BUYSELL 2024-02-01 09:30:00 ZZQ.TO 30 CAD 10.00 300.00 0\n"
        "BUYSELL 2024-03-01 09:30:00 ZZR.TO 10 CAD 20.00 200.00 0\n")
    r = _tjs(root, "run", "--no-input")
    if r.returncode != 0:
        raise RuntimeError(f"the project's run failed: {r.stderr[-2000:]}")


def tearDownModule():
    _WIDTH.stop()
    if _BUILT is not None:
        _BUILT.cleanup()


def _project(td: str, snapshots=()) -> Path:
    """A copy of the built project with holdings/ snapshots: (name,
    [meta] lines, [(symbol, quantity, extra lines)])."""
    root = Path(td) / "p"
    shutil.copytree(Path(_BUILT.name) / "p", root)
    if snapshots:
        (root / "holdings").mkdir()
    for name, meta, rows in snapshots:
        (root / "holdings" / name).write_text(_toml(meta, rows))
    return root


def _toml(meta: str, rows) -> str:
    body = "".join(f'\n[[holding]]\nsymbol = "{s}"\nquantity = {q}\n'
                   f'currency = "CAD"\n{extra}' for s, q, extra in rows)
    return f"[meta]\n{meta}{body}"


_BOTH = [("ZZQ.TO", 30, "total_cost = 300\n"),
         ("ZZR.TO", 10, "total_cost = 200\n")]


class TestWholeSnapshot(unittest.TestCase):

    def test_brokers_inputs_1_holdings_count_mismatch_is_refused(self):
        """brokers-inputs#1: sanity compares a holdings file whose
        holdings_count disagrees with its tables and says OK (the B37
        fix only covered the cost reader). The holdings/ snapshot says
        it holds 3 positions and has 2: refused with an Error naming the
        file, never "OK", `complete` or a ticked checklist step."""
        meta = ('as_of = "2024-06-30"\naccount = "margin"\n'
                'holdings_count = 3\n')
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, [("margin_holdings.toml", meta, _BOTH)])
            for argv in (("sanity",), ("sanity", "--json")):
                r = _tjs(root, *argv)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn("margin_holdings.toml: [meta] holdings_count "
                              "= 3 but the file has 2 [[holding]] "
                              "table(s)", r.stderr)
                self.assertIn("download it again", r.stderr)
                self.assertNotIn("OK", r.stdout)
                self.assertNotIn('"complete": true', r.stdout)
            r = _tjs(root, "checklist", "--run", "--only", "sanity",
                     "--json")
            step = [s for s in json.loads(r.stdout)["steps"]
                    if s["id"] == "sanity"][0]
            self.assertEqual(step["status"], "attention")
            self.assertIn("holdings_count = 3", step["detail"])
            # The same file with the right count compares as before.
            (root / "holdings" / "margin_holdings.toml").write_text(
                _toml(meta.replace("= 3", "= 2"), _BOTH))
            r = _tjs(root, "sanity", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            doc = json.loads(r.stdout)
            self.assertTrue(doc["clean"] and doc["complete"])

    def test_robustness_2_contract_size_and_count_refused_in_paired_form(
            self):
        """robustness#2: incomplete fix of B37 and B36 — `tjs sanity`
        still compares a holdings TOML whose [meta] holdings_count
        disagrees with its tables, or that has contract_multiplier <= 0,
        and says OK. Both are refused in the ACCOUNT=FILE form too, also
        behind a row whose cost cannot be read; a malformed cost alone
        still only skips the cost, and taxjson-export's
        `contract_multiplier_unknown = true` still compares."""
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            f = Path(td) / "h.toml"
            meta = 'as_of = "2024-12-31"\n'
            cases = [
                (meta + "holdings_count = 5\n", _BOTH,
                 "holdings_count = 5 but the file has 2"),
                (meta, [_BOTH[0], ("ZZR.TO", 10,
                                   "contract_multiplier = -50\n")],
                 "contract_multiplier -50 is not a positive number"),
                (meta, [_BOTH[0], ("ZZR.TO", 10, "multiplier = 0\n")],
                 "multiplier 0 is not a positive number"),
                # A cost that cannot be read on row 1 no longer hides
                # row 2's contract size.
                (meta, [("ZZQ.TO", 30, 'total_cost = "abc"\n'),
                        ("ZZR.TO", 10, "contract_multiplier = 0\n")],
                 "#2 (ZZR.TO): contract_multiplier 0 is not a positive"),
                (meta, [_BOTH[0], ("ZZR.TO", 10,
                                   'contract_multiplier = "x"\n')],
                 "contract_multiplier 'x' is not a number"),
            ]
            for m, rows, want in cases:
                f.write_text(_toml(m, rows))
                r = _tjs(root, "sanity", f"margin={f}")
                self.assertEqual(r.returncode, 2,
                                 f"{want}: {r.stdout}{r.stderr}")
                self.assertIn(want, r.stderr)
                self.assertNotIn("OK", r.stdout)
            # A malformed cost alone: quantities compared, cost skipped.
            f.write_text(_toml(meta, [("ZZQ.TO", 30, 'total_cost = "abc"\n'),
                                      _BOTH[1]]))
            r = _tjs(root, "sanity", f"margin={f}", "--details")
            self.assertEqual(r.returncode, 0, r.stderr)
            # (the reason before the clause break: backlog TJ-0160)
            self.assertIn("cost is not compared: total_cost 'abc' is not "
                          "a number — its quantities are.", r.stderr)
        # taxjson-export's file for an option of unknown contract size.
        from taxjson.lib import positions_reports as pr
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "export.toml"
            p.write_text('[meta]\nholdings_count = 1\nas_of = "2026-01-02"\n'
                         f'[[holding]]\nsymbol = "{OPT}"\nquantity = 1\n'
                         'currency = "USD"\nasset_type = "option"\n'
                         'cost_per_share = 150.0\n'
                         'contract_multiplier_unknown = true\n')
            rep = pr.read_positions(p)
            self.assertEqual((rep.rows[0].quantity, rep.rows[0].cost),
                             (1.0, 150.0))
            for body, cls in (('contract_multiplier = -1\n',
                               pr.PositionsSnapshotError),
                              ('total_cost = "abc"\n',
                               pr.PositionsReportError)):
                p.write_text('[meta]\nas_of = "2026-01-02"\n[[holding]]\n'
                             f'symbol = "{OPT}"\nquantity = 1\n{body}')
                with self.assertRaises(cls) as cm:
                    pr.read_positions(p)
                if cls is pr.PositionsReportError:
                    self.assertNotIsInstance(cm.exception,
                                             pr.PositionsSnapshotError)

    def test_first_project_3_run_never_says_positions_match(self):
        """first-project#3: B37/B36 refusals reduced to an Info in
        sanity — a snapshot whose holdings_count says rows are missing
        was certified OK and `tjs run` said "positions match the
        broker's holdings files". The run's end check now says the
        check could not run and points at the Error, never "match" nor
        "differ … missing history"."""
        meta = ('as_of = "2024-12-31"\nbroker_account = "99900090"\n'
                'holdings_count = 2\n')
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, [("margin_2024-12-31.toml", meta,
                                  [_BOTH[0]])])
            r = _tjs(root, "run", "--no-input")
            out = r.stdout + r.stderr
            self.assertIn("holdings_count = 2 but the file has 1", out)
            self.assertIn("holdings check could not run", out)
            self.assertIn("The Error above names the file to fix", out)
            self.assertNotIn("positions match", out)
            self.assertNotIn("positions differ", out)
            # The console path (a terminal's width) says the same.
            from taxjson.bin import taxjson_run as tr
            err = io.StringIO()
            with contextlib.redirect_stderr(err), \
                    self.assertRaises(SystemExit) as cm:
                tr._sanity_console(root)
            self.assertEqual(cm.exception.code, tr._SANITY_REFUSED)
            self.assertIn("holdings_count = 2", err.getvalue())


class TestDatedSnapshots(unittest.TestCase):

    def test_first_project_2_dated_snapshots_without_broker_are_not_summed(
            self):
        """first-project#2: sanity sums two dated holdings/ snapshots of
        one account (B17 fix incomplete) when they name the account the
        way getting-started says to: a correct pair (30 ZZQ.TO on
        2024-02-15; 30 ZZQ.TO and 10 ZZR.TO on 2024-12-31) was compared
        as 60 ZZQ.TO, a false difference. One broker account at two
        dates cannot be told from two broker accounts without a broker
        id: refused, naming the files and `broker_account`; with it they
        are compared one date at a time; files of one date still sum."""
        feb = [("ZZQ.TO", 30, "")]
        for metas, word in (
                (('account = "margin"\n', 'account = "margin"\n'), "none"),
                (("", ""), "none"),
                (('broker_account = "99900090"\n', ""), "not every one")):
            with tempfile.TemporaryDirectory() as td:
                root = _project(td, [
                    ("margin_2024-02-15.toml",
                     metas[0] + 'as_of = "2024-02-15"\n', feb),
                    ("margin_2024-12-31.toml",
                     metas[1] + 'as_of = "2024-12-31"\n', _BOTH)])
                r = _tjs(root, "sanity")
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn("margin_2024-02-15.toml (2024-02-15), "
                              "margin_2024-12-31.toml (2024-12-31) are "
                              f"dated differently and {word} names its "
                              "broker account", r.stderr)
                self.assertIn('Add `broker_account = "..."`', r.stderr)
                self.assertNotIn("MISSING_IN", r.stdout)
                self.assertNotIn("QTY_MISMATCH", r.stdout)
        # Each naming its broker account: one date at a time, both OK.
        with tempfile.TemporaryDirectory() as td:
            ba = 'broker_account = "99900090"\n'
            root = _project(td, [
                ("margin_2024-02-15.toml", ba + 'as_of = "2024-02-15"\n',
                 feb),
                ("margin_2024-12-31.toml", ba + 'as_of = "2024-12-31"\n',
                 _BOTH)])
            r = _tjs(root, "sanity", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            doc = json.loads(r.stdout)
            self.assertTrue(doc["clean"] and doc["complete"])
            self.assertEqual([g["as_of"] for g in doc["groups"]],
                             ["2024-02-15", None])
        # Two broker accounts' files of one date, named by file name only:
        # summed, as one taxjson account across two brokers.
        with tempfile.TemporaryDirectory() as td:
            day = 'as_of = "2024-12-31"\n'
            root = _project(td, [("margin_ib_holdings.toml", day,
                                  [_BOTH[0]]),
                                 ("margin_wb_holdings.toml", day,
                                  [_BOTH[1]])])
            r = _tjs(root, "sanity", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(json.loads(r.stdout)["clean"])


if __name__ == "__main__":
    unittest.main()
