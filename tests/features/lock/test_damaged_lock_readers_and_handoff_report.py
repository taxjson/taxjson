"""Backlog group `handoff` — handoff, carryover, carryforward, the
estimate, amt and option-boundary: TJ-0014, TJ-0016, TJ-0052, TJ-0068,
TJ-0069, TJ-0078, TJ-0084, TJ-0085, TJ-0115, TJ-0144, TJ-0145,
TJ-0146. Synthetic data only.

Formerly: test_fix_backlog_handoff"""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tax_rules import rule
from tax_rules.dual import cli
from taxjson.rules import ids as R
from _style import CapturedWidth

from taxjson.project.settings import Settings

_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


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


# ------------------------------------------------------------ TJ-0068
class TestFxCashV2Close(unittest.TestCase):
    """TJ-0068: a damaged fx_cash_v2 close damages the lock (check-filed
    no longer calls it OK); the repair names the record actually read.

    Formerly: test_fix_backlog_handoff.TestFxCashV2Close"""

    def test_damaged_close_is_lock_damage(self):
        from taxjson.lib.filed_locks import lock_damage
        doc = {"year": 2024, "accounts": {}, "totals": {},
               "fx_cash_v2": {"year": 2024, "currency": "CAD",
                              "books": "damaged"}}
        self.assertIn("fx_cash_v2 is not a table of books",
                      lock_damage(doc, 2024, "filed/2024.json"))
        doc["fx_cash_v2"] = {"year": 2024, "books": {
            "margin/questrade": {"USD": {"units": "abc", "cost": 1.0}}}}
        self.assertIn("no finite units and cost",
                      lock_damage(doc, 2024, "filed/2024.json"))
        doc["fx_cash_v2"] = {"year": 2023, "books": {}}
        self.assertIn("close of 2023", lock_damage(doc, 2024, "f"))
        doc["fx_cash_v2"] = {"year": 2024, "books": {
            "margin/questrade": {"USD": {"units": 100.0, "cost": 140.0}}}}
        self.assertIsNone(lock_damage(doc, 2024, "filed/2024.json"))

    def test_check_filed_refuses_a_damaged_close(self):
        from taxjson.lib import filed_locks as TF
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "filed" / "2024.json"
            _lock(p, 2024, 0.0, {"opening": 0.0, "created": 0.0,
                                 "applied": 0.0, "closing": 0.0},
                  fx_cash_v2={"year": 2024, "books": "damaged"})
            with self.assertRaises(TF.LockProblem):
                TF.read_lock(p)

    def test_repair_names_the_prior_year_record(self):
        from taxjson.pipeline.run import _fx_cash_carry, _fx_cash_carry_record
        from taxjson.lib.fx_cash_v2 import _carry_openings
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p26"
            root.mkdir()
            rec = Path(td) / "elsewhere" / "lock2025.json"
            rec.parent.mkdir()
            rec.write_text(json.dumps(
                {"year": 2025, "country": "canada", "fx_cash_v2": {
                    "year": 2025, "currency": "CAD", "books": {
                        "margin/questrade": {"USD": {"units": "abc",
                                                     "cost": 1.0}}}}}))
            settings = Settings.from_table({"prior_year_record": "../elsewhere/lock2025.json"})
            self.assertEqual(_fx_cash_carry_record(settings, 2026),
                             "../elsewhere/lock2025.json")
            self.assertEqual(_fx_cash_carry_record(Settings.from_table({}), 2026),
                             "filed/2025.json")
            carry = dict(_fx_cash_carry(root, settings, 2026,
                                        country="canada"),
                         record=_fx_cash_carry_record(settings, 2026))
            problems = []
            _carry_openings(carry, {}, None, "CAD", 2026, problems)
            self.assertIn("restore ../elsewhere/lock2025.json from git",
                          problems[0]["text"])
            self.assertNotIn("filed/2025.json", problems[0]["text"])


# ------------------------------------------------------------ TJ-0069
_TIMING = {"option_premium_timing": "grant", "option_grant_since": 2023}
_BAD_NCL = {"opening": 0.0, "created": 0.0, "applied": 0.0}   # no closing


class TestLockReaders(unittest.TestCase):
    """TJ-0069: the readers that took a lock raw (option-boundary, the
    grant-since hint, compare) apply the one lock check; `years` judges
    a lock's country; carryover's warning on the project year's own
    damaged lock names it as such.

    Formerly: test_fix_backlog_handoff.TestLockReaders"""

    def test_filed_locks_skip_a_damaged_lock(self):
        from taxjson.lib.option_boundary import filed_locks
        with tempfile.TemporaryDirectory() as td:
            _lock(Path(td) / "filed" / "2024.json", 2024, 0.0, _BAD_NCL,
                  option_timing=_TIMING)
            said = []
            years, timing = filed_locks(td, {"country": "canada",
                                             "year": 2025}, warn=said.append)
            self.assertEqual(years, {2024})
            self.assertEqual(timing, {})
            self.assertIn("has no closing", said[0])

    def test_grant_since_hint_skips_a_damaged_lock(self):
        from taxjson.pipeline.run import _locked_grant_since
        with tempfile.TemporaryDirectory() as td:
            p = _lock(Path(td) / "filed" / "2024.json", 2024, 0.0,
                      _BAD_NCL, option_timing=_TIMING)
            self.assertIsNone(_locked_grant_since(
                Path(td), {"country": "canada", "year": 2025}))
            doc = json.loads(p.read_text())
            doc["carryforwards"]["net_capital_loss"]["closing"] = 0.0
            p.write_text(json.dumps(doc))
            self.assertEqual(_locked_grant_since(
                Path(td), {"country": "canada", "year": 2025})[0], 2023)

    def test_compare_refuses_a_damaged_lock(self):
        from taxjson.lib import compare
        with tempfile.TemporaryDirectory() as td:
            _lock(Path(td) / "filed" / "2024.json", 2024, 0.0, _BAD_NCL,
                  frozen={"inputs": {}})
            (Path(td) / "filed" / "2024" / "reports").mkdir(parents=True)
            with self.assertRaises(compare.CompareError) as cm:
                compare.load(Path(td), {"settings": {
                    "country": "canada", "year": 2024}}, compare.FILED)
            self.assertIn("has no closing", str(cm.exception))

    def test_years_judges_the_lock_country(self):
        from taxjson.lib.years_report import years_report
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "2024"
            d.mkdir()
            (d / "taxjson.toml").write_text(
                '[settings]\nyear = 2024\ncountry = "canada"\n'
                'base_currency = "CAD"\n[accounts.margin]\n'
                'type = "taxable"\n')
            p = _lock(d / "filed" / "2024.json", 2024, 0.0,
                      {"opening": 0.0, "created": 0.0, "applied": 0.0,
                       "closing": 0.0})
            rec = years_report(Path(td))["years"][0]
            self.assertIsNone(rec["problem"])
            doc = json.loads(p.read_text())
            doc["country"] = "usa"
            p.write_text(json.dumps(doc))
            rec = years_report(Path(td))["years"][0]
            self.assertIn('was closed under country = "usa"', rec["problem"])
            self.assertIsNone(rec["totals"])

    def test_carryover_warns_on_its_own_damaged_lock(self):
        with tempfile.TemporaryDirectory() as td:
            p = _project(Path(td) / "p24", 2024, [
                _row("b1", "2024-02-02", "ZZXA.TO", 10, -500.0),
                _row("s1", "2024-03-02", "ZZXA.TO", -10, 400.0)])
            _lock(p / "filed" / "2024.json", 2024, -100.0, _BAD_NCL)
            r = cli(p, "carryover")
            # A warning, not a refusal: the row is the books' either way.
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("could not be read or is damaged", r.stderr)


# ------------------------------------------------- TJ-0115, TJ-0144
class TestHandoffReport(unittest.TestCase):
    """Formerly: test_fix_backlog_handoff.TestHandoffReport"""

    def test_header_names_the_project_year(self):
        """TJ-0115: a record two years back opens 'into' the project's
        own year, not the record's year + 1."""
        from taxjson.lib import handoff
        rep = {"year": 2023, "positions": [], "missed": [], "double": [],
               "boundary": [], "timing": [], "carry": [], "partial": [],
               "notes": [], "problems": 0}
        with redirect_stdout(io.StringIO()):
            head = handoff.render(rep, "2023/filed/2023.json", 2025)[0]
        self.assertEqual(head,
                         "HAND-OFF CHECK — 2023 (closed) into 2025 "
                         "(this project)")
        with redirect_stdout(io.StringIO()):
            head = handoff.render(rep, "2023/filed/2023.json")[0]
        self.assertIn("into 2024 (this project)", head)

    @rule(R.CA_RPT_08)
    def test_amend_choice_opens_with_the_corrected_cost(self):
        """TJ-0144: keep-as-filed opens with the record's cost, amend
        with this project's (the corrected one)."""
        from taxjson.lib import handoff
        rec = {"year": 2024, "dispositions": [], "boundary_rows": [],
               "year_end": {"equity": {"ZZPN.US": {
                   "qty": 100.0, "acb": 838.95, "deferred": 0.0}}}}
        opening = {"equity": {"ZZPN.US": {"qty": 100.0, "acb": 438.95,
                                          "deferred": 0.0}}}
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "work").mkdir()
            rep = handoff.check(Path(td), {
                "settings": {"country": "canada", "year": 2025},
                "accounts": {"margin": {"type": "taxable"}}}, rec, opening)
        why = rep["positions"][0]["why"]
        self.assertIn("keep 2024 as filed and open with the cost that "
                      "return implied (838.95)", why)
        self.assertIn("amend 2024 and open with 438.95", why)


if __name__ == "__main__":
    unittest.main()
