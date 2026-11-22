"""Backlog group `handoff` — handoff, carryover, carryforward, the
estimate, amt and option-boundary: TJ-0014, TJ-0016, TJ-0052, TJ-0068,
TJ-0069, TJ-0078, TJ-0084, TJ-0085, TJ-0115, TJ-0144, TJ-0145,
TJ-0146. Synthetic data only.

Formerly: test_fix_backlog_handoff"""
import json
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from tax_rules.dual import cli
from taxjson.rules import ids as R
from _style import CapturedWidth

_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _ok(r):
    assert r.returncode == 0, (r.returncode, r.stdout[-1500:],
                               r.stderr[-1500:])
    return r


def _flat(text: str) -> str:
    return " ".join(text.split())


def _base(td: str, rows) -> Path:
    work = Path(td) / "work"
    work.mkdir(parents=True, exist_ok=True)
    (work / "margin_base.json").write_text(json.dumps(
        {"transactions": rows}))
    return work


def _row(i, date, sym, qty, net, action="BUYSELL", settle=None):
    return {"id": i, "action": action, "date": date,
            "date_settle": settle or date, "time": "10:00:00",
            "symbol": sym, "quantity": qty,
            "price": abs(net / qty) if qty else 0.0, "net_amount": net,
            "currency": "CAD", "account": "margin"}


# ------------------------------------------------------------ TJ-0052
def _lock(path, year, realized, ncl, **extra):
    doc = {"schema_version": 2, "year": year, "country": "canada",
           "accounts": {"margin": {"realized": realized}},
           "totals": {"realized": realized},
           "carryforwards": {"version": 1, "net_capital_loss": ncl}}
    doc.update(extra)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc))
    return path


def _project(root: Path, year: int, rows, extra: str = "") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "inputs" / "margin").mkdir(parents=True, exist_ok=True)
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = {year}\ncountry = "canada"\n'
        f'base_currency = "CAD"\nsource_currencies = []\n{extra}'
        f'[accounts.margin]\ntype = "taxable"\n')
    _base(str(root), rows)
    return root


class TestLockedYearApplied(unittest.TestCase):
    """TJ-0052: a locked year with no [carryover] claimed entry shows
    the losses its lock applied, and offers them no second time.

    Formerly: test_fix_backlog_handoff.TestLockedYearApplied"""

    NETS = {2024: {"net": -900.0, "st": 0, "lt": 0, "dispositions": 1},
            2025: {"net": 300.0, "st": 0, "lt": 0, "dispositions": 1},
            2026: {"net": -100.0, "st": 0, "lt": 0, "dispositions": 1}}

    @rule(R.CA_CARRY_04)
    def test_ledger_takes_the_lock_applied(self):
        from taxjson.bin.taxjson_carryover import build_canada_ledger
        led = build_canada_ledger(dict(self.NETS), {},
                                  {2024: 900.0, 2025: 600.0},
                                  {2024: 0.0, 2025: 0.0},
                                  {2024: 0.0, 2025: 300.0})
        by = {r["year"]: r for r in led["rows"]}
        self.assertEqual(by[2025]["claimed_applied"], 300.0)
        self.assertEqual(by[2025]["carryforward_balance"], 600.0)
        self.assertEqual(by[2025]["available_to_apply"], 0.0)
        # 2025's gain is sheltered: no carry-back room for 2026's loss.
        self.assertEqual(by[2026]["carryback_candidates"], [])
        # A claim under the year wins over the lock (0 is a claim).
        led = build_canada_ledger(dict(self.NETS), {2025: 0.0},
                                  {2024: 900.0, 2025: 600.0},
                                  {2024: 0.0, 2025: 0.0},
                                  {2024: 0.0, 2025: 300.0})
        by = {r["year"]: r for r in led["rows"]}
        self.assertEqual(by[2025]["claimed_applied"], 0.0)
        from taxjson.lib import tax_logic as TL
        txt = [rr.text for _t, rs in TL.rule_sections(
            "canada", {"country": "canada", "year": 2026})
            for rr in rs if rr.id == "CA-CARRY-04"][0]
        self.assertIn("the losses its lock applied", txt)

    @rule(R.CA_CARRY_04)
    def test_carryover_reads_the_lock_applied(self):
        with tempfile.TemporaryDirectory() as td:
            t = Path(td)
            p = _project(t / "p26", 2026, [
                _row("b1", "2026-02-02", "ZZXA.TO", 10, -500.0),
                _row("s1", "2026-03-02", "ZZXA.TO", -10, 400.0)])
            _lock(p / "filed" / "2024.json", 2024, -900.0,
                  {"opening": 0.0, "created": 900.0, "applied": 0.0,
                   "closing": 900.0})
            _lock(p / "filed" / "2025.json", 2025, 300.0,
                  {"opening": 900.0, "created": 0.0, "applied": 300.0,
                   "closing": 600.0})
            r = _ok(cli(p, "carryover", "--json"))
            by = {x["year"]: x for x in json.loads(r.stdout)["rows"]}
            self.assertEqual(by[2025]["claimed_applied"], 300.0)
            self.assertEqual(by[2025]["available_to_apply"], 0.0)
            text = _ok(cli(p, "carryover")).stdout
            self.assertNotIn("2025: apply up to", text)


# ------------------------------------------- TJ-0078, TJ-0084, TJ-0145
class TestCarryBackChain(unittest.TestCase):
    """A closed 2023 (gain 700.00) and its 2024 successor (loss 900.00)
    whose [carryover] claimed = { 2023 = 700.00 } records the T1A
    carry-back.

    Formerly: test_fix_backlog_handoff.TestCarryBackChain"""
    tmp = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        t = Path(cls.tmp.name)

        def proj(name, year, book, extra=""):
            root = t / name
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "inputs" / "margin" / "m.tt").write_text(book)
            (root / "taxjson.toml").write_text(
                f'[settings]\nyear = {year}\ncountry = "canada"\n'
                f'base_currency = "CAD"\nsource_currencies = []\n'
                f'province = "ON"\n{extra}'
                f'[accounts.margin]\ntype = "taxable"\n')
            _ok(cli(root, "run", "--no-input"))
            return root
        cls.p23 = proj("p23", 2023,
                       "BUYSELL 2023-02-01 10:00:00 ZZGA.TO 100 CAD 10 "
                       "1000 0\n"
                       "BUYSELL 2023-06-01 10:00:00 ZZGA.TO -100 CAD 17 "
                       "1700 0\n")
        _ok(cli(cls.p23, "close-year", "--yes"))
        cls.p24 = proj(
            "p24", 2024,
            "BUYSELL 2024-02-01 10:00:00 ZZLB.TO 100 CAD 20 2000 0\n"
            "BUYSELL 2024-06-03 10:00:00 ZZLB.TO -100 CAD 11 1100 0\n",
            'prior_year_record = "../p23/filed/2023.json"\n'
            '[carryover]\nclaimed = { 2023 = 700.00 }\n'
            '[estimate]\nother_income = 90000\n'
            '[instalments]\nbasis = "prior_year"\n'
            'paid = [ { date = "2024-03-15", amount = 500 },\n'
            '         { date = "2024-06-15", amount = 500 } ]\n'
            'prior_year_net_tax = 4000\nsecond_prior_net_tax = 3500\n'
            'withheld = 0\n')

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @rule(R.CA_CARRY_05)
    def test_handoff_takes_the_carry_back_out(self):
        """TJ-0078: the claim under 2023 is this project's own loss
        carried back (T1A): no difference from the 2023 record."""
        r = cli(self.p24, "handoff", "--json")
        self.assertEqual(r.returncode, 0, r.stdout[-1500:] + r.stderr)
        self.assertEqual(json.loads(r.stdout)["carry"], [])
        led = json.loads(_ok(cli(self.p24, "carryover", "--json")).stdout)
        r24 = next(x for x in led["rows"] if x["year"] == 2024)
        self.assertEqual(r24["carried_back_to"], {"2023": 700.0})

    @rule(R.CA_CARRY_03)
    def test_estimate_leaves_the_carry_back_out(self):
        """TJ-0084: the estimate's unused losses carried forward are the
        loss less the carry-back, as carryover and the lock have it."""
        text = _flat(_ok(cli(self.p24, "estimate")).stdout)
        self.assertIn("Unused capital losses: 200.00 (carry forward; "
                      "700.00 carried back", text)

    def test_carryover_notes_name_the_toml(self):
        """TJ-0145: `tjs carryover` rejects --claimed FILE; its notes
        name [carryover] claimed."""
        text = _ok(cli(self.p24, "carryover", "--details")).stdout
        self.assertNotIn("--claimed", text)
        self.assertIn("plus your [carryover] claimed records", _flat(text))

    def test_estimate_names_the_instalments_behind(self):
        """TJ-0146: the shortfall is the instalments behind, not the
        balance owed on April 30."""
        text = _flat(_ok(cli(self.p24, "estimate")).stdout)
        self.assertIn("=> instalments behind 3,000.00 [due with the "
                      "balance April 30; tjs instalments]", text)
        self.assertNotIn("=> balance due", text)


if __name__ == "__main__":
    unittest.main()
