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
with `taxjson elect` wins; a taxable account is still asked.

Every fixture is synthetic: invented tickers, amounts and account ids.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule, rule_absent
from tax_rules.dual import cli, settings_for

from _style import CapturedWidth

from taxjson.lib.country import CANADA, USA, HOME_CURRENCY

# The assertions read captured output (`note:`), TAXJSON_WIDTH=0.
_WIDTH = CapturedWidth()


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


_SPIN = ('PARNT(US0000000777) Spinoff  1 for 4 '
         '(SPNCO, SPINCO CORP, US0000000778)')
_MRG = ('ABC(CA0000000001) Merged(Acquisition) WITH CA0000000002 1 for 2 '
        '({t}, {n}, {i})')


def _ib(country, acct, *, trades=(), corp=(), xfers=()):
    """A synthetic IB activity statement in the country's currency:
    `trades` (symbol, date, qty, price), `corp` (description, date, qty,
    value) and `xfers` (symbol, date, qty, market value)."""
    cur = HOME_CURRENCY[country]
    exch = "TSE" if country == CANADA else "NYSE"
    out = ('Statement,Header,Field Name,Field Value\n'
           'Statement,Data,BrokerName,Interactive Brokers\n'
           'Statement,Data,Title,Activity Statement\n'
           'Statement,Data,Period,"January 1, 2025 - December 31, 2025"\n'
           'Account Information,Header,Field Name,Field Value\n'
           f'Account Information,Data,Account,{acct}\n'
           f'Account Information,Data,Base Currency,{cur}\n'
           'Financial Instrument Information,Header,Asset Category,'
           'Symbol,Description,Conid,Security ID,Underlying,Listing Exch,'
           'Multiplier,Expiry,Delivery Month,Type,Strike,Code\n')
    for i, sym in enumerate(("PARNT", "SPNCO", "ABC", "XYZ")):
        out += (f'Financial Instrument Information,Data,Stocks,{sym},'
                f'"{sym} SAMPLE CORP",99900010{i},,,{exch},1,,,COMMON,,\n')
    if trades:
        out += ('Trades,Header,DataDiscriminator,Asset Category,Currency,'
                'Account,Symbol,Date/Time,Quantity,T. Price,C. Price,'
                'Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code\n')
        for sym, d, q, p in trades:
            out += (f'Trades,Data,Order,Stocks,{cur},{acct},{sym},'
                    f'"{d}, 10:00:00",{q},{p},0,{-q * p},0,0,0,0,'
                    f'{"O" if q > 0 else "C"}\n')
    if corp:
        out += ('Corporate Actions,Header,Asset Category,Currency,Report '
                'Date,Date/Time,Description,Quantity,Proceeds,Value,'
                'Realized P/L,Code\n')
        for desc, d, q, v in corp:
            out += (f'Corporate Actions,Data,Stocks,{cur},{d},'
                    f'"{d}, 20:25:00","{desc}",{q},0,{v},0,\n')
    if xfers:
        out += ('Transfers,Header,Asset Category,Currency,Symbol,Date,Type,'
                'Direction,Xfer Company,Xfer Account,Qty,Xfer Price,'
                'Market Value,Realized P/L,Cash Amount,Code\n')
        for sym, d, q, mv in xfers:
            out += (f'Transfers,Data,Stocks,{cur},{sym},{d},ACATS,'
                    f'{"Out" if q < 0 else "In"},Other Broker,5550009,'  # pii-ok
                    f'{q},0,{mv if q > 0 else -mv},0,0,\n')
    return out


def _flat(r):
    return " ".join((r.stdout + r.stderr).split())


class _Base:
    """The checks, run per country by the tagged subclasses below."""
    COUNTRY = ""
    PLAN = ""            # the sheltered account's name (its plan)
    ELECTION = ""        # the country's FMV spin-off election

    # ------------------------------------------------------------ fixtures
    def _project(self, td, *, spin_in=None, mode=None, merger=False,
                 withdraw=False, margin_trades=()):
        """margin (taxable) and PLAN (sheltered), both IB. The spin-off
        (or, `merger`, an ABC -> XYZ share exchange) happens in `spin_in`
        (default: the sheltered account). `withdraw`: the 25 spun-off
        shares move from the plan to margin at a market value of 500 and
        margin sells them for 750."""
        c, plan = self.COUNTRY, self.PLAN
        spin_in = spin_in or plan
        root = Path(td) / "p"
        root.mkdir()
        extra = {"year": 2025, "source_currencies": []}
        if mode:
            extra["sheltered_elections"] = mode
        (root / "taxjson.toml").write_text(
            settings_for(c, **extra)
            + '[accounts.margin]\ntype = "taxable"\n'
            + f'[accounts.{plan}]\ntype = "sheltered"\ntransfers = true\n')
        if merger:
            ev = dict(trades=[("ABC", "2025-01-10", 100, 10.0)],
                      corp=[(_MRG.format(t="ABC", n="ABC CORP",
                                         i="CA0000000001"),
                             "2025-03-03", -100, -2500),
                            (_MRG.format(t="XYZ", n="XYZ CORP",
                                         i="CA0000000002"),
                             "2025-03-03", 50, 2500)])
        else:
            ev = dict(trades=[("PARNT", "2025-01-10", 100, 10.0)],
                      corp=[(_SPIN, "2025-03-03", 25, 250)])
        plan_kw = dict(ev) if spin_in == plan else {}
        margin_kw = dict(ev) if spin_in == "margin" else {}
        if withdraw:
            plan_kw["xfers"] = [("SPNCO", "2025-05-01", -25, 500)]
            margin_kw["xfers"] = [("SPNCO", "2025-05-02", 25, 500)]
            margin_kw["trades"] = (list(margin_kw.get("trades", []))
                                   + [("SPNCO", "2025-07-02", -25, 30.0)])
        if margin_trades:
            margin_kw["trades"] = (list(margin_kw.get("trades", []))
                                   + list(margin_trades))
        for acct, kw, aid in ((plan, plan_kw, "U5550002"),     # pii-ok
                              ("margin", margin_kw, "U5550001")):  # pii-ok
            d = root / "inputs" / acct
            d.mkdir(parents=True)
            if kw:
                d.joinpath("ib.csv").write_text(_ib(c, aid, **kw))
        return root

    def _corp_rows(self, root, acct):
        doc = json.loads((root / "work" / f"{acct}_ib_corp.json")
                         .read_text())
        return doc["transactions"]

    def _event_id(self, root, acct):
        return self._corp_rows(root, acct)[0]["corp_event_id"]

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
                          "cost only affects the holdings view — `taxjson "
                          "elect` sets a fair value if you want it there",
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
            # Nothing written to the manifest: an election there wins.
            man = json.loads((root / "inputs" / self.PLAN / "manifest.json")
                             .read_text())
            self.assertEqual(man.get("elections") or {}, {})
            self.assertFalse((root / "work" / "pending_elections.json")
                             .exists())
            p = cli(root, "elect", "--pending")
            self.assertIn("No pending elections", p.stdout)
            s = cli(root, "run", "--no-input", "--strict")
            self.assertEqual(s.returncode, 0, _flat(s)[-3000:])
            self.assertEqual(self._info_count(s, head), 1)
            sp = cli(root, "spinoffs", "--json")
            item = json.loads(sp.stdout)["spinoffs"][0]
            self.assertEqual((item["election"], item["flags"],
                              item["new_cost"]),
                             ("sheltered_default", [], 0.0))
            ck = cli(root, "checklist", "--all")    # the full list
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
            e = cli(root, "elect", self.PLAN, "--set",
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
        totals, with the plan's event defaulted or `elect`ed."""
        root = self._project(td, margin_trades=[
            ("SPNCO", "2025-02-03", 10, 20.0),
            ("SPNCO", "2025-03-10", -10, 10.0)])
        r = cli(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, _flat(r)[-3000:])
        if elect:
            eid = self._event_id(root, self.PLAN)
            e = cli(root, "elect", self.PLAN, "--set", f"{eid}={elect}")
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


@rule("CA-CORP-11")
class TestCanadaRegisteredAccount(_Base, unittest.TestCase):
    COUNTRY = CANADA
    PLAN = "lira"
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


@rule("US-CORP-12")
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

    @rule("CA-CORP-11")
    @rule_absent("CA-CORP-11", country="usa")
    @rule("US-CORP-12")
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
                    "--manifest", str(Path(td) / "m.json"), "--sheltered"]
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
