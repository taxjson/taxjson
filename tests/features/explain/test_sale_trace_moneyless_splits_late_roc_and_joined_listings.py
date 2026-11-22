"""Fix round 7 (wave B), FX rates and `tjs audit`: a Yahoo rate is not
forward-filled twice (TJ-0843), a bad intraday quote does not poison
the rates file (TJ-0845), futures converted twice keep the composed
rate (TJ-0745); the audit needs no rate for a split that carries no
money (TJ-0765), traces a record-dated return of capital paid later
(TJ-0766) and finds a listing the run joined (TJ-0773).

Every fetcher is stubbed: no network. Synthetic books and rates only.

Formerly: test_fix_1029_r7b_ratesaudit"""
import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

import _hermetic  # noqa: F401  (the synthetic HOME before taxjson's caches read it: TJ-3857)
from taxjson.lib import project_layout as _PL
from taxjson.bin.taxjson_convert_currency import reset_fallback_tally
from tax_rules import rule
from taxjson.rules import ids as R


def _audit_books(parsed, books, *, country="canada", year="2025",
                 currency="CAD", extra=()):
    """taxjson-audit in process over a parsed file, its base book and
    the gains the run would save from it: (rc, --json document, err)."""
    from _support.audit_rows import _audit
    from taxjson.lib.tx_load import load_transactions
    from taxjson.lib.pipeline import GainsRequest, run_gains
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        (p / "margin_tt_trades.json").write_text(json.dumps(
            {"transactions": parsed,
             "metadata": {"source_file": "inputs/margin/trades.tt"}}))
        (p / "base.json").write_text(json.dumps({"transactions": books}))
        with contextlib.redirect_stderr(io.StringIO()):
            saved = run_gains(load_transactions(p / "base.json"), [],
                              req=GainsRequest(country=country,
                                               year=int(year),
                                               taxable=True,
                                               phantom_hint=False))
        (p / "check.json").write_text(json.dumps(
            {"transactions": saved["transactions"]}, default=str))
        return _audit(["--country", country, "--year", year,
                       "--base", str(p / "base.json"),
                       "--source", str(p / "margin_tt_trades.json"),
                       "--check", str(p / "check.json"),
                       "--base-currency", currency, "--no-trace",
                       *extra])


class TestAuditSplitNeedsNoRate(unittest.TestCase):
    """TJ-0765: a split that carries no money, labelled in a currency
    with no rate, is relabelled by the converter; the audit asked for a
    rate and failed correct books.

    Formerly: test_fix_1029_r7b_ratesaudit.TestAuditSplitNeedsNoRate"""

    def test_moneyless_split_in_another_currency_ties(self):
        from _support.audit_rows import _row
        from taxjson.bin.taxjson_convert_currency import process_transactions
        from taxjson.lib.tx_load import coerce_transaction_row
        parsed = [
            _row(action="BUYSELL", date="2025-01-02", symbol="ZZQ.US",
                 quantity=10, price=10.0, net_amount=100.0, currency="CAD"),
            _row(action="SPLIT", date="2025-02-03", symbol="ZZQ.US",
                 quantity=2.0, currency="USD"),
            _row(action="BUYSELL", date="2025-03-03", symbol="ZZQ.US",
                 quantity=-20, price=6.0, net_amount=120.0, currency="CAD"),
        ]
        reset_fallback_tally()
        self.addCleanup(reset_fallback_tally)
        books = [t.to_dict() for t in process_transactions(
            [coerce_transaction_row(dict(r), i, "test")
             for i, r in enumerate(parsed)], "CAD", {}, None,
            country="canada")]
        rc, d, err = _audit_books(parsed, books)
        self.assertEqual(rc, 0, (d.get("reconciliation_failures"), err))
        self.assertFalse(d["failed"])
        [split] = [r for r in d["pool_other_rows"]["rows"]
                   if r["action"] == "SPLIT"]
        self.assertIs(split["ties"], True)

    def test_a_split_carrying_money_in_the_books_still_fails(self):
        from taxjson.bin import taxjson_audit as AUDIT
        raw = {"action": "SPLIT", "currency": "USD", "quantity": 2.0,
               "date": "2025-02-03"}
        base = dict(raw, currency="CAD", net_amount=5.0)
        _fx, _w, fails = AUDIT.money_lineage(
            "x", raw, base, base_currency="CAD", fx_history={},
            default_rate=None)
        self.assertEqual(len(fails), 1)
        self.assertIn("a split carries no money to convert", fails[0])


class TestAuditRecordDatedRoc(unittest.TestCase):
    """TJ-0766: a return of capital paid more than 31 days after the
    last audited sale, with a record date before the sale, lowers the
    sale's cost (Canada) but was left out of the pool check, so a book
    amount unlike the broker's passed.

    Formerly: test_fix_1029_r7b_ratesaudit.TestAuditRecordDatedRoc"""

    def _rows(self, paid):
        from _support.audit_rows import _row
        return [
            _row(action="BUYSELL", date="2025-12-01", symbol="ZZU.UN.TO",
                 quantity=100, price=1.0, net_amount=100.0, currency="CAD"),
            _row(action="BUYSELL", date="2025-12-30", symbol="ZZU.UN.TO",
                 quantity=-100, price=1.2, net_amount=120.0, currency="CAD"),
            _row(action="ADJUST", date=paid, symbol="ZZU.UN.TO",
                 net_amount=-50.0, currency="CAD", type="roc",
                 record_date="2025-12-29"),
        ]

    @rule(R.CA_INC_DATE_ROC_TRUST)
    def test_a_late_paid_roc_is_traced_and_its_difference_fails(self):
        for paid in ("2026-01-05", "2026-03-02"):
            with self.subTest(paid=paid):
                parsed = self._rows(paid)
                rc, d, err = _audit_books(parsed, copy.deepcopy(parsed))
                self.assertEqual(rc, 0, (d.get("reconciliation_failures"),
                                         err))
                [roc] = [r for r in d["pool_other_rows"]["rows"]
                         if r["action"] == "ADJUST"]
                self.assertIs(roc["ties"], True)
                books = copy.deepcopy(parsed)
                books[2]["net_amount"] = -20.0
                rc, d, err = _audit_books(parsed, books)
                self.assertEqual(rc, 1, err)
                [m] = d["pool_other_rows"]["mismatched"]
                self.assertEqual(m["action"], "ADJUST")


class TestAuditFindsAJoinedListing(unittest.TestCase):
    """TJ-0773: a listing the run joined by its transfer journal is in
    the run's effective map only; `tjs audit QZD.U.TO` read ticker.map
    and matched nothing.

    Formerly: test_fix_1029_r7b_ratesaudit.TestAuditFindsAJoinedListing"""

    @classmethod
    def setUpClass(cls):
        from _support.qt_gambit_rows import _project, _run
        cls._td = tempfile.TemporaryDirectory()
        cls.root = _project(cls._td.name)
        cls.r = _run(cls.root, "run", "--no-input")

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def _audit(self, *sym):
        from _support.qt_gambit_rows import _run
        r = _run(self.root, "audit", *sym, "--json", "--no-trace")
        return r.returncode, json.loads(r.stdout or "{}"), r.stderr

    @rule(R.CA_XLIST_03)
    def test_the_joined_listing_filters_to_the_same_sale(self):
        self.assertEqual(self.r.returncode, 0, self.r.stderr[-2000:])
        self.assertIn("TOBASE QZD.U.TO QZD.TO", (
            self.root / "work" / _PL.EFFECTIVE_MAP).read_text())
        rc, whole, err = self._audit("QZD.TO")
        self.assertEqual(rc, 0, err)
        rc, d, err = self._audit("QZD.U.TO")
        self.assertEqual(rc, 0, err)
        ids = sorted(e["id"] for e in d["events"])
        self.assertTrue(ids)
        self.assertEqual(ids, sorted(e["id"] for e in whole["events"]))


if __name__ == "__main__":
    unittest.main()
