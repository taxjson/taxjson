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


def _tt_project(root, accounts, *, year=2025, country="canada",
                extra=""):
    """accounts: {name: (type, tt text)}."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    cur = "CAD" if country == "canada" else "USD"
    cfg = (f'[settings]\nyear = {year}\ncountry = "{country}"\n'
           f'base_currency = "{cur}"\nsource_currencies = []\n' + extra)
    for name, (typ, tt) in accounts.items():
        cfg += f'[accounts.{name}]\ntype = "{typ}"\n'
        d = root / "inputs" / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "hist.tt").write_text(tt)
    (root / "taxjson.toml").write_text(cfg)
    return root


class TestSumUnreadableAccount(unittest.TestCase):
    """A2-1119, A2-1120: `sum` never drops an account it cannot read
    with only a warning and rc 0."""

    @rule("CA-ACB-05")
    def test_a2_1119_unreadable_sheltered_gains_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = _tt_project(Path(td) / "p", {
                "margin": ("taxable",
                           "BUYSELL 2025-01-06 10:00:00 AAA.TO 100 CAD 10.00"
                           " 1000.00 0\n"
                           "BUYSELL 2025-05-06 10:00:00 AAA.TO -100 CAD 9.00"
                           " 900.00 0\n"),
                "rrsp": ("sheltered",
                         "BUYSELL 2025-01-06 10:00:00 BBB.TO 100 CAD 10.00"
                         " 1000.00 0\n"
                         "BUYSELL 2025-05-06 10:00:00 BBB.TO -100 CAD 15.00"
                         " 1500.00 0\n")})
            r = _taxjson(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            ok = _taxjson(root, "sum")
            self.assertEqual(ok.returncode, 0, ok.stderr[-2000:])
            for f in (root / "work").glob("rrsp_gains*.json"):
                f.write_text(f.read_text()[:40])
            s = _taxjson(root, "sum")
            self.assertNotEqual(s.returncode, 0, s.stdout[-2000:])
            self.assertIn("rrsp", s.stderr)

    @rule("US-RPT-01")
    def test_a2_1120_us_entry_without_term_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = _tt_project(Path(td) / "p", {
                "brkA": ("taxable",
                         "BUYSELL 2025-01-06 10:00:00 AAA.US 100 USD 10.00"
                         " 1000.00 0\n"
                         "BUYSELL 2025-05-06 10:00:00 AAA.US -100 USD 15.00"
                         " 1500.00 0\n"),
                "brkB": ("taxable",
                         "BUYSELL 2025-01-06 10:00:00 BBB.US 100 USD 10.00"
                         " 1000.00 0\n"
                         "BUYSELL 2025-05-06 10:00:00 BBB.US -100 USD 30.00"
                         " 3000.00 0\n")}, country="usa")
            r = _taxjson(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            for f in (root / "work").glob("brkB_gains*.json"):
                doc = json.loads(f.read_text())
                for t in doc.get("transactions", []):
                    t.pop("term", None)
                f.write_text(json.dumps(doc))
            s = _taxjson(root, "sum")
            self.assertNotEqual(s.returncode, 0, s.stdout[-2000:])
            self.assertIn("brkB", s.stderr)


class TestEstimateFlagGuards(unittest.TestCase):
    """A2-1122, A2-1123: the estimate's flag guards are the ones that
    fire (named flag, before the province check), so a test can tell
    them from the later library checks."""

    def _project(self, tmp, province=True):
        root = Path(tmp)
        (root / "work").mkdir()
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = []\n'
            + ('province = "ON"\n' if province else "")
            + '[accounts.margin]\ntype = "taxable"\n')
        (root / "work" / "margin_gains.json").write_text(json.dumps(
            {"summary": {"year": "2025"}, "transactions": [
                {"date": "2025-03-01", "symbol": "AAA.TO", "qty": 10,
                 "proceeds": 500.0, "cost": 1000.0, "gain": -500.0,
                 "currency": "CAD", "days_held": 30}]}))
        return root

    def test_a2_1122_deductions_flag_checked_before_province(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp, province=False)
            for flag in ("--deductions", "--carrying-charges"):
                for v in ("inf", "-5", "nan"):
                    r = _taxjson(root, "estimate", flag, v)
                    self.assertNotEqual(r.returncode, 0)
                    self.assertIn(f"{flag} must be a non-negative finite "
                                  f"number", r.stderr)
                    self.assertNotIn("needs a province", r.stderr)

    def test_a2_1123_other_income_flags_one_guard_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._project(tmp, province=False)
            for cmd in ("sum", "estimate"):
                for flag, v in (("--other-income", "nan"),
                                ("--other-losses", "-5"),
                                ("--other-income", "inf")):
                    r = _taxjson(root, cmd, flag, v)
                    self.assertNotEqual(r.returncode, 0)
                    self.assertIn(f"{flag} must be a non-negative finite "
                                  f"number", r.stderr)
            (root / "taxjson.toml").write_text(
                (root / "taxjson.toml").read_text()
                + "[estimate]\nother_losses = -100\n")
            r = _taxjson(root, "estimate")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("[estimate] other_losses must be a non-negative "
                          "finite number", r.stderr)


class TestInstalmentPrepayment(unittest.TestCase):
    """A2-0648: a December prepayment of next year's instalments is
    accepted when its row names the tax year (credited from Jan 1, as
    the interest model already does); an undesignated prior-year date
    still refuses (the year-rollover trap)."""

    def _run(self, paid, year=2025):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        (root / "taxjson.toml").write_text(
            f'[settings]\nyear = {year}\ncountry = "canada"\n'
            'base_currency = "CAD"\nprovince = "ON"\n'
            '[accounts.margin]\ntype = "taxable"\n'
            '[instalments]\nbasis = "prior_year"\n'
            'prior_year_net_tax = 20000\nprescribed_rate = 0.08\n'
            f'paid = [{paid}]\n')
        (root / "work").mkdir()
        (root / "work" / "margin_gains.json").write_text(json.dumps(
            {"summary": {"year": year}, "transactions": [],
             "inventory": [], "wash_sales": []}))
        return _taxjson(root, "instalments", "--json")

    @rule("CA-RPT-11")
    def test_designated_december_prepayment_counts_from_jan1(self):
        pre = self._run('{ date = "2024-12-20", amount = 5000, '
                        'tax_year = 2025 }')
        self.assertEqual(pre.returncode, 0, pre.stderr)
        jan = self._run('{ date = "2025-01-01", amount = 5000 }')
        self.assertEqual(jan.returncode, 0, jan.stderr)
        a, b = json.loads(pre.stdout), json.loads(jan.stdout)
        self.assertEqual(json.dumps(a.get("interest"), sort_keys=True),
                         json.dumps(b.get("interest"), sort_keys=True))
        self.assertIn("2024-12-20", pre.stdout)

    @rule("CA-RPT-11")
    def test_undesignated_prior_year_payment_still_refuses(self):
        r = self._run('{ date = "2024-12-20", amount = 5000 }')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("outside tax year 2025", r.stderr)
        self.assertIn("tax_year = 2025", r.stderr)
        # A designation for another year is refused too.
        r = self._run('{ date = "2024-12-20", amount = 5000, '
                      'tax_year = 2024 }')
        self.assertNotEqual(r.returncode, 0)
        # Only a payment before Jan 1 of the year may be designated.
        r = self._run('{ date = "2026-06-01", amount = 5000, '
                      'tax_year = 2025 }')
        self.assertNotEqual(r.returncode, 0)


def _estimate_project(tmp, rows, base_rows=None, *, country="canada"):
    """A project with a hand-written gains file (and base book) for one
    taxable account; `taxjson estimate --json` reads them."""
    root = Path(tmp)
    (root / "work").mkdir()
    cur = "CAD" if country == "canada" else "USD"
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = 2025\ncountry = "{country}"\n'
        f'base_currency = "{cur}"\nsource_currencies = []\n'
        + ('province = "ON"\n' if country == "canada" else "")
        + '[accounts.margin]\ntype = "taxable"\n')
    (root / "work" / "margin_gains.json").write_text(json.dumps(
        {"summary": {"year": 2025}, "transactions": rows,
         "inventory": [], "wash_sales": []}))
    if base_rows is not None:
        (root / "work" / "margin_base.json").write_text(json.dumps(
            {"transactions": base_rows}))
    return root


def _div_rows(sym, issuer, action="DIVIDEND"):
    g = {"date": "2025-06-30", "date_settle": "2025-06-30",
         "symbol": sym, "qty": 0.0, "currency": "CAD", "gain": 0.0,
         "cost": 0.0, "proceeds": 0.0, "dividend": 4000.0,
         "account": "margin", "id": "d1", "action": action}
    if action == "DIVIDEND_IN_LIEU":
        g.update(pil=0.0, deemed_dividend="ITA s.260")
    b = {"action": action, "date": "2025-06-30", "time": "10:00:00",
         "date_settle": "2025-06-30", "symbol": sym, "quantity": 0.0,
         "price": 0.0, "net_amount": 4000.0, "gross_amount": 4000.0,
         "currency": "CAD", "account": "margin", "id": "d1",
         "dealer_country": "CA"}
    if issuer:
        b["issuer_country"] = issuer
    return [g], [b]


class TestEstimateCanadianIssuer(unittest.TestCase):
    """A2-0319, A2-0662: the Canada estimate decides a dividend's
    issuer like the engine (income_dating.is_canadian_issuer: the CA
    ISIN, else the listing) — a Canadian issuer on a US listing is an
    eligible dividend with no assumed foreign tax credit."""

    def _est(self, sym, issuer, action="DIVIDEND"):
        g, b = _div_rows(sym, issuer, action)
        with tempfile.TemporaryDirectory() as td:
            root = _estimate_project(td, g, b)
            r = _taxjson(root, "estimate", "--other-income", "100000",
                         "--json")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            return json.loads(r.stdout)["estimate"]

    @rule("CA-RPT-04")
    def test_a2_0319_ca_isin_on_us_listing_is_eligible(self):
        to = self._est("ZQX.TO", "")
        us_ca = self._est("ZQX.US", "CA")
        us_us = self._est("ZQX.US", "US")
        self.assertEqual(us_ca["ftc_assumed"], 0.0)
        self.assertAlmostEqual(us_ca["grossed_eligible"],
                               to["grossed_eligible"], places=2)
        self.assertAlmostEqual(us_ca["estimated_tax"], to["estimated_tax"],
                               places=2)
        # A US issuer stays foreign (the control).
        self.assertGreater(us_us["ftc_assumed"], 0.0)
        self.assertEqual(us_us["grossed_eligible"], 0.0)

    @rule("CA-INC-03")
    def test_a2_0662_s260_pil_ca_isin_us_listing_is_eligible(self):
        to = self._est("ZQX.TO", "", "DIVIDEND_IN_LIEU")
        us_ca = self._est("ZQX.US", "CA", "DIVIDEND_IN_LIEU")
        self.assertEqual(us_ca["ftc_assumed"], 0.0)
        self.assertAlmostEqual(us_ca["grossed_eligible"],
                               to["grossed_eligible"], places=2)


class TestUsEstimateSection1256(unittest.TestCase):
    """A2-1124: the US estimate says when §1256 P/L (futures, index
    options) is in its short-term figure — taxed as short-term, the
    60/40 split not modelled — and its Assumes line says so."""

    def _rows(self, sym, gain):
        return {"date": "2025-03-03", "date_settle": "2025-03-04",
                "symbol": sym, "qty": -1, "currency": "USD",
                "proceeds": 1000.0 + gain, "cost": 1000.0, "gain": gain,
                "days_held": 10, "term": "SHORT_TERM", "account": "margin",
                "action": "BUYSELL"}

    @rule("US-FUT-02")
    def test_futures_pl_named_in_us_estimate(self):
        rows = [self._rows("F:ESH5", 15000.0),
                self._rows("AAA.US", 500.0)]
        with tempfile.TemporaryDirectory() as td:
            root = _estimate_project(td, rows, country="usa")
            j = _taxjson(root, "estimate", "--other-income", "100000",
                         "--json")
            self.assertEqual(j.returncode, 0, j.stderr[-2000:])
            est = json.loads(j.stdout)["estimate"]
            self.assertAlmostEqual(est["section_1256_gain"], 15000.0,
                                   places=2)
            self.assertTrue(any("60/40" in n for n in est["notes"]),
                            est["notes"])
            t = _taxjson(root, "estimate", "--other-income", "100000")
            self.assertEqual(t.returncode, 0, t.stderr[-2000:])
            self.assertIn("§1256", t.stdout)
            self.assertIn("60/40", t.stdout)

    @rule("US-FUT-02")
    def test_no_section_1256_no_note(self):
        with tempfile.TemporaryDirectory() as td:
            root = _estimate_project(td, [self._rows("AAA.US", 500.0)],
                                     country="usa")
            j = _taxjson(root, "estimate", "--other-income", "100000",
                         "--json")
            self.assertEqual(j.returncode, 0, j.stderr[-2000:])
            est = json.loads(j.stdout)["estimate"]
            self.assertEqual(est.get("section_1256_gain", 0.0), 0.0)
            self.assertFalse(any("60/40" in n
                                 for n in est.get("notes") or []))


class TestInterestAndSlipWording(unittest.TestCase):
    """A2-0644, A2-1153, A2-1101: the estimate, the checklist and
    docs/filing.md no longer say no taxjson output totals interest (each
    .sum prints a NET 'CASH INTEREST' line), and the Canada Assumes line
    says mapped T5 box 18 amounts are included."""

    @rule("CA-RPT-06")
    def test_ca_assumptions_point_at_cash_interest_and_box18(self):
        from taxjson.lib.tax_estimate import CA_ASSUMPTIONS
        self.assertNotIn("no taxjson view totals", CA_ASSUMPTIONS)
        self.assertIn("CASH INTEREST", CA_ASSUMPTIONS)
        self.assertNotIn("capital gains on T3/T5 slips are not included",
                         CA_ASSUMPTIONS)
        self.assertIn("T5 box 18 amounts not named in "
                      "capital_gains_dividends.map", CA_ASSUMPTIONS)

    def test_checklist_and_filing_doc_wording(self):
        import inspect
        from taxjson.lib import checklist
        src = inspect.getsource(checklist)
        self.assertNotIn("command totals the interest", src)
        self.assertNotIn("command totals it", src)
        self.assertEqual(src.count("CASH INTEREST"), 2)
        doc = (REPO_ROOT / "docs" / "filing.md").read_text()
        self.assertNotIn("no taxjson command totals", doc)
        self.assertIn("CASH INTEREST", doc)


if __name__ == "__main__":
    unittest.main()
