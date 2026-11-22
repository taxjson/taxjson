"""Backlog group fxcorp: the FX-on-cash ledger v2 (lib/fx_cash_v2, the
Kraken cash reader) and the spin-off views and checks (lib/corp_views,
lib/missing_history, lib/positions_check, `tjs sanity`). One test
per backlog item, each named for it. Synthetic data only.

Formerly: test_fix_backlog_fxcorp"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tax_rules import rule
from taxjson.rules import ids as R
from taxjson.bin.taxjson_corp_actions import extractor_for
from _support.qt_spinoff import _cli, _ACCT, _q, _project, _qt_project


# ------------------------------------------------- TJ-0134, TJ-0166

def _views_proj(rows, elections):
    td = tempfile.TemporaryDirectory()
    root = Path(td.name)
    (root / "work").mkdir()
    (root / "inputs" / "m").mkdir(parents=True)
    (root / "work" / "m_base.json").write_text(json.dumps(rows))
    (root / "inputs" / "m" / "manifest.json").write_text(
        json.dumps({"elections": elections}))
    cfg = {"settings": {"base_currency": "CAD", "country": "canada"},
           "accounts": {"m": {"type": "taxable"}}}
    return td, root, cfg


def _row(action, d, sym, qty, net=0.0, **kw):
    r = {"action": action, "date": d, "time": "09:30:00", "symbol": sym,
         "quantity": qty, "net_amount": net}
    r.update(kw)
    return r


def _spin(eid, d, parent, child, new, old, received, cur, fmv=0.0, **kw):
    from taxjson.lib.corp_actions import CorporateAction
    return CorporateAction(
        date=d, time="09:30:00", action_type="spinoff",
        source_symbol=parent, source_isin="", target_symbol=child,
        target_isin="", ratio_new=new, ratio_old=old, qty_disposed=0.0,
        qty_received=received, fmv=fmv, currency=cur, target_currency=cur,
        account="m", event_id=eid, target_fmv=fmv, **kw)


class TestSpinoffViews(unittest.TestCase):
    """Formerly: test_fix_backlog_fxcorp.TestSpinoffViews"""
    def test_tj0134_broker_value_labelled_in_its_currency(self):
        """TJ-0134: `tjs spinoffs` printed the broker's USD value
        per share as "20.00 CAD/share"."""
        from taxjson.lib import corp_views as CV
        eid = "20240603-parnt-spnco-0001"
        desc = "Spinoff PARNT.US→SPNCO.US (deemed dividend at FMV)"
        rows = [_row("DIVIDEND", "2024-06-03", "SPNCO.US", 0, 681.75,
                     corp_event_id=eid, description=desc),
                _row("BUYSELL", "2024-06-03", "SPNCO.US", 25, 681.75,
                     corp_event_id=eid, description=desc)]
        ev = _spin(eid, "2024-06-03", "PARNT.US", "SPNCO.US", 1, 4, 25,
                   "USD", fmv=500.0)
        td, root, cfg = _views_proj(rows, {eid: {
            "election": "taxable_deemed_dividend", "hints": {}}})
        with td, patch.object(CV, "_current_events",
                              return_value={eid: ev}):
            doc = CV.spinoffs(root, cfg, extractor_for=extractor_for)
        s = doc["spinoffs"][0]
        self.assertEqual((s["fmv_per_share"], s["fmv_currency"]),
                         (20.0, "USD"))
        text = "\n".join(CV.render_spinoffs(doc))
        self.assertIn("20.00 USD/share (the broker's value)", text)
        self.assertNotIn("CAD/share", text)

    @rule(R.CA_CORP_05)
    def test_tj0166_ratio_and_cash_in_lieu_count_the_fraction(self):
        """TJ-0166: an s.86.1 spin-off of 10.5 new shares for 105 (10
        delivered, 0.5 paid in cash) showed "0.0952381 new per share
        (10 for 105)" while 10.5 are booked. The views give the
        entitlement and the cash in lieu; `elect --pending` says it
        too."""
        from taxjson.lib import corp_views as CV
        eid = "20240614-zzp-zzs-0001"
        desc = "Spinoff ZZP.TO→ZZS.TO (s.86.1 rollover)"
        rows = [_row("BUYSELL", "2024-06-14", "ZZS.TO", 10.5, 210.0,
                     corp_event_id=eid, description=desc,
                     corp_election="rollover_s_86_1"),
                _row("BUYSELL", "2024-06-18", "ZZS.TO", -0.5, 12.0,
                     corp_event_id=eid, description=desc,
                     corp_election="rollover_s_86_1")]
        ev = _spin(eid, "2024-06-14", "ZZP.TO", "ZZS.TO", 10, 105, 10,
                   "CAD", fraction_in_lieu=0.5, cash_in_lieu=12.0,
                   cash_in_lieu_currency="CAD",
                   cash_in_lieu_date="2024-06-18")
        td, root, cfg = _views_proj(rows, {eid: {
            "election": "rollover_s_86_1",
            "hints": {"allocated_acb_cad": 210}}})
        with td, patch.object(CV, "_current_events",
                              return_value={eid: ev}):
            doc = CV.spinoffs(root, cfg, extractor_for=extractor_for)
        s = doc["spinoffs"][0]
        self.assertEqual(s["ratio"], "0.1 new per share (10.5 for 105)")
        self.assertEqual(s["cash_in_lieu"],
                         "0.5 of the 10.5 new shares, paid in cash: 12.00 "
                         "CAD on 2024-06-18 (booked as their sale)")
        self.assertIn("cash in lieu:  0.5 of the 10.5 new shares",
                      "\n".join(CV.render_spinoffs(doc)))
        # An export that pays cash without sizing the fraction (RBC).
        self.assertEqual(CV.cash_in_lieu_line(10, 0, 12, "CAD",
                                              "2024-06-18"),
                         "12.00 CAD on 2024-06-18, for a fraction the "
                         "export does not size")
        self.assertEqual(CV.cash_in_lieu_line(10, 0, 0, "CAD", ""), "")
        # `tjs elect --pending` on the same event, from a Questrade
        # export.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _qt_project(root, "2024-06-14", "2024-06-18")
            r = _cli(root, "elect", "--pending")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertIn("cash in lieu: 0.5 of the 10.5 new shares, paid "
                          "in cash: 12.00 CAD on 2024-06-18", r.stdout)


# ------------------------------------------------- TJ-0101, TJ-0149


def _elect(root):
    pend = json.loads((root / "work" / "pending_elections.json").read_text())
    eid = pend["accounts"]["margin"]["pending"][0]["event_id"]
    r = _cli(root, "elect", "margin", "--set", f"{eid}=rollover_s_86_1",
             "--hint", "allocated_acb_cad=210")
    assert r.returncode == 0, r.stderr
    return eid


class TestSpinoffFraction(unittest.TestCase):
    """Formerly: test_fix_backlog_fxcorp.TestSpinoffFraction"""
    @rule(R.CA_CORP_05)
    def test_tj0101_hand_booked_fraction_is_not_a_missing_basis(self):
        """TJ-0101: the documented .tt fix for an RBC cash in lieu (a
        BUY of the fraction at 0, then its SELL for the cash) cleared
        UNBOOKED but was called a "$0 cost" position whose sale
        overstates the gain, though the pooled s.86.1 cost covers it.
        A $0 BUY of less than one share, sold again by a .tt line, into
        a pool the election gave a cost is not flagged; one sold by the
        broker, or a whole share, still is."""
        from taxjson.model.transaction import TaxTransaction
        from taxjson.lib.missing_history import \
            detect_zero_basis_acquisitions as zero

        def tx(d, q, net, src, election=""):
            return TaxTransaction(
                action="BUYSELL", date=d, time="10:00:00", symbol="ZZS.TO",
                quantity=q, price=abs(net / q) if net else 0.0,
                net_amount=net, account="margin", date_settle=d,
                currency="CAD", source=src, corp_election=election)
        spin = tx("2024-06-14", 10, 210.0, "", "rollover_s_86_1")
        sale = tx("2024-09-10", -10, 250.0, "rbc_spin.csv")
        self.assertEqual(zero([spin, tx("2024-06-18", 0.5, 0, "f.tt"),
                               tx("2024-06-18", -0.5, 12.0, "f.tt"), sale],
                              2024), [])
        for txs in ([spin, tx("2024-06-18", 0.5, 0, "f.tt"),
                     tx("2024-06-18", -0.5, 12.0, "x.csv"), sale],
                    [spin, tx("2024-06-18", 1, 0, "f.tt"),
                     tx("2024-06-18", -1, 24.0, "f.tt"), sale],
                    [tx("2024-06-14", 10, 210.0, "x.csv"),
                     tx("2024-06-18", 0.5, 0, "f.tt"),
                     tx("2024-06-18", -0.5, 12.0, "f.tt"), sale]):
            self.assertEqual(len(zero(txs, 2024)), 1, txs)
        # The documented fix on an RBC export, end to end.
        head = ('Account Activity Detail Report\n'
                f'"Account Number: {_ACCT}"\n'
                '"Date Range: January 1, 2024 - December 31, 2024"\n\n'
                'Date,Activity,Symbol,Description,Quantity,Price,'
                'Settlement Date,Currency,Value,Amount\n')
        rows = ("01/05/2024,Buy,ZZP,ZZP PARENT CORP,105,10.00,01/09/2024,"
                "CAD,-1050.00,-1050.00\n"
                "06/14/2024,Reorganization,ZZS,DIS - ZZS SPINCO INC SPINOFF "
                "ON 105 SHS FROM SEC# X123456 ZZP PARENT CORP REC 06/01/24 "
                "PAY 06/14/24,10,0,06/14/2024,CAD,0,0\n"
                "06/18/2024,Reorganization,ZZS,CIL - ZZS SPINCO INC CASH IN "
                "LIEU OF FRAC SHARES,0,0,06/18/2024,CAD,12.00,12.00\n"
                "09/10/2024,Sell,ZZS,ZZS SPINCO INC - Sell,10,25.00,"
                "09/11/2024,CAD,250.00,250.00\n")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _project(root, {"rbc_spin.csv": head + rows})
            _elect(root)
            (root / "inputs" / "margin" / "fraction.tt").write_text(
                "BUYSELL 2024-06-18 10:00:00 ZZS.TO 0.5 CAD 0 0\n"
                "BUYSELL 2024-06-18 10:00:01 ZZS.TO -0.5 CAD 24 12\n")
            r = _cli(root, "run", "--no-input", "--strict")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertNotIn("$0 cost", r.stderr)
            r = _cli(root, "find-missing-history")
            self.assertNotIn("$0-COST", r.stdout)

    @rule(R.CA_CORP_05)
    def test_tj0149_fraction_awaiting_its_cash_is_no_difference(self):
        """TJ-0149: an s.86.1 spin-off on 2024-12-27 whose cash in lieu
        is paid 2025-01-06: the books hold 10.5 at the year end, the
        broker 10, and sanity said QTY_MISMATCH (a missing trade or a
        booking problem) and exited 1. The fraction is named instead;
        any other difference is still one."""
        from taxjson.lib.positions_check import fractions_awaiting_cash
        rows = [{"action": "BUYSELL", "date": "2024-12-27",
                 "symbol": "ZZS.TO", "quantity": 10.5,
                 "corp_event_id": "e1", "corp_election": "rollover_s_86_1"},
                {"action": "BUYSELL", "date": "2025-01-06",
                 "symbol": "ZZS.TO", "quantity": -0.5,
                 "corp_event_id": "e1", "corp_election": "rollover_s_86_1"}]
        want = {"ZZS.TO": [{"quantity": 0.5, "received": "2024-12-27",
                            "paid": "2025-01-06"}]}
        self.assertEqual(fractions_awaiting_cash(rows, "2024-12-31"), want)
        for day in ("2024-12-26", "2025-01-06"):
            self.assertEqual(fractions_awaiting_cash(rows, day), {}, day)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sell = _q("2025-02-10", "Sell", "ZZP.TO", "ZZP PARENT CORP",
                      "-105", net="1200", price="11.43", gross="1200",
                      act="Trades")
            _qt_project(root, "2024-12-27", "2025-01-06", more=sell)
            _elect(root)
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            hold = root / "holdings" / "margin_holdings.toml"
            hold.parent.mkdir(exist_ok=True)

            def holdings(zzs):
                hold.write_text(
                    '[meta]\nas_of = "2024-12-31"\n\n[[holding]]\n'
                    'symbol = "ZZP.TO"\nquantity = 105\ncurrency = "CAD"\n'
                    'total_cost = 840.00\n\n[[holding]]\nsymbol = "ZZS.TO"'
                    f'\nquantity = {zzs}\ncurrency = "CAD"\n'
                    'total_cost = 200.00\n')
            holdings(10)
            r = _cli(root, "sanity")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn("QTY_MISMATCH", r.stdout)
            self.assertIn("ZZS.TO: 0.5 more in the books, a spin-off's "
                          "fraction (s.86.1, received 2024-12-27) sold for "
                          "its cash in lieu on 2025-01-06", r.stdout)
            out = json.loads(_cli(root, "sanity", "--json").stdout)
            grp = [g for g in out["groups"] if g["accounts"] == ["margin"]]
            self.assertTrue(grp, out)
            self.assertEqual(grp[0]["discrepancies"], [])
            self.assertEqual(
                grp[0]["fractions_awaiting_cash_in_lieu"][0]["symbol"],
                "ZZS.TO")
            holdings(9)
            r = _cli(root, "sanity")
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("QTY_MISMATCH", r.stdout)


if __name__ == "__main__":
    unittest.main()
