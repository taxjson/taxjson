"""A sheltered account's spin-off / merger is booked without asking
(owner request, 2026-10-08; tax-logic CA-CORP-11 / US-CORP-12).

Inside a registered account (Canada: RRSP, LIRA, TFSA ...; US: IRA, Roth,
401(k) ...) no gain is taxed and nothing taxable reads the account's cost:
an in-kind withdrawal is a purchase at fair market value, and the loss
rules count the account's units. So with [settings] sheltered_elections =
"zero" (the default) a spin-off there is booked at $0 cost for the new
shares (the parent keeps its cost) and a merger carries the old shares'
cost, with one Info line per run — never pending, never a warning, and
`run --strict` passes. "ask" restores the question; an election saved
with `tjs elections` wins; a taxable account is still asked.

Every fixture is synthetic: invented tickers, amounts and account ids.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule, rule_absent
from tax_rules.dual import cli
from _support.ib_sheltered_elections import (
    _SPIN, _ib, _flat, _sheltered_project, _corp_rows, _event_id, _CA_PLAN,
)
from taxjson.rules import ids as R

from _style import CapturedWidth

from taxjson.lib.country import CANADA, USA

# The assertions read captured output (`note:`), TAXJSON_WIDTH=0.
_WIDTH = CapturedWidth()


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


class _Base:
    """The checks, run per country by the tagged subclasses below."""
    COUNTRY = ""
    PLAN = ""            # the sheltered account's name (its plan)
    ELECTION = ""        # the country's FMV spin-off election

    # ------------------------------------------------------------ fixtures
    def _project(self, td, **kw):
        return _sheltered_project(td, self.COUNTRY, self.PLAN, **kw)

    _corp_rows = staticmethod(_corp_rows)
    _event_id = staticmethod(_event_id)

    def _info_count(self, r, text):
        return _flat(r).count(text)

    # -------------------------------------------------------------- checks
    def check_spinoff_booked_at_zero_once_not_pending(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td)
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, _flat(r)[-3000:])
            flat = _flat(r)
            head = f"sheltered account {self.PLAN}: spin-off SPNCO"
            self.assertEqual(flat.count(head), 1, flat)
            self.assertIn("booked at $0 cost for the distributed shares; "
                          "cost only affects the holdings view — `tjs "
                          "elections` sets a fair value if you want it there",
                          flat)
            self.assertNotIn("need an election", flat)
            # Info, never a warning (`note:` is its captured form).
            self.assertIn("note: " + head, flat)
            rows = self._corp_rows(root, self.PLAN)
            # The new shares at $0, the parent's cost untouched: no
            # income row, no parent cost reduction.
            self.assertEqual(
                [(x["action"], x["quantity"], x["net_amount"],
                  x["corp_election"]) for x in rows],
                [("BUYSELL", 25.0, 0.0, "sheltered_default")])
            # Nothing written to the manifest: an election there wins
            # (no manifest at all until an election is saved).
            _mp = root / "inputs" / self.PLAN / "elections.json"
            man = json.loads(_mp.read_text()) if _mp.exists() else {}
            self.assertEqual(man.get("elections") or {}, {})
            self.assertFalse((root / "work" / "pending_elections.json")
                             .exists())
            p = cli(root, "elections", "--pending")
            self.assertIn("No pending elections", p.stdout)
            s = cli(root, "run", "--no-input", "--strict")
            self.assertEqual(s.returncode, 0, _flat(s)[-3000:])
            self.assertEqual(self._info_count(s, head), 1)
            sp = cli(root, "spinoffs", "--json")
            item = json.loads(sp.stdout)["spinoffs"][0]
            self.assertEqual((item["election"], item["flags"],
                              item["new_cost"]),
                             ("sheltered_default", [], 0.0))
            ck = cli(root, "checklist", "--run", "--all")    # the full list
            self.assertIn("No unresolved merger or spin-off election",
                          _flat(ck))

    def check_ask_restores_the_question(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, mode="ask")
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 3, _flat(r)[-3000:])
            agg = json.loads((root / "work" / "pending_elections.json")
                             .read_text())
            pend = agg["accounts"][self.PLAN]["pending"]
            self.assertEqual(len(pend), 1)
            self.assertTrue(pend[0]["sheltered"])
            self.assertNotIn("booked at $0 cost for the distributed",
                             _flat(r))

    def check_explicit_election_wins(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td)
            self.assertEqual(cli(root, "run", "--no-input").returncode, 0)
            eid = self._event_id(root, self.PLAN)
            e = cli(root, "elections", self.PLAN, "--set",
                    f"{eid}={self.ELECTION}")
            self.assertEqual(e.returncode, 0, _flat(e))
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, _flat(r)[-3000:])
            self.assertNotIn("booked at $0 cost", _flat(r))
            buys = [x for x in self._corp_rows(root, self.PLAN)
                    if x["action"] == "BUYSELL"]
            # The broker's value (250) is the new shares' cost.
            self.assertEqual([(x["net_amount"], x["corp_election"])
                              for x in buys], [(250.0, self.ELECTION)])

    def check_taxable_account_still_asked(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, spin_in="margin")
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 3, _flat(r)[-3000:])
            agg = json.loads((root / "work" / "pending_elections.json")
                             .read_text())
            self.assertEqual(len(agg["accounts"]["margin"]["pending"]), 1)
            self.assertNotIn("booked at $0 cost", _flat(r))
            s = cli(root, "run", "--no-input", "--strict")
            self.assertNotEqual(s.returncode, 0)

    def check_withdrawal_in_kind_costs_fair_market_value(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, withdraw=True)
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, _flat(r)[-3000:])
            self.assertIn("withdrawal", _flat(r))
            t = json.loads(cli(root, "sum", "--json").stdout)[
                "filing"]["totals"]
            cost = t.get("acb", t.get("cost"))
            # Sold for 750 on the 500 fair value — never the plan's $0.
            self.assertEqual((t["proceeds"], cost, t["gain"]),
                             (750.0, 500.0, 250.0))

    def _loss_near_the_spin_off(self, td, elect=None):
        """margin buys 10 SPNCO at 20 and sells them at 10 a week after
        the plan received 25 SPNCO in the spin-off (still held at day
        30): a 100 loss with the plan's shares in its window. The filing
        totals, with the plan's event defaulted or elected."""
        root = self._project(td, margin_trades=[
            ("SPNCO", "2025-02-03", 10, 20.0),
            ("SPNCO", "2025-03-10", -10, 10.0)])
        r = cli(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, _flat(r)[-3000:])
        if elect:
            eid = self._event_id(root, self.PLAN)
            e = cli(root, "elections", self.PLAN, "--set", f"{eid}={elect}")
            self.assertEqual(e.returncode, 0, _flat(e))
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, _flat(r)[-3000:])
        return json.loads(cli(root, "sum", "--json").stdout)[
            "filing"]["totals"]

    def check_merger_carries_the_cost(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, merger=True)
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, _flat(r)[-3000:])
            flat = _flat(r)
            self.assertEqual(flat.count(
                f"sheltered account {self.PLAN}: merger into XYZ"), 1,
                flat)
            self.assertIn("booked with the old shares' cost carried to "
                          "the new shares", flat)
            rows = self._corp_rows(root, self.PLAN)
            self.assertEqual([(x["action"], x["corp_election"])
                              for x in rows],
                             [("SPLIT", "sheltered_default")])


@rule(R.CA_CORP_11)
class TestCanadaRegisteredAccount(_Base, unittest.TestCase):
    COUNTRY = CANADA
    PLAN = _CA_PLAN
    ELECTION = "taxable_deemed_dividend"

    def test_spinoff_booked_at_zero_once_not_pending(self):
        self.check_spinoff_booked_at_zero_once_not_pending()

    def test_ask_restores_the_question(self):
        self.check_ask_restores_the_question()

    def test_explicit_election_wins(self):
        self.check_explicit_election_wins()

    def test_taxable_account_still_asked(self):
        self.check_taxable_account_still_asked()

    def test_withdrawal_in_kind_costs_fair_market_value(self):
        self.check_withdrawal_in_kind_costs_fair_market_value()

    def test_merger_carries_the_cost(self):
        self.check_merger_carries_the_cost()

    def test_defaulted_shares_count_for_the_superficial_loss_rule(self):
        """Canada as built: the registered account's spun-off shares
        are acquired on the distribution date, as under every spin-off
        election, so the margin loss a week later is denied (for good:
        the holder is a registered plan)."""
        with tempfile.TemporaryDirectory() as td:
            t = self._loss_near_the_spin_off(td)
        self.assertEqual(t["gain"], 0.0, t)
        self.assertEqual(t["permanently_denied"], 100.0, t)

    def test_tax_logic_states_both_modes(self):
        from taxjson.lib.tax_logic import rule_sections
        text = {m: next(r.text for _t, rs in rule_sections(
            self.COUNTRY, {"sheltered_elections": m}) for r in rs
            if r.id == "CA-CORP-11") for m in ("zero", "ask")}
        self.assertIn("booked without asking", text["zero"])
        self.assertIn("superficial-loss rule", text["zero"])
        self.assertIn("is asked like a taxable account's", text["ask"])


@rule(R.US_CORP_12)
class TestUsRetirementAccount(_Base, unittest.TestCase):
    COUNTRY = USA
    PLAN = "ira"
    ELECTION = "taxable_distribution_301"

    def test_spinoff_booked_at_zero_once_not_pending(self):
        self.check_spinoff_booked_at_zero_once_not_pending()

    def test_ask_restores_the_question(self):
        self.check_ask_restores_the_question()

    def test_explicit_election_wins(self):
        self.check_explicit_election_wins()

    def test_taxable_account_still_asked(self):
        self.check_taxable_account_still_asked()

    def test_withdrawal_in_kind_costs_fair_market_value(self):
        self.check_withdrawal_in_kind_costs_fair_market_value()

    def test_merger_carries_the_cost(self):
        self.check_merger_carries_the_cost()

    def test_defaulted_shares_are_not_a_wash_sale_purchase(self):
        """As under tax_free_355: the IRA's defaulted spun-off shares do
        not wash the margin loss a week later."""
        with tempfile.TemporaryDirectory() as td:
            t = self._loss_near_the_spin_off(td)
        self.assertEqual((t["gain"], t["permanently_denied"]),
                         (-100.0, 0.0), t)

    def test_an_explicit_301_election_still_counts(self):
        """taxable_distribution_301 elected in the IRA: the shares are a
        purchase, and the IRA replacement denies the loss for good (Rev.
        Rul. 2008-5)."""
        with tempfile.TemporaryDirectory() as td:
            t = self._loss_near_the_spin_off(
                td, elect="taxable_distribution_301")
        self.assertEqual(t["permanently_denied"], 100.0, t)

    def test_tax_logic_states_both_modes(self):
        from taxjson.lib.tax_logic import rule_sections
        text = {m: next(r.text for _t, rs in rule_sections(
            self.COUNTRY, {"sheltered_elections": m}) for r in rs
            if r.id == "US-CORP-12") for m in ("zero", "ask")}
        self.assertIn("booked without asking", text["zero"])
        self.assertIn("wash-sale rule", text["zero"])
        self.assertIn("not a purchase for the wash-sale rule, as under "
                      "tax_free_355", text["zero"])
        self.assertIn("is asked like a taxable account's", text["ask"])


class TestPartition(unittest.TestCase):
    """The same book under both countries: the plan's defaulted spun-off
    shares are acquired for Canada's superficial-loss rule (CA-CORP-11)
    and not a purchase for the US wash-sale rule (US-CORP-12)."""

    @rule(R.CA_CORP_11)
    @rule_absent(R.CA_CORP_11, country="usa")
    @rule(R.US_CORP_12)
    def test_registered_spin_off_counts_in_canada_only(self):
        got = {}
        for cls in (TestCanadaRegisteredAccount, TestUsRetirementAccount):
            t = cls("test_merger_carries_the_cost")
            with tempfile.TemporaryDirectory() as td:
                got[cls.COUNTRY] = t._loss_near_the_spin_off(td)[
                    "permanently_denied"]
        self.assertEqual(got, {CANADA: 100.0, USA: 0.0})


class TestSettingRefused(unittest.TestCase):
    def test_a_typo_is_refused_by_every_config_reader(self):
        from taxjson.lib.config_check import bool_setting_problems
        self.assertEqual(bool_setting_problems(
            {"settings": {"sheltered_elections": "zero"}}), [])
        self.assertEqual(bool_setting_problems(
            {"settings": {"sheltered_elections": "ask"}}), [])
        probs = bool_setting_problems(
            {"settings": {"sheltered_elections": "never"}})
        self.assertEqual(probs, ['[settings] sheltered_elections must be '
                                 '"zero" or "ask" (got \'never\')'])

    def test_the_stage_asks_with_ask(self):
        """taxjson-corp-actions --sheltered: "zero" (the default) emits
        the default rows; "ask" exits 3 without a TTY."""
        import subprocess
        import sys
        from tax_rules.dual import SRC
        with tempfile.TemporaryDirectory() as td:
            csv = Path(td) / "ib.csv"
            csv.write_text(_ib(CANADA, "U5550002",                 # pii-ok
                               trades=[("PARNT", "2025-01-10", 100, 10.0)],
                               corp=[(_SPIN, "2025-03-03", 25, 250)]))
            base = [sys.executable, "-m", "taxjson.bin.taxjson_corp_actions",
                    "--country", CANADA, "--brokerage", "ib", "--no-input",
                    "--elections", str(Path(td) / "m.json"), "--sheltered"]
            env = dict(os.environ, PYTHONPATH=str(SRC))
            z = subprocess.run(base + [str(csv)], capture_output=True,
                               text=True, env=env, stdin=subprocess.DEVNULL)
            self.assertEqual(z.returncode, 0, z.stderr)
            doc = json.loads(z.stdout)
            self.assertEqual(len(doc["metadata"]["sheltered_defaults"]), 1)
            a = subprocess.run(base + ["--sheltered-elections", "ask",
                                       str(csv)],
                               capture_output=True, text=True, env=env,
                               stdin=subprocess.DEVNULL)
            self.assertEqual(a.returncode, 3, a.stderr)


if __name__ == "__main__":
    unittest.main()
