"""Re-audit-2 tests-tagging round (filing / estimate area): the estimate
callers' routing of each income kind, pinned through the code that
builds the estimator's inputs (every figure synthetic)."""
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule

REPO_ROOT = Path(__file__).resolve().parent.parent


def _us(est, **kw):
    from taxjson.bin.taxjson_run import _tax_estimate_result
    cfg = {"settings": {"country": "usa", "year": 2025}}
    base = {"realized": 0.0, "st": 0.0, "lt": 0.0, "div_ca": 0.0,
            "div_foreign": 0.0, "pil": 0.0, "staking": 0.0}
    base.update(est)
    with contextlib.redirect_stderr(io.StringIO()):
        return _tax_estimate_result(cfg, base, other_income=60000.0,
                                    other_losses=0.0, province=None, **kw)


def _usa(**kw):
    from taxjson.lib.tax_estimate import estimate_usa
    args = dict(st=0.0, lt=0.0, qualified_div=0.0, pil=0.0,
                other_income=60000.0, other_losses=0.0, year=2025)
    args.update(kw)
    return estimate_usa(**args)["estimated_tax"]


class TestUsEstimateRouting(unittest.TestCase):
    """A2-0835, A2-1504 (US-RPT-07): payments in lieu and staking are
    ordinary income, gains with no term are short-term — as the caller
    builds the estimator's inputs, not only inside the estimator."""

    @rule("US-RPT-07")
    def test_payment_in_lieu_is_ordinary(self):
        got = _us({"pil": 10000.0})["estimated_tax"]
        self.assertEqual(got, _usa(pil=10000.0))
        self.assertNotEqual(got, _usa(qualified_div=10000.0))

    @rule("US-RPT-07")
    def test_staking_is_ordinary(self):
        got = _us({"staking": 10000.0})["estimated_tax"]
        self.assertEqual(got, _usa(pil=10000.0))
        self.assertNotEqual(got, _usa(qualified_div=10000.0))

    @rule("US-RPT-07")
    def test_gains_with_no_term_are_short_term(self):
        r = _us({"realized": 10000.0})
        self.assertEqual(r["st_input"], 10000.0)
        self.assertEqual(r["estimated_tax"], _usa(st=10000.0))
        self.assertGreater(r["estimated_tax"], _usa())


class TestCanadaEstimatePaymentInLieu(unittest.TestCase):
    """A2-0490 (CA-INC-03): a payment in lieu reaches the estimate as
    ordinary income, never as a foreign dividend with a 15% credit."""

    def _project(self, tmp):
        root = Path(tmp)
        (root / "work").mkdir()
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2026\ncountry = "canada"\n'
            'base_currency = "CAD"\nprovince = "ON"\n'
            '[accounts.margin]\ntype = "taxable"\n')
        (root / "work" / "margin_gains.json").write_text(json.dumps({
            "summary": {"year": "2026"}, "transactions": [
                {"action": "DIVIDEND_IN_LIEU", "symbol": "KO.US",
                 "pil": 4000.0, "currency": "CAD"}]}))
        return root

    @rule("CA-INC-03", "CA-RPT-03")
    def test_pil_is_ordinary_income_in_the_estimate(self):
        from taxjson.lib.tax_estimate import estimate_canada
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
                 str(self._project(tmp)), "sum", "--json",
                 "--other-income", "80000"],
                cwd=REPO_ROOT, capture_output=True, text=True,
                stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads(r.stdout)["estimate"]["estimated_tax"]
        common = dict(realized=0.0, eligible_div=0.0, year=2026,
                      other_income=80000.0, other_losses=0.0,
                      province="ON")
        want = estimate_canada(foreign_div=0.0, pil=4000.0, **common)
        self.assertEqual(got, want["estimated_tax"])
        wrong = estimate_canada(foreign_div=4000.0, pil=0.0, **common)
        self.assertNotEqual(got, wrong["estimated_tax"])



def _etx(acct, date, settle, sym, qty):
    return {"account": acct, "action": "BUYSELL", "date": date,
            "date_settle": settle, "time": "10:00:00", "symbol": sym,
            "quantity": qty, "net_amount": 0.0, "currency": "CAD",
            "id": f"{acct}-{sym}-{date}-{qty}"}


def _egain(date, settle, sym, qty, raw, denied=0.0, perm=0.0):
    return {"account": "margin", "date": date, "date_settle": settle,
            "symbol": sym, "qty": qty, "gain": raw + denied,
            "raw_gain": raw, "disallowed_amount": denied,
            "permanently_disallowed": perm, "proceeds": 1000.0,
            "cost": 1000.0 - raw, "id": f"g-{sym}-{date}"}


class TestEdgeCasesCountryGates(unittest.TestCase):
    """A2-0855, A2-1495: every country gate in edge-cases, on one book
    read as a Canadian and as a US project (CA-RPT-07 / US-RPT-05)."""

    CALL = "ABC260116C00010000.US"

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        root = cls.root = Path(cls._td.name)
        work = root / "work"
        work.mkdir()
        margin = [
            # Straddles: a closing sale and an option write.
            _etx("margin", "2025-01-02", "2025-01-03", "XYZ.US", 100),
            _etx("margin", "2025-12-31", "2026-01-02", "XYZ.US", -100),
            _etx("margin", "2025-12-31", "2026-01-02",
                 "ZZW260320C00050000.US", -1),
            # A written option left to expire in the year.
            _etx("margin", "2025-06-02", "2025-06-03",
                 "ZZE251231C00050000.US", -1),
            # ABC: loss settling 03-04; a rebuy sold on day 30 and a long
            # call bought on day 30.
            _etx("margin", "2025-01-10", "2025-01-13", "ABC.US", 100),
            _etx("margin", "2025-03-03", "2025-03-04", "ABC.US", -100),
            _etx("margin", "2025-03-10", "2025-03-11", "ABC.US", 50),
            _etx("margin", "2025-04-02", "2025-04-03", "ABC.US", -50),
            _etx("margin", "2025-04-02", "2025-04-03", cls.CALL, 1),
            # DEF / GHI: losses whose window crosses the year end.
            _etx("margin", "2025-06-02", "2025-06-03", "DEF.US", 10),
            _etx("margin", "2025-12-19", "2025-12-22", "DEF.US", -10),
            _etx("margin", "2025-06-02", "2025-06-03", "GHI.US", 10),
            _etx("margin", "2025-12-19", "2025-12-22", "GHI.US", -10),
            _etx("margin", "2026-01-06", "2026-01-07", "GHI.US", 10),
            _etx("margin", "2026-01-08", "2026-01-09", "GHI.US", -10),
        ]
        plan = [
            _etx("plan", "2025-04-01", "2025-04-02", "ABC.US", 5),
            _etx("plan", "2026-01-05", "2026-01-06", "DEF.US", 10),
            _etx("plan", "2025-12-31", "2026-01-02", "QQQ.US", 5),
        ]
        (work / "margin_base.json").write_text(json.dumps(margin))
        (work / "plan_base.json").write_text(json.dumps(plan))
        (work / "margin_gains_wash.json").write_text(json.dumps({
            "transactions": [
                _egain("2025-12-31", "2026-01-02", "XYZ.US", 100, 500.0),
                _egain("2025-03-03", "2025-03-04", "ABC.US", 100, -300.0,
                       denied=15.0, perm=15.0),
                _egain("2025-12-19", "2025-12-22", "DEF.US", 10, -100.0,
                       denied=100.0, perm=100.0),
                _egain("2025-12-19", "2025-12-22", "GHI.US", 10, -100.0)]}))
        cls.out = {}
        from taxjson.lib.edge_cases import analyze, render_text
        for c, tax_date in (("canada", "settle"), ("usa", "settle")):
            cfg = {"settings": {"year": 2025, "country": c,
                                "tax_date": tax_date,
                                "option_premium_timing": "grant",
                                "option_grant_timing_since": 2025}
                   if c == "canada" else
                   {"year": 2025, "country": c, "tax_date": tax_date},
                   "accounts": {"margin": {"type": "taxable"},
                                "plan": {"type": "sheltered"}}}
            with contextlib.redirect_stderr(io.StringIO()):
                doc = analyze(root, cfg)
            cls.out[c] = "\n".join(render_text(doc, verbose=True))

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    @rule("CA-RPT-07")
    @rule("US-RPT-05")
    def test_registered_account_words(self):
        ca, us = self.out["canada"], self.out["usa"]
        self.assertIn("registered account: no tax effect", ca)
        self.assertIn("IRA: no tax effect", us)
        self.assertNotIn("IRA", ca)
        self.assertNotIn("registered", us)
        self.assertIn("(registered)", ca)
        self.assertIn("(IRA)", us)
        self.assertIn("registered replacement", ca)
        self.assertIn("IRA replacement", us)

    @rule("CA-RPT-07")
    @rule("US-RPT-05")
    def test_option_premium_words(self):
        ca, us = self.out["canada"], self.out["usa"]
        self.assertIn("§1234", us)
        self.assertNotIn("§1234", ca)
        self.assertIn("under grant timing the premium is a gain", ca)
        self.assertIn("under grant timing the premium was already a gain",
                      ca)
        self.assertNotIn("grant timing", us)

    @rule("CA-RPT-07")
    @rule("US-RPT-05")
    def test_allowed_verdict_and_header(self):
        ca, us = self.out["canada"], self.out["usa"]
        self.assertIn("not still held on day 30", ca)
        self.assertIn("matched none of those purchases", us)
        self.assertNotIn("matched none of those purchases", ca)
        self.assertIn("(settlement date, CRA)", ca)
        self.assertIn("(settlement date)", us)
        self.assertNotIn("CRA", us)

    @rule("CA-RPT-07", "CA-SL-05")
    @rule("US-RPT-05")
    def test_window_items_near_day_30(self):
        # Canada lists the day-30 sale and the long call as window items;
        # the US has no still-held test and lists a call as a warning
        # only (A2-0855).
        ca, us = self.out["canada"], self.out["usa"]
        self.assertIn("sale of 50 in margin", ca)
        self.assertNotIn("sale of 50 in margin", us)
        # The call: a window item AND a calls-section line in Canada; in
        # the US the calls section's warning only.
        self.assertEqual(ca.count("long call of 1 in margin"), 2)
        self.assertEqual(us.count("long call of 1 in margin"), 1)
        self.assertIn("a warning only", us)



class TestChecklistCountryGates(unittest.TestCase):
    """A2-1494: the checklist's country words — the denial's source and
    where it goes, the disposition slips, the interest form — in a
    Canadian and a US project (CA-SL-09 / US-WASH-11, CA-RPT-01's T5008
    slips vs US-RPT-01's 1099-B, carrying charges)."""

    def _ctx(self, root, country):
        from datetime import date
        from taxjson.lib import checklist as cl
        cfg = {"settings": {"country": country, "year": 2025,
                            "base_currency": ("USD" if country == "usa"
                                              else "CAD")},
               "accounts": {"m": {"type": "taxable"}}}
        return cl.Ctx(root=root, cfg=cfg, year=2025,
                      today=date(2026, 6, 1),
                      run_sub=lambda *a, **k: (0, "", ""))

    def _wash(self, root, country, perm):
        from taxjson.lib import checklist as cl
        (root / "work").mkdir(parents=True, exist_ok=True)
        (root / "work" / "m_gains.json").write_text(json.dumps({
            "summary": {"year": 2025}, "transactions": [
                {"symbol": "XYZ.US", "date": "2025-03-03",
                 "date_settle": "2025-03-04", "qty": -10, "gain": 0.0,
                 "raw_gain": -500.0, "disallowed_amount": 500.0,
                 "permanently_disallowed": perm}]}))
        return cl.d_wash_reviewed(self._ctx(root, country)).detail

    @rule("CA-SL-09")
    @rule("US-WASH-09", "US-WASH-11")
    def test_wash_reviewed_names_each_countrys_source(self):
        with tempfile.TemporaryDirectory() as td:
            ca = self._wash(Path(td) / "ca", "canada", 500.0)
            us = self._wash(Path(td) / "us", "usa", 500.0)
            ca_d = self._wash(Path(td) / "ca2", "canada", 0.0)
            us_d = self._wash(Path(td) / "us2", "usa", 0.0)
        self.assertIn("registered-account or affiliated-person", ca)
        self.assertIn("(IRA repurchase)", us)
        self.assertNotIn("IRA", ca)
        self.assertIn("(added to ACB)", ca_d)
        self.assertIn("(added to the replacement's basis)", us_d)

    @rule("CA-RPT-01")
    @rule("US-RPT-01")
    def test_slips_and_interest_form(self):
        from taxjson.lib import checklist as cl
        with tempfile.TemporaryDirectory() as td:
            ca, us = (self._ctx(Path(td), c) for c in ("canada", "usa"))
            self.assertEqual(cl._slip_names(ca), "T5008")
            self.assertEqual(cl._slip_names(us), "1099-B")
            fca, fus = cl.d_fees(ca).detail, cl.d_fees(us).detail
        self.assertIn("line 22100", fca)
        self.assertNotIn("4952", fca)
        self.assertIn("Form 4952", fus)
        self.assertNotIn("22100", fus)


if __name__ == "__main__":
    unittest.main()
