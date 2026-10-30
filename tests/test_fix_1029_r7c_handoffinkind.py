"""Fix round 7 (wave C) of 2026-10-29, the handoff and in-kind group:
handoff's lot checks with renamed accounts and re-described options,
ALLOWLOSS units, the in-kind contribution date on the registered side,
RESP withdrawal guidance, a per-share INKIND value with a contract
size, the ACQUIRED repair price for options and the SIMILAR warning
(synthetic data only)."""
import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

from _qa_project import tj
from tax_rules import rule
from tax_rules.dual import tx

from taxjson.lib import loss_overrides as LO
from taxjson.lib.country import CANADA as _CA


def _ov(date, symbol, qty=None, account="margin", reason="r"):
    return {"account": account, "date": date, "symbol": symbol,
            "qty": qty, "reason": reason, "where": "inputs/margin/x.tt:1",
            "line": f"ALLOWLOSS {date} {symbol} reason=\"{reason}\""}


_QH = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
       "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
       "Activity Type,Account #,Account Type\n")


def _project(root, accounts, year=2025, extra=""):
    """A Canadian project of `accounts` ({name: type}, a sheltered one
    a TFSA) with transfers kept, and its input folders."""
    head = ('[settings]\nlocal_timezone = "America/Toronto"\n'
            f'year = {year}\ncountry = "canada"\nbase_currency = "CAD"\n'
            'source_currencies = []\n')
    body = "".join(
        f'[accounts.{a}]\ntype = "{t}"\n'
        + ('plan = "tfsa"\ntransfers = true\n' if t == "sheltered" else "")
        for a, t in accounts.items())
    (root / "taxjson.toml").write_text(head + body + extra)
    (root / "ticker.map").write_text("")
    for a in accounts:
        (root / "inputs" / a).mkdir(parents=True, exist_ok=True)
    return root


def _transfer(day, sym, qty, num, typ, desc="SYN TRANSFER"):
    return (f"{day},{day},TF6,{sym},{desc},{qty},0,0,0,0,CAD,Transfers,"
            f"{num},{typ}\n")


def _work(root, name):
    p = root / "work" / name
    return json.loads(p.read_text()) if p.exists() else None


def _gains(book, **req):
    from taxjson.lib.pipeline import GainsRequest, run_gains
    with contextlib.redirect_stderr(io.StringIO()):
        return run_gains(copy.deepcopy(list(book)), [], (),
                         req=GainsRequest(country=_CA, taxable=True, **req))


class TestAllowLossUnits(unittest.TestCase):
    """TJ-0921: the units an ALLOWLOSS line gives match a sale's units
    within a tolerance relative to them, not an absolute 0.000001 that
    is half a percent of a small coin quantity."""

    _BOOK = [tx("BUYSELL", "2025-01-02", "SYN.KR", 0.0001, -100,
                currency="CAD"),
             tx("BUYSELL", "2025-06-02", "SYN.KR", -0.0001, 90,
                currency="CAD"),
             tx("BUYSELL", "2025-06-03", "SYN.KR", 0.0001, -90,
                currency="CAD")]

    @rule("CA-SL-18")
    def test_small_quantity_must_match(self):
        for q, want in ((0.0001, LO.STATUS_APPLIED),
                        (0.0001005, LO.STATUS_UNMATCHED),
                        (0.0002, LO.STATUS_UNMATCHED)):
            with self.subTest(q=q):
                res = _gains(self._BOOK, loss_overrides=(
                    _ov("2025-06-02", "SYN.KR", qty=q),))
                self.assertEqual(res["loss_overrides"][0]["status"], want)

    @rule("CA-SL-18")
    def test_float_noise_still_matches(self):
        res = _gains(self._BOOK, loss_overrides=(
            _ov("2025-06-02", "SYN.KR", qty=0.0001 * (1 + 1e-12)),))
        self.assertEqual(res["loss_overrides"][0]["status"],
                         LO.STATUS_APPLIED)


class TestAcquiredOptionRepair(unittest.TestCase):
    """TJ-0934: the price an ACQUIRED line's total agrees with is total
    / (quantity x contract size): an option's is per share, not 100
    times it."""

    def _advice(self, line, formula, expected):
        from taxjson.lib.tt_totals import Mismatch
        m = Mismatch("plan.tt:1", "110.00", formula, expected, repr(line))
        return " ".join(m.message("", line)[1])

    def test_option_price_is_per_share(self):
        line = ("ACQUIRED 2025-01-02 09:30:00 SYN251219C00010000.TO 1 CAD "
                "1 110 ARRIVED 2025-06-02")
        self.assertIn("(1.10)", self._advice(line, "qty*price*100+fee",
                                             "100.00"))
        self.assertNotIn("(110.00)", self._advice(
            line, "qty*price*100+fee", "100.00"))

    def test_mini_option_and_share(self):
        line = ("ACQUIRED 2025-01-02 09:30:00 SYN251219C00010000.TO 2 CAD "
                "1 110 ARRIVED 2025-06-02")
        self.assertIn("(5.50)", self._advice(line, "qty*price*10+fee",
                                             "20.00"))
        share = ("ACQUIRED 2025-01-02 09:30:00 SYN.TO 10 CAD 10 110 "
                 "ARRIVED 2025-06-02")
        self.assertIn("(11.00)", self._advice(share, "qty*price+fee",
                                              "100.00"))


class TestInKindPerShareOptionSize(unittest.TestCase):
    """TJ-0933: an INKIND line's per-share price of an option is valued
    at the contract's own size (a ticker.map MULT line), not 100."""

    _SYM = "SYN251219C00010000.TO"

    def _fmv(self, value):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td), {"margin": "taxable",
                                       "plan": "sheltered"})
            (root / "ticker.map").write_text(f"MULT {self._SYM} 10\n")
            (root / "inputs/margin/trades.tt").write_text(
                f"BUYSELL 2025-01-02 09:30:00 {self._SYM} 1 CAD 1 10 0 "
                f"x10\nINKIND 2025-06-02 {self._SYM} -1 CAD {value} "
                f"plan=tfsa\n")
            for a, q, num, typ in (("margin", -1, "99900001", "Margin"),
                                   ("plan", 1, "99900002", "TFSA")):
                (root / "inputs" / a / "qt.csv").write_text(
                    _QH + _transfer("2025-06-02", self._SYM, q, num, typ))
            r = tj(root, "run", "--no-input", check=False)
            ik = _work(root, "in_kind.json") or {}
            return r, [m.get("fmv") for m in ik.get("moves", [])], \
                list(ik.get("problems", []))

    def test_per_share_price_uses_mult(self):
        r, fmv, probs = self._fmv("2")
        self.assertEqual(fmv, [20.0], r.stdout[-2000:] + r.stderr[-2000:])
        self.assertEqual(probs, [])

    def test_total_control(self):
        _r, fmv, _p = self._fmv("0 20")
        self.assertEqual(fmv, [20.0])

    def test_price_and_total_agree_at_the_real_size(self):
        r, fmv, probs = self._fmv("2 20")
        self.assertEqual(fmv, [20.0], r.stdout[-2000:] + r.stderr[-2000:])
        self.assertEqual(probs, [])

    def test_price_and_total_disagree_at_the_real_size(self):
        _r, _fmv, probs = self._fmv("2 200")
        self.assertTrue(any("differs from" in p and "x 10" in p
                            for p in probs), probs)


class TestRespWithdrawalGuidance(unittest.TestCase):
    """TJ-0926: an RESP withdrawal's value is not said to be, without
    condition, an educational assistance payment on the student's T4A:
    a refund of contributions is tax-free, with no slip."""

    @rule("CA-INKIND-05")
    def test_resp_text_names_refund_of_contributions(self):
        from types import SimpleNamespace
        from taxjson.lib import in_kind as IK
        m = SimpleNamespace(kind=IK.WITHDRAWAL, plan="resp")
        text = IK.withdrawal_income(m, _CA)
        self.assertIn("refund", text)
        self.assertIn("contributions", text)
        self.assertIn("no slip", text)
        self.assertNotIn("that value is an educational", text)
        m = SimpleNamespace(kind=IK.WITHDRAWAL, plan="rrsp")
        self.assertIn("T4RSP", IK.withdrawal_income(m, _CA))


class TestInKindDeclaredDatePlanSide(unittest.TestCase):
    """TJ-0925: an INKIND line's date is the plan's acquisition date for
    the superficial-loss rule too, not the plan's later posting."""

    def _run(self, plan_day):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td), {"margin": "taxable",
                                       "plan": "sheltered"})
            (root / "inputs/margin/trades.tt").write_text(
                "BUYSELL 2025-01-02 09:30:00 SYN.TO 20 CAD 10 200 0\n"
                "BUYSELL 2025-05-05 09:30:00 SYN.TO -10 CAD 8 80 0\n"
                "INKIND 2025-06-02 SYN.TO -10 CAD 9 plan=tfsa\n")
            (root / "inputs/margin/qt.csv").write_text(
                _QH + _transfer("2025-06-02", "SYN.TO", -10, "99900001",
                                "Margin"))
            (root / "inputs/plan/qt.csv").write_text(
                _QH + _transfer(plan_day, "SYN.TO", 10, "99900002", "TFSA"))
            tj(root, "run", "--no-input")
            sb = _work(root, "sheltered_base.json") or {}
            buys = [t for t in sb.get("transactions", [])
                    if t.get("action") == "BUYSELL"
                    and t.get("account") == "plan"]
            fe = json.loads(tj(root, "form-export", "--json").stdout)
            return [str(t.get("date"))[:10] for t in buys], fe

    @rule("CA-INKIND-04")
    def test_late_posting_takes_the_declared_date(self):
        late, fe_late = self._run("2025-06-05")
        same, fe_same = self._run("2025-06-02")
        self.assertEqual(late, ["2025-06-02"])
        self.assertEqual(same, ["2025-06-02"])
        # The May 5 loss of 20 is superficial (the plan holds the shares
        # from June 2, day 28, through day 30); the contribution's loss
        # of 10 is nil (TFSA): no gain, no loss.
        self.assertAlmostEqual(fe_late.get("gain_unrounded"), 0.0)
        self.assertEqual(fe_late.get("gain_unrounded"),
                         fe_same.get("gain_unrounded"))


def _us_project(root, year, accounts, prior=None):
    root.mkdir(parents=True, exist_ok=True)
    head = ('[settings]\nlocal_timezone = "America/New_York"\n'
            f'year = {year}\ncountry = "usa"\nbase_currency = "USD"\n'
            'source_currencies = []\n')
    if prior:
        head += f'prior_year_record = "{prior}"\n'
    (root / "taxjson.toml").write_text(head + "".join(
        f'[accounts.{a}]\ntype = "taxable"\n' for a in accounts))
    (root / "ticker.map").write_text("")
    for a in accounts:
        (root / "inputs" / a).mkdir(parents=True, exist_ok=True)
    return root


def _handoff(top, prev_books, now_books):
    """Close 2024 with `prev_books` ({account: .tt text}), then run 2025
    with `now_books` and return its `handoff --json` (rc, doc)."""
    prev = _us_project(top / "2024", 2024, list(prev_books))
    for a, text in prev_books.items():
        (prev / "inputs" / a / "trades.tt").write_text(text)
    tj(prev, "run", "--no-input")
    tj(prev, "close-year", "--force", "--yes")
    now = _us_project(top / "2025", 2025, list(now_books),
                      prior="../2024/filed/2024.json")
    for a, text in now_books.items():
        (now / "inputs" / a / "trades.tt").write_text(text)
    tj(now, "run", "--no-input")
    r = tj(now, "handoff", "--json", check=False)
    try:
        doc = json.loads(r.stdout)
    except ValueError:
        doc = None
    return r, doc


class TestHandoffRenamedAccountLots(unittest.TestCase):
    """TJ-0919: renamed accounts whose lot sequences tie on date and
    units are paired by their whole lots (basis too), not by name order."""

    @staticmethod
    def _lots(acct, basis):
        return [{"account": acct, "date": "2024-01-02", "qty": 10.0,
                 "basis": basis}]

    def test_same_lots_any_name_order(self):
        from taxjson.lib.handoff import _same_lots
        rec = self._lots("a", 200.0) + self._lots("b", 100.0)
        for x, y in (("x", "y"), ("y", "x")):
            with self.subTest(order=(x, y)):
                # In account-name order, as the books list them.
                op = sorted(self._lots(x, 200.0) + self._lots(y, 100.0),
                            key=lambda lot: lot["account"])
                self.assertTrue(_same_lots(rec, op))
        op = self._lots("x", 200.0) + self._lots("y", 150.0)
        self.assertFalse(_same_lots(rec, op))

    def test_handoff_accepts_swapped_rename(self):
        with tempfile.TemporaryDirectory() as td:
            top = Path(td)
            buy = "BUYSELL 2024-01-02 09:30:00 SYN.US 10 USD {p} {c} 0\n"
            sell = "BUYSELL 2025-06-02 09:30:00 SYN.US -5 USD 30 150 0\n"
            r, doc = _handoff(
                top, {"a": buy.format(p=20, c=200),
                      "b": buy.format(p=10, c=100)},
                {"x": buy.format(p=10, c=100) + sell,
                 "y": buy.format(p=20, c=200) + sell})
            self.assertEqual(r.returncode, 0, r.stdout[-3000:]
                             + r.stderr[-2000:])


class TestHandoffRedescribedOptionLots(unittest.TestCase):
    """TJ-0920: an option the opening re-describes (SYN -> SYN.B) has its
    lots compared too: a changed acquisition date is not accepted."""

    _OLD = "SYN260116C00010000.US"
    _NEW = "SYN.B260116C00010000.US"

    def _run(self, day):
        with tempfile.TemporaryDirectory() as td:
            return _handoff(
                Path(td),
                {"margin": f"BUYSELL 2023-12-01 09:30:00 {self._OLD} 1 USD "
                           f"1 100 0\n"},
                {"margin": f"BUYSELL {day} 09:30:00 {self._NEW} 1 USD 1 "
                           f"100 0\nBUYSELL 2025-02-03 09:30:00 "
                           f"{self._NEW} -1 USD 1.5 150 0\n"})

    def test_changed_date_is_flagged(self):
        r, _doc = self._run("2024-12-02")
        self.assertEqual(r.returncode, 1, r.stdout[-3000:])
        self.assertIn(self._NEW, r.stdout + r.stderr)
        self.assertIn("2023-12-01", r.stdout + r.stderr)

    def test_same_date_is_accepted(self):
        r, _doc = self._run("2023-12-01")
        self.assertEqual(r.returncode, 0, r.stdout[-3000:]
                         + r.stderr[-2000:])


class TestSimilarStillHeld(unittest.TestCase):
    """TJ-0935 / TJ-0936: the SIMILAR warning's still-held test counts
    the registered account's shares whatever another account's short
    says, and follows a dated rename of the replacement."""

    def _state(self, plan_extra="", other=""):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td), {"margin": "taxable",
                                       "plan": "sheltered",
                                       "other": "taxable"})
            (root / "inputs/margin/trades.tt").write_text(
                "BUYSELL 2025-01-02 09:30:00 SYNA.TO 10 CAD 10 100 0\n"
                "BUYSELL 2025-06-02 09:30:00 SYNA.TO -10 CAD 8 80 0\n")
            (root / "inputs/plan/trades.tt").write_text(
                "BUYSELL 2025-06-03 09:30:00 SYNB.TO 10 CAD 10 100 0\n"
                + plan_extra)
            (root / "inputs/other/trades.tt").write_text(other)
            (root / "ticker.map").write_text("SIMILAR SYNA.TO SYNB.TO\n")
            r = tj(root, "run", "--no-input", check=False)
            st = _work(root, "similar.state") or {}
            return r, st.get("findings", [])

    def test_control(self):
        _r, f = self._state()
        self.assertEqual(len(f), 1)

    def test_short_elsewhere_does_not_hide_held_shares(self):
        r, f = self._state(other="BUYSELL 2025-01-02 09:30:00 SYNB.TO -10 "
                                  "CAD 12 120 0\n")
        self.assertEqual(len(f), 1, r.stdout[-2000:] + r.stderr[-2000:])

    def test_rename_inside_the_window_keeps_the_holding(self):
        r, f = self._state(plan_extra="RENAME 2025-06-05 SYNB.TO SYNC.TO\n")
        self.assertEqual(len(f), 1, r.stdout[-2000:] + r.stderr[-2000:])

    def test_renamed_then_sold_is_not_held(self):
        r, f = self._state(plan_extra=(
            "RENAME 2025-06-05 SYNB.TO SYNC.TO\n"
            "BUYSELL 2025-06-10 09:30:00 SYNC.TO -10 CAD 10 100 0\n"))
        self.assertEqual(f, [], r.stdout[-2000:] + r.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
