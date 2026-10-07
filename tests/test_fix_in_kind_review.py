"""Pre-release review of the in-kind feature (lib/in_kind,
price_chain.close_on; tax-logic CA-INKIND-* / US-INKIND-*).

- F1: a same-kind pair (taxable <-> taxable, registered <-> registered)
  is paired before any taxable <-> registered pair, whatever the gap; a
  cross-kind pair with another plausible candidate is NOT booked: a
  warning names both and the line that settles it (`run --strict`
  stops). `INKIND ... plan=own` declares a move of your own.
- F2: two identical moves are two booked rows (the merge kept one).
- F3: a transfer-out received by a plan in parts is warned about.
- F4 (test_feat_in_kind.TestCloseLookup): the close cache is keyed
  by the Yahoo spelling too.
- F6: INKIND refuses a non-finite value and an empty `plan=`.

Every fixture is synthetic: invented tickers, amounts and account ids.
"""
import json
import os
import tempfile
import unittest

from tax_rules import rule
from tax_rules.dual import cli
from test_feat_in_kind import (_QT, _filing, _flat, _ib, _project, _qt,
                               _run, _xfer_qt)


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"


_HEAD = "in-kind move(s) between your taxable and registered accounts"


def _cur(country):
    return (("CAD", "TSE", "QZK.TO") if country == "canada"
            else ("USD", "NYSE", "QZK.US"))


def _f1_root(td, country, name=None, extra=None):
    """margin (IB) sends 100 out on 03-14 -> margin2 (Questrade) gets 100
    on 03-20; the plan (IB) sends 100 out on 03-14 -> plan2 (Questrade)
    gets 100 on 03-17; margin2 sells the 100 at 20 (cost 1,000)."""
    cur, exch, _sym = _cur(country)
    plan = "tfsa" if country == "canada" else "ira"
    files = {
        "inputs/margin/ib.csv": _ib([("2025-01-10", 100, 10.0)],
                                    [("2025-03-14", -100, 600)],
                                    cur=cur, exch=exch),
        "inputs/margin2/questrade.csv": _QT
        + _xfer_qt("2025-03-20", 100, cur=cur, acct="55500002")  # pii-ok
        + _qt("2025-04-01", "Sell", -100, 20.0, cur=cur,
              acct="55500002"),                                    # pii-ok
        f"inputs/{plan}/ib.csv": _ib([("2025-01-10", 100, 9.0)],
                                     [("2025-03-14", -100, 600)],
                                     cur=cur, exch=exch,
                                     acct="U5550003"),             # pii-ok
        f"inputs/{plan}2/questrade.csv": _QT
        + _xfer_qt("2025-03-17", 100, cur=cur, acct="55500004"),   # pii-ok
    }
    files.update(extra or {})
    return _project(td, files, country=country, name=name or country,
                    accounts=('[accounts.margin]\ntype = "taxable"\n'
                              '[accounts.margin2]\ntype = "taxable"\n'
                              f'[accounts.{plan}]\ntype = "sheltered"\n'
                              'transfers = true\n'
                              f'[accounts.{plan}2]\ntype = "sheltered"\n'
                              'transfers = true\n'))


class TestSameKindPairsFirst(unittest.TestCase):
    """F1: own moves stay own moves, in both countries."""

    @rule("CA-INKIND-01")
    @rule("US-INKIND-01")
    def test_taxable_to_taxable_beats_a_closer_cross_kind_leg(self):
        with tempfile.TemporaryDirectory() as td:
            for country in ("canada", "usa"):
                root = _f1_root(td, country)
                r = _run(self, root)
                flat = _flat(r)
                self.assertNotIn(_HEAD, flat, country)
                self.assertNotIn("contribution margin", flat, country)
                self.assertNotIn("withdrawal", flat, country)
                t = _filing(root)
                self.assertEqual(t["gain"], 1000.0, (country, t))
                self.assertEqual(t.get("denied_contribution", 0.0), 0.0)
                self.assertFalse((root / "work" / "in_kind.json").exists(),
                                 country)

    def _leg(self, acct, qty, date, plan=""):
        from taxjson.lib.in_kind import Leg
        return Leg(account=acct, broker="ib", key="QZK.TO", plan=plan,
                   row={"action": "TRANSFER", "symbol": "QZK.TO",
                        "quantity": qty, "date": date})

    @rule("CA-INKIND-01")
    def test_unit_same_kind_globally_first(self):
        from taxjson.lib import in_kind as IK
        legs = [self._leg("margin", -100, "2025-03-14"),
                self._leg("margin2", 100, "2025-03-20"),
                self._leg("tfsa", -100, "2025-03-14", "tfsa"),
                self._leg("tfsa2", 100, "2025-03-17", "tfsa")]
        self.assertEqual(IK.pair(legs), [])

    @rule("CA-INKIND-01")
    def test_unit_cross_kind_with_another_candidate_is_ambiguous(self):
        from taxjson.lib import in_kind as IK
        # margin's transfer-out could be rrsp's or tfsa's transfer-in.
        legs = [self._leg("margin", -100, "2025-03-14"),
                self._leg("rrsp", 100, "2025-03-16", "rrsp"),
                self._leg("tfsa", 100, "2025-03-18", "tfsa")]
        moves = IK.pair(legs)
        self.assertEqual(len(moves), 1)
        m = moves[0]
        self.assertEqual((m.problem, m.registered.account,
                          [a.account for a in m.alternatives]),
                         ("ambiguous", "rrsp", ["tfsa"]))
        self.assertFalse(m.booked)
        # plan=tfsa on the INKIND line settles it for the tfsa leg.
        moves = IK.pair(legs, declared={id(legs[0].row): "tfsa"})
        self.assertEqual([(m.problem, m.registered.account) for m in moves],
                         [("", "tfsa")])
        # plan=own: no move at all.
        self.assertEqual(IK.pair(legs, own={id(legs[0].row)}), [])
        # plan=fhsa names no leg here: no pair with either plan's leg (the
        # line then declares a plan outside the project).
        self.assertEqual(IK.pair(legs, declared={id(legs[0].row): "fhsa"}),
                         [])

    @rule("CA-INKIND-01")
    def test_unit_two_taxable_sources_for_one_plan_leg(self):
        from taxjson.lib import in_kind as IK
        legs = [self._leg("margin", -100, "2025-03-14"),
                self._leg("cash", -100, "2025-03-15"),
                self._leg("rrsp", 100, "2025-03-16", "rrsp")]
        moves = IK.pair(legs)
        self.assertEqual([m.problem for m in moves], ["ambiguous"])
        # Declaring cash's move of your own leaves margin's unambiguous.
        moves = IK.pair(legs, own={id(legs[1].row)})
        self.assertEqual([(m.problem, m.taxable.account) for m in moves],
                         [("", "margin")])
        # Declaring cash's move in kind books cash -> rrsp.
        moves = IK.pair(legs, declared={id(legs[1].row): ""})
        self.assertEqual([(m.problem, m.taxable.account) for m in moves],
                         [("", "cash")])


class TestAmbiguousCrossKind(unittest.TestCase):
    """F1: a cross-kind pair with another candidate is not booked."""

    def _root(self, td, extra_tt=None):
        files = {"inputs/margin/ib.csv": _ib([("2025-01-10", 100, 10.0)],
                                             [("2025-03-14", -100, 1500)]),
                 "inputs/rrsp/questrade.csv": _QT
                 + _xfer_qt("2025-03-16", 100),
                 "inputs/tfsa/questrade.csv": _QT
                 + _xfer_qt("2025-03-18", 100,
                            acct="55500004")}                      # pii-ok
        if extra_tt:
            files["inputs/margin/inkind.tt"] = extra_tt
        return _project(td, files, accounts=(
            '[accounts.margin]\ntype = "taxable"\n'
            '[accounts.rrsp]\ntype = "sheltered"\ntransfers = true\n'
            '[accounts.tfsa]\ntype = "sheltered"\ntransfers = true\n'))

    @rule("CA-INKIND-01")
    def test_warned_not_booked_strict_stops_line_settles(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root(td)
            r = _run(self, root)
            flat = _flat(r)
            self.assertIn("contribution margin → rrsp: 100 QZK.TO on "
                          "2025-03-14: NOT booked — ambiguous", flat)
            self.assertIn("tfsa's transfer-in of 100 on 2025-03-18", flat)
            self.assertIn("INKIND 2025-03-14 QZK.TO -100 CAD <price per "
                          "share> plan=rrsp", flat)
            self.assertIn("INKIND 2025-03-14 QZK.TO -100 plan=own", flat)
            self.assertEqual(_filing(root)["gain"], 0.0)
            r = cli(root, "run", "--no-input", "--strict")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("in-kind move(s) or INKIND line(s) not booked",
                          _flat(r))
        with tempfile.TemporaryDirectory() as td:
            root = self._root(
                td, "INKIND 2025-03-14 QZK.TO -100 CAD 15 plan=tfsa\n")
            r = _run(self, root)
            self.assertIn("contribution margin → tfsa: 100 QZK.TO on "
                          "2025-03-14 at 1,500.00 CAD (INKIND line "
                          "inkind.tt:1): gain 500.00 CAD", _flat(r))
            self.assertNotIn("ambiguous", _flat(r))
            self.assertEqual(_filing(root)["gain"], 500.0)
            _run(self, root, "--strict")

    @rule("CA-INKIND-01")
    def test_plan_own_keeps_it_a_move_of_your_own(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root(td, "INKIND 2025-03-14 QZK.TO -100 plan=own\n")
            r = _run(self, root, "--strict")
            self.assertNotIn(_HEAD, _flat(r))
            self.assertEqual(_filing(root)["gain"], 0.0)

    @rule("CA-INKIND-01")
    def test_plan_own_matching_nothing_is_listed(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root(td, "INKIND 2025-06-14 QZK.TO -100 plan=own\n")
            r = _run(self, root)
            self.assertIn("the INKIND line inkind.tt:1 matches no transfer "
                          "row of margin", _flat(r))


class TestIdenticalMoves(unittest.TestCase):
    """F2: two identical moves are two sales."""

    @rule("CA-INKIND-02")
    def test_two_identical_contributions_are_both_booked(self):
        with tempfile.TemporaryDirectory() as td:
            files = {"inputs/margin/ib.csv": _ib(
                        [("2025-01-10", 100, 10.0)],
                        [("2025-03-14", -50, 750), ("2025-03-14", -50, 750)]),
                     "inputs/rrsp/questrade.csv": _QT
                     + _xfer_qt("2025-03-17", 50)
                     + _xfer_qt("2025-03-17", 50)}
            root = _project(td, files)
            r = _run(self, root)
            self.assertIn("2 in-kind move(s)", _flat(r))
            t = _filing(root)
            self.assertEqual((t["proceeds"], t["acb"], t["gain"]),
                             (1500.0, 1000.0, 500.0))
            hold = (root / "reports" / "margin_holdings.toml").read_text()
            self.assertNotIn("QZK", hold)

    @rule("CA-INKIND-04")
    def test_unit_rows_are_distinct(self):
        from taxjson.lib import in_kind as IK
        from taxjson.lib.in_kind import Leg

        def leg(acct, q, d, plan=""):
            return Leg(account=acct, broker="ib", key="QZK.TO", plan=plan,
                       row={"action": "TRANSFER", "symbol": "QZK.TO",
                            "quantity": q, "date": d, "currency": "CAD",
                            "market_value": 750})
        legs = [leg("margin", -50, "2025-03-14"),
                leg("margin", -50, "2025-03-14"),
                leg("rrsp", 50, "2025-03-17", "rrsp"),
                leg("rrsp", 50, "2025-03-17", "rrsp")]
        moves = IK.pair(legs)
        IK.value(moves)
        rows = IK.booked_rows(moves, "canada")["margin"]
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]["description"], rows[1]["description"])
        # The plan side, when the context lacks its rows.
        ctx = []
        self.assertEqual(IK.mark_sheltered(ctx, moves, currency="CAD"), 2)
        self.assertNotEqual(ctx[0]["description"], ctx[1]["description"])


class TestPartialDelivery(unittest.TestCase):
    """F3: a transfer-out a plan received in parts is warned about."""

    def _root(self, td, extra_tt=None):
        files = {"inputs/margin/ib.csv": _ib([("2025-01-10", 100, 10.0)],
                                             [("2025-03-14", -100, 1500)]),
                 "inputs/rrsp/questrade.csv": _QT
                 + _xfer_qt("2025-03-17", 60) + _xfer_qt("2025-03-17", 40)}
        if extra_tt:
            files["inputs/margin/inkind.tt"] = extra_tt
        return _project(td, files)

    @rule("CA-INKIND-01")
    def test_warned_strict_stops(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root(td)
            r = _run(self, root)
            flat = _flat(r)
            self.assertEqual(flat.count("possibly in-kind in parts"), 1,
                             flat)
            self.assertIn("margin: 100 QZK.TO out on 2025-03-14; rrsp "
                          "received 60 on 2025-03-17, 40 on 2025-03-17",
                          flat)
            self.assertIn("INKIND 2025-03-14 QZK.TO -100 CAD <price per "
                          "share>", flat)
            r = cli(root, "run", "--no-input", "--strict")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("possibly in-kind in parts", _flat(r))

    @rule("CA-INKIND-01", "CA-INKIND-04")
    def test_inkind_line_books_it_with_the_parts(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root(td, "INKIND 2025-03-14 QZK.TO -100 CAD 15\n")
            r = _run(self, root, "--strict")
            flat = _flat(r)
            self.assertIn("contribution margin → rrsp: 100 QZK.TO on "
                          "2025-03-14 at 1,500.00 CAD", flat)
            self.assertNotIn("in parts", flat)
            self.assertEqual(_filing(root)["gain"], 500.0)
            doc = json.loads((root / "work" / "in_kind.json").read_text())
            self.assertEqual(len(doc["moves"][0]["registered_legs"]), 2)

    @rule("CA-INKIND-01")
    def test_plan_own_silences_it(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._root(td, "INKIND 2025-03-14 QZK.TO -100 plan=own\n")
            r = _run(self, root, "--strict")
            self.assertNotIn("in parts", _flat(r))


class TestInkindLineChecks(unittest.TestCase):
    """F6."""

    @rule("CA-INKIND-06")
    def test_non_finite_and_empty_plan(self):
        from taxjson.bin.taxjson_convert_tt import parse_inkind_line as p
        with self.assertRaisesRegex(ValueError, "not a finite"):
            p("INKIND 2025-03-14 QZK.TO -1e308 CAD 1e308")
        with self.assertRaisesRegex(ValueError, "plan= needs a value"):
            p("INKIND 2025-03-14 QZK.TO -1 CAD 1 plan=")
        own = p("INKIND 2025-03-14 QZK.TO -100 plan=own")
        self.assertEqual((own["plan"], own["quantity"], own["total"]),
                         ("own", -100.0, None))
        self.assertEqual(p("INKIND 2025-03-14 QZK.TO -100 CAD 0 0 "
                           "plan=OWN")["plan"], "own")
        with self.assertRaisesRegex(ValueError, "plan=own"):
            p("INKIND 2025-03-14 QZK.TO -100 CAD plan=own")


if __name__ == "__main__":
    unittest.main()
