"""Re-audit-2 tests-pins-01: filing-command fixes that held but had no
test failing when reverted.

- close-year rounds totals once across accounts (A2-0867, S031-20);
- close-year --force still refuses per-account (unblended) books
  (A2-0868, S046-01);
- the [settings] year check in the soft readers (A2-0869 / A2-0870);
- fx-cash refuses a taxable account with inputs but no native file
  (A2-0884, S046-18);
- the LEAPS view's same-stamp order follows the engine (A2-0891, S040-01);
- crypto-sends' per-year stablecoin currency gain counts only gifts and
  payments and leaves a superficial loss out (A2-0507, A2-0874);
- `taxjson audit` passes the crypto fill prices (A2-0860, R1-271).

Synthetic data only.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule


REPO_ROOT = Path(__file__).resolve().parent.parent


def _cli(root, *args, home=None):
    env = dict(os.environ, TAXJSON_OFFLINE="1", NO_COLOR="1")
    if home is not None:
        env.update(HOME=str(home), TAXJSON_LOCAL_TZ="America/Toronto")
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True, env=env,
        stdin=subprocess.DEVNULL)


def _project(root, accounts, year=2025):
    """accounts: {name: (type, .tt text)}."""
    t = (f'[settings]\nlocal_timezone = "America/Toronto"\nyear = {year}\ncountry = "canada"\n'
         f'province = "ON"\nbase_currency = "CAD"\nsource_currencies = []\n'
         f'tax_date = "settle"\noption_grant_timing_since = 2025\n')
    for name, (typ, tt) in accounts.items():
        t += f'[accounts.{name}]\ntype = "{typ}"\n'
        (root / "inputs" / name).mkdir(parents=True)
        (root / "inputs" / name / "trades.tt").write_text(tt)
    (root / "taxjson.toml").write_text(t)


def _run(tc, root, *extra):
    r = _cli(root, "run", "--no-input", *extra)
    tc.assertEqual(r.returncode, 0, r.stdout[-1500:] + r.stderr[-1500:])


class TestCloseYearRoundsOnce(unittest.TestCase):
    def test_disallowed_total_is_rounded_once_across_accounts(self):
        # A2-0867: three accounts each deny 100.0045 — the lock holds
        # 300.01 (the wash-sales total), not 3 x 100.00.
        accounts = {}
        for i, s in enumerate(("AAA", "BBB", "CCC")):
            accounts[f"a{i}"] = ("taxable", (
                f"BUYSELL 2025-03-03 10:00:00 {s}.TO 1 CAD 200.0045 "
                f"200.0045 0\n"
                f"BUYSELL 2025-06-02 10:00:00 {s}.TO -1 CAD 100 100 0\n"
                f"BUYSELL 2025-06-10 10:00:00 {s}.TO 1 CAD 100 100 0\n"))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _project(root, accounts)
            _run(self, root)
            w = _cli(root, "wash-sales", "--json")
            c = _cli(root, "close-year")
            self.assertEqual(c.returncode, 0, c.stderr[-1500:])
            lock = json.loads((root / "filed" / "2025.json").read_text())
            f = _cli(root, "check-filed")
        self.assertAlmostEqual(json.loads(w.stdout)["totals"]["denied"],
                               300.01, places=2)
        self.assertAlmostEqual(lock["totals"]["disallowed"], 300.01,
                               places=2)
        self.assertEqual(f.returncode, 0, f.stdout[-1500:] + f.stderr[-800:])


class TestCloseYearForceRefusesUnblendedBooks(unittest.TestCase):
    def test_force_does_not_lock_per_account_books(self):
        # A2-0868: books from `run --account` only (no blended pass);
        # --force overrides the run-state check but never this refusal.
        tt = ("BUYSELL 2025-03-03 10:00:00 ZZZ.TO 100 CAD 10 1000 0\n"
              "BUYSELL 2025-06-02 10:00:00 ZZZ.TO -100 CAD 12 1200 0\n")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _project(root, {"a": ("taxable", tt), "b": ("taxable", tt)})
            for acct in ("a", "b"):
                _run(self, root, "--account", acct)
            c = _cli(root, "close-year", "--force")
            locked = (root / "filed" / "2025.json").exists()
        self.assertNotEqual(c.returncode, 0, c.stdout[-1500:])
        self.assertIn("only has per-account (pre-wash, unblended) gains",
                      c.stderr)
        self.assertFalse(locked)


class TestSoftReadersCheckTheYear(unittest.TestCase):
    def test_an_implausible_year_is_refused_after_a_run(self):
        # A2-0869 / A2-0870: the books were built for 2025, then
        # [settings] year was edited. sum and divs-sum refuse instead of
        # reporting tax year 1850 / 0 / True with rc 0.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _project(root, {"margin": ("taxable",
                    "BUYSELL 2025-03-03 10:00:00 ZZZ.TO 100 CAD 10 1000 0\n"
                    "BUYSELL 2025-06-02 10:00:00 ZZZ.TO -100 CAD 12 1200 "
                    "0\n")})
            _run(self, root)
            cfg = (root / "taxjson.toml").read_text()
            for bad, msg in (("1850", "not a plausible tax year"),
                             ("0", "not a plausible tax year"),
                             ("true", "must be an integer tax year")):
                (root / "taxjson.toml").write_text(
                    cfg.replace("year = 2025", f"year = {bad}"))
                for cmd in ("sum", "divs-sum", "checklist"):
                    r = _cli(root, cmd)
                    self.assertEqual(r.returncode, 1, (bad, cmd, r.stdout[-500:]))
                    self.assertIn(msg, r.stderr, (bad, cmd))


class TestFxCashRefusesAMissingNativeFile(unittest.TestCase):
    def test_account_with_inputs_but_no_native_file(self):
        # A2-0884: fx-cash skipped the account (rc 0, a partial ledger).
        from taxjson.bin.taxjson_run import _NATIVE_TX_SUFFIXES
        tt = ("BUYSELL 2025-03-03 10:00:00 ZZZ.TO 100 CAD 10 1000 0\n"
              "BUYSELL 2025-06-02 10:00:00 ZZZ.TO -100 CAD 12 1200 0\n")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _project(root, {"margin": ("taxable", tt),
                            "cash": ("taxable", tt.replace("ZZZ", "YYY"))})
            _run(self, root)
            gone = [p for suf in _NATIVE_TX_SUFFIXES
                    for p in [root / "work" / f"cash{suf}"] if p.exists()]
            self.assertTrue(gone)
            for p in gone:
                p.unlink()
            r = _cli(root, "fx-cash")
            s = _cli(root, "sum")
        self.assertNotEqual(r.returncode, 0, r.stdout[-1000:])
        self.assertIn("no native transaction file for taxable account "
                      "'cash'", r.stderr)
        self.assertEqual(s.returncode, 0, s.stderr[-1500:])
        self.assertIn("FX-on-cash (line 15300) estimate omitted", s.stderr)


class TestLeapsViewFollowsTheEngineOrder(unittest.TestCase):
    def test_same_stamp_sell_then_buy_is_not_a_leaps_position(self):
        # A2-0891 (S040-01): a sell listed before a same-stamp buy is a
        # write and its buy-back (the engine books it SHORT), so no LEAPS
        # contract exists; buy-first is a LEAPS bought and closed.
        opt = "XXX270618C00045000.TO"
        sell = f"BUYSELL 2025-06-02 10:00:00 {opt} -1 CAD 9.00 900.00 0\n"
        buy = f"BUYSELL 2025-06-02 10:00:00 {opt} 1 CAD 8.00 800.00 0\n"
        out = {}
        for tag, tt in (("sell_first", sell + buy), ("buy_first", buy + sell)):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                _project(root, {"margin": ("taxable", tt)})
                _run(self, root)
                r = _cli(root, "leaps")
                self.assertEqual(r.returncode, 0, r.stderr[-1500:])
                out[tag] = r.stdout
        self.assertIn("No LEAPS contracts found", out["sell_first"])
        self.assertIn(opt, out["buy_first"])


def _sends_project(td):
    import test_fix_sends as T
    return T._project(td), T


@rule("CA-CRYPTO-08")
class TestCryptoSendsStablecoinFxTotal(unittest.TestCase):
    """The per-year line counts the currency gain of stablecoin GIFTS and
    PAYMENTS only, a superficial loss excluded: Kraken's 100-USDC gift
    gains 7.97; Coinbase's 10-USDC send loses 0.21 (superficial); the
    2026 Hybrid Earn sweep is a self move."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        (cls.root, cls.home), cls.T = _sends_project(cls._tmp.name)
        r = _cli(cls.root, "run", "--no-input", home=cls.home)
        assert r.returncode == 0, r.stderr[-2000:]

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _total(self, cb_decision):
        T = self.T
        for sid, dec in ((T.KR_USDC_ID, "gift"), (T.CB_USDC_ID, cb_decision),
                         (T.HYBRID_ID, "self")):
            r = _cli(self.root, "crypto-sends", "crypto", "--set",
                     f"{sid}={dec}", home=self.home)
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        r = _cli(self.root, "crypto-sends", "crypto", home=self.home)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        self.assertIn("likely SUPERFICIAL", r.stdout)
        # The per-year totals: `  YYYY:  +n.nn CAD` under the section
        # heading, then the "Superficial losses are excluded" paragraph.
        lines = r.stdout.splitlines()
        i = lines.index("STABLECOIN GIFTS AND PAYMENTS — currency gain")
        self.assertIn("Superficial losses are excluded",
                      " ".join(r.stdout.split()))
        return [ln.strip() for ln in lines[i + 1:]
                if re.match(r"  \d{4}:", ln)]

    def test_superficial_loss_is_excluded(self):
        # A2-0874: the CB gift's -0.21 is superficial, so 2025 = +7.97.
        self.assertEqual(self._total("gift"), ["2025:  +7.97 CAD"])

    def test_self_sends_are_not_counted(self):
        # A2-0507: the 2026 self sweep (-0.10, not superficial) stays out.
        self.assertEqual(self._total("self"), ["2025:  +7.97 CAD"])


class TestAuditUsesTheFillPrices(unittest.TestCase):
    @rule("CA-INC-04")
    def test_crypto_payment_from_a_fill_priced_reward_ties_out(self):
        # A2-0860 (R1-271): a coin reward with no price in the export is
        # priced by fill-crypto-prices; the 2026 payment's cost comes
        # from it. The audit recomputes with those fill prices and ties
        # out to the run.
        with tempfile.TemporaryDirectory() as td:
            (root, home), T = _sends_project(td)
            r = _cli(root, "run", "--no-input", home=home)
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            r = _cli(root, "crypto-sends", "crypto", "--set",
                     f"{T.PAY_ID}=payment", home=home)
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            r = _cli(root, "run", "--no-input", home=home)
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            self.assertTrue((root / "work" / "crypto_filled.json").exists())
            a = _cli(root, "audit", "--json", home=home)
        self.assertEqual(a.returncode, 0, a.stdout[-1500:] + a.stderr[-800:])
        self.assertFalse(json.loads(a.stdout)["failed"])


if __name__ == "__main__":
    unittest.main()
