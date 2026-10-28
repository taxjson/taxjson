"""Audit 2026-10-24, group "reports-state": the recorded wash inputs
and a new account (D01), the T1135 cost walk and an assignment that
covers a short (D02), a damaged work/cross_listings.state read as "no
pending journals" (D05), an incomplete work/<acct>_report.json served
by `sum` (D07), and `migrate` sharing a tobase.map that is not UTF-8
text (A42). One test per finding, each the audit's probe.

Every fixture is synthetic: made-up tickers (ZZQ.US), account names
(a, b, c, margin, tfsa), no ids, no amounts of 1,000 or more with cents.
"""
import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from taxjson.lib.dispatch import python_module_argv
from tax_rules import rule

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    """`taxjson -C root args...` from this worktree's sources (the
    audit's CLI probes; python_module_argv, never `-m` from the cwd)."""
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run")
        + ["-C", str(root), *args],
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        timeout=180)


_CFG = {"settings": {"year": 2026, "country": "canada"},
        "accounts": {"margin": {"type": "taxable"}}}


def _project(root: Path) -> None:
    """A minimal Canadian project with an empty work/ (the probes')."""
    (root / "work").mkdir()
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2026\ncountry = "canada"\n'
        '[accounts.margin]\ntype = "taxable"\n')


def _older(p: Path, than: Path, seconds: float) -> None:
    t = than.stat().st_mtime + seconds
    os.utime(p, (t, t))


class TestD01NewAccountMakesTheBlendStale(unittest.TestCase):

    def _blend(self, root: Path, blend: str = "ca", *, roster=True):
        """A recorded blend of a and b (a tfsa book present), the wash
        file a_gains_wash.json; `roster=False` strips the `books` list
        as a record of an older release has none."""
        from taxjson.lib import report_model as rm
        inputs = []
        for acct in ("a", "b"):
            for suffix in ("_base.json", "_gains.json"):
                p = root / (acct + suffix)
                p.write_text('{"transactions":[]}')
                inputs.append(p)
        for suffix in ("_base.json", "_gains.json"):
            (root / ("tfsa" + suffix)).write_text('{"transactions":[]}')
        wash = root / "a_gains_wash.json"
        wash.write_text('{"transactions":[]}')
        rm.record_wash_inputs(root, [wash], inputs, blend=blend)
        if not roster:
            rec = root / rm.WASH_INPUTS_FILE
            doc = json.loads(rec.read_text())
            doc["wash"][wash.name].pop("books")
            rec.write_text(json.dumps(doc))
        return wash

    def _new_account(self, root: Path, wash: Path) -> None:
        (root / "c_base.json").write_text(
            '{"transactions":[{"symbol":"ZZQ.US"}]}')
        (root / "c_gains.json").write_text('{"transactions":[]}')
        for suffix in ("_base.json", "_gains.json"):
            _older(root / ("c" + suffix), wash, 10)

    def test_d01_new_account_books_newer_than_the_blend_are_stale(self):
        """D01: Recorded wash inputs fail to detect a newly added
        account. A wash result recorded from accounts a and b did not
        become stale when account c was added and its books built: the
        recorded-input branch checked the recorded names only. The
        record now carries the roster of accounts the pass saw; c, not
        in it, with books newer than the wash file, makes it stale."""
        from taxjson.lib import report_model as rm
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            wash = self._blend(root)
            doc = json.loads((root / rm.WASH_INPUTS_FILE).read_text())
            self.assertEqual(doc["wash"][wash.name]["books"],
                             ["a", "b", "tfsa"])
            self.assertEqual(rm.stale_wash_inputs(wash), [])
            self._new_account(root, wash)
            self.assertEqual(rm.stale_wash_inputs(wash),
                             ["c_base.json", "c_gains.json"])
            # The account's own books rebuilt unchanged are current, and
            # a sheltered book the pass saw (in the roster) rebuilt
            # unchanged is too: GitHub #15's precision kept.
            (root / "c_base.json").unlink()
            (root / "c_gains.json").unlink()
            for n in ("a_base.json", "a_gains.json", "tfsa_base.json",
                      "tfsa_gains.json"):
                _older(root / n, wash, 20)
            self.assertEqual(rm.stale_wash_inputs(wash), [])
        with tempfile.TemporaryDirectory() as td:
            # A record without a roster (an older release's): the new
            # account is still found — the conservative side.
            root = Path(td)
            wash = self._blend(root, roster=False)
            self._new_account(root, wash)
            self.assertEqual(rm.stale_wash_inputs(wash),
                             ["c_base.json", "c_gains.json"])
        with tempfile.TemporaryDirectory() as td:
            # A single-account pass (the blend tag is the account's own
            # name) depends on no other account.
            root = Path(td)
            wash = self._blend(root, blend="a")
            self._new_account(root, wash)
            self.assertEqual(rm.stale_wash_inputs(wash), [])


def _row(d, sym, q, amount, action="BUYSELL"):
    return dict(date=d, date_settle=d, action=action, symbol=sym,
                quantity=q, net_amount=amount, currency="CAD",
                account="margin", open_close="O" if d < "2025-01-03" else "C")


_PUT = "ZZQ250117P00010000.US"


def _t1135_vs_engine(rows, with_report=False):
    """(the T1135 property row, the engine's inventory) for `rows`, and
    the whole report `with_report`."""
    from taxjson.bin.taxjson_t1135 import build_report
    from taxjson.lib.core import CanadaTaxRules, TaxTransaction
    with contextlib.redirect_stderr(io.StringIO()):
        engine = CanadaTaxRules().compute_gains(
            [TaxTransaction(**r) for r in rows], detect_wash_sales=False)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        base, gains = root / "base.json", root / "gains.json"
        base.write_text(json.dumps({"transactions": rows, "metadata": {
            "target_currency": "CAD"}}))
        gains.write_text(json.dumps(engine))
        with contextlib.redirect_stderr(io.StringIO()):
            report = build_report([base], [gains], 2025, {}, "CAD")
    props = {p["symbol"]: p for p in report["properties"]}
    if with_report:
        return props, engine["inventory"], report
    return props, engine["inventory"]


class TestD02AssignmentCoveringAShort(unittest.TestCase):

    @rule("CA-RPT-12", "CA-RPT-19")
    def test_d02_premium_share_of_the_kept_shares_stays_in_their_cost(self):
        """D02: T1135 loses assignment premium allocated to shares
        remaining after covering a short. Short 50 shares for 500,
        write a put for 100, take assignment buying 100 shares for
        1000: the engine leaves 50 shares at cost 450 (the premium
        apportioned by quantity between the cover and the shares
        kept); the T1135 walk folded the premium into the short pool,
        the cover drained it, and the kept shares opened at 500. The
        walk now stages the premium for the delivery leg and
        apportions it as the engine does: both say 450."""
        rows = [_row("2025-01-01", "ZZQ.US", -50, 500),
                _row("2025-01-02", _PUT, -1, 100),
                _row("2025-01-03", _PUT, 1, 0, "ASSIGN"),
                _row("2025-01-03", "ZZQ.US", 100, 1000)]
        props, inventory = _t1135_vs_engine(rows)
        (held,) = [i for i in inventory if i["symbol"] == "ZZQ.US"]
        self.assertEqual(held["qty"], 50)
        self.assertAlmostEqual(held["total_cost"], 450.0, places=2)
        self.assertAlmostEqual(props["ZZQ.US"]["year_end_cost"], 450.0,
                               places=2)
        self.assertAlmostEqual(props["ZZQ.US"]["max_cost"], 450.0, places=2)
        # No short to cover: the whole premium lowers the shares' cost,
        # as before the change (the staged amount lands with the buy).
        rows = [_row("2025-01-02", _PUT, -1, 100),
                _row("2025-01-03", _PUT, 1, 0, "ASSIGN"),
                _row("2025-01-03", "ZZQ.US", 100, 1000)]
        props, inventory = _t1135_vs_engine(rows)
        (held,) = [i for i in inventory if i["symbol"] == "ZZQ.US"]
        self.assertAlmostEqual(held["total_cost"], 900.0, places=2)
        self.assertAlmostEqual(props["ZZQ.US"]["year_end_cost"], 900.0,
                               places=2)
        # The delivery only covers part of the short: no shares are
        # held, so no cost amount (a short is not property held; the
        # premium's whole share went to the cover's gain). Held only
        # short in the year, it is named, not a row (CA-RPT-19).
        rows = [_row("2025-01-01", "ZZQ.US", -200, 2000),
                _row("2025-01-02", _PUT, -1, 100),
                _row("2025-01-03", _PUT, 1, 0, "ASSIGN"),
                _row("2025-01-03", "ZZQ.US", 100, 1000)]
        props, inventory, report = _t1135_vs_engine(rows, with_report=True)
        self.assertEqual([i["qty"] for i in inventory
                          if i["symbol"] == "ZZQ.US"], [-100])
        self.assertNotIn("ZZQ.US", props)
        self.assertEqual(report["short_only_symbols"], ["ZZQ.US"])


class TestD05DamagedJournalState(unittest.TestCase):

    def test_d05_damaged_state_blocks_the_journals_and_the_step(self):
        """D05: Unreadable journal state yields no pending journals and
        a completed checklist item. A work/cross_listings.state holding
        broken JSON passed the existence checks and read as empty record
        lists: `journals --pending --json` exited 0 with pending 0 and
        checklist.d_journals said done. The report now refuses a state
        file it cannot read, naming why, and the step is blocked."""
        from taxjson.lib import checklist
        from taxjson.lib import cross_listings as XL
        from taxjson.lib.journals import JournalsError, report
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _project(root)
            state = root / "work" / XL.STATE
            ctx = SimpleNamespace(root=root, cache=root / "work", cfg=_CFG)
            state.write_text("{broken")
            run = _cli(root, "journals", "--pending", "--json")
            self.assertNotEqual(run.returncode, 0, run.stdout)
            self.assertEqual(run.stdout, "")
            self.assertIn(f"work/{XL.STATE} is not JSON", run.stderr)
            self.assertIn("run `taxjson run` first", run.stderr)
            with self.assertRaises(JournalsError) as cm:
                report(root, _CFG)
            self.assertIn("is not JSON", str(cm.exception))
            res = checklist.d_journals(ctx)
            self.assertEqual(res.status, "blocked")
            self.assertIn(f"work/{XL.STATE} is not JSON", res.detail)
            self.assertIn("run `taxjson run`", res.detail)
            # Another version's record (or a hand-edited one): the same.
            state.write_text(json.dumps({"format": "cross_listings/0",
                                         "joined": []}))
            res = checklist.d_journals(ctx)
            self.assertEqual(res.status, "blocked")
            self.assertIn("not 'cross_listings/1'", res.detail)
            self.assertEqual(XL.state_problem(state).split(" (")[0],
                             "has format 'cross_listings/0', not "
                             "'cross_listings/1'")
            # The run's own record with nothing in it reads as before:
            # nothing pending, the step done.
            state.write_text(json.dumps({"format": XL.FORMAT}))
            self.assertIsNone(XL.state_problem(state))
            run = _cli(root, "journals", "--pending", "--json")
            self.assertEqual(run.returncode, 0, run.stderr[-1000:])
            self.assertEqual(json.loads(run.stdout)["pending"], 0)
            self.assertEqual(checklist.d_journals(ctx).status, "done")


class TestD07IncompleteSummaryCache(unittest.TestCase):

    GAINS = {"summary": {"year": 2026}, "transactions": [
        {"symbol": "ZZQ.US", "date": "2026-01-02", "qty": 1, "cost": 10,
         "proceeds": 20, "gain": 10, "currency": "CAD"}]}

    def _sum(self, root: Path):
        run = _cli(root, "sum", "--json")
        self.assertEqual(run.returncode, 0, run.stderr[-1500:])
        return json.loads(run.stdout), run.stderr

    def test_d07_report_without_an_aggregate_is_recomputed_and_said(self):
        """D07: An incomplete summary cache overrides valid gains and
        makes one report contradict itself. A newer margin_report.json
        with the accepted version, account, year and basis but an empty
        gains object was served as the aggregate: `sum --json` reported
        totals.realized 0 beside filing.totals.gain 10. The fast path
        now requires the aggregate's fields (ticker_stats, total_fees);
        otherwise the row is recomputed from the gains file, with a
        Warning naming both files, and the two totals agree."""
        from taxjson.bin.taxjson_sum_gains import summarize_gains
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _project(root)
            gains_p = root / "work" / "margin_gains.json"
            report_p = root / "work" / "margin_report.json"
            gains_p.write_text(json.dumps(self.GAINS))
            head = {"schema_version": 1, "year": 2026, "account": "margin",
                    "basis": "pre-wash"}
            report_p.write_text(json.dumps(dict(head, gains={})))
            _older(report_p, gains_p, 5)
            doc, err = self._sum(root)
            self.assertEqual(doc["totals"]["realized"], 10.0)
            self.assertEqual(doc["filing"]["totals"]["gain"], 10.0)
            self.assertEqual(doc["accounts"][0]["realized"], 10.0)
            self.assertIn("work/margin_report.json holds no usable gains "
                          "aggregate", err)
            self.assertIn("recomputed from margin_gains.json", err)
            # A gains object that is not an object at all: the same.
            report_p.write_text(json.dumps(dict(head, gains=[1, 2])))
            _older(report_p, gains_p, 5)
            doc, err = self._sum(root)
            self.assertEqual(doc["totals"]["realized"], 10.0)
            self.assertIn("recomputed from", err)
            # The run's complete aggregate is served, without the Warning
            # (an account with no trades has empty objects, not none).
            report_p.write_text(json.dumps(dict(
                head, gains=summarize_gains(self.GAINS))))
            _older(report_p, gains_p, 5)
            doc, err = self._sum(root)
            self.assertEqual(doc["totals"]["realized"], 10.0)
            self.assertEqual(doc["filing"]["totals"]["gain"], 10.0)
            self.assertNotIn("recomputed", err)
            self.assertNotIn("usable gains aggregate", err)


class TestA42SharedTobaseNotUtf8(unittest.TestCase):

    def test_a42_an_unreadable_winning_map_is_refused(self):
        """A42: Sharing accepts an invalid UTF-8 winning map and
        propagates it to every year. The newest year's tobase.map, not
        UTF-8 text, was compared as an empty map and proposed as the
        shared file with no problem recorded; identical copies were not
        looked at. plan_shared now records the problem for the winner,
        identical or not, and `migrate` writes nothing."""
        from taxjson.lib import tobase_map as TB
        from test_fix_shared_tobase import _years
        with tempfile.TemporaryDirectory() as td:
            top = _years(td, tobase="")
            bad = top / "2025" / "tobase.map"
            bad.write_bytes(bytes([255]))
            plan = TB.plan_shared(top)
            self.assertEqual(len(plan.problems), 1, plan.problems)
            self.assertIn("2025/tobase.map is not UTF-8 text (byte 0xff "
                          "at offset 0)", plan.problems[0])
            self.assertFalse(plan.identical)
            r = _cli(top, "migrate")
            self.assertNotEqual(r.returncode, 0, r.stdout)
            self.assertIn("2025/tobase.map is not UTF-8 text", r.stderr)
            self.assertIn("Nothing was changed", r.stderr)
            self.assertFalse((top / "tobase.map").exists())
            self.assertEqual(bad.read_bytes(), bytes([255]))
            self.assertEqual((top / "2024" / "tobase.map").read_text(), "")
            # Every copy the same unreadable bytes: no fast path past
            # the check.
            (top / "2024" / "tobase.map").write_bytes(bytes([255]))
            plan = TB.plan_shared(top)
            self.assertTrue(plan.identical)
            self.assertEqual(len(plan.problems), 1, plan.problems)
            self.assertIn("is not UTF-8 text", plan.problems[0])
            r = _cli(top, "migrate")
            self.assertNotEqual(r.returncode, 0, r.stdout)
            self.assertFalse((top / "tobase.map").exists())
            # Readable copies share as before.
            for y in ("2024", "2025"):
                (top / y / "tobase.map").write_text("")
            plan = TB.plan_shared(top)
            self.assertEqual(plan.problems, [])
            self.assertTrue(plan.identical)


if __name__ == "__main__":
    unittest.main()
