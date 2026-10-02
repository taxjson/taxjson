"""Re-audit-2 fixes, filing-reports list (helper D): the run pipeline
(crypto-sends stage, raw pass phantoms, blended .sum diagnostics, run
state banner), `sum`/`estimate` account handling and flag guards,
instalment dates, and the estimate's Canadian-issuer test and wording.

All data is synthetic (fake account ids, invented tickers).
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule

REPO_ROOT = Path(__file__).resolve().parent.parent


def _taxjson(root, *a, env=None):
    e = {**os.environ, "TAXJSON_OFFLINE": "1", "NO_COLOR": "1",
         "PYTHONPATH": str(REPO_ROOT / "src")}
    if env:
        e.update(env)
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *a], cwd=REPO_ROOT, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, env=e)


class TestCryptoSendsStageFailure(unittest.TestCase):
    """A2-0112: a decided gift/payment the run cannot write (unpriced,
    or a malformed sends.json) is not a console-only WARNING: it reaches
    the account .sum DIAGNOSTICS, and `run --strict` stops."""

    @classmethod
    def setUpClass(cls):
        from test_fix_sends import _project, _cli, TAO_ID
        cls._cli = staticmethod(_cli)
        cls.tao = TAO_ID
        cls._td = tempfile.TemporaryDirectory()
        cls.root, cls.home = _project(cls._td.name)
        r = _cli(cls.root, cls.home, "run", "--no-input")
        if r.returncode != 0:
            raise AssertionError(r.stdout[-3000:] + r.stderr[-3000:])

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def _set_manifest(self, decision):
        man = self.root / "inputs" / "crypto" / "sends.json"
        man.write_text(json.dumps({"sends": {
            self.tao: {"decision": decision, "note": "synthetic"}}}))

    def _sum_text(self):
        return (self.root / "reports" / "crypto.sum").read_text()

    @rule("CA-CRYPTO-07")
    def test_malformed_decision_reaches_sum_and_strict_stops(self):
        self._set_manifest("Gift")
        r = self._cli(self.root, self.home, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertIn("crypto sends", self._sum_text())
        self.assertIn("'Gift'", self._sum_text())
        s = self._cli(self.root, self.home, "run", "--no-input", "--strict")
        self.assertNotEqual(s.returncode, 0)
        self.assertIn("--strict", s.stderr)
        self.assertIn("crypto sends", s.stderr)


class TestCryptoSendsUnpriced(unittest.TestCase):
    """A2-0112: a decided payment with no fair value (offline, not
    cached) is not booked; the run says so in the .sum and --strict
    stops, until the send is priced."""

    KR = ("txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,"
          "balance\n"
          "LA2AAA,RA2,2026-01-05 12:00:00,earn,reward,currency,SOL,spot,5,"
          "0,5\n"
          "LC1CCC,RC1,2026-05-06 12:00:00,withdrawal,,currency,SOL,spot,-1,"
          "0,4\n")

    @rule("CA-CRYPTO-07")
    def test_unpriced_payment_reaches_sum_and_strict_stops(self):
        from test_fix_sends import _project, _cli
        with tempfile.TemporaryDirectory() as td:
            root, home = _project(td)
            acct = root / "inputs" / "crypto"
            (acct / "cb_2025.csv").unlink()
            (acct / "kr_ledgers.csv").write_text(self.KR)
            (home / ".crypto_price_cache.json").write_text(
                json.dumps({"SOL-2026-01-05": 150.0}))
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            doc = json.loads(_cli(root, home, "crypto-sends", "crypto",
                                  "--json").stdout)
            sid = doc["accounts"]["crypto"]["sends"][0]["id"]
            (acct / "sends.json").write_text(json.dumps({"sends": {
                sid: {"decision": "payment"}}}))
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            sum_text = (root / "reports" / "crypto.sum").read_text()
            self.assertIn("no fair value for", sum_text)
            s = _cli(root, home, "run", "--no-input", "--strict")
            self.assertNotEqual(s.returncode, 0)
            self.assertIn("no fair value for", s.stderr)
            self.assertIn("--strict", s.stderr)
            # Priced by hand: booked, and the note goes away.
            p = _cli(root, home, "crypto-sends", "crypto", "--set",
                     f"{sid}=payment", "--price", "200")
            self.assertEqual(p.returncode, 0, p.stderr)
            r = _cli(root, home, "run", "--no-input", "--strict")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            sum_text = (root / "reports" / "crypto.sum").read_text()
            self.assertNotIn("no fair value for", sum_text)


QT_HDR = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
          "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
          "Account #,Activity Type,Account Type\n")


def _qt_row(date, action, sym, qty, price, net, desc="D", cur="CAD"):
    return (f"{date} 09:30:00 AM,{date} 12:00:00 AM,{action},{sym},{desc},"
            f"{qty},{price:.2f},{abs(qty) * price:.2f},0.00,{net:.2f},"
            f"{cur},55500001,Trades,Individual\n")  # pii-ok


def _qt_project(root, accounts, year=2025, country="canada", extra=""):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    cur = "CAD" if country == "canada" else "USD"
    cfg = (f'[settings]\nyear = {year}\ncountry = "{country}"\n'
           f'base_currency = "{cur}"\nsource_currencies = []\n' + extra)
    for name, body in accounts.items():
        typ = "taxable"
        if isinstance(body, tuple):
            typ, body = body
        cfg += f'[accounts.{name}]\ntype = "{typ}"\n'
        d = root / "inputs" / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "questrade.csv").write_text(QT_HDR + body)
    (root / "taxjson.toml").write_text(cfg)
    return root


class TestBlendedWashSumDiagnostics(unittest.TestCase):
    """A2-0654, A2-1117: the filing-basis <acct>_wash.sum of a blended
    account does not carry the isolated per-account pass's s.40(3)
    notes (the blended s.47 pool booked no deemed gain); the per-account
    <acct>.sum baseline keeps them."""

    ROC = "ISHARES XEI RETURN OF CAPITAL ON 10 SHS"

    def _run(self, accounts):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = _qt_project(Path(td.name) / "p", accounts)
        r = _taxjson(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        rep = root / "reports"
        return ((rep / "acctA.sum").read_text(),
                (rep / "acctA_wash.sum").read_text())

    @rule("CA-ACB-07")
    def test_a2_0654_roc_beyond_isolated_acb(self):
        pre, wash = self._run({
            "acctA": _qt_row("2025-01-10", "Buy", "XEI.TO", 10, 10.0, -100.0)
            + _qt_row("2025-03-31", "DIV", "XEI.TO", 0, 0, 150.0,
                      desc=self.ROC),
            "acctB": _qt_row("2025-02-10", "Buy", "XEI.TO", 100, 20.0,
                             -2000.0)})
        self.assertIn("s.40(3)", pre)
        self.assertNotIn("s.40(3)", wash)

    @rule("CA-ACB-07")
    def test_a2_1117_roc_on_empty_account_pool(self):
        pre, wash = self._run({
            "acctA": _qt_row("2025-01-10", "Buy", "XEI.TO", 10, 10.0, -100.0)
            + _qt_row("2025-02-10", "Sell", "XEI.TO", -10, 12.0, 120.0)
            + _qt_row("2025-03-31", "DIV", "XEI.TO", 0, 0, 5.0,
                      desc=self.ROC),
            "acctB": _qt_row("2025-01-15", "Buy", "XEI.TO", 100, 20.0,
                             -2000.0)})
        self.assertIn("EMPTY pool", pre)
        self.assertNotIn("EMPTY pool", wash)
        self.assertNotIn("s.40(3)", wash)


class TestAbortedFirstRunBanner(unittest.TestCase):
    """A2-0658: a first run that aborted after writing work/ (no .sum in
    reports/) leaves books the report commands still read; the 'not the
    clean result' banner fires for them."""

    def test_partial_books_without_reports_are_flagged(self):
        from taxjson.bin import taxjson_run as R
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "work").mkdir()
            (root / "reports").mkdir()
            cfg = {"settings": {"year": 2025, "country": "canada"},
                   "accounts": {"margin": {"type": "taxable"}}}
            self.assertEqual(R._run_state_problems(root, cfg), [])
            (root / "work" / "margin_gains.json").write_text(
                '{"transactions": [], "summary": {}}')
            probs = R._run_state_problems(root, cfg)
            self.assertEqual(len(probs), 1, probs)
            self.assertIn("did not finish", probs[0])
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                R._warn_run_state(root, cfg)
            self.assertIn("not the clean result", err.getvalue())


if __name__ == "__main__":
    unittest.main()
