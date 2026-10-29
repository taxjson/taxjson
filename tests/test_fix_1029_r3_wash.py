"""The 2026-10-29 fix round 3, group wash: the superficial-loss views
and the planning tools (backlog TJ-0600, TJ-0620, TJ-0599, TJ-0615).
Synthetic data only: made-up tickers (ZZR, OTH, SYN, ZZC, ZZQ) and
account numbers in throwaway folders."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from _qa_project import tj
from tax_rules import rule
from test_fix_1029_planning import _toml
from test_fix_1029_r2_review import _short_cover_gains
from test_fix_1029_transfers import _three_accounts
from test_fix_backlog_radar import _radar, _row


def _flat(text: str) -> str:
    """Whitespace-collapsed: where a line breaks differs with the temp
    path's length (backlog TJ-0625)."""
    return " ".join(text.split())


class TestShelteredAccountExplain(unittest.TestCase):
    """TJ-0600: the RRSP buys ZZR.TO 10 at 10, sells them at 5 and buys
    10 back 7 days later. `wash-sales rrsp` says no superficial losses;
    `wash-sales rrsp --explain` traced the RRSP sale as a superficial
    loss, each window row twice (the book merged as taxable and again
    as the sheltered context). A registered account's own sale is never
    one: the trace now says what the table says, and why."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.roots = {}
        for country, acct, sym, cur in (("canada", "rrsp", "ZZR.TO", "CAD"),
                                        ("usa", "ira", "ZZR.US", "USD")):
            root = Path(cls._td.name) / country
            root.mkdir()
            (root / "taxjson.toml").write_text(_toml(
                country, year=2026,
                extra=f'[accounts.{acct}]\ntype = "sheltered"\n'))
            books = {
                acct: (f"BUYSELL 2026-01-05 10:00:00 {sym} 10 {cur} 10 100 0\n"
                       f"BUYSELL 2026-02-02 10:00:00 {sym} -10 {cur} 5 50 0\n"
                       f"BUYSELL 2026-02-09 10:00:00 {sym} 10 {cur} 5 50 0\n"),
                "margin": (f"BUYSELL 2026-01-05 10:00:00 OTH{sym[3:]} 10 "
                           f"{cur} 10 100 0\n"),
            }
            for a, text in books.items():
                (root / "inputs" / a).mkdir(parents=True)
                (root / "inputs" / a / "m.tt").write_text(text)
            tj(root, "run", "--no-input")
            cls.roots[country] = (root, acct, sym)

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def _check_none(self, country, term):
        root, acct, sym = self.roots[country]
        table = json.loads(tj(root, "wash-sales", acct, "--json").stdout)
        self.assertEqual(table["rows"], [])
        r = tj(root, "wash-sales", acct, "--explain", check=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = _flat(r.stdout)
        self.assertIn(f"No {term} for account '{acct}' — it is a "
                      f"sheltered account.", out)
        self.assertIn("Its purchases can deny a taxable account's loss",
                      out)
        self.assertNotIn(sym, r.stdout + r.stderr)
        self.assertNotIn("repeat an earlier row", r.stderr)

    @rule("CA-ACB-05")
    def test_tj0600_explain_says_none_for_an_rrsp(self):
        self._check_none("canada", "superficial losses")

    @rule("US-BASIS-07")
    def test_tj0600_explain_says_none_for_an_ira(self):
        self._check_none("usa", "wash sales")

    @rule("CA-ACB-05")
    def test_tj0600_the_project_trace_has_no_registered_sale(self):
        root, _acct, sym = self.roots["canada"]
        r = tj(root, "wash-sales", "--explain", check=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn(f"{sym} — 2026-02-02", r.stdout)


class TestShortCoverIsNoPartialTaintLoss(unittest.TestCase):
    """TJ-0620: a short sold before the data (OPENING_BALANCE -10
    SYN.US), covered on 2025-06-02 for 500. The gains warned of an
    'unknown-cost loss of 500.00 with a buy on 2025-06-02 inside the
    window': the 'loss' was the cover cost (the short sale's proceeds
    are the unknown figure) and the 'buy' was the cover itself. A long
    sale's unknown cost counts as 0, so its raw loss is a loss: it is
    still flagged."""

    def _check_short(self, country):
        g = _short_cover_gains(country)
        self.assertEqual(
            [m["direction"] for m in g["manual_reporting_required"]],
            ["SHORT"])
        self.assertNotIn("superficial_loss_warnings", g)

    @rule("CA-ACB-12")
    def test_tj0620_a_short_cover_is_not_flagged(self):
        self._check_short("canada")

    @rule("US-BASIS-04")
    def test_tj0620_a_us_short_cover_is_not_flagged(self):
        self._check_short("usa")

    def _gains(self, rows):
        from taxjson.lib.core import TaxTransaction as T
        from taxjson.lib.pipeline import GainsRequest, run_gains
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            g = run_gains(
                [T(id=i, action=a, date=d, date_settle=d, symbol="SYN.TO",
                   quantity=q, net_amount=n, currency="CAD",
                   account="margin") for i, a, d, q, n in rows],
                req=GainsRequest(country="canada", year=2025, taxable=True,
                                 phantom_hint=False))
        return g, err.getvalue()

    @rule("CA-ACB-12")
    def test_tj0620_a_buy_after_the_cover_is_not_flagged_either(self):
        g, err = self._gains([
            ("open", "OPENING_BALANCE", "2024-01-02", -10, 0.0),
            ("cover", "BUYSELL", "2025-06-02", 10, -500.0),
            ("b2", "BUYSELL", "2025-06-09", 5, -250.0)])
        self.assertNotIn("superficial_loss_warnings", g)
        self.assertNotIn("superficial-loss check", err)

    @rule("CA-ACB-12")
    def test_tj0620_a_long_unknown_cost_loss_is_still_flagged(self):
        # 5 units held before the data and 5 bought for 500, all sold
        # for 200 (a loss whatever the 5 cost); 2 bought back a week on.
        g, err = self._gains([
            ("open", "OPENING_BALANCE", "2024-01-02", 5, 0.0),
            ("b1", "BUYSELL", "2025-01-06", 5, -500.0),
            ("s", "BUYSELL", "2025-03-03", -10, 200.0),
            ("b2", "BUYSELL", "2025-03-10", 2, -40.0)])
        w, = g["superficial_loss_warnings"]
        self.assertEqual((w["loss_date"], w["acquisition_date"],
                          w["raw_loss"]), ("2025-03-03", "2025-03-10", -300.0))
        self.assertIn("with a buy on 2025-03-10 inside the window", err)


class TestDraftsReadTheRunsPairing(unittest.TestCase):
    """TJ-0599: qa sends 100 ZZQ out on 2024-03-01; qc receives 100 on
    03-02 (the move's other leg: the run pairs them) and qb 100 on
    03-08 from outside the books, at a stated book value.
    `find-missing-history qb --write-purchases` dropped qc's transfer-in
    before pairing, so it paired qb's with qa's transfer-out: "a move
    from your account qa", while the run books it at its book value."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.root = _three_accounts(Path(cls._td.name) / "p", "canada")
        tj(cls.root, "run", "--no-input")

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def _drafts(self, *account):
        r = tj(self.root, "find-missing-history", *account,
               "--write-purchases", check=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return _flat(r.stdout)

    @rule("CA-ACB-OWN-MOVE", "CA-ACB-TRANSFER-BV")
    def test_tj0599_one_account_names_the_runs_pairing(self):
        out = self._drafts("qb")
        self.assertIn("[qb] 2024-03-08 100 units — the run already books "
                      "it at the stated book value", out)
        self.assertNotIn("move from your account", out)
        self.assertIn("[qc] 2024-03-02 100 units — a move from your "
                      "account qa", self._drafts("qc"))
        both = self._drafts()
        self.assertIn("[qb] 2024-03-08 100 units — the run already books "
                      "it at the stated book value", both)
        self.assertIn("[qc] 2024-03-02 100 units — a move from your "
                      "account qa", both)

    @rule("CA-ACB-OWN-MOVE")
    def test_tj0599_without_a_project_every_row_is_paired(self):
        # The library's own pairing (no project around the books): an
        # account's drafts still pair every account's rows.
        from taxjson.lib.missing_history import draft_purchases

        def tr(acct, d, q, desc):
            return {"action": "TRANSFER", "date": d, "symbol": "ZZQ.TO",
                    "quantity": q, "currency": "CAD", "account": acct,
                    "net_amount": 0.0, "description": desc}
        rows = [tr("qa", "2024-03-01", -100, "ZZQ CORP TRANSFER OUT"),
                tr("qc", "2024-03-02", 100,
                   "ZZQ CORP TRANSFER BOOK VALUE 250.00"),
                tr("qb", "2024-03-08", 100,
                   "ZZQ CORP TRANSFER BOOK VALUE 250.00")]
        drafts, gaps = draft_purchases([], country="canada",
                                       accounts={"qb"}, transfer_rows=rows)
        self.assertEqual([(d.account, d.quantity) for d in drafts],
                         [("qb", 100.0)])
        self.assertEqual(gaps, [])
        # The run's pairing, given, is the one read: here it paired
        # nothing, so qc's transfer-in is drafted too.
        drafts, gaps = draft_purchases([], country="canada",
                                       transfer_rows=rows, own_moves=[])
        self.assertEqual(sorted((d.account, d.quantity) for d in drafts),
                         [("qb", 100.0), ("qc", 100.0)])


class TestRadarNamesTheTradeDate(unittest.TestCase):
    """TJ-0615: a Canadian buy traded on 2026-10-15 settles on 10-16.
    The radar's notes named the settlement date as the day it was
    bought ("SHELTERED 'tfsa' bought 2026-10-16"), while its
    `recent_buy` names the trade date the broker shows. The window and
    the clear date still count settlement dates."""

    _TAX = [_row("2026-01-07", "ZZC.TO", 100, 10.0, settle="2026-01-08")]
    _BUY = _row("2026-10-15", "ZZC.TO", 20, 9.0, acct="tfsa",
                settle="2026-10-16")

    def _row_of(self, taxable, sheltered):
        return _radar("canada", taxable, sheltered,
                      as_of="2026-10-29")["ZZC.TO"]

    @rule("CA-PLAN-01")
    def test_tj0615_caution_names_the_trade_date(self):
        sold = _row("2026-10-22", "ZZC.TO", -20, 9.0, acct="tfsa",
                    settle="2026-10-23")
        r = self._row_of(self._TAX, [self._BUY, sold])
        self.assertEqual(r["category"], "CAUTION", r["advisory"])
        self.assertIn("SHELTERED 'tfsa' bought 2026-10-15 but holds none",
                      r["advisory"])
        self.assertNotIn("2026-10-16", r["advisory"])

    @rule("CA-PLAN-01")
    def test_tj0615_locked_and_clear_name_the_trade_date(self):
        r = self._row_of(self._TAX, [self._BUY])
        self.assertEqual(r["category"], "LOCKED", r["advisory"])
        self.assertIn("'tfsa' bought 2026-10-15 and still holds 20",
                      r["advisory"])
        # The clear date still counts from the settlement (10-16).
        self.assertEqual(r["clears_at"], "2026-11-13")
        r = self._row_of([_row("2026-01-07", "OTH.TO", 1, 1.0)],
                         [self._BUY])
        self.assertEqual(r["category"], "CLEAR", r["advisory"])
        self.assertIn("('tfsa' bought it 2026-10-15)", r["advisory"])
        self.assertEqual(r["sheltered_buy"],
                         {"account": "tfsa", "date": "2026-10-15"})

    @rule("CA-PLAN-01")
    def test_tj0615_a_taxable_buy_too(self):
        buy = _row("2026-10-15", "ZZC.TO", 10, 9.0, settle="2026-10-16")
        r = self._row_of(self._TAX + [buy], None)
        self.assertEqual(r["category"], "EXITABLE", r["advisory"])
        self.assertIn("Recent buy in 'margin' on 2026-10-15.",
                      r["advisory"])
        self.assertEqual(r["recent_buy"]["date"], "2026-10-15")


if __name__ == "__main__":
    unittest.main()
