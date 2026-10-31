"""Regression tests for GitHub issue #92 (area `split-dedup`): one split
reported twice (two sources, adjacent dates) must be ONE corporate event
in every walk of split events — the gains engine, the record-date holding
of a [[distributions]] row, the settle-lag re-denomination of a trade
straddling the split, and the per-account apportionment of a blended
pool. All data synthetic: fake tickers, fake account ids."""
import json
import tempfile
import unittest

from taxjson.lib.core import TaxTransaction
from tax_rules import rule
from tax_rules.dual import cli_both, projects_both

_QT_HEADER = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
              "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
              "Account #,Activity Type,Account Type\n")


def _row(action, date, symbol, qty, *, settle=None, account="margin",
         time="10:00:00", **kw):
    return dict({"action": action, "date": date, "date_settle": settle or date,
                 "time": time, "symbol": symbol, "quantity": float(qty),
                 "account": account}, **kw)


def _book(account="margin", sym="SYNTH.TO"):
    """The issue's book: buy 1000, a 200-share sale executed BEFORE a 2:1
    split and settling after it, the split reported twice (06-03 and
    06-04), then the rest sold."""
    return [
        _row("BUYSELL", "2025-01-06", sym, 1000, settle="2025-01-07",
             account=account),
        _row("BUYSELL", "2025-06-02", sym, -200, settle="2025-06-05",
             account=account),
        _row("SPLIT", "2025-06-03", sym, 2, account=account,
             time="00:00:01", symbol_new=sym),
        _row("SPLIT", "2025-06-04", sym, 2, account=account,
             time="00:00:01", symbol_new=sym),
        _row("BUYSELL", "2025-09-02", sym, -1600, settle="2025-09-03",
             account=account),
    ]


class TestSharedSplitDedup(unittest.TestCase):
    """The one corporate-event dedup serves engine rows and dict rows."""

    @rule("CA-CORP-12")
    @rule("US-CORP-13")
    def test_dicts_and_engine_rows_dedupe_alike(self):
        from taxjson.lib.corporate_timeline import (SplitTimeline,
                                                    dedupe_split_events)
        rows = _book()
        kept = [t for t in dedupe_split_events(rows)
                if t["action"] == "SPLIT"]
        self.assertEqual([t["date"] for t in kept], ["2025-06-03"])
        objs = [TaxTransaction(**{k: v for k, v in r.items()})
                for r in rows]
        kept_o = [t for t in SplitTimeline.dedupe(objs)
                  if t.action == "SPLIT"]
        self.assertEqual([t.date for t in kept_o], ["2025-06-03"])

    @rule("CA-CORP-12")
    @rule("US-CORP-13")
    def test_earliest_copy_kept_whatever_the_row_order(self):
        from taxjson.lib.corporate_timeline import dedupe_split_events
        rows = list(reversed(_book()))
        kept = [t for t in dedupe_split_events(rows)
                if t["action"] == "SPLIT"]
        self.assertEqual([t["date"] for t in kept], ["2025-06-03"])

    @rule("CA-CORP-12")
    @rule("US-CORP-13")
    def test_per_account_keeps_each_accounts_copy(self):
        from taxjson.lib.corporate_timeline import dedupe_split_events
        rows = _book("A") + _book("B")
        g = [t for t in dedupe_split_events(rows) if t["action"] == "SPLIT"]
        p = [t for t in dedupe_split_events(rows, per_account=True)
             if t["action"] == "SPLIT"]
        self.assertEqual(len(g), 1)
        self.assertEqual(sorted(t["account"] for t in p), ["A", "B"])


class TestSettleLagCountsTheSplitOnce(unittest.TestCase):
    """Issue #92: the settle-lag factor multiplied by both copies."""

    @rule("CA-DIST-01", "CA-CORP-12")
    @rule("US-DIST-01", "US-CORP-13")
    def test_lag_factor_is_the_ratio_once(self):
        from taxjson.bin.taxjson_apply_distributions import (
            _settle_lag_factors)
        rows = _book()
        f = _settle_lag_factors(rows)
        self.assertEqual(list(f.values()), [2.0])
        self.assertIn(id(rows[1]), f)

    @rule("CA-DIST-01", "CA-CORP-12")
    @rule("US-DIST-01", "US-CORP-13")
    def test_record_date_holding(self):
        from taxjson.bin.taxjson_apply_distributions import balance_on
        # (1000 - 200) x 2: the 06-02 sale is not the record holder's
        # shares on 06-06 (it settled 06-05).
        self.assertAlmostEqual(balance_on(_book(), "SYNTH.TO",
                                          "2025-06-06"), 1600.0)
        self.assertAlmostEqual(balance_on(_book(), "SYNTH.TO",
                                          "2025-12-31"), 0.0)

    @rule("CA-DIST-01")
    @rule("US-DIST-01")
    def test_control_single_report(self):
        from taxjson.bin.taxjson_apply_distributions import balance_on
        rows = [r for r in _book() if r["date"] != "2025-06-04"]
        self.assertAlmostEqual(balance_on(rows, "SYNTH.TO", "2025-06-06"),
                               1600.0)


class TestSplitGainsApportionment(unittest.TestCase):
    """Issue #92, second output: the per-account apportionment of a
    blended (account-less) Canadian pool walks the account's own base
    book with the same helper."""

    @rule("CA-CORP-12")
    def test_account_share_of_blended_pool(self):
        from taxjson.bin.taxjson_split_gains import split_for_account
        # Account A: the issue's lagged sale and duplicate split, still
        # holding 600 post-split shares; account B: 500 pre-split shares.
        a = _book("A")[:-1] + [_row("BUYSELL", "2025-09-02", "SYNTH.TO",
                                    -1000, settle="2025-09-03",
                                    account="A")]
        b = [_row("BUYSELL", "2025-01-06", "SYNTH.TO", 500,
                  settle="2025-01-07", account="B"),
             _row("SPLIT", "2025-06-03", "SYNTH.TO", 2, account="B",
                  time="00:00:01", symbol_new="SYNTH.TO")]
        combined = {"transactions": [], "inventory": [
            {"symbol": "SYNTH.TO", "qty": 1600.0, "total_cost": 16000.0}],
            "summary": {"tax_date_basis": "settle", "year": 2025}}
        inv_a = split_for_account(combined, "A", a)["inventory"]
        inv_b = split_for_account(combined, "B", b)["inventory"]
        self.assertAlmostEqual(inv_a[0]["qty"], 600.0)
        self.assertAlmostEqual(inv_a[0]["total_cost"], 6000.0)
        self.assertAlmostEqual(inv_b[0]["qty"], 1000.0)


def _simple(account="A", sym="QZS.TO", second="2025-06-04"):
    """Buy 100, a 2:1 split reported twice (06-03 and `second`), then
    nothing else: 200 shares held after the split."""
    return [
        _row("BUYSELL", "2025-01-06", sym, 100, account=account),
        _row("SPLIT", "2025-06-03", sym, 2, account=account,
             time="00:00:01"),
        _row("SPLIT", second, sym, 2, account=account, time="00:00:01"),
    ]


class TestOtherSplitWalksCountOnce(unittest.TestCase):
    """The sweep for issue #92: every other walk that scales positions by
    a split's ratio asks the same split_copies rule."""

    @rule("CA-CORP-12")
    @rule("US-CORP-13")
    def test_positions_on_two_accounts(self):
        # `tjs sanity` walks a group of accounts keyed by symbol:
        # each account's copy of the split is the same event.
        from taxjson.lib.positions_check import positions_on
        rows = _simple("A") + _simple("B", second="2025-06-03")
        self.assertAlmostEqual(positions_on(rows, "2025-12-31")["QZS.TO"],
                               400.0)

    @rule("CA-CORP-12")
    def test_export_coverage_open_positions(self):
        from datetime import date
        from taxjson.lib.export_coverage import open_positions
        self.assertEqual(open_positions(_simple(), date(2025, 12, 31)),
                         [("QZS.TO", 200.0)])

    @rule("CA-CORP-12")
    def test_edge_cases_walk(self):
        from taxjson.lib.edge_cases import _walk
        from datetime import date
        rows = [dict(r, _acct=r["account"]) for r in _simple()]
        pos = _walk(rows, lambda r: date.fromisoformat(r["date"]))
        self.assertAlmostEqual(pos[("A", "QZS.TO")], 200.0)

    @rule("CA-CORP-12")
    def test_cross_listing_radar_held(self):
        from datetime import date
        from taxjson.lib.xlist_loss_radar import _held_at
        self.assertAlmostEqual(_held_at(_simple(), date(2025, 12, 31),
                                        "canada"), 200.0)

    @rule("CA-CORP-12")
    def test_lint_crosslistings_net(self):
        from taxjson.bin.taxjson_lint_crosslistings import _net_by_symbol
        rows = [dict(r, _src="a.json") for r in _simple()]
        net = _net_by_symbol(rows)[0]
        self.assertAlmostEqual(net["QZS.TO"], 200.0)

    @rule("CA-CORP-12")
    def test_renames_carried_quantity(self):
        from taxjson.lib.renames import _walk_states
        rows = _simple() + [_row("SPLIT", "2025-08-01", "QZS.TO", 1,
                                 account="A", symbol_new="QZN.TO")]
        snaps = _walk_states(rows)
        self.assertEqual(snaps[id(rows[-1])][0], 200.0)

    @rule("CA-CORP-12")
    @rule("US-CORP-13")
    def test_option_walks(self):
        from taxjson.lib.option_close_check import unbacked_option_closes
        opt = "QZS250919C00010000"
        rows = [TaxTransaction(**r) for r in (
            _row("BUYSELL", "2025-01-06", opt, 1, account="A"),
            _row("SPLIT", "2025-06-03", opt, 2, account="A"),
            _row("SPLIT", "2025-06-04", opt, 2, account="A"),
            _row("BUYSELL", "2025-07-01", opt, -3, account="A",
                 open_close="C"))]
        # 2 contracts after the one split: a close of 3 sells more than
        # is held and is flagged (with the split doubled, 4 "backed" it).
        got = unbacked_option_closes(rows)
        self.assertEqual([(g["symbol"], g["quantity"]) for g in got],
                         [(opt, -3.0)])

    @rule("CA-CORP-12")
    def test_splits_view_second_copy_changes_nothing(self):
        from taxjson.lib.corp_views import _held
        self.assertAlmostEqual(_held(_simple(), "QZS.TO", "2025-12-31",
                                     True), 200.0)


def _files(country):
    sym, ccy, tt_sym = (("SYNTH.TO", "CAD", "SYNTH.TO") if country == "canada"
                        else ("SYNTH", "USD", "SYNTH.US"))
    csv = _QT_HEADER + "".join(
        f"{d},{s},{a},{sym},Synthetic,{q},{p},{g},0,{n},{ccy},"
        f"99900001,Trades,Individual\n"   # pii-ok: synthetic account
        for d, s, a, q, p, g, n in (
            ("2025-01-06", "2025-01-07", "Buy", 1000, 10, 10000, -10000),
            ("2025-06-02", "2025-06-05", "Sell", -200, 15, 3000, 3000),
            ("2025-09-02", "2025-09-03", "Sell", -1600, 10, 16000, 16000)))
    tt = (f"SPLIT 2025-06-03 00:00:01 {tt_sym} {tt_sym} 2\n"
          f"SPLIT 2025-06-04 00:00:01 {tt_sym} {tt_sym} 2\n")
    return {"inputs/margin/questrade.csv": csv,
            "inputs/margin/splits.tt": tt}, tt_sym


class TestIssue92EndToEnd(unittest.TestCase):
    """The issue's synthetic repro through `tjs run`, both countries:
    duplicate split reports + a sale settling across the split + a
    reinvested per-share distribution + a later sale."""

    @rule("CA-DIST-01", "CA-CORP-12")
    @rule("US-DIST-01", "US-CORP-13")
    def test_distribution_and_gains(self):
        from taxjson.lib import country as C
        with tempfile.TemporaryDirectory() as td:
            projects = {}
            for c in C.COUNTRIES:
                files, tt_sym = _files(c)
                # One project per country (each with its own listing).
                p = projects_both(
                    f"{td}/{c}", files=files,
                    tail=("[[distributions]]\n"
                          f'symbol = "{tt_sym}"\n'
                          "record_date = 2025-06-06\n"
                          "per_share = 0.5\n"))
                projects[c] = p[c]
            runs = cli_both(projects, "run", "--no-input")
            for c, r in runs.items():
                self.assertEqual(r.returncode, 0, f"{c}: {r.stderr[-2000:]}")
            for c, root in projects.items():
                base = json.loads((root / "work" / "margin_base.json")
                                  .read_text())
                txs = base["transactions"] if isinstance(base, dict) \
                    else base
                adj = [t for t in txs if t.get("action") == "ADJUST"
                       and str(t.get("id") or "").startswith("DIST-")]
                self.assertEqual(len(adj), 1, c)
                self.assertAlmostEqual(abs(float(adj[0]["net_amount"])),
                                       800.0, msg=c)
                # The run's wash radar walks the same split (the radar's
                # own settle-lag step): everything was sold, so no
                # position is left — a doubled factor left a short.
                radar = json.loads((root / "reports" /
                                    "wash_radar_margin.json").read_text())
                held = [r.get("taxable_qty") for sec in radar["sections"]
                        for r in sec["rows"]
                        if abs(float(r.get("taxable_qty") or 0)) > 1e-9]
                self.assertEqual(held, [], c)
            sums = cli_both(projects, "sum", "--json")
            for c, r in sums.items():
                self.assertEqual(r.returncode, 0, f"{c}: {r.stderr[-2000:]}")
                out = r.stdout[r.stdout.index("{"):]
                acc = json.loads(out)["accounts"][0]
                self.assertAlmostEqual(acc["realized"], 8200.0, msg=c)


if __name__ == "__main__":
    unittest.main()
