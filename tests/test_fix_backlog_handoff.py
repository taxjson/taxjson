"""Backlog group `handoff` — handoff, carryover, carryforward, the
estimate, amt and option-boundary: TJ-0014, TJ-0016, TJ-0052, TJ-0068,
TJ-0069, TJ-0078, TJ-0084, TJ-0085, TJ-0115, TJ-0144, TJ-0145,
TJ-0146. Synthetic data only."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path

from tax_rules import rule
from tax_rules.dual import cli
from _style import CapturedWidth

from taxjson.lib.core import TaxTransaction

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


_CFG24 = {"settings": {"country": "canada", "year": 2024},
          "accounts": {"margin": {"type": "taxable"}}}


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


# ------------------------------------------------------------ TJ-0014
class TestCorrectedJanuaryRow(unittest.TestCase):
    """TJ-0014: the broker corrects an early-January fill the closed
    year's inputs held; the row here (same date and quantity, another
    amount) counts in this year: not 'in neither return'."""

    def setUp(self):
        self.rec = {"year": 2023, "boundary_rows": [
            {"account": "margin", "action": "BUYSELL",
             "symbol": "ZZQG.TO", "date": "2024-01-03",
             "date_settle": "2024-01-04", "quantity": 10.0,
             "net": 204.95, "tax_date": "2024-01-04"}]}

    @rule("CA-RPT-08")
    def test_corrected_amount_is_the_same_row(self):
        from taxjson.lib import handoff
        with tempfile.TemporaryDirectory() as td:
            work = _base(td, [_row("b1", "2024-01-03", "ZZQG.TO", 10,
                                   209.95, settle="2024-01-04")])
            self.assertEqual(handoff._boundary_issues(
                work, _CFG24, self.rec, "settle", set()), [])
            notes = []
            handoff._boundary_issues(work, _CFG24, self.rec, "settle",
                                     set(), notes)
            self.assertEqual(len(notes), 1)
            self.assertIn("the export was corrected", notes[0])
            self.assertIn("209.95 here, 204.95 when the 2023 project "
                          "read it", notes[0])

    @rule("CA-RPT-08")
    def test_a_missing_row_is_still_in_neither_return(self):
        from taxjson.lib import handoff
        with tempfile.TemporaryDirectory() as td:
            work = _base(td, [_row("b1", "2024-01-05", "ZZQG.TO", 10,
                                   209.95)])
            out = handoff._boundary_issues(work, _CFG24, self.rec,
                                           "settle", set())
            self.assertEqual(len(out), 1)
            self.assertIn("Add it here", out[0]["why"])


# ------------------------------------------------------------ TJ-0016
class TestMinimumTaxProvincialShare(unittest.TestCase):
    """TJ-0016: the ON share of a minimum-tax carryover recovery is the
    federal amount x the factor, off basic ON tax; the surtax it saves
    is shown apart, never inside the share."""

    def _est(self):
        from taxjson.lib import tax_estimate as TE
        return TE.estimate_canada(
            realized=0.0, eligible_div=0.0, foreign_div=0.0, pil=0.0,
            other_income=200000.0, other_losses=0.0, province="ON",
            year=2025, amt_carryover={2024: 900.5},
            amt_carryover_source="[estimate] amt_carryover")

    @rule("CA-AMT-05")
    def test_share_is_the_factor_of_the_federal_amount(self):
        from taxjson.lib import amt_report as AR
        r = self._est()
        a = r["amt"]
        c = a["carryover"]
        self.assertEqual(c["recovered_federal"], 900.5)
        share = round(900.5 * a["provincial_factor"], 2)
        self.assertAlmostEqual(c["recovered_provincial_credit"], share,
                               places=2)
        # The surtax it saves is the rest of the provincial drop.
        self.assertGreater(c["recovered_provincial_surtax"], 0.0)
        self.assertAlmostEqual(c["recovered_provincial_credit"]
                               + c["recovered_provincial_surtax"],
                               c["recovered_provincial"], places=2)
        text = "\n".join(AR.render(AR.build(r, 2025, "CAD")))
        line = next(ln for ln in text.splitlines()
                    if "Recovered: ON share" in ln)
        self.assertIn(f"{share:,.2f}", line)
        self.assertNotIn(f"{c['recovered_provincial']:,.2f}", line)
        self.assertIn("ON surtax it saves", text)
        from taxjson.lib import tax_logic as TL
        txt = [rr.text for _t, rs in TL.rule_sections(
            "canada", {"country": "canada", "year": 2025})
            for rr in rs if rr.id == "CA-AMT-05"][0]
        self.assertIn("surtax it saves apart", txt)

    @rule("CA-AMT-05")
    def test_lock_records_the_share(self):
        from taxjson.lib import amt_report as AR
        from taxjson.lib import carryforward as CF
        r = self._est()
        mt = CF.record_block("canada", r, {})["minimum_tax"]
        c = r["amt"]["carryover"]
        self.assertEqual(mt["recovered_provincial_credit"],
                         c["recovered_provincial_credit"])
        text = "\n".join(AR.render_recorded(2025, "filed/2025.json", mt))
        self.assertIn("provincial surtax it saves", text)
        # A lock written before: its figure is the whole drop, said so.
        del mt["recovered_provincial_credit"]
        text = "\n".join(AR.render_recorded(2025, "filed/2025.json", mt))
        self.assertIn("provincial tax (surtax included)", text)


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
    the losses its lock applied, and offers them no second time."""

    NETS = {2024: {"net": -900.0, "st": 0, "lt": 0, "dispositions": 1},
            2025: {"net": 300.0, "st": 0, "lt": 0, "dispositions": 1},
            2026: {"net": -100.0, "st": 0, "lt": 0, "dispositions": 1}}

    @rule("CA-CARRY-04")
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

    @rule("CA-CARRY-04")
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


# ------------------------------------------------------------ TJ-0068
class TestFxCashV2Close(unittest.TestCase):
    """TJ-0068: a damaged fx_cash_v2 close damages the lock (check-filed
    no longer calls it OK); the repair names the record actually read."""

    def test_damaged_close_is_lock_damage(self):
        from taxjson.bin.taxjson_filed import lock_damage
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
        from taxjson.bin import taxjson_filed as TF
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "filed" / "2024.json"
            _lock(p, 2024, 0.0, {"opening": 0.0, "created": 0.0,
                                 "applied": 0.0, "closing": 0.0},
                  fx_cash_v2={"year": 2024, "books": "damaged"})
            with self.assertRaises(TF.LockProblem):
                TF.read_lock(p)

    def test_repair_names_the_prior_year_record(self):
        from taxjson.bin.taxjson_run import (_fx_cash_carry,
                                             _fx_cash_carry_record)
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
            settings = {"prior_year_record": "../elsewhere/lock2025.json"}
            self.assertEqual(_fx_cash_carry_record(settings, 2026),
                             "../elsewhere/lock2025.json")
            self.assertEqual(_fx_cash_carry_record({}, 2026),
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
    damaged lock names it as such."""

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
        from taxjson.bin.taxjson_run import _locked_grant_since
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
        from taxjson.lib.project_layout import years_report
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


# ------------------------------------------- TJ-0078, TJ-0084, TJ-0145
class TestCarryBackChain(unittest.TestCase):
    """A closed 2023 (gain 700.00) and its 2024 successor (loss 900.00)
    whose [carryover] claimed = { 2023 = 700.00 } records the T1A
    carry-back."""
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

    @rule("CA-CARRY-05")
    def test_handoff_takes_the_carry_back_out(self):
        """TJ-0078: the claim under 2023 is this project's own loss
        carried back (T1A): no difference from the 2023 record."""
        r = cli(self.p24, "handoff", "--json")
        self.assertEqual(r.returncode, 0, r.stdout[-1500:] + r.stderr)
        self.assertEqual(json.loads(r.stdout)["carry"], [])
        led = json.loads(_ok(cli(self.p24, "carryover", "--json")).stdout)
        r24 = next(x for x in led["rows"] if x["year"] == 2024)
        self.assertEqual(r24["carried_back_to"], {"2023": 700.0})

    @rule("CA-CARRY-03")
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


# ------------------------------------------------------------ TJ-0085
def _T(**kw):
    return TaxTransaction(**{"action": "BUYSELL", "currency": "CAD",
                             "account": "margin", **kw})


_OPT = "QZW250117C00030000.TO"


def _assigned_book():
    return [_T(date="2024-11-04", date_settle="2024-11-05", symbol=_OPT,
               quantity=-1, price=2.0, net_amount=198.0),
            _T(date="2025-01-10", date_settle="2025-01-13", symbol="QZW.TO",
               quantity=100, price=28, net_amount=-2800.0),
            _T(action="ASSIGN", date="2025-01-17", date_settle="2025-01-17",
               symbol=_OPT, quantity=1, price=0, net_amount=0.0),
            _T(action="ASSIGN", date="2025-01-17", date_settle="2025-01-20",
               symbol="QZW.TO", quantity=-100, price=30,
               net_amount=3000.0)]


class TestOptionBoundaryAmended(unittest.TestCase):
    """TJ-0085: once the write year's lock no longer reports the
    assigned contract's premium (the amendment re-closed), no T1-ADJ is
    asked for."""
    TODAY = date(2026, 9, 29)

    def _action(self, premiums):
        from taxjson.lib.option_boundary import straddling
        rows = straddling(_assigned_book(), 2025, "grant", 2024,
                          filed_years={2024}, today=self.TODAY,
                          filed_premiums=premiums)
        return [r for r in rows if r["close_kind"] == "assignment"][0]

    @rule("CA-OPT-07")
    def test_amended_lock_asks_no_t1_adj(self):
        from taxjson.lib.option_boundary import reported_premiums
        filed = reported_premiums({"dispositions": [
            {"symbol": _OPT, "date": "2024-11-04",
             "date_settle": "2024-11-05", "gain": 198.0}]})
        self.assertTrue(self._action(None)["action"].startswith("T1-ADJ"))
        self.assertTrue(self._action({2024: filed})["action"]
                        .startswith("T1-ADJ"))
        amended = reported_premiums({"dispositions": [
            {"symbol": "ZZOT.TO", "date": "2024-05-01", "gain": 5.0}]})
        act = self._action({2024: amended})["action"]
        self.assertTrue(act.startswith("no amendment"), act)
        self.assertIn("no longer reports the 198.00 premium", act)
        # A lock with no disposition list says nothing: the T1-ADJ stays.
        self.assertIsNone(reported_premiums({"year": 2024}))
        from taxjson.lib import tax_logic as TL
        txt = [rr.text for _t, rs in TL.rule_sections(
            "canada", {"country": "canada", "year": 2025})
            for rr in rs if rr.id == "CA-OPT-07"][0]
        self.assertIn("no longer reports the premium", txt)

    @rule("CA-OPT-07")
    def test_filed_locks_collect_the_reported_premiums(self):
        from taxjson.lib.option_boundary import filed_locks
        with tempfile.TemporaryDirectory() as td:
            _lock(Path(td) / "filed" / "2024.json", 2024, 0.0,
                  {"opening": 0.0, "created": 0.0, "applied": 0.0,
                   "closing": 0.0}, option_timing=_TIMING,
                  dispositions=[{"symbol": _OPT, "date": "2024-11-04",
                                 "gain": 198.0}])
            got = {}
            filed_locks(td, {"country": "canada", "year": 2025},
                        premiums=got)
            self.assertEqual(got, {2024: {(_OPT, "2024-11-04"): 198.0}})


# ------------------------------------------------- TJ-0115, TJ-0144
class TestHandoffReport(unittest.TestCase):

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

    @rule("CA-RPT-08")
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


# ------------------------------------------------------------ TJ-0145
class TestCarryoverNotes(unittest.TestCase):
    """TJ-0145: inside a project the notes name [carryover] claimed;
    the standalone tool keeps --claimed FILE."""

    LEDGER = {"country": "canada", "rows": [
        {"year": 2024, "net_gain": -100.0, "claimed_applied": 0.0,
         "carryforward_balance": 100.0, "available_to_apply": 0.0,
         "carryback_candidates": [], "carried_back": 0.0}],
        "final_carryforward": 100.0}

    def test_in_project_names_the_toml(self):
        from taxjson.bin.taxjson_carryover import render
        text = _flat(render(dict(self.LEDGER), "CAD", 2024, False,
                            in_project=True))
        self.assertNotIn("--claimed", text)
        self.assertIn("record it in taxjson.toml, [carryover] then "
                      "`claimed = { YEAR = AMOUNT }`", text)
        text = _flat(render(dict(self.LEDGER), "CAD", 2024, False))
        self.assertIn("--claimed FILE", text)


if __name__ == "__main__":
    unittest.main()
