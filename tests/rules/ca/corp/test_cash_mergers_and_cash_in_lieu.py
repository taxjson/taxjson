"""Re-audit-2 conformance-spec fixes: tax-logic states what the code does
(fix lists conformance-spec-01/02). Synthetic data only.

Each test carries the rule id it pins; a rule that differs between the
countries has a test per country.

Formerly: test_fix_a2_spec
"""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from tax_rules.dual import cli, settings_for, tx
from taxjson.rules import ids as R

from taxjson.lib import tax_logic as TL


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


def _text(country, rid, **settings):
    """The rendered text of one rule under `settings`."""
    st = dict(settings, country=country, year=2025)
    for _t, rules in TL.rule_sections(country, st):
        for r in rules:
            if r.id == rid:
                return r.text
    raise AssertionError(f"{rid} not stated for {country}")


def _project(td, country, files, *, accounts=("margin",), year=2025,
             **settings):
    root = Path(td) / country
    root.mkdir(parents=True, exist_ok=True)
    if not isinstance(accounts, dict):
        accounts = {a: "taxable" for a in accounts}
    acc = "".join(f'[accounts.{a}]\ntype = "{t}"\n'
                  for a, t in accounts.items())
    (root / "taxjson.toml").write_text(
        settings_for(country, year=year, **settings) + acc)
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


def _gains_one(country, book, sheltered=(), **req):
    """One country's pipeline.run_gains on `book` (stderr captured)."""
    from taxjson.lib.pipeline import GainsRequest, run_gains
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        res = run_gains(list(book), list(sheltered), [],
                        req=GainsRequest(country=country, taxable=True,
                                         **dict(dict(year=2025), **req)))
    res["_stderr"] = err.getvalue()
    return res


# ------------------------------------------------------------ corporate
class TestMergerCash(unittest.TestCase):
    """A2-1475 / A2-0824 (corporate half) / A2-1477: cash in a merger and
    cash in lieu of a fraction, stated per country.

    Formerly: test_fix_a2_spec.TestMergerCash"""

    CASH = ('TGT(CA0000000555) Merged(Acquisition) FOR CAD 30.00 PER '
            'SHARE (TGT, TARGET CO, CA0000000555)')
    BOTH = ('OLDC(CA0000000801) Merged(Acquisition) WITH CA0000000802 1 '
            'for 2 AND CAD 5.00 ({t}, {n}, {i})')

    def _ib(self, *rows, cur="CAD"):
        from _support.ib_corp_projects import _IB_CA, _IB_HEAD, _IB_TRADES
        head = _IB_HEAD.format(acct='U5550001')  # pii-ok: synthetic
        return (head.replace("Base Currency,CAD", f"Base Currency,{cur}")
                + _IB_TRADES + rows[0] + _IB_CA + "".join(rows[1:]))

    def _run(self, country, body):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, country, {"inputs/margin/ib_2026.csv": body},
                            year=2026)
            r = cli(root, "run", "--no-input")
            out = {"rc": r.returncode, "err": r.stderr + r.stdout}
            g = root / "work" / "margin_gains_wash.json"
            if g.exists():
                out["gains"] = json.loads(g.read_text())
            pe = root / "work" / "pending_elections.json"
            if pe.exists():
                out["pending"] = json.loads(pe.read_text())[
                    "accounts"]["margin"]["pending"]
            return out

    def _cash_takeover(self, country):
        from _support.ib_corp_projects import _ib_buy, _ib_ca
        cur, sym = (("CAD", "TGT.TO") if country == "canada"
                    else ("USD", "TGT.US"))
        desc = self.CASH.replace("CAD", cur).replace("CA0000000555",
                                                     "US0000000555")
        r = self._run(country, self._ib(
            _ib_buy('TGT', 100, 20, cur=cur),
            _ib_ca(desc, -100, -3000, proceeds=3000,
                   when='2026-06-01, 20:25:00', cur=cur), cur=cur))
        self.assertEqual(r["rc"], 0, r["err"][-2000:])
        sales = [t for t in r["gains"]["transactions"]
                 if t.get("symbol") == sym and t.get("qty")]
        self.assertEqual(len(sales), 1)
        self.assertAlmostEqual(sales[0]["proceeds"], 3000.0, places=2)
        self.assertAlmostEqual(sales[0]["gain"], 1000.0, places=2)
        self.assertEqual([i for i in r["gains"]["inventory"]
                          if abs(i["qty"]) > 1e-9], [])

    def _stock_and_cash(self, country):
        from _support.ib_corp_projects import _ib_buy, _ib_ca
        cur = "CAD" if country == "canada" else "USD"
        both = self.BOTH.replace("CAD", cur).replace("CA00", "US00")
        r = self._run(country, self._ib(
            _ib_buy('OLDC', 100, 20, cur=cur),
            _ib_ca(both.format(t='NEWC', n='NEWC CORP',
                               i='US0000000802'), 50, 2000, cur=cur),
            _ib_ca(both.format(t='OLDC', n='OLDC CORP',
                               i='US0000000801'), -100, -2500,
                   proceeds=500, cur=cur), cur=cur))
        self.assertEqual(r["rc"], 3, r["err"][-2000:])
        self.assertEqual(len(r["pending"]), 1)
        self.assertIn("UNSUPPORTED", r["pending"][0]["summary"])

    @rule(R.CA_CORP_09)
    def test_ca_cash_takeover_is_a_sale(self):
        self._cash_takeover("canada")
        self.assertIn("wholly in cash", _text("canada", "CA-CORP-09"))

    @rule(R.US_CORP_10)
    def test_us_cash_takeover_is_a_sale(self):
        self._cash_takeover("usa")

    @rule(R.CA_CORP_10)
    def test_ca_stock_and_cash_stops(self):
        self._stock_and_cash("canada")
        self.assertNotIn("not modelled", _text("canada", "CA-CORP-05"))

    @rule(R.US_CORP_11)
    def test_us_stock_and_cash_stops(self):
        self._stock_and_cash("usa")

    _CIL = ("BUYSELL 2023-02-01 10:00:00 QZS.US 10 USD 20 200 0\n"
            "BUYSELL 2025-03-03 10:00:00 QZS.US 10 USD 40 400 0\n")

    def _cil_book(self):
        # Questrade's CIL pair: the fraction at $0, then sold for the cash.
        return [tx("BUYSELL", "2023-02-01", "QZS.US", 10, 200),
                tx("BUYSELL", "2025-03-03", "QZS.US", 10, 400),
                tx("BUYSELL", "2025-06-30", "QZS.US", 0.4, 0.0,
                   time="09:30:00"),
                tx("BUYSELL", "2025-06-30", "QZS.US", -0.4, 3.10,
                   time="09:30:01")]

    @rule(R.CA_CORP_05)
    def test_ca_cil_is_a_sale_on_the_pool(self):
        r = _gains_one("canada", self._cil_book())
        g = [t for t in r["transactions"] if t.get("qty")]
        self.assertEqual(len(g), 1)
        # Average cost of the pool with the $0 fraction: 600 / 20.4.
        self.assertAlmostEqual(g[0]["cost"], 0.4 * 600 / 20.4, places=2)

    @rule(R.US_CORP_09)
    def test_us_cil_takes_the_oldest_lot(self):
        r = _gains_one("usa", self._cil_book())
        g = [t for t in r["transactions"] if t.get("qty")]
        self.assertEqual(len(g), 1)
        self.assertAlmostEqual(g[0]["cost"], 8.0, places=2)
        self.assertEqual(g[0]["term"], "LONG_TERM")
        self.assertIn("oldest lot", _text("usa", "US-CORP-09"))


if __name__ == "__main__":
    unittest.main()
