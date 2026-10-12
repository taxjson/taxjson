"""Owner decision of 2026-10-02: renames are dated events (audit
A2-0197). Every fixture is synthetic: invented tickers, fake account ids
marked pii-ok."""
import io
import json
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.core import TaxTransaction
from taxjson.lib.corporate_timeline import SplitTimeline
from tax_rules import rule
from tax_rules.dual import cli, gains_both, projects_both, tx


def _gains(res):
    return [(g['symbol'], g['date'], round(g['gain'], 2),
             round(g.get('disallowed', 0) or 0, 2))
            for g in res['transactions'] if 'gain' in g]


def _reuse_book():
    """OLD renamed to NEW on 2024-06-01; NEW sold at a 500 loss; another
    company listed as OLD bought a week later."""
    return [tx("BUYSELL", "2024-01-10", "OLD.US", 100, 1000),
            tx("SPLIT", "2024-06-01", "OLD.US", 1.0, 0, symbol_new="NEW.US"),
            tx("BUYSELL", "2025-03-03", "NEW.US", -100, 500),
            tx("BUYSELL", "2025-03-10", "OLD.US", 100, 2000)]


# ------------------------------------------------------------ the engines
class TestDatedRenameEngines(unittest.TestCase):
    """A2-0197: OLD after its rename date is not the renamed security."""

    @rule("CA-ACB-RENAME")
    @rule("US-BASIS-RENAME")
    def test_reused_ticker_does_not_wash_the_renamed_loss(self):
        r = gains_both(_reuse_book(), year=2025)
        for c in ("canada", "usa"):
            g = [x for x in _gains(r[c]) if x[0] == "NEW.US"]
            self.assertEqual(g, [("NEW.US", "2025-03-03", -500.0, 0.0)], c)
            self.assertEqual(r[c]["summary"]["total_disallowed"], 0.0, c)
            self.assertIn("DIFFERENT security", r[c]["_stderr"], c)

    @rule("CA-ACB-RENAME")
    @rule("US-BASIS-RENAME")
    def test_rename_still_carries_identity_across_the_date(self):
        # A loss on OLD before the rename, NEW bought back after it: one
        # security (the rename carries the identity).
        book = [tx("BUYSELL", "2024-01-10", "OLD.US", 100, 1000),
                tx("BUYSELL", "2024-05-25", "OLD.US", -100, 600),
                tx("SPLIT", "2024-06-01", "OLD.US", 1.0, 0,
                   symbol_new="NEW.US"),
                tx("BUYSELL", "2024-06-10", "NEW.US", 100, 700)]
        r = gains_both(book, year=2024)
        for c in ("canada", "usa"):
            self.assertAlmostEqual(r[c]["summary"]["total_disallowed"],
                                   400.0, places=2, msg=c)

    @rule("CA-ACB-RENAME")
    @rule("US-BASIS-RENAME")
    def test_rename_carries_cost_and_acquisition_date(self):
        book = [tx("BUYSELL", "2023-01-10", "OLD.US", 100, 1000),
                tx("SPLIT", "2024-06-01", "OLD.US", 1.0, 0,
                   symbol_new="NEW.US"),
                tx("BUYSELL", "2025-03-03", "NEW.US", -100, 1500)]
        r = gains_both(book, year=2025)
        for c in ("canada", "usa"):
            g = [x for x in r[c]["transactions"] if "gain" in x]
            self.assertEqual(len(g), 1, c)
            self.assertAlmostEqual(g[0]["gain"], 500.0, places=2, msg=c)
        # The acquisition date carries: held since 2023 (long term).
        us = [x for x in r["usa"]["transactions"] if "gain" in x][0]
        self.assertGreater(us["days_held"], 365)


class TestSplitTimelineDatedClasses(unittest.TestCase):

    def _tl(self, rows):
        return SplitTimeline.from_transactions(rows)

    def test_same_classes_as_canonical_without_late_rows(self):
        rows = [tx("SPLIT", "2024-06-01", "A.US", 1.0, 0, symbol_new="B.US"),
                tx("SPLIT", "2024-09-01", "B.US", 2.0, 0, symbol_new="C.US")]
        tl = self._tl(rows)
        for sym, d in (("A.US", "2024-01-01"), ("B.US", "2024-07-01"),
                       ("C.US", "2025-01-01")):
            self.assertEqual(tl.class_at(sym, d), tl.class_at("C.US", d))
        # The SPLIT rows themselves act on the identity before the date.
        self.assertEqual(tl.class_of_row(rows[0]),
                         tl.class_at("C.US", "2025-01-01"))

    def test_late_row_is_its_own_class(self):
        tl = self._tl([tx("SPLIT", "2024-06-01", "OLD.US", 1.0, 0,
                          symbol_new="NEW.US")])
        new = tl.class_at("NEW.US", "2025-01-01")
        self.assertEqual(tl.class_at("OLD.US", "2024-05-31"), new)
        # On the rename date itself OLD is still the renamed security
        # (the S069-14 shape: the broker books the change that day).
        self.assertEqual(tl.class_at("OLD.US", "2024-06-01"), new)
        self.assertNotEqual(tl.class_at("OLD.US", "2024-06-02"), new)
        self.assertNotEqual(tl.class_at("OLD.US"), new)        # now
        # date-blind view unchanged
        self.assertEqual(tl.canonical("OLD.US"), tl.canonical("NEW.US"))

    def test_reused_ticker_renamed_again_joins_its_new_company(self):
        tl = self._tl([
            tx("SPLIT", "2024-06-01", "OLD.US", 1.0, 0, symbol_new="NEW.US"),
            tx("SPLIT", "2025-02-01", "XYZ.US", 1.0, 0, symbol_new="OLD.US")])
        self.assertEqual(tl.class_at("XYZ.US", "2025-01-15"),
                         tl.class_at("OLD.US", "2025-03-01"))
        self.assertNotEqual(tl.class_at("OLD.US", "2025-03-01"),
                            tl.class_at("NEW.US", "2025-03-01"))


class TestRadarDatedRename(unittest.TestCase):
    """The wash radar matches on the same dated classes as the engine."""

    @rule("CA-ACB-RENAME")
    def test_reused_ticker_is_not_a_violation_of_the_renamed_loss(self):
        from test_wash_radar import _run_json, _stx
        rows = [
            _stx("BUYSELL", "2026-01-05", "2026-01-06", "OLD.TO", 100, 50.0),
            {"action": "SPLIT", "date": "2026-03-02", "time": "00:00:00",
             "date_settle": "2026-03-02", "symbol": "OLD.TO",
             "symbol_new": "NEW.TO", "quantity": 1.0, "currency": "CAD",
             "account": "margin", "description": "rename"},
            _stx("BUYSELL", "2026-08-24", "2026-08-25", "NEW.TO", -100, 40.0),
            _stx("BUYSELL", "2026-09-01", "2026-09-02", "OLD.TO", 100, 41.0),
        ]
        cats = {t: r["category"]
                for t, r in _run_json(rows, "2026-09-10").items()}
        self.assertNotEqual(cats.get("NEW.TO"), "VIOLATION", cats)
        # ...while a rebuy of NEW is one.
        rows[-1] = _stx("BUYSELL", "2026-09-01", "2026-09-02", "NEW.TO",
                        100, 41.0)
        cats = {t: r["category"]
                for t, r in _run_json(rows, "2026-09-10").items()}
        self.assertEqual(cats.get("NEW.TO"), "VIOLATION", cats)


# ------------------------------------------------------------ ticker.map
class TestTickerMapRenameLines(unittest.TestCase):

    def _parse(self, text):
        from taxjson.bin.taxjson_ticker_map import _parse_map_file
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ticker.map"
            p.write_text(text)
            return _parse_map_file(p)

    def test_dated_and_undated(self):
        tmap, problems, _ = self._parse(
            "RENAME OLD.TO NEW.TO 2024-06-01 late=separate\n"
            "RENAME QQ.US RR.US\n"
            "GLOBAL A.US B.US\n")
        self.assertEqual(problems, [])
        self.assertEqual(len(tmap.dated), 1)
        d = tmap.dated[0]
        self.assertEqual((d.old, d.new, d.date, d.late),
                         ("OLD.TO", "NEW.TO", "2024-06-01", "separate"))
        # The undated RENAME is exactly GLOBAL.
        self.assertEqual(tmap.glob, {"QQ.US": "RR.US", "A.US": "B.US"})
        self.assertIn("QQ.US", tmap.undated_rename)

    def test_bad_lines_are_problems(self):
        for line in ("RENAME A.US B.US 2024-13-01",
                     "RENAME A.US B.US 2024-06-01 late=maybe",
                     "RENAME A.US B.US 2024-06-01 late=fold extra",
                     "RENAME A.US B.US 06/01/2024"):
            _, problems, _ = self._parse(line + "\n")
            self.assertEqual(len(problems), 1, line)

    def test_dated_and_undated_rule_for_one_symbol_conflict(self):
        _, problems, _ = self._parse("GLOBAL A.US B.US\n"
                                     "RENAME A.US C.US 2024-06-01\n")
        self.assertEqual(len(problems), 1)
        self.assertIn("undated", problems[0])

    def test_one_event_twice_is_a_problem(self):
        _, problems, _ = self._parse("RENAME A.US B.US 2024-06-01\n"
                                     "RENAME A.US B.US 2024-06-03\n")
        self.assertEqual(len(problems), 1)


def _t(action, date, sym, qty, net, acct="m", **kw):
    return TaxTransaction(action=action, date=date, time="10:00:00",
                          date_settle=date, symbol=sym, quantity=qty,
                          price=abs(net / qty) if qty else 0.0,
                          net_amount=net, currency="CAD", account=acct,
                          **kw)


class TestApplyDatedRenames(unittest.TestCase):

    def _dr(self, late=""):
        from taxjson.lib.renames import DatedRename
        return DatedRename("OLD.TO", "NEW.TO", "2024-06-01", late,
                           "ticker.map:1", "RENAME ...")

    def _apply(self, txs, dr):
        from taxjson.lib.renames import apply_dated_renames
        err = io.StringIO()
        out = apply_dated_renames(txs, [dr], stream=err)
        return out, err.getvalue()

    def test_books_the_event_in_each_holding_account(self):
        txs = [_t("BUYSELL", "2024-01-10", "OLD.TO", 100, 1000, acct="a"),
               _t("BUYSELL", "2024-02-10", "OLD.TO", 50, 600, acct="b"),
               _t("BUYSELL", "2024-03-10", "ZZZ.TO", 50, 600, acct="c"),
               _t("BUYSELL", "2025-01-10", "NEW.TO", -100, 900, acct="a")]
        out, err = self._apply(txs, self._dr())
        sp = [t for t in out if t.action == "SPLIT"]
        self.assertEqual(sorted((t.account, t.symbol, t.symbol_new,
                                 t.date, t.quantity) for t in sp),
                         [("a", "OLD.TO", "NEW.TO", "2024-06-01", 1.0),
                          ("b", "OLD.TO", "NEW.TO", "2024-06-01", 1.0)])
        self.assertTrue(all(t.source == "ticker.map" for t in sp))
        # placed in date order
        self.assertEqual([t.date for t in out], sorted(t.date for t in out))
        self.assertIn("booked in 2 account(s)", err)

    def test_broker_event_is_not_doubled(self):
        txs = [_t("BUYSELL", "2024-01-10", "OLD.TO", 100, 1000),
               _t("SPLIT", "2024-06-03", "OLD.TO", 1.0, 0,
                  symbol_new="NEW.TO")]
        out, _ = self._apply(txs, self._dr())
        self.assertEqual(len([t for t in out if t.action == "SPLIT"]), 1)

    def test_broker_event_to_another_symbol_is_refused(self):
        from taxjson.lib.renames import RenameConflict
        txs = [_t("BUYSELL", "2024-01-10", "OLD.TO", 100, 1000),
               _t("SPLIT", "2024-06-01", "OLD.TO", 1.0, 0,
                  symbol_new="OTHER.TO")]
        with self.assertRaises(RenameConflict):
            self._apply(txs, self._dr())

    def test_late_fold_rebooks_shares_and_options(self):
        txs = [_t("BUYSELL", "2024-01-10", "OLD.TO", 100, 1000),
               _t("BUYSELL", "2024-07-10", "OLD.TO", -100, 900),
               _t("BUYSELL", "2024-07-11", "OLD240920C00010000.TO", 1, 50)]
        out, err = self._apply(txs, self._dr("fold"))
        self.assertEqual([t.symbol for t in out if t.action != "SPLIT"],
                         ["OLD.TO", "NEW.TO", "NEW240920C00010000.TO"])
        self.assertIn("2 OLD.TO row(s)", err)


def _fake_project(td, base_rows, tmap="", raw_rows=()):
    root = Path(td)
    (root / "work").mkdir(parents=True, exist_ok=True)
    (root / "taxjson.toml").write_text(
        '[settings]\ncountry = "canada"\nbase_currency = "CAD"\n'
        'year = 2025\n[accounts.margin]\ntype = "taxable"\n')
    (root / "ticker.map").write_text(tmap)
    (root / "work" / "margin_base.json").write_text(json.dumps(
        {"transactions": [t.to_dict() for t in base_rows]}))
    (root / "work" / "margin_sources.list").write_text("tt/m.tt\n")
    (root / "work" / "margin_tt_m.json").write_text(json.dumps(
        {"transactions": [t.to_dict() for t in raw_rows]}))
    cfg = {"settings": {"country": "canada", "base_currency": "CAD",
                        "year": 2025},
           "accounts": {"margin": {"type": "taxable"}}}
    return root, cfg


class TestRenamesReport(unittest.TestCase):

    def test_undated_rule_gets_the_dated_form_from_the_broker_row(self):
        from taxjson.lib.renames import render, report
        raw = [_t("SPLIT", "2024-06-01", "OLD.TO", 1.0, 0,
                  symbol_new="NEW.TO", acct="margin")]
        with tempfile.TemporaryDirectory() as td:
            root, cfg = _fake_project(td, [], "GLOBAL OLD.TO NEW.TO\n", raw)
            doc = report(root, cfg)
        self.assertEqual(doc["undated"], [
            {"rule": "GLOBAL", "old": "OLD.TO", "new": "NEW.TO",
             "source": "legacy undated map",
             "broker_dates": ["2024-06-01"]}])
        # The dated form is a .tt line, date first (lib/dated_events).
        self.assertIn("RENAME 2024-06-01 OLD.TO NEW.TO",
                      "\n".join(render(doc)))

    def test_checklist_and_edge_cases_name_the_late_trade(self):
        from taxjson.lib import checklist, edge_cases
        rows = [_t("BUYSELL", "2024-01-10", "OLD.TO", 100, 1000,
                   acct="margin"),
                _t("SPLIT", "2024-06-01", "OLD.TO", 1.0, 0,
                   symbol_new="NEW.TO", acct="margin"),
                _t("BUYSELL", "2025-03-10", "OLD.TO", 10, 200,
                   acct="margin")]
        with tempfile.TemporaryDirectory() as td:
            root, cfg = _fake_project(td, rows)
            from datetime import date
            ctx = checklist.Ctx(root, cfg, 2025, date(2026, 1, 5),
                                lambda *a, **k: (0, "", ""))
            r = checklist.d_renames(ctx)
            self.assertEqual(r.status, "attention")
            late = edge_cases._renamed_late(root, cfg, None)
            self.assertEqual([(x["symbol"], x["resolution"]) for x in late],
                             [("OLD.TO", "unresolved")])
            (root / "ticker.map").write_text(
                "RENAME OLD.TO NEW.TO 2024-06-01 late=separate\n")
            self.assertEqual(checklist.d_renames(ctx).status, "done")


# ------------------------------------------------------- the project view
# Each country's project in its home currency (no FX rates needed).
_TT = ("BUYSELL 2024-01-10 10:00:00 OLD.{x} 100 {c} 10.00 -1000.00 0.00\n"
       "SPLIT 2024-06-01 09:00:00 OLD.{x} NEW.{x} 1\n"
       "BUYSELL 2025-03-03 10:00:00 NEW.{x} -100 {c} 5.00 500.00 0.00\n"
       "BUYSELL 2025-03-10 10:00:00 OLD.{x} 100 {c} 20.00 -2000.00 0.00\n")
_HOME = {"canada": ("TO", "CAD"), "usa": ("US", "USD")}


class TestRenamesCommand(unittest.TestCase):
    """`taxjson renames`, `run --strict` and the ticker.map declarations
    on a full run (both countries: the same rule in each)."""

    @rule("CA-ACB-RENAME")
    @rule("US-BASIS-RENAME")
    def test_late_trade_listed_strict_stops_until_declared(self):
        with tempfile.TemporaryDirectory() as td:
            ps = projects_both(td)
            for c, p in ps.items():
                x, cur = _HOME[c]
                (p / "inputs" / "margin").mkdir(parents=True)
                (p / "inputs" / "margin" / "m.tt").write_text(
                    _TT.format(x=x, c=cur))
                r = cli(p, "run", "--no-input", "--strict")
                self.assertNotEqual(r.returncode, 0, c)
                self.assertIn("renamed ticker after its rename", r.stderr, c)
                v = cli(p, "renames", "--json")
                self.assertEqual(v.returncode, 1, c)
                doc = json.loads(v.stdout)
                self.assertEqual(doc["unresolved"], 1, c)
                self.assertEqual(doc["late"][0]["symbol"], f"OLD.{x}")
                ev = doc["renames"][0]
                self.assertEqual((ev["date"], ev["old"], ev["new"]),
                                 ("2024-06-01", f"OLD.{x}", f"NEW.{x}"))
                self.assertEqual(ev["carried"][0]["qty"], 100)
                self.assertTrue(ev["sources"][0].startswith(".tt line"))
                # Declared another company: strict passes, loss allowed.
                (p / "ticker.map").write_text(
                    f"RENAME OLD.{x} NEW.{x} 2024-06-01 late=separate\n")
                r = cli(p, "run", "--no-input", "--strict")
                self.assertEqual(r.returncode, 0, c + r.stderr[-800:])
                self.assertEqual(cli(p, "renames").returncode, 0, c)
                # Declared the renamed shares: the OLD buy is NEW, and
                # the NEW loss a week before it is denied.
                (p / "ticker.map").write_text(
                    f"RENAME OLD.{x} NEW.{x} 2024-06-01 late=fold\n")
                r = cli(p, "run", "--no-input", "--strict")
                self.assertEqual(r.returncode, 0, c + r.stderr[-800:])
                g = json.loads((p / "work" / "margin_gains_wash.json")
                               .read_text())
                self.assertGreater(g["summary"]["total_disallowed"], 0, c)

    @rule("CA-ACB-RENAME")
    def test_dated_ticker_map_rename_books_the_event(self):
        tt = ("BUYSELL 2024-01-10 10:00:00 OLD.TO 100 CAD 10.00 -1000.00 0.00\n"
              "BUYSELL 2025-03-03 10:00:00 NEW.TO -100 CAD 15.00 1500.00 0.00\n")
        with tempfile.TemporaryDirectory() as td:
            p = projects_both(td, files={
                "inputs/margin/m.tt": tt,
                "ticker.map": "RENAME OLD.TO NEW.TO 2024-06-01\n"})["canada"]
            r = cli(p, "run", "--no-input", "--strict")
            self.assertEqual(r.returncode, 0, r.stderr[-800:])
            g = json.loads((p / "work" / "margin_gains_wash.json")
                           .read_text())
            gains = [x for x in g["transactions"] if "gain" in x]
            self.assertEqual([(x["symbol"], round(x["gain"], 2))
                              for x in gains], [("NEW.TO", 500.0)])
            doc = json.loads(cli(p, "renames", "--json").stdout)
            self.assertEqual(doc["renames"][0]["sources"],
                             ["ticker.map line"])
            self.assertEqual(doc["renames"][0]["carried"][0]["book_cost"],
                             1000.0)


if __name__ == '__main__':
    unittest.main()
