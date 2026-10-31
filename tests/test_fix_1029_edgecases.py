"""The 2026-10-29 fix round, group "edgecases": `tjs edge-cases`
and `tjs option-boundary` on synthetic books.

- TJ-0396: a contract on explicit close timing (option_premium_timing =
  "close") whose write year's lock records grant timing, or reports the
  premium, is a double count, flagged ATTENTION like a transition
  contract's (CA-OPT-07).
- TJ-0225: edge-cases names a lock reached through [settings]
  prior_year_record by its path, as option-boundary does, not as
  filed/<year>.json.

Made-up symbols (ZZQ, Q) and amounts only.
"""
import json
import os
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path

from tax_rules import rule

from test_fix_edge_cases_1026 import (DENIED as CA_DENIED,
                                      STOCK as CA_STOCK, _edge as _ca_edge,
                                      _t as _ct)
from test_fix_us_year_end_1027 import _edge as _us_edge, _t as _ut

from taxjson.lib import project_layout as PL
from taxjson.lib.core import TaxTransaction
from taxjson.lib.dispatch import python_module_argv
from taxjson.lib.edge_cases import render_text
from taxjson.lib.option_boundary import filed_locks, straddling

SRC = Path(__file__).resolve().parents[1] / "src"
OPT = "Q260116C00050000.TO"          # expires 2026-01-16
TODAY = date(2026, 9, 30)
GRANT25 = {2025: {"option_premium_timing": "grant",
                  "option_grant_since": 2025}}


def T(action="BUYSELL", **kw):
    base = {"action": action, "currency": "CAD", "account": "margin",
            "price": 1.0, "time": "10:00:00"}
    base.update(kw)
    base.setdefault("date_settle", base["date"])
    return TaxTransaction(**base)


def write(d="2025-12-15", q=-1, net=100.0, sym=OPT):
    return T(date=d, symbol=sym, quantity=q, net_amount=net)


def buy(d="2026-01-12", q=1, net=-50.0, sym=OPT, price=0.5):
    return T(date=d, symbol=sym, quantity=q, net_amount=net, price=price)


def close_rows(book, **kw):
    """The 2026 project's rows on explicit close timing (its default
    since is its own year)."""
    return straddling(book, 2026, "close", 2026, today=TODAY, **kw)


@rule("CA-OPT-07")
class TestExplicitCloseTimingDoubleCount(unittest.TestCase):
    """TJ-0396: 2025 is locked under grant timing (the 100.00 premium
    reported there); the 2026 project on close timing taxes it again."""

    def test_buyback_on_a_grant_lock_is_attention(self):
        (r,) = close_rows([write(), buy()], filed_years={2025},
                          filed_timing=GRANT25,
                          filed_labels={2025: "previous.json"})
        self.assertTrue(r["attention"], r)
        self.assertTrue(r["action"].startswith(
            "ATTENTION: 2025 is locked (previous.json) and its lock "
            "records grant timing, so the 2025 return reported the 100.00 "
            "premium; this project is on close timing "
            "(option_premium_timing = \"close\") and taxes it again in "
            "2026."), r["action"])
        self.assertIn('option_premium_timing = "grant" with '
                      'option_grant_timing_since = 2025', r["action"])
        # Not the old advice: T1-ADJ 2025 to ADD a premium that 2025
        # already reported.
        self.assertNotIn("2025 was filed: T1-ADJ 2025", r["action"])
        self.assertNotIn("the Act puts", r["action"])

    def test_expiry_and_open_rows_too(self):
        expired = T(date="2026-01-16", symbol=OPT, quantity=1,
                    net_amount=0.0, price=0.0)
        (r,) = close_rows([write(), expired], filed_years={2025},
                          filed_timing=GRANT25)
        self.assertEqual(r["close_kind"], "expiry")
        self.assertTrue(r["attention"], r)
        self.assertIn("taxes it again in 2026", r["action"])
        later = "Q270115C00050000.TO"
        (r,) = close_rows([write(sym=later)], filed_years={2025},
                          filed_timing=GRANT25)
        self.assertEqual(r["close_kind"], "open")
        self.assertTrue(r["attention"], r)
        self.assertIn("taxes it again when it closes", r["action"])

    def test_the_locks_reported_premium_is_evidence(self):
        # A lock that predates the timing record, but whose
        # dispositions report the write's premium as a 2025 gain.
        (r,) = close_rows([write(), buy()], filed_years={2025},
                          filed_timing={2025: {"note": "old lock"}},
                          filed_premiums={2025: {(OPT, "2025-12-15"):
                                                 100.0}})
        self.assertTrue(r["attention"], r)
        self.assertIn("its lock reports the 100.00 premium as a 2025 "
                      "gain", r["action"])

    def test_known_dispositions_without_the_premium_are_not_a_double(self):
        # The lock records grant timing, but its return's dispositions
        # leave the premium out: nothing was reported twice.
        (r,) = close_rows([write(), buy()], filed_years={2025},
                          filed_timing=GRANT25,
                          filed_premiums={2025: {}})
        self.assertFalse(r["attention"], r)

    def test_a_close_timing_lock_keeps_the_amendment(self):
        # 2025 filed on close timing: the premium is in no 2025 return,
        # and the Act puts it there (as before the fix).
        (r,) = close_rows([write(), buy()], filed_years={2025},
                          filed_timing={2025: {"option_premium_timing":
                                               "close"}})
        self.assertFalse(r["attention"], r)
        self.assertTrue(r["action"].endswith("; 2025 was filed: "
                                             "T1-ADJ 2025"), r["action"])

    def test_a_close_timing_lock_wins_over_a_same_day_gain(self):
        # The lock records close timing; a gain on the write date (a
        # same-day close of another lot) is not this premium.
        (r,) = close_rows([write(), buy()], filed_years={2025},
                          filed_timing={2025: {"option_premium_timing":
                                               "close"}},
                          filed_premiums={2025: {(OPT, "2025-12-15"):
                                                 40.0}})
        self.assertFalse(r["attention"], r)

    def test_no_lock_is_unchanged(self):
        (r,) = close_rows([write(), buy()])
        self.assertFalse(r["attention"])
        self.assertIn("enable grant timing with option_grant_timing_since "
                      "= 2025 to correct", r["action"])

    def test_the_locks_own_since_is_the_hint(self):
        (r,) = close_rows([write(), buy()], filed_years={2025},
                          filed_timing={2025: {"option_premium_timing":
                                               "grant",
                                               "option_grant_since": 2023}})
        self.assertIn("option_grant_timing_since = 2023", r["action"])

    def test_assignment_is_the_amendment(self):
        book = [write(),
                T(action="ASSIGN", date="2026-01-16", symbol=OPT,
                  quantity=1, net_amount=0.0, price=0.0),
                T(action="ASSIGN", date="2026-01-16",
                  date_settle="2026-01-19", symbol="Q.TO", quantity=-100,
                  net_amount=5000.0, price=50.0)]
        (r,) = close_rows(book, filed_years={2025}, filed_timing=GRANT25)
        self.assertEqual(r["close_kind"], "assignment")
        self.assertTrue(r["action"].startswith(
            "T1-ADJ 2025: remove the 100.00 premium reported for 2025 "
            "(s.49(4))"), r["action"])

    def test_transition_wording_is_unchanged(self):
        # Grant mode, written before since: the transition's own text.
        (r,) = straddling([write(), buy()], 2026, "grant", 2026,
                          filed_years={2025}, filed_timing=GRANT25,
                          today=TODAY)
        self.assertTrue(r["attention"])
        self.assertIn("this project keeps the contract on close timing "
                      "(option_grant_timing_since = 2026)", r["action"])


class TestFiledLocksLabels(unittest.TestCase):
    def test_labels_name_the_prior_record_by_its_path(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p26"
            (root / "filed").mkdir(parents=True)
            prev = Path(td) / "p25" / "filed"
            prev.mkdir(parents=True)
            (prev / "2025.json").write_text(json.dumps({"year": 2025}))
            (root / "filed" / "2024.json").write_text(
                json.dumps({"year": 2024}))
            labels = {}
            years, _timing = filed_locks(
                root, {"country": "canada", "year": 2026,
                       "prior_year_record": "../p25/filed/2025.json"},
                labels=labels)
        self.assertEqual(years, {2024, 2025})
        self.assertEqual(labels, {2024: "filed/2024.json",
                                  2025: "../p25/filed/2025.json"})


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *args]),
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        timeout=600)


@rule("CA-OPT-07")
class TestPriorRecordLockCLI(unittest.TestCase):
    """The audit's reproduction (TJ-0408 for TJ-0225, TJ-0396): a 2025
    project on grant timing writes a call for 100 and is closed; its
    lock is kept as previous.json, which the 2026 project (the same
    folder, year bumped) names as its prior_year_record; the call is
    bought back for 50 in 2026."""

    TT = ("BUYSELL 2025-12-15 10:00:00 ZZQ260116C00010000.TO -1 CAD 1 "
          "100 0\n"
          "BUYSELL 2026-01-12 10:00:00 ZZQ260116C00010000.TO 1 CAD 0.5 "
          "50 0\n")

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory(prefix="tj1029-")
        p = cls.root = Path(cls._td.name) / "p"
        (p / "inputs" / "margin").mkdir(parents=True)
        (p / "taxjson.toml").write_text(
            '[settings]\nyear = 2025\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = []\n\n'
            '[accounts.margin]\ntype = "taxable"\n')
        (p / "inputs" / "margin" / "m.tt").write_text(cls.TT)
        for args in (("run", "--no-input"), ("close-year", "--yes")):
            r = _cli(p, *args)
            assert r.returncode == 0, r.stdout + r.stderr
        (p / "filed" / "2025.json").rename(p / "previous.json")
        cls._set(year=2026, prior_year_record="previous.json")

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    @classmethod
    def _set(cls, **keys):
        toml = cls.root / "taxjson.toml"
        text = toml.read_text()
        for k, v in keys.items():
            text = PL.set_key_text(text, ("settings", k), v)
        toml.write_text(text)
        r = _cli(cls.root, "run", "--no-input")
        assert r.returncode == 0, r.stdout + r.stderr

    def _both(self):
        r = _cli(self.root, "edge-cases", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        (edge,) = json.loads(r.stdout)["year_boundary"]["written_options"]
        r = _cli(self.root, "option-boundary", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        ob = json.loads(r.stdout)
        (row,) = ob["rows"]
        return edge, row, ob

    def test_a_default_project_and_then_explicit_close_timing(self):
        # TJ-0225: grant timing by default (since = 2026): the 2025
        # contract is a transition contract taxed twice; edge-cases
        # named a filed/2025.json that does not exist.
        self._set(option_premium_timing="grant")
        edge, row, ob = self._both()
        self.assertTrue(edge["attention"])
        self.assertIn("2025 is locked (previous.json)", edge["action"])
        self.assertNotIn("filed/2025.json", edge["action"])
        self.assertEqual(edge["action"], row["action"])
        # TJ-0396: option_premium_timing = "close" said attention 0 and
        # "2025 was filed: T1-ADJ 2025" though 2025 reported the 100.
        self._set(option_premium_timing="close")
        edge, row, ob = self._both()
        self.assertEqual(ob["attention"], 1, ob)
        self.assertTrue(row["attention"])
        self.assertTrue(edge["attention"])
        self.assertEqual(edge["action"], row["action"])
        self.assertIn("2025 is locked (previous.json)", row["action"])
        self.assertIn('option_premium_timing = "close"', row["action"])
        self.assertNotIn("2025 was filed: T1-ADJ 2025", row["action"])
        # The default text says the row needs attention under it; the
        # details give the action.
        r = _cli(self.root, "edge-cases")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("needs attention: tjs option-boundary --details",
                      r.stdout)
        r = _cli(self.root, "edge-cases", "--details")
        self.assertIn("=> ATTENTION: 2025 is locked (previous.json)",
                      " ".join(r.stdout.split()))


@rule("US-RPT-13")
class TestShortDeferralWording(unittest.TestCase):
    """TJ-0342: a short covered at a loss and re-shorted in the window
    carries the denial in the replacement short's proceeds (Reg.
    §1.1091-1(g)); edge-cases worded it as the cost of units held."""

    SHORT = [_ut("margin", "2025-11-03", -10, 1000),
             _ut("margin", "2025-11-17", 10, -1500),
             _ut("margin", "2025-11-20", -10, 1000)]

    def test_short_rows_say_proceeds_and_covered(self):
        _doc, edge, text = _us_edge({"margin": self.SHORT})
        (r,) = edge["year_boundary"]["deferred_at_year_end"]
        self.assertEqual((r["qty"], r["deferred"]), (-10.0, 500.0))
        self.assertEqual(
            r["why"], "500.00 of denied losses sits in the proceeds of the "
            "10 units still short at the end of 2025; it reduces the gain "
            "(or grows the loss) of the year they are covered")
        self.assertNotIn("-10 units", r["why"])
        self.assertIn(r["why"], " ".join(text.split()))
        default = "\n".join(render_text(edge, width_=200))
        self.assertIn("negative UNITS: the proceeds of units still short",
                      " ".join(default.split()))

    def test_long_rows_are_unchanged(self):
        _doc, edge, _text = _us_edge({"margin": [
            _ut("margin", "2025-11-03", 10, -1000),
            _ut("margin", "2025-11-17", -10, 500),
            _ut("margin", "2025-11-20", 10, -500)]})
        (r,) = edge["year_boundary"]["deferred_at_year_end"]
        self.assertEqual(
            r["why"], "500.00 of denied losses sits in the cost of the 10 "
            "units still held at the end of 2025; it reduces the gain (or "
            "grows the loss) of the year they are sold")
        default = "\n".join(render_text(edge, width_=200))
        self.assertIn("DEFERRED: denied losses in the cost of units still "
                      "held; they lower the gain when sold.",
                      " ".join(default.split()))


def _deferred(edge):
    return [(r["account"], r["qty"], r["deferred"], r["as_of"], r["why"])
            for r in edge["year_boundary"]["deferred_at_year_end"]]


# A 500.00 loss on 2025-11-17 disallowed by the 10 shares bought back on
# 2025-11-20 (§1091(d)), which are sold on Dec 31: the trade settles
# 2026-01-02.
WASHED_DEC31 = [_ut("margin", "2025-11-03", 10, -1000),
                _ut("margin", "2025-11-17", -10, 500),
                _ut("margin", "2025-11-20", 10, -500),
                _ut("margin", "2025-12-31", -10, 700, settle="2026-01-02")]


@rule("US-RPT-13")
class TestUSSettleYearEnd(unittest.TestCase):
    """TJ-0343: a US project on tax_date = "settle" reads the lots at
    the end of Dec 31 on settlement dates: a sale traded Dec 31 that
    settles in January is a next-year disposition, and the disallowed
    loss in the basis of the lots it sells goes into that return."""

    def test_a_december_31_sale_settling_in_january(self):
        doc, edge, text = _us_edge({"margin": WASHED_DEC31},
                                   tax_date="settle")
        self.assertEqual(doc["inventory_year_end_basis"], "settle")
        ((acct, qty, dw, as_of, why),) = _deferred(edge)
        self.assertEqual((acct, qty, dw, as_of),
                         ("margin", 10.0, 500.0, "2025-12-31"))
        self.assertIn("500.00 of denied losses sits in the cost of the 10 "
                      "units still held at the end of 2025; it reduces",
                      why)
        self.assertNotIn("(by ", why)
        self.assertIn(why, " ".join(text.split()))
        # The straddle section still lists the sale.
        self.assertEqual(
            [(r["symbol"], r["lands_in"])
             for r in edge["year_boundary"]["straddles"]],
            [("ZZQ.US", 2026)])

    def test_on_trade_dates_the_sale_is_this_years(self):
        # The same books on trade dates: the sale is a 2025 disposition,
        # its deferral used in the 2025 return; nothing is carried.
        doc, edge, _text = _us_edge({"margin": WASHED_DEC31})
        self.assertNotIn("inventory_year_end_basis", doc)
        self.assertEqual(_deferred(edge), [])

    def test_a_replacement_settling_in_january_is_not_held(self):
        # The replacement trades Dec 31 and settles 2026-01-02: on
        # settlement dates it was not held at the end of 2025 (as a
        # January replacement; the window across Dec 31 lists it).
        rows = [_ut("margin", "2025-11-03", 10, -1000),
                _ut("margin", "2025-12-15", -10, 500),
                _ut("margin", "2025-12-31", 10, -500, settle="2026-01-02")]
        _doc, settle, _ = _us_edge({"margin": rows}, tax_date="settle")
        self.assertEqual(_deferred(settle), [])
        _doc, trade, _ = _us_edge({"margin": rows})
        self.assertEqual([r[:4] for r in _deferred(trade)],
                         [("margin", 10.0, 500.0, "2025-12-31")])

    def test_blended_accounts_keep_the_basis(self):
        doc, edge, _text = _us_edge({
            "margin": WASHED_DEC31,
            "margin2": [_ut("margin2", "2025-06-02", 5, -500)]},
            tax_date="settle")
        self.assertEqual([r[:4] for r in _deferred(edge)],
                         [("margin", 10.0, 500.0, "2025-12-31")])
        self.assertNotIn("(by ", _deferred(edge)[0][4])

    def test_an_older_gains_file_says_trade_date(self):
        # A gains file written before the fix has no
        # inventory_year_end_basis: its year end is the trade-date
        # walk's, and the settle project is told so.
        doc, _edge, _text = _us_edge({"margin": WASHED_DEC31[:3]},
                                     tax_date="settle")
        doc.pop("inventory_year_end_basis")
        from taxjson.lib.edge_cases import analyze
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "work").mkdir()
            (root / "work" / "margin_base.json").write_text(json.dumps(
                {"transactions": [t.to_dict() for t in WASHED_DEC31[:3]]}))
            (root / "work" / "margin_gains_wash.json").write_text(
                json.dumps(doc))
            edge = analyze(root, {
                "settings": {"year": 2025, "country": "usa",
                             "tax_date": "settle"},
                "accounts": {"margin": {"type": "taxable"}}})
        ((_a, _q, _dw, _as_of, why),) = _deferred(edge)
        self.assertIn("still held at the end of 2025 (by trade date);", why)


@rule("CA-RPT-07")
class TestCanadaTradeYearEnd(unittest.TestCase):
    """TJ-0343's mirror: a Canadian project on tax_date = "trade" reads
    the pools at the end of Dec 31 on trade dates. A sale traded Dec 31
    that settles in January is this year's disposition there; the
    deferral it released went into this year's return, not the next."""

    # 250.00 denied on 2025-11-17 (the other 5 units held on day 30).
    DEC31 = CA_DENIED + [_ct("margin", "2025-12-31", "2026-01-02",
                             CA_STOCK, -5, 400)]

    def _rows(self, edge):
        return [(r["account"], r["qty"], r["deferred"], r["as_of"])
                for r in edge["year_boundary"]["deferred_at_year_end"]]

    def test_the_december_31_sale_has_left_the_pool(self):
        doc, edge, _ = _ca_edge({"margin": self.DEC31}, tax_date="trade")
        self.assertEqual(doc["inventory_year_end_basis"], "trade")
        self.assertEqual(self._rows(edge), [])
        # On settlement dates (the CRA default) it is next year's sale.
        _doc, settle, _ = _ca_edge({"margin": self.DEC31})
        self.assertEqual(self._rows(settle),
                         [("margin", 5.0, 250.0, "2025-12-31")])
        self.assertNotIn("(by ", settle["year_boundary"]
                         ["deferred_at_year_end"][0]["why"])

    def test_blended_accounts_split_on_trade_dates(self):
        # margin2 buys 5 in December; margin sells its 5 on Dec 31. On
        # trade dates the pool left is margin2's 5 with half the
        # deferral; on settlement dates both accounts hold 5.
        books = {"margin": self.DEC31,
                 "margin2": [_ct("margin2", "2025-12-01", "2025-12-02",
                                 CA_STOCK, 5, -500)]}
        _doc, trade, _ = _ca_edge(books, tax_date="trade")
        self.assertEqual(self._rows(trade),
                         [("margin2", 5.0, 125.0, "2025-12-31")])
        _doc, settle, _ = _ca_edge(books)
        self.assertEqual(sorted(self._rows(settle)),
                         [("margin", 5.0, 125.0, "2025-12-31"),
                          ("margin2", 5.0, 125.0, "2025-12-31")])


class TestRenamesSayTheCostIsUnadjusted(unittest.TestCase):
    """TJ-0357: `tjs renames` carries the book cost as bought (a replay
    of the trades, docs/commands.md); a denied loss the books add to
    the replacement's cost is not in it. The view now says so, with the
    amount, so the figure is not read as the books' adjusted cost."""

    def _project(self, td, country):
        root = Path(td) / country
        (root / "inputs" / "margin").mkdir(parents=True)
        cur = "CAD" if country == "canada" else "USD"
        sfx = "TO" if country == "canada" else "US"
        (root / "taxjson.toml").write_text(
            f'[settings]\nyear = 2025\ncountry = "{country}"\n'
            f'base_currency = "{cur}"\nsource_currencies = []\n\n'
            f'[accounts.margin]\ntype = "taxable"\n')
        return root, cur, sfx

    def _renames(self, root):
        r = _cli(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr)
        r = _cli(root, "renames", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        (ev,) = json.loads(r.stdout)["renames"]
        text = _cli(root, "renames").stdout
        return ev, " ".join(text.split())

    def _check(self, country):
        with tempfile.TemporaryDirectory() as td:
            root, cur, sfx = self._project(td, country)
            # A 500 loss on 2025-03-03, the shares bought back on
            # 2025-03-10 (denied: added to their cost), then renamed.
            (root / "inputs" / "margin" / "m.tt").write_text(
                f"BUYSELL 2025-01-06 10:00:00 ZZQ.{sfx} 10 {cur} 100 "
                f"1000 0\n"
                f"BUYSELL 2025-03-03 10:00:00 ZZQ.{sfx} -10 {cur} 50 "
                f"500 0\n"
                f"BUYSELL 2025-03-10 10:00:00 ZZQ.{sfx} 10 {cur} 50 "
                f"500 0\n"
                f"RENAME 2025-07-01 ZZQ.{sfx} ZZR.{sfx}\n")
            ev, text = self._renames(root)
        self.assertEqual([(c["qty"], c["book_cost"]) for c in
                          ev["carried"]], [(10.0, 500.0)])
        self.assertEqual(ev["denied_not_in_cost"], 500.0)
        self.assertIn(f"book cost 500.00 {cur} carried", text)
        self.assertIn(f"Book cost is as bought: the 500.00 of denied "
                      f"losses on ZZQ.{sfx} by 2025-07-01 is in the "
                      f"replacement shares' cost in the books, not here "
                      f"(`tjs list` shows the adjusted cost).", text)

    @rule("US-BASIS-01")
    def test_us_wash_sale(self):
        self._check("usa")

    @rule("CA-ACB-01")
    def test_canada_superficial_loss(self):
        self._check("canada")

    def test_no_denial_no_note(self):
        with tempfile.TemporaryDirectory() as td:
            root, cur, sfx = self._project(td, "usa")
            (root / "inputs" / "margin" / "m.tt").write_text(
                f"BUYSELL 2025-01-06 10:00:00 ZZQ.{sfx} 10 {cur} 100 "
                f"1000 0\n"
                f"RENAME 2025-07-01 ZZQ.{sfx} ZZR.{sfx}\n")
            ev, text = self._renames(root)
        self.assertEqual(ev["denied_not_in_cost"], 0.0)
        self.assertNotIn("Book cost is as bought", text)


def _windows(edge, sym="ZZQ.US"):
    return [(w["loss_date"], w["allowed_by"], w["why"])
            for w in edge["year_boundary"]["loss_windows"]
            if w["symbol"] == sym]


@rule("US-RPT-05")
class TestUSAllowedReason(unittest.TestCase):
    """TJ-0286: a US loss across Dec 31 with nothing disallowed said "it
    was the lot sold, or an earlier loss used it" for every loss,
    unchecked. Now each purchase in the window is accounted for from
    the books, or the text claims no reason."""

    def test_the_purchase_is_the_lot_sold(self):
        _doc, edge, text = _us_edge({"margin": [
            _ut("margin", "2024-12-20", 10, -1000),
            _ut("margin", "2025-01-10", -10, 500)]})
        ((day, by, why),) = _windows(edge)
        self.assertEqual((day, by), ("2025-01-10", "lot_sold"))
        self.assertTrue(why.endswith(
            "allowed: none of those purchases is its replacement (margin "
            "2024-12-20 +10 is the lot sold)"), why)
        self.assertNotIn("or an earlier loss used it", text)

    def test_one_sale_of_two_lots(self):
        # One sale is a row per lot: the December lot is sold by the
        # same sale as the November one.
        _doc, edge, _text = _us_edge({"margin": [
            _ut("margin", "2024-11-01", 10, -1000),
            _ut("margin", "2024-12-20", 5, -500),
            _ut("margin", "2025-01-10", -15, 600)]})
        rows = _windows(edge)
        self.assertEqual([by for _d, by, _w in rows],
                         ["lot_sold", "lot_sold"])
        self.assertIn("(margin 2024-12-20 +5 is the lot sold)", rows[0][2])

    def test_the_purchase_replaced_another_loss(self):
        # 2026-01-05's 10 shares replace the 2025-12-10 loss (its wash
        # sale names them), so the 2025-12-20 loss has none left.
        _doc, edge, _text = _us_edge({"margin": [
            _ut("margin", "2025-11-01", 20, -2000),
            _ut("margin", "2025-12-10", -10, 500),
            _ut("margin", "2025-12-20", -10, 500),
            _ut("margin", "2026-01-05", 10, -500)]})
        rows = dict((d, (by, why)) for d, by, why in _windows(edge))
        self.assertIsNone(rows["2025-12-10"][0])            # denied
        by, why = rows["2025-12-20"]
        self.assertEqual(by, "replacement_used")
        self.assertTrue(why.endswith(
            "allowed: none of those purchases is its replacement (margin "
            "2026-01-05 +10 replaced the 2025-12-10 loss)"), why)

    def test_a_reason_the_books_do_not_hold_is_not_claimed(self):
        # The 2024-12-22 purchase replaced a 2024 loss, which is not in
        # this year's books: no reason is claimed.
        _doc, edge, _text = _us_edge({"margin": [
            _ut("margin", "2024-11-01", 20, -2000),
            _ut("margin", "2024-12-15", -10, 500),
            _ut("margin", "2024-12-22", 10, -500),
            _ut("margin", "2025-01-08", -10, 500)]})
        ((day, by, why),) = _windows(edge)
        self.assertEqual((day, by), ("2025-01-08", "unknown"))
        self.assertTrue(why.endswith(
            "no disallowance applied by the engine (the books do not "
            "record why margin 2024-12-22 +10 is not its replacement)"), why)
        self.assertNotIn("lot sold", why)


if __name__ == "__main__":
    unittest.main()
