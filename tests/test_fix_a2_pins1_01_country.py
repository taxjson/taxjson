"""Re-audit-2 tests-pins-01: duplicate country gates in the command
layer, each pinned by a test that fails when the gate is removed (Canadian
and US tax law never mix).

- crypto-sends' header and decision prompt (A2-0499, A2-0540);
- buy-check's still-held clause and sell-check's scope note (A2-0499);
- the run's reports/wash_radar_*.json sidecars (A2-0547);
- audit: rule labels, wash title, deferral destination, the US equity
  per-account basis and standalone taxjson-audit's default date basis
  (A2-0540); US crypto --no-wash and no crypto blend (A2-0862);
- carryover's US per-account basis, check-filed's US crypto recompute and
  wash-sales --explain's Canada crypto blend (A2-0862).

Synthetic data only.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from tax_rules import rule, rule_absent
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


REPO_ROOT = Path(__file__).resolve().parent.parent
COUNTRIES = ("canada", "usa")


def _cli(root, *args, home=None):
    env = dict(os.environ, TAXJSON_OFFLINE="1", NO_COLOR="1")
    if home is not None:
        env.update(HOME=str(home), TAXJSON_LOCAL_TZ="America/Toronto")
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True, env=env,
        stdin=subprocess.DEVNULL)


def _project(root, country, accounts, year=2025):
    """accounts: {name: (.tt text, crypto?)}; symbols written with the
    {S} suffix (TO / US) and {C} currency placeholders."""
    cur = "USD" if country == "usa" else "CAD"
    suf = "US" if country == "usa" else "TO"
    t = (f'[settings]\nlocal_timezone = "America/Toronto"\nyear = {year}\ncountry = "{country}"\n'
         f'base_currency = "{cur}"\nsource_currencies = []\n'
         + ('option_grant_timing_since = 2025\n'
            if country == "canada" else ""))
    for name, (tt, crypto) in accounts.items():
        t += f'[accounts.{name}]\ntype = "taxable"\n'
        if crypto:
            t += "crypto = true\n"
        (root / "inputs" / name).mkdir(parents=True)
        (root / "inputs" / name / "x.tt").write_text(
            tt.replace("{S}", suf).replace("{C}", cur))
    (root / "taxjson.toml").write_text(t)


def _run(tc, root):
    r = _cli(root, "run", "--no-input")
    tc.assertEqual(r.returncode, 0, r.stdout[-1500:] + r.stderr[-1500:])


# ------------------------------------------------------------ crypto-sends
class TestCryptoSendsWordingByCountry(unittest.TestCase):
    @rule("CA-CRYPTO-07")
    @rule_absent("CA-CRYPTO-07", country="usa")
    @rule("US-SEND-02")
    def test_header_and_decision_prompt(self):
        import test_fix_sends as T
        out = {}
        for c in COUNTRIES:
            with tempfile.TemporaryDirectory() as td:
                root, home = T._project(td, country=c)
                if c == "usa":
                    (root / "taxjson.toml").write_text(
                        '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2026\ncountry = "usa"\n'
                        'base_currency = "USD"\n'
                        'source_currencies = ["CAD"]\n'
                        '[accounts.crypto]\ntype = "taxable"\n'
                        'crypto = true\n')
                    T._cad_usd_rates_file(root / "work" / "to_base.csv")
                    (root / "inputs" / "crypto" / "cb_2025.csv").unlink()
                r = _cli(root, "run", "--no-input", home=home)
                self.assertEqual(r.returncode, 0, r.stderr[-1500:])
                out[c] = _cli(root, "crypto-sends", "crypto", home=home)
                out[c + "_d"] = _cli(root, "crypto-sends", "crypto",
                                     "--details", home=home)
        # The default view's legend is one short line; --details keeps
        # the sentence (docs/output-style.md, Essentials first).
        self.assertIn("gift or payment: a disposition at fair market "
                      "value", out["canada"].stdout)
        self.assertIn("self: a move to your own wallet (or a gift), no "
                      "sale", out["usa"].stdout)
        self.assertNotIn("gift or payment", out["usa"].stdout)
        ca, us = out["canada_d"].stdout, out["usa_d"].stdout
        self.assertIn("a gift or a payment is a disposition at fair market "
                      "value", ca)
        self.assertNotIn("not a sale for a US donor", ca)
        self.assertIn("ID=self|gift|payment", ca)
        self.assertIn("A gift is not a sale for a US donor: record it as "
                      "self", us)
        self.assertNotIn("a gift or a payment is a disposition", us)
        self.assertIn("ID=self|payment ", us)


# ---------------------------------------------------- radar-based checks
def _wash_book():
    """A 500 loss on ZZZ 8 days ago with 50 rebought in another taxable
    account 5 days ago; a 500 loss on YYY with no rebuy."""
    t = date.today()
    d = lambda n: (t + timedelta(days=n)).isoformat()  # noqa: E731
    return {
        "margin": (
            f"BUYSELL {d(-30)} 10:00:00 ZZZ.{{S}} 100 {{C}} 20 2000 0\n"
            f"BUYSELL {d(-8)} 10:00:00 ZZZ.{{S}} -100 {{C}} 15 1500 0\n"
            f"BUYSELL {d(-30)} 10:00:00 YYY.{{S}} 100 {{C}} 20 2000 0\n"
            f"BUYSELL {d(-8)} 10:00:00 YYY.{{S}} -100 {{C}} 15 1500 0\n",
            False),
        "cash": (f"BUYSELL {d(-5)} 10:00:00 ZZZ.{{S}} 50 {{C}} 16 800 0\n",
                 False),
    }


class TestRadarWordingByCountry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.roots = {}
        for c in COUNTRIES:
            root = Path(cls._tmp.name) / c
            _project(root, c, _wash_book(), year=date.today().year)
            r = _cli(root, "run", "--no-input")
            assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
            cls.roots[c] = root

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _out(self, c, *args):
        r = _cli(self.roots[c], *args)
        self.assertIn(r.returncode, (0, 1), r.stderr[-1500:])
        return r.stdout

    @rule("CA-SL-02")
    @rule_absent("CA-SL-02", country="usa")
    @rule("US-WASH-06")
    def test_buy_check_still_held_clause(self):
        # A2-0499: the s.54 still-held clause is Canadian; §1091 has none.
        ca = self._out("canada", "buy-check", "YYY.TO")
        us = self._out("usa", "buy-check", "YYY.US")
        self.assertIn("COOLING", ca)
        self.assertIn("COOLING", us)
        self.assertIn("if you still hold the shares 30 days after", ca)
        self.assertNotIn("still hold", us)
        self.assertIn("PERMANENT if bought in an IRA", us)

    @rule("CA-SL-04")
    @rule_absent("CA-SL-04", country="usa")
    @rule("US-WASH-04")
    def test_sell_check_scope_note(self):
        # A2-0499: the US sell-check carried ITA s.251.1.
        ca = self._out("canada", "sell-check", "ZZZ.TO")
        us = self._out("usa", "sell-check", "ZZZ.US")
        self.assertIn("ITA s.251.1", ca)
        self.assertNotIn("251.1", us)
        self.assertIn("(§1091; IRS Pub. 550)", us)

    @rule("CA-SL-02")
    @rule_absent("CA-SL-02", country="usa")
    @rule("US-WASH-01")
    def test_run_radar_sidecars_carry_the_country(self):
        # A2-0547: the run's per-account and COMBINED sidecars.
        for c in COUNTRIES:
            rep = self.roots[c] / "reports"
            for name in ("margin", "cash", "COMBINED"):
                doc = json.loads((rep / f"wash_radar_{name}.json")
                                 .read_text())
                self.assertEqual(doc["country"], c, (c, name))
        cats = {}
        for c in COUNTRIES:
            doc = json.loads((self.roots[c] / "reports"
                              / "wash_radar_COMBINED.json").read_text())
            cats[c] = {x["ticker"]: sec["category"]
                       for sec in doc["sections"] for x in sec["rows"]}
        self.assertEqual(cats["canada"]["ZZZ.TO"], "VIOLATION")
        self.assertEqual(cats["usa"]["ZZZ.US"], "WASHED")
        us_rpt = (self.roots["usa"] / "reports"
                  / "wash_radar_COMBINED.rpt").read_text()
        self.assertIsNone(re.search(r"\bITA\b|s\.54\b|251\.1", us_rpt))

    @rule("CA-SL-01")
    @rule_absent("CA-SL-01", country="usa")
    @rule("US-WASH-01")
    def test_audit_labels(self):
        # A2-0540: rule text, wash title and the deferral destination.
        ca = self._out("canada", "audit")
        us = self._out("usa", "audit")
        for txt in ("ACB pool — ITA s.47", "superficial loss — s.40(2)(g)",
                    "replacement lot's ACB (s.53(1)(f))"):
            self.assertIn(txt, ca)
            self.assertNotIn(txt, us)
        for txt in ("FIFO lots — IRC, wash sale §1091", "wash sale — §1091",
                    "§1223(3)"):
            self.assertIn(txt, us)
            self.assertNotIn(txt, ca)


# ------------------------------------------- per-account basis (US equity)
class TestUsPerAccountBasisInRecomputes(unittest.TestCase):
    """Account a sells 50 of its own 100 (cost 100); account b holds an
    older lot at 200. US FIFO is per account (+2,500); Canada pools the
    two accounts at an average 150 (0)."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.roots = {}
        for c in COUNTRIES:
            root = Path(cls._tmp.name) / c
            _project(root, c, {
                "a": ("BUYSELL 2024-02-12 10:00:00 ZZZ.{S} 100 {C} 100 "
                      "10000 0\n"
                      "BUYSELL 2025-06-02 10:00:00 ZZZ.{S} -50 {C} 150 "
                      "7500 0\n", False),
                "b": ("BUYSELL 2024-01-05 10:00:00 ZZZ.{S} 100 {C} 200 "
                      "20000 0\n", False)})
            r = _cli(root, "run", "--no-input")
            assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
            cls.roots[c] = root

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    @rule("US-BASIS-01")
    @rule_absent("US-BASIS-01", country="canada")
    @rule("CA-ACB-01")
    def test_audit_and_carryover(self):
        want = {"canada": 0.0, "usa": 2500.0}
        for c in COUNTRIES:
            a = _cli(self.roots[c], "audit", "--json")
            self.assertEqual(a.returncode, 0, a.stdout[-1500:])
            doc = json.loads(a.stdout)
            self.assertFalse(doc["failed"], c)
            self.assertAlmostEqual(doc["total_gain"], want[c], places=2)
            co = _cli(self.roots[c], "carryover", "--json")
            self.assertEqual(co.returncode, 0, co.stderr[-1500:])
            row = {x["year"]: x for x in json.loads(co.stdout)["rows"]}[2025]
            self.assertAlmostEqual(row["net_gain"], want[c], places=2)


# ---------------------------------------------------------------- crypto
_CRYPTO_US = {
    "c1": ("BUYSELL 2024-02-12 10:00:00 BTC 1 USD 30000 30000 0\n"
           "BUYSELL 2025-06-02 10:00:00 BTC -0.5 USD 40000 20000 0\n"
           "BUYSELL 2025-08-01 10:00:00 BTC -0.25 USD 20000 5000 0\n"
           "BUYSELL 2025-08-10 10:00:00 BTC 0.25 USD 21000 5250 0\n", True),
    "c2": ("BUYSELL 2024-01-05 10:00:00 BTC 1 USD 60000 60000 0\n", True),
}


class TestUsCryptoRecomputes(unittest.TestCase):
    @rule("US-WASH-13")
    def test_audit_and_check_filed_neither_wash_nor_blend(self):
        # A2-0862: c1's own FIFO: +5,000 then -2,500, no wash (crypto),
        # no pooling with c2's older, dearer coin: 2,500.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _project(root, "usa", _CRYPTO_US)
            _run(self, root)
            a = _cli(root, "audit", "--json")
            c = _cli(root, "close-year")
            self.assertEqual(c.returncode, 0, c.stderr[-1500:])
            f = _cli(root, "check-filed")
        self.assertEqual(a.returncode, 0, a.stdout[-1500:])
        doc = json.loads(a.stdout)
        self.assertFalse(doc["failed"])
        self.assertAlmostEqual(doc["total_gain"], 2500.0, places=2)
        self.assertAlmostEqual(doc["total_disallowed"], 0.0, places=2)
        self.assertEqual(f.returncode, 0, f.stdout[-1500:] + f.stderr[-800:])
        self.assertIn("filed 2025: OK", f.stdout + f.stderr)


class TestCanadaCryptoExplainBlends(unittest.TestCase):
    @rule("CA-SL-13")
    def test_two_exchange_superficial_loss_is_traced(self):
        # A2-0862: a loss on one exchange, the rebuy on the other.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _project(root, "canada", {
                "c1": ("BUYSELL 2024-02-12 10:00:00 BTC 1 CAD 30000 30000 0\n"
                       "BUYSELL 2025-08-01 10:00:00 BTC -0.5 CAD 20000 "
                       "10000 0\n", True),
                "c2": ("BUYSELL 2024-01-05 10:00:00 BTC 1 CAD 60000 60000 0\n"
                       "BUYSELL 2025-08-10 10:00:00 BTC 0.25 CAD 21000 "
                       "5250 0\n", True)})
            _run(self, root)
            r = _cli(root, "wash-sales", "--explain")
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        # The report layout (docs/output-style.md).
        self.assertIn("denied 6,250.00)", r.stdout)
        self.assertIn("Superficial loss (ITA s.54)", r.stdout)


# ------------------------------------------------- standalone audit dates
class TestStandaloneAuditDateBasis(unittest.TestCase):
    @rule("US-DATE-01")
    @rule_absent("CA-DATE-01", country="usa")
    @rule("CA-DATE-01")
    def test_default_date_basis_by_country(self):
        # A2-0540: a Dec-31 sale settling Jan 2 is a 2025 event on the US
        # trade-date default and a 2026 one on Canada's settle default.
        rows = [
            {"action": "BUYSELL", "date": "2025-03-03",
             "date_settle": "2025-03-04", "time": "10:00:00",
             "symbol": "ZZZ.US", "quantity": 100, "net_amount": -1000.0,
             "price": 10.0, "currency": "USD", "account": "margin",
             "id": "b1"},
            {"action": "BUYSELL", "date": "2025-12-31",
             "date_settle": "2026-01-02", "time": "10:00:00",
             "symbol": "ZZZ.US", "quantity": -100, "net_amount": 1200.0,
             "price": 12.0, "currency": "USD", "account": "margin",
             "id": "s1"}]
        out = {}
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "margin_base.json"
            base.write_text(json.dumps({"transactions": rows}))
            for c in COUNTRIES:
                r = subprocess.run(
                    [sys.executable, "-m", "taxjson.bin.taxjson_audit",
                     "--country", c, "--year", "2025", "--base", str(base),
                     "--summary"], cwd=REPO_ROOT, capture_output=True,
                    text=True, stdin=subprocess.DEVNULL,
                    env=dict(os.environ, NO_COLOR="1"))
                self.assertEqual(r.returncode, 0, r.stderr[-1500:])
                out[c] = re.search(r"events audited\s+(\d+)",
                                   r.stdout).group(1)
        self.assertEqual(out, {"usa": "1", "canada": "0"})


if __name__ == "__main__":
    unittest.main()
