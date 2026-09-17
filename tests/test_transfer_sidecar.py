"""Custody-transfer sidecar + `taxjson transfers` view.

TRANSFER rows are deliberately NOT tax events in a taxable book (basis
comes from the buy/sell history), but they are custody EVIDENCE — a
depot flip or broker migration is exactly what explains a confusing
position later (2026-09: the OR.US/OR.TO InterDepot mystery). The
parse stage must keep them aside in a sidecar instead of silently
deleting them, and the `transfers` view must surface sidecar + in-book
rows.
"""

import argparse
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# A minimal IBKR flex CSV: one trade + one InterDepot transfer row.
IB_CSV = """\
Trades,Header,DataDiscriminator,Asset Category,Currency,Account,Symbol,Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code
Trades,Data,Order,Stocks,USD,U1,OR,"2026-03-20, 15:05:52",300,32.68,32.87,-9804,-1.5,9805.5,0,57,O
Transfers,Header,Asset Category,Currency,Account,Symbol,Date,Type,Direction,Xfer Company,Xfer Account,Qty,Xfer Price,Market Value,Realized P/L,Cash Amount,Code
Transfers,Data,Stocks,CAD,U1,OR,2026-07-02,InterDepot,In,--,U1,300,--,"13,473.00",0.00,0.00,
"""


def _run_brokerage(tmp: Path, *extra):
    csv = tmp / "ib.csv"
    csv.write_text(IB_CSV, encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_brokerage",
         "--brokerage", "ib", "--account", "margin", str(csv), *extra],
        cwd=REPO_ROOT, capture_output=True, text=True)


class TestSidecar(unittest.TestCase):
    def test_transfers_kept_aside_not_deleted(self):
        with tempfile.TemporaryDirectory() as td:
            side = Path(td) / "ib_transfers.json"
            r = _run_brokerage(Path(td), "--transfers-out", str(side))
            self.assertEqual(r.returncode, 0, r.stderr)
            book = json.loads(r.stdout)
            actions = {t["action"] for t in book["transactions"]}
            self.assertNotIn("TRANSFER", actions, "book stays clean")
            self.assertIn("kept aside", r.stderr)
            doc = json.loads(side.read_text())
            self.assertEqual(doc["metadata"]["kind"], "transfer_sidecar")
            self.assertEqual(doc["metadata"]["account"], "margin")
            rows = doc["transactions"]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["description"], "InterDepot")
            self.assertEqual(rows[0]["account"], "margin",
                             "sidecar rows get the account label")

    def test_transfers_flag_keeps_rows_in_book_no_sidecar(self):
        with tempfile.TemporaryDirectory() as td:
            side = Path(td) / "ib_transfers.json"
            r = _run_brokerage(Path(td), "--transfers",
                               "--transfers-out", str(side))
            self.assertEqual(r.returncode, 0, r.stderr)
            book = json.loads(r.stdout)
            self.assertIn("TRANSFER",
                          {t["action"] for t in book["transactions"]})
            self.assertFalse(side.exists(),
                             "--transfers means nothing is excluded")

    def test_stale_sidecar_replaced_when_no_transfers(self):
        # A re-parse that finds no transfers must overwrite last run's
        # sidecar, not leave stale custody rows behind.
        with tempfile.TemporaryDirectory() as td:
            side = Path(td) / "ib_transfers.json"
            side.write_text(json.dumps(
                {"transactions": [{"stale": True}],
                 "metadata": {"kind": "transfer_sidecar"}}))
            csv = Path(td) / "ib.csv"
            csv.write_text(IB_CSV.split("Transfers,Header")[0],
                           encoding="utf-8")     # trades only
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_brokerage",
                 "--brokerage", "ib", "--account", "margin",
                 "--transfers-out", str(side), str(csv)],
                cwd=REPO_ROOT, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(
                json.loads(side.read_text())["transactions"], [])


class TestDerivedPricesAreReprClean(unittest.TestCase):
    """Prices parsers derive by DIVISION must round to 8dp before
    storage: repr() is the display path (round-trippable taxtext), so
    an unrounded 10,840.20/420 stored 25.810000000000002 and the
    events view printed it (round-five follow-up). 8dp keeps
    satoshi-level crypto prices intact."""

    def test_ib_transfer_price_clean(self):
        csv = IB_CSV.replace(
            'Transfers,Data,Stocks,CAD,U1,OR,2026-07-02,InterDepot,In,'
            '--,U1,300,--,"13,473.00",0.00,0.00,',
            'Transfers,Data,Stocks,CAD,U1,OR,2026-07-02,InterDepot,In,'
            '--,U1,420,--,"10,840.20",0.00,0.00,')
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ib.csv"
            p.write_text(csv, encoding="utf-8")
            side = Path(td) / "side.json"
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_brokerage",
                 "--brokerage", "ib", "--account", "m",
                 "--transfers-out", str(side), str(p)],
                cwd=REPO_ROOT, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            row = json.loads(side.read_text())["transactions"][0]
        self.assertEqual(row["price"], 25.81)
        self.assertEqual(repr(row["price"]), "25.81",
                         "division noise must not reach storage")


class TestTransfersView(unittest.TestCase):
    def _project(self, td):
        root = Path(td)
        (root / "taxjson.toml").write_text(
            '[settings]\nyear = 2026\ncountry = "canada"\n'
            'base_currency = "CAD"\n'
            '[accounts.margin]\ntype = "taxable"\n'
            '[accounts.rrsp]\ntype = "sheltered"\ntransfers = true\n')
        work = root / "work"
        work.mkdir()
        (work / "margin_ib_transfers.json").write_text(json.dumps(
            {"transactions": [
                {"action": "TRANSFER", "date": "2026-07-02",
                 "symbol": "OR.TO", "quantity": 300.0,
                 "currency": "CAD", "net_amount": 13473.0,
                 "account": "margin", "description": "InterDepot"}],
             "metadata": {"kind": "transfer_sidecar",
                          "account": "margin", "brokerage": "ib"}}))
        (work / "rrsp_base.json").write_text(json.dumps(
            {"transactions": [
                {"action": "TRANSFER", "date": "2026-05-09",
                 "symbol": "XYZ.US", "quantity": 500.0,
                 "currency": "CAD", "net_amount": 48200.0,
                 "account": "rrsp", "description": ""},
                {"action": "BUYSELL", "date": "2026-05-01",
                 "symbol": "XYZ.US", "quantity": 100.0,
                 "currency": "CAD", "net_amount": 10000.0,
                 "account": "rrsp"}]}))
        return root

    def _run(self, root, **kw):
        from taxjson.bin.taxjson_run import cmd_transfers_view
        out = io.StringIO()
        ns = dict(dir=str(root), account=None, json=False)
        ns.update(kw)
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            cmd_transfers_view(argparse.Namespace(**ns))
        return out.getvalue()

    def test_view_merges_sidecar_and_book_rows(self):
        with tempfile.TemporaryDirectory() as td:
            out = self._run(self._project(td))
        self.assertIn("OR.TO", out)
        self.assertIn("InterDepot", out)
        self.assertIn("sidecar", out)
        self.assertIn("XYZ.US", out)
        self.assertIn("book", out)
        self.assertIn("2 transfer row(s)", out)
        self.assertNotIn("BUYSELL", out)

    def test_account_filter(self):
        with tempfile.TemporaryDirectory() as td:
            out = self._run(self._project(td), account="margin")
        self.assertIn("OR.TO", out)
        self.assertNotIn("XYZ.US", out)

    def test_json_mode(self):
        with tempfile.TemporaryDirectory() as td:
            out = self._run(self._project(td), json=True)
        doc = json.loads(out)
        self.assertEqual(doc["count"], 2)
        wheres = {r["where"] for r in doc["transfers"]}
        self.assertEqual(wheres, {"sidecar", "book"})


class TestEvidencedDepotFlips(unittest.TestCase):
    """_apply_transfer_evidence: holdings re-symbol ONLY quantities the
    sidecar proves journaled between listings of one security. A
    JOURNAL map line re-symbols unconditionally (right for DLR's
    gambit classes); ordinary cross-listings get the evidence path."""

    def _tmap(self):
        from collections import namedtuple
        TM = namedtuple("TM", ["glob", "tobase", "journal", "delete",
                               "distinct"])
        return TM({}, {"OR.US": "OR.TO"}, {}, set(), set())

    def _agg(self):
        return {"OR.US": {"qty": 300.0, "total_cost": 9805.5,
                          "currency": "USD",
                          "cost_by_currency": {"USD": 9805.5},
                          "position_start_date": "2026-03-20"},
                "OR.TO": {"qty": 1700.0, "total_cost": 73626.0,
                          "currency": "CAD",
                          "cost_by_currency": {"CAD": 73626.0},
                          "position_start_date": "2026-07-17"}}

    def _sidecar(self, td, rows):
        p = Path(td) / "m_ib_transfers.json"
        p.write_text(json.dumps(
            {"transactions": [
                {"action": "TRANSFER", "date": d, "symbol": s,
                 "quantity": q, "currency": "CAD", "net_amount": 0.0,
                 "account": "margin", "description": "InterDepot"}
                for d, s, q in rows],
             "metadata": {"kind": "transfer_sidecar",
                          "account": "margin", "brokerage": "ib"}}))
        return p

    def _apply(self, agg, rows):
        from taxjson.bin.taxjson_export import _apply_transfer_evidence
        with tempfile.TemporaryDirectory() as td:
            p = self._sidecar(td, rows)
            with redirect_stderr(io.StringIO()):
                _apply_transfer_evidence(agg, [str(p)], self._tmap())
        return agg

    def test_net_residual_moves_between_listings(self):
        # The real OR shape: 6-leg churn netting to US -300 / TO +300.
        agg = self._apply(self._agg(), [
            ("2026-07-02", "OR.TO", 300), ("2026-07-02", "OR.TO", -300),
            ("2026-07-06", "OR.TO", 300), ("2026-07-02", "OR.US", -300),
            ("2026-07-02", "OR.US", 300), ("2026-07-06", "OR.US", -300)])
        self.assertNotIn("OR.US", agg, "emptied bucket pruned")
        d = agg["OR.TO"]
        self.assertAlmostEqual(d["qty"], 2000.0)
        self.assertTrue(d["mixed_currency"])
        self.assertAlmostEqual(d["cost_by_currency"]["USD"], 9805.5)
        self.assertAlmostEqual(d["cost_by_currency"]["CAD"], 73626.0)
        self.assertEqual(d["position_start_date"], "2026-03-20")

    def test_flip_flipped_back_moves_nothing(self):
        # CNQ/PAAS shape: out-and-back on both listings, net zero.
        agg = self._apply(self._agg(), [
            ("2026-07-02", "OR.TO", 300), ("2026-07-03", "OR.TO", -300),
            ("2026-07-02", "OR.US", -300), ("2026-07-03", "OR.US", 300)])
        self.assertAlmostEqual(agg["OR.US"]["qty"], 300.0)
        self.assertAlmostEqual(agg["OR.TO"]["qty"], 1700.0)

    def test_lone_migration_leg_ignored(self):
        # ATON arrival: positive residual, no source in the class —
        # the book's buy/sell history already carries the position.
        agg = self._apply(self._agg(), [("2026-07-02", "OR.TO", 300)])
        self.assertAlmostEqual(agg["OR.US"]["qty"], 300.0)
        self.assertAlmostEqual(agg["OR.TO"]["qty"], 1700.0)

    def test_unrelated_symbols_never_pair(self):
        # Out-leg of a symbol OUTSIDE the identity class must not fund
        # an in-leg inside it: shares are never invented across
        # securities.
        agg = self._apply(self._agg(), [
            ("2026-07-02", "XYZ.US", -300),
            ("2026-07-02", "OR.TO", 300)])
        self.assertAlmostEqual(agg["OR.US"]["qty"], 300.0)
        self.assertAlmostEqual(agg["OR.TO"]["qty"], 1700.0)

    def test_move_capped_at_held_quantity(self):
        # Evidence says 500 moved but the bucket only holds 300 (e.g.
        # partial history imported): move what exists, never go short.
        agg = self._apply(self._agg(), [
            ("2026-07-02", "OR.US", -500),
            ("2026-07-02", "OR.TO", 500)])
        self.assertNotIn("OR.US", agg)
        self.assertAlmostEqual(agg["OR.TO"]["qty"], 2000.0)


class TestCryptoSendsBecomeEvidence(unittest.TestCase):
    """KNOWN_ISSUES graduation: crypto withdrawals/sends were counted
    and DROPPED — an off-platform gift (taxable disposition at FMV)
    left no discoverable trace. They now emit TRANSFER rows, land in
    the sidecar via the standard machinery, and the parse prints the
    FMV-disposition note. Tax semantics still not assumed: the rows
    are evidence; the user declares gifts as .tt sells."""

    KR_LEDGER = (
        'txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,balance\n'
        'L1,R1,2026-06-17 14:51:23,withdrawal,,currency,USDC,spot,'
        '-50.0,1.0,0.0\n'
        'L2,R2,2026-05-10 04:37:05,hybridearnwithdrawal,,currency,'
        'USDC,earn,-852.387604,0,0.0\n'
        'L3,R3,2026-07-01 10:00:00,deposit,,currency,BTC,spot,'
        '0.5,0,0.5\n')

    CB_CSV = (
        "ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
        "Price Currency,Price at Transaction,Subtotal,"
        "Total (inclusive of Fees and/or Spread),"
        "Fees and/or Spread,Notes\n"
        "x1,2026-05-04 22:50:14 UTC,Send,TAO,0.102,USD,400.00,,,,"
        "Sent 0.102 TAO to wallet\n")

    def _parse(self, brokerage, name, content):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / name
            src.write_text(content, encoding="utf-8")
            side = Path(td) / "side.json"
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_brokerage",
                 "--brokerage", brokerage, "--account", "crypto",
                 "--transfers-out", str(side), str(src)],
                cwd=REPO_ROOT, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            doc = json.loads(side.read_text())
            book = json.loads(r.stdout)
        return doc["transactions"], book, r.stderr

    def test_kraken_withdrawal_and_deposit_to_sidecar(self):
        rows, book, err = self._parse(
            "kraken", "kr_ledgers.csv", self.KR_LEDGER)
        by_sym = {(t["symbol"], t["description"]): t for t in rows}
        w = by_sym[("USDC", "withdrawal")]
        self.assertEqual(w["quantity"], -50.0)
        self.assertEqual(w["date"], "2026-06-17")
        self.assertEqual(w["fee"], 1.0)
        d = by_sym[("BTC", "deposit")]
        self.assertEqual(d["quantity"], 0.5)
        # Earn shuffles are internal — still ignored, never evidence.
        self.assertNotIn(("USDC", "hybridearnwithdrawal"), by_sym)
        # Books stay clean of TRANSFERs.
        self.assertNotIn("TRANSFER",
                         {t["action"] for t in book["transactions"]})
        self.assertIn("taxable DISPOSITION at fair market", err)

    def test_coinbase_send_to_sidecar_with_spot(self):
        rows, book, err = self._parse(
            "coinbase", "cb.csv", self.CB_CSV)
        self.assertEqual(len(rows), 1)
        t = rows[0]
        self.assertEqual(t["symbol"], "TAO")
        self.assertEqual(t["quantity"], -0.102)
        self.assertEqual(t["description"], "Send")
        # Spot price carried so declaring the FMV sell is copy-paste.
        self.assertEqual(t["price"], 400.0)
        self.assertAlmostEqual(t["net_amount"], 40.80)
        self.assertIn("taxable DISPOSITION at fair market", err)


class TestEvidenceFoldInteractions(unittest.TestCase):
    """Round-five adversarial findings 4/5: evidence nets must be
    computed on journal-FOLDED keys, and the base inventory must
    REPLAY the native pass's moves rather than re-derive them."""

    def _tmap(self, journal=None, tobase=None):
        from collections import namedtuple
        TM = namedtuple("TM", ["glob", "tobase", "journal", "delete",
                               "distinct"])
        return TM({}, tobase or {}, journal or {}, set(), set())

    def _sidecar(self, td, rows):
        p = Path(td) / "m_ib_transfers.json"
        p.write_text(json.dumps(
            {"transactions": [
                {"action": "TRANSFER", "date": d, "symbol": s,
                 "quantity": q, "currency": "CAD", "net_amount": 0.0,
                 "account": "margin", "description": "InterDepot"}
                for d, s, q in rows],
             "metadata": {"kind": "transfer_sidecar",
                          "account": "margin", "brokerage": "ib"}}))
        return str(p)

    def test_journal_folded_class_never_evidence_moved(self):
        """Finding 4: buy 1000 DLR.TO, journal 500 to the U line
        (sidecar pair), sell 300 U. The agg is journal-FOLDED (one
        DLR.TO bucket, 700 left); the evidence legs fold to the same
        key and cancel — the flip must be a no-op, not a re-symboling
        of shares already sold through the other listing."""
        from taxjson.bin.taxjson_export import _apply_transfer_evidence
        tmap = self._tmap(journal={"DLR.U.TO": "DLR.TO"})
        agg = {"DLR.TO": {"qty": 700.0, "total_cost": 7000.0,
                          "currency": "CAD",
                          "cost_by_currency": {"CAD": 7000.0},
                          "position_start_date": "2026-01-05"}}
        with tempfile.TemporaryDirectory() as td:
            p = self._sidecar(td, [
                ("2026-06-15", "DLR.TO", -500),
                ("2026-06-15", "DLR.U.TO", +500)])
            with redirect_stderr(io.StringIO()):
                moves = _apply_transfer_evidence(agg, [p], tmap)
        self.assertEqual(moves, [])
        self.assertEqual(set(agg), {"DLR.TO"})
        self.assertAlmostEqual(agg["DLR.TO"]["qty"], 700.0)

    def test_base_inventory_replays_native_moves(self):
        """Round-six finding 1 (corrects the round-five pin's wrong
        premise): the RAW-base pipeline keeps cross-listings
        PER-LISTING (GLOBAL renames only, no TOBASE consolidation),
        so a native OR.US->OR.TO evidence move must REPLAY on the
        base inventory — the flipped shares' base-currency cost moves
        with them. The old to-base fold made this a no-op and the
        300 shares' 13,443.14 CAD basis vanished from holdings."""
        from taxjson.bin.taxjson_export import (_apply_transfer_evidence,
                                                _replay_moves_on_base)
        tmap = self._tmap(tobase={"OR.US": "OR.TO"})
        agg = {"OR.US": {"qty": 300.0, "total_cost": 9805.5,
                         "currency": "USD",
                         "cost_by_currency": {"USD": 9805.5},
                         "position_start_date": "2026-03-20"},
               "OR.TO": {"qty": 1700.0, "total_cost": 73626.0,
                         "currency": "CAD",
                         "cost_by_currency": {"CAD": 73626.0},
                         "position_start_date": "2026-07-17"}}
        base = {"OR.US": {"qty": 300.0, "total_cost": 13443.14,
                          "currency": "CAD",
                          "cost_by_currency": {"CAD": 13443.14},
                          "position_start_date": "2026-03-20"},
                "OR.TO": {"qty": 1700.0, "total_cost": 73626.0,
                          "currency": "CAD",
                          "cost_by_currency": {"CAD": 73626.0},
                          "position_start_date": "2026-07-17"}}
        with tempfile.TemporaryDirectory() as td:
            p = self._sidecar(td, [
                ("2026-07-02", "OR.US", -300),
                ("2026-07-02", "OR.TO", +300)])
            with redirect_stderr(io.StringIO()):
                moves = _apply_transfer_evidence(agg, [p], tmap)
            _replay_moves_on_base(base, moves, tmap)
        self.assertEqual(moves, [("OR.US", "OR.TO", 300.0)])
        self.assertAlmostEqual(agg["OR.TO"]["qty"], 2000.0)
        self.assertNotIn("OR.US", agg)
        # Base REPLAYS: 2,000 shares under OR.TO carrying the WHOLE
        # 87,069.14 CAD cost; nothing vanishes.
        self.assertEqual(set(base), {"OR.TO"})
        self.assertAlmostEqual(base["OR.TO"]["qty"], 2000.0)
        self.assertAlmostEqual(base["OR.TO"]["total_cost"], 87069.14)

    def test_journal_pair_base_replay_is_noop(self):
        """A JOURNAL pair's move endpoints fold to one key in BOTH
        inventories — the base replay must no-op, not double-move."""
        from taxjson.bin.taxjson_export import _replay_moves_on_base
        tmap = self._tmap(journal={"DLR.U.TO": "DLR.TO"})
        base = {"DLR.TO": {"qty": 1000.0, "total_cost": 10000.0,
                           "currency": "CAD",
                           "cost_by_currency": {"CAD": 10000.0},
                           "position_start_date": None}}
        _replay_moves_on_base(
            base, [("DLR.U.TO", "DLR.TO", 500.0)], tmap)
        self.assertAlmostEqual(base["DLR.TO"]["qty"], 1000.0)
        self.assertAlmostEqual(base["DLR.TO"]["total_cost"], 10000.0)

    def test_base_replay_moves_when_keys_distinct_in_base(self):
        """When the base books did NOT fold the pair (no map entry),
        the base inventory replays the same move proportionally."""
        from taxjson.bin.taxjson_export import _replay_moves_on_base
        tmap = self._tmap()
        base = {"A.TO": {"qty": 100.0, "total_cost": 1000.0,
                         "currency": "CAD",
                         "cost_by_currency": {"CAD": 1000.0},
                         "position_start_date": None}}
        _replay_moves_on_base(base, [("A.TO", "B.TO", 40.0)], tmap)
        self.assertAlmostEqual(base["A.TO"]["qty"], 60.0)
        self.assertAlmostEqual(base["A.TO"]["total_cost"], 600.0)
        self.assertAlmostEqual(base["B.TO"]["qty"], 40.0)
        self.assertAlmostEqual(base["B.TO"]["total_cost"], 400.0)


if __name__ == "__main__":
    unittest.main()
