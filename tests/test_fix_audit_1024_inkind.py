"""Audit 2026-10-24, group "inkind": B20, B43, B05, B16, A13, B36, B37,
B14 — in-kind moves, the corporate views, the holdings folder, the
holdings TOML reader and the [[distributions]] ADJUST. Synthetic data
only."""
import io
import contextlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from taxjson.lib import in_kind as ik

from tax_rules import rule

OPT = "ZZQ270115C00010000.US"


def _tx(d, q, sym="ZZQ.US", action="BUYSELL", **kw):
    return dict(date=d, date_settle=d, time="10:00:00", account="margin",
                symbol=sym, action=action, quantity=q, net_amount=0,
                currency="USD", **kw)


class TestInKind(unittest.TestCase):

    @rule("CA-INKIND-06")
    def test_b20_option_valued_at_the_rows_contract_size(self):
        """B20: In-kind valuations ignore an adjusted option contract
        multiplier — one contract of an adjusted series (multiplier 50)
        quoted at 2 is worth 100, not 200, and its booked price is per
        that size."""
        leg = ik.Leg("margin", "generic",
                     _tx("2026-01-02", -1, sym=OPT, action="TRANSFER",
                         multiplier=50), OPT)
        m = ik.Move(ik.CONTRIBUTION, leg, None, "rrsp", 1, "2026-01-02")
        ik.value([m], lambda m: SimpleNamespace(price=2, currency="USD",
                                                day="2026-01-02"))
        self.assertEqual(m.fmv, 100.0)
        row = ik.booked_rows([m], "canada")["margin"][0]
        self.assertEqual((row["price"], row["net_amount"],
                          row["multiplier"]), (2.0, 100.0, 50))
        # No declared size: the standard 100 still applies.
        leg = ik.Leg("margin", "generic",
                     _tx("2026-01-02", -1, sym=OPT, action="TRANSFER"), OPT)
        m = ik.Move(ik.CONTRIBUTION, leg, None, "rrsp", 1, "2026-01-02")
        ik.value([m], lambda m: SimpleNamespace(price=2, currency="USD",
                                                day="2026-01-02"))
        self.assertEqual(m.fmv, 200.0)
        row = ik.booked_rows([m], "canada")["margin"][0]
        self.assertEqual(row["price"], 2.0)
        self.assertNotIn("multiplier", row)
        # The plan's leg's declared size counts when only it has one.
        plan = ik.Leg("rrsp", "generic",
                      _tx("2026-01-02", 1, sym=OPT, action="TRANSFER",
                          multiplier=10), OPT, "rrsp")
        m = ik.Move(ik.CONTRIBUTION, leg, plan, "rrsp", 1, "2026-01-02")
        ik.value([m], lambda m: SimpleNamespace(price=2, currency="USD",
                                                day="2026-01-02"))
        self.assertEqual(m.fmv, 20.0)
        row = ik.booked_rows([m], "canada")["margin"][0]
        self.assertEqual((row["price"], row["multiplier"]), (2.0, 10.0))

    @rule("CA-INKIND-01")
    def test_b43_parts_count_every_candidate_leg(self):
        """B43: In-kind split-delivery matching declares uniqueness after
        discarding candidates — a ninth leg that makes a second set of
        parts makes the match ambiguous (nothing booked), instead of the
        first set being booked as the only one."""
        d = ik.Leg("margin", "generic",
                   _tx("2026-01-02", -10, action="TRANSFER"), "ZZQ.US")

        def cands(qs):
            return [(i, ik.Leg("rrsp", "generic",
                               _tx("2026-01-02" if i < 8 else "2026-01-03",
                                   q, action="TRANSFER"), "ZZQ.US", "rrsp"))
                    for i, q in enumerate(qs)]
        self.assertEqual(ik._parts(d, cands([4, 6, 7, 7, 7, 7, 7, 7, 4]),
                                   10), [])
        # Without the ninth leg the one set is found, whatever its place.
        self.assertEqual(ik._parts(d, cands([7, 7, 7, 7, 7, 7, 4, 6]), 10),
                         [6, 7])
        # A ninth leg that makes no second set changes nothing.
        self.assertEqual(ik._parts(d, cands([4, 6, 7, 7, 7, 7, 7, 7, 9]),
                                   10), [0, 1])
        # Past the search budget nothing is proven unique: [].
        with patch.object(ik, "_PARTS_STEPS", 4):
            self.assertEqual(ik._parts(d, cands([4, 6, 7, 7, 7, 7, 7, 7]),
                                       10), [])


class TestHoldingsDir(unittest.TestCase):

    def test_a13_another_accounts_snapshot_is_not_claimed_by_file_name(self):
        """A13: Holdings discovery assigns an excluded account snapshot to
        another account by filename — a snapshot stating the broker id
        of an account with its own `holdings = [...]` is that account's
        (not compared, said in a note), not margin's for its name."""
        from taxjson.lib import holdings_dir as hd
        cfg = {"margin": {"account": "99900001"},
               "other": {"account": "99900002", "holdings": ["manual.toml"]}}
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "holdings").mkdir()
            snap = root / "holdings" / "margin.toml"
            snap.write_text('[meta]\nbroker_account="99900002"\n'
                            '[[holding]]\nsymbol="ZZQ.US"\nquantity=1\n')
            found, notes = hd.discover(root / "holdings", cfg, root)
            self.assertEqual(found, {})
            self.assertEqual(len(notes), 1)
            self.assertIn("is other's", notes[0])
            self.assertIn("99***", notes[0])
            self.assertNotIn("99900002", notes[0])
            # Its own id, or no id at all: margin's, as before.
            for meta in ('broker_account="99900001"\n', ""):
                snap.write_text(f"[meta]\n{meta}"
                                '[[holding]]\nsymbol="ZZQ.US"\nquantity=1\n')
                found, notes = hd.discover(root / "holdings", cfg, root)
                self.assertEqual((list(found), notes), (["margin"], []))


class TestHoldingsToml(unittest.TestCase):

    def test_b36_nonpositive_contract_multiplier_is_refused(self):
        """B36: Holdings import accepts a negative contract multiplier
        and creates negative cost — refused, naming the row."""
        from taxjson.lib import positions_reports as pr
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "holdings.toml"

            def write(key, value):
                p.write_text('[meta]\nas_of="2026-01-02"\n[[holding]]\n'
                             f'symbol="{OPT}"\nquantity=1\ncurrency="USD"\n'
                             f'average_entry_price=2\n{key}={value}\n')
            for key in ("contract_multiplier", "multiplier"):
                for bad in ("-50", "0"):
                    write(key, bad)
                    with self.assertRaisesRegex(
                            pr.PositionsReportError,
                            f"{key} {bad} is not a positive number"):
                        pr.read_positions(p)
            write("contract_multiplier", "50")
            self.assertEqual(pr.read_positions(p).rows[0].cost, 100.0)

    def test_b37_declared_holdings_count_must_match_the_tables(self):
        """B37: Holdings import ignores a declared holding count larger
        than the rows present — a file with fewer (or more) [[holding]]
        tables than its `holdings_count` is refused as incomplete."""
        from taxjson.lib import positions_reports as pr
        one = '[[holding]]\nsymbol="ZZQ.US"\nquantity=1\n'
        two = one + '[[holding]]\nsymbol="ZZR.US"\nquantity=2\n'
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "holdings.toml"
            for count, body in ((2, one), (1, two), (0, one)):
                p.write_text(f'[meta]\nholdings_count={count}\n'
                             f'as_of="2026-01-02"\n{body}')
                with self.assertRaisesRegex(
                        pr.PositionsReportError,
                        f"holdings_count = {count} but the file has "
                        f"{body.count('[[holding]]')} "):
                    pr.read_positions(p)
            p.write_text('[meta]\nholdings_count=2\nas_of="2026-01-02"\n'
                         + two)
            self.assertEqual(len(pr.read_positions(p).rows), 2)
            p.write_text('[meta]\nas_of="2026-01-02"\n' + one)
            self.assertEqual(len(pr.read_positions(p).rows), 1)


class TestCorpViews(unittest.TestCase):

    @rule("CA-DATE-14")
    def test_b05_held_applies_a_clock_stamped_split_at_the_open(self):
        """B05: Corporate holdings replay applies a late-stamped split
        after same-day trades — the view replays like the balance walk
        (a noon split scales yesterday's 10, not today's 5: 25, not 30)."""
        from taxjson.lib import corp_views as cv
        from taxjson.bin.taxjson_apply_distributions import balance_on
        rows = [_tx("2026-01-01", 10), _tx("2026-01-02", 5),
                _tx("2026-01-02", 2, action="SPLIT")]
        rows[-1]["time"] = "12:00:00"
        self.assertEqual(cv._held(rows, "ZZQ.US", "2026-01-02", True), 25.0)
        self.assertEqual(cv._held(rows, "ZZQ.US", "2026-01-02", True),
                         balance_on(rows, "ZZQ.US", "2026-01-02"))
        self.assertEqual(cv._held(rows, "ZZQ.US", "2026-01-02", False),
                         10.0)
        rows.append(_tx("2026-01-03", 1, action="SPLIT",
                        symbol_new="ZZR.US"))
        self.assertEqual(cv._held(rows, "ZZR.US", "9999-12-31", True),
                         25.0)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "work").mkdir()
            (root / "work" / "margin_base.json").write_text(
                json.dumps({"transactions": rows[:3]}))
            s = cv.splits(root, {"accounts": {"margin": {"type": "taxable"}}})
            self.assertEqual((s[0]["held_before"], s[0]["held_after"]),
                             (10.0, 20.0))

    def test_b16_saved_zero_beside_a_broker_value_is_explained(self):
        """B16: Spin-off view replaces an explicit zero-value election
        with broker FMV. The booking uses the broker's value when the
        saved hint is 0: a 0 is what a user enters when they do not
        know the value, so the broker's figure wins (the user's
        decision, 2026-10-25; the booking is pinned by
        test_fix_corp.TestSpinoffBrokerValue.test_default_uses_broker_value).
        The view showed that value with no word about the saved 0; it
        now says why the 0 is not used. It is not a flag (nothing to
        fix) and offers no command (`elect --set` takes no
        fmv_per_share while the broker reports a value)."""
        from taxjson.lib import corp_views as cv
        ev = SimpleNamespace(action_type="spinoff", source_symbol="ZZQ.US",
                             target_symbol="ZZR.US", ratio=1, ratio_new=1,
                             ratio_old=1, target_fmv=100, qty_received=10,
                             currency="USD", date="2026-01-02")
        cfg = {"settings": {"country": "canada"},
               "accounts": {"margin": {"type": "taxable"}}}

        def view(hints):
            man = {"event": {"election": "taxable_deemed_dividend",
                             "hints": hints}}
            with patch.object(cv, "_current_events",
                              return_value={"event": ev}), \
                    patch.object(cv, "_manifest", return_value=man), \
                    patch.object(cv, "_rows", return_value=[]):
                return cv.spinoffs(Path("/synthetic"), cfg)
        doc = view({"fmv_per_share": 0})
        row = doc["spinoffs"][0]
        self.assertEqual((row["fmv_per_share"], row["value_from_broker"],
                          row["flags"]), (10.0, True, []))
        why = "\n".join(row["why"])
        self.assertIn("the saved fmv_per_share=0 is not used", why)
        self.assertIn("100.00 USD", why)
        self.assertNotIn("tjs elect", why)
        text = "\n".join(cv.render_spinoffs(doc))
        self.assertIn("the saved fmv_per_share=0 is not used", text)
        self.assertNotIn("HINT-UNUSED", text)
        # No hint saved: the broker's value is the default, no flag.
        row = view({})["spinoffs"][0]
        self.assertEqual((row["fmv_per_share"], row["flags"]), (10.0, []))


class TestDistributions(unittest.TestCase):

    def _gains(self, rows):
        from taxjson.lib.core import TaxTransaction, USATaxRules
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            return USATaxRules().compute_gains(
                [TaxTransaction(**r) for r in rows], detect_wash_sales=False)

    @rule("US-DIST-01")
    def test_b14_each_lot_settled_by_the_record_date_takes_its_part(self):
        """B14: Distribution backdating puts a later eligible lots
        adjustment on the oldest lot — three 10-share lots, the middle
        one settling after the record date: the first and third take
        10 each (cost 110), the middle one nothing (100)."""
        from taxjson.bin import taxjson_apply_distributions as dist
        rows = [_tx("2026-01-01", 10), _tx("2026-01-02", 10),
                _tx("2026-01-03", 10)]
        rows[1]["date_settle"] = "2026-01-06"
        rows[2]["date_settle"] = "2026-01-04"
        for r in rows:
            r["net_amount"], r["price"] = 100, 10
        self.assertEqual(dist._stamp_date(rows, "ZZQ.US", "2026-01-05",
                                          "margin"), "2026-01-05")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            adjusted, n = dist.apply_distributions(
                {"transactions": rows, "metadata": {"target_currency": "USD"}},
                [("ZZQ.US", "2026-01-05", 1)], "margin", country="usa")
        self.assertEqual(n, 1)
        adj = [t for t in adjusted["transactions"] if t["action"] == "ADJUST"]
        self.assertEqual([(a["date"], a["date_settle"], a["record_date"],
                           a["net_amount"]) for a in adj],
                         [("2026-01-05", "2026-01-05", "2026-01-05", 20.0)])
        self.assertNotIn("ATTENTION", err.getvalue())
        sales = [_tx("2026-01-07", -10), _tx("2026-01-08", -10),
                 _tx("2026-01-09", -10)]
        for s in sales:
            s["net_amount"], s["price"] = 200, 20
        result = self._gains(adjusted["transactions"] + sales)
        self.assertEqual([t["cost"] for t in result["transactions"]],
                         [110.0, 100.0, 110.0])

    @rule("US-DIST-01")
    def test_b14_straddling_sale_still_backdates_and_misses_are_said(self):
        """B14: a sale straddling the record date keeps the backdated
        stamp (its lot is the holder's); a purchase between that sale
        and the record date cannot then take its part, and the US run
        says so (ATTENTION) instead of placing it silently; Canada's
        pool needs no lot and gets no warning."""
        from taxjson.bin import taxjson_apply_distributions as dist
        rows = [_tx("2026-01-01", 10), _tx("2026-01-02", -10),
                _tx("2026-01-03", 10)]
        rows[1]["date_settle"] = "2026-01-06"
        rows[2]["date_settle"] = "2026-01-04"
        self.assertEqual(dist._stamp_date(rows, "ZZQ.US", "2026-01-05",
                                          "margin"), "2026-01-01")
        self.assertIsNone(dist._stamp_problem(rows, "ZZQ.US", "2026-01-05",
                                              "margin", "2026-01-05"))
        self.assertIn("bought 10 on 2026-01-03",
                      dist._stamp_problem(rows, "ZZQ.US", "2026-01-05",
                                          "margin", "2026-01-01"))
        for country, said in (("usa", True), ("canada", False)):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                adjusted, n = dist.apply_distributions(
                    {"transactions": [dict(r) for r in rows],
                     "metadata": {"target_currency": "USD"}},
                    [("ZZQ.US", "2026-01-05", 1)], "margin",
                    country=country)
            self.assertEqual(n, 1, country)
            self.assertEqual("ATTENTION" in err.getvalue(), said, country)
            adj = [t for t in adjusted["transactions"]
                   if t["action"] == "ADJUST"][0]
            self.assertEqual((adj["date"], adj["net_amount"]),
                             ("2026-01-01", 20.0))

    @rule("US-DIST-01")
    def test_b14_engine_skips_lots_settling_after_the_record_date(self):
        """B14: the US engine spreads a 'dist' ADJUST with a record date
        over the open lots settled by it only; with none settled the
        row is not applied and says so."""
        rows = [_tx("2026-01-01", 10), _tx("2026-01-02", 10)]
        rows[1]["date_settle"] = "2026-01-06"
        for r in rows:
            r["net_amount"], r["price"] = 100, 10
        adj = dict(action="ADJUST", date="2026-01-05", time="23:59:58",
                   date_settle="2026-01-05", record_date="2026-01-05",
                   symbol="ZZQ.US", quantity=0.0, net_amount=10.0,
                   currency="USD", type="dist", account="margin",
                   id="DIST-ZZQ.US-2026-01-05-margin")
        sales = [_tx("2026-01-07", -10), _tx("2026-01-08", -10)]
        for s in sales:
            s["net_amount"], s["price"] = 200, 20
        result = self._gains(rows + [adj] + sales)
        self.assertEqual([t["cost"] for t in result["transactions"]],
                         [110.0, 100.0])
        # Without a record date (a .tt ADJUST, a broker's row): pro rata
        # over every open lot, as before.
        plain = dict(adj, record_date="", type="")
        result = self._gains(rows + [plain] + sales)
        self.assertEqual([t["cost"] for t in result["transactions"]],
                         [105.0, 105.0])
        # Every open lot settling after the record date: not applied.
        late = dict(adj, record_date="2025-12-31")
        result = self._gains(rows + [late] + sales)
        self.assertEqual([t["cost"] for t in result["transactions"]],
                         [100.0, 100.0])


if __name__ == "__main__":
    unittest.main()
