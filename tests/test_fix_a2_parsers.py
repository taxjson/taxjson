"""Re-audit-2 fixes, area parsers-common: cross-file dedup per broker
account (A2-0008 and its twins), order-independent dedup (A2-0105,
A2-0624), .tt near-duplicates (A2-0295), restated statements (A2-0108),
taxjson-sort --dedup accounts (A2-0297). Synthetic data only."""
import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taxjson.bin.taxjson_sort import plan_dedup
from taxjson.lib.core import TaxTransaction

SRC = str(Path(__file__).resolve().parents[1] / "src")


def _env():
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC + os.pathsep + env.get("PYTHONPATH", "")
    env["TAXJSON_OFFLINE"] = "1"
    return env


def _tx(date, qty, source, acct="", **kw):
    kw.setdefault("net_amount", -10.0 * qty)
    return TaxTransaction(action="BUYSELL", date=date, symbol="XYZ.TO",
                          quantity=qty, price=10.0,
                          currency="CAD", account="margin",
                          source=source, source_account=acct, **kw)


class TestDedupPerAccount(unittest.TestCase):

    def test_rows_of_different_accounts_never_collapse(self):
        """A2-0008: one file's rows on the shared dates are a subset of
        the other's (a passive account with the same holdings) — the
        overlap test would read a re-export; the row accounts say two."""
        a = [_tx("2025-0%d-28" % m, 0, "qa.csv", "h1", net_amount=9.0)
             for m in range(2, 6)]
        b = [_tx("2025-0%d-28" % m, 0, "qb.csv", "h2", net_amount=9.0)
             for m in range(2, 6)]
        plan = plan_dedup(a + b)
        self.assertEqual(plan.drop, [])
        self.assertEqual(len(plan.keep), 8)
        self.assertEqual(plan.attention, [])
        self.assertEqual(len(plan.notes), 1)
        # Same account in both files: still the re-export rule.
        b2 = [_tx("2025-0%d-28" % m, 0, "qb.csv", "h1", net_amount=9.0)
              for m in range(2, 6)]
        self.assertEqual(len(plan_dedup(a + b2).drop), 4)

    def test_row_account_beats_a_multi_account_file_set(self):
        """A file naming two accounts overlaps a one-account file at the
        file level; the rows themselves are of different accounts."""
        rows = [_tx("2025-03-03", 100, "both.csv", "h2"),
                _tx("2025-03-03", 100, "one.csv", "h1")]
        plan = plan_dedup(rows, {"both.csv": ["h1", "h2"], "one.csv": ["h1"]})
        self.assertEqual(plan.keep, [0, 1])

    def test_kept_set_does_not_depend_on_file_order(self):
        """A2-0105 / A2-0624: two accounts' identical fill plus a .tt
        line equal to it — 2 rows in every order (the .tt line is one
        of them); two .tt files plus one export — 2 rows too."""
        for case, want in (
                ([("acct1.csv", "h1"), ("acct2.csv", "h2"), ("hand.tt", "")], 2),
                ([("a.tt", ""), ("b.tt", ""), ("export.csv", "")], 2),
                ([("x.csv", "h1"), ("hand.tt", "")], 1)):
            kept = set()
            for perm in itertools.permutations(case):
                rows = [_tx("2025-03-03", 100, s, a) for s, a in perm]
                plan = plan_dedup(rows)
                kept.add(len(plan.keep))
                self.assertEqual(len({rows[i].id if i not in plan.relabel
                                      else plan.relabel[i]
                                      for i in plan.keep}), len(plan.keep))
            self.assertEqual(kept, {want}, case)

    def test_tt_line_repeating_an_exported_row_is_named(self):
        """A2-0295: the .tt line never has the export's id (description,
        settle date and gross differ) — both are booked, loudly."""
        exp = TaxTransaction(action="BUYSELL", date="2025-03-03",
                             date_settle="2025-03-04", symbol="XYZ.US",
                             quantity=200, price=10.0, gross_amount=-2000,
                             net_amount=-2001.0, currency="USD",
                             description="XYZ CORP", source="ib.csv")
        tt = TaxTransaction(action="BUYSELL", date="2025-03-04",
                            symbol="XYZ.US", quantity=200, price=10.0,
                            net_amount=-2001.0, currency="USD",
                            source="manual.tt")
        self.assertNotEqual(exp.id, tt.id)
        plan = plan_dedup([exp, tt])
        self.assertEqual(plan.keep, [0, 1])
        self.assertEqual(len(plan.attention), 1)
        self.assertIn("manual.tt line", plan.attention[0])
        self.assertIn("BOTH are booked", plan.attention[0])
        # A different quantity is a different trade: no line.
        tt2 = TaxTransaction(action="BUYSELL", date="2025-03-04",
                             symbol="XYZ.US", quantity=100, price=10.0,
                             net_amount=-1001.0, currency="USD",
                             source="manual.tt")
        self.assertEqual(plan_dedup([exp, tt2]).attention, [])

    def test_restated_rows_are_named(self):
        """A2-0108: a newer statement of one account restated a buy
        (a commission refund folded in): the old and new versions are
        both booked — the ATTENTION line names them."""
        old = [_tx("2025-02-03", 100, "h1.csv", "h1", net_amount=-1435.93),
               _tx("2025-03-03", -100, "h1.csv", "h1")]
        new = [_tx("2025-02-03", 100, "full.csv", "h1", net_amount=-1434.5),
               _tx("2025-03-03", -100, "full.csv", "h1"),
               _tx("2025-08-01", 5, "full.csv", "h1")]
        plan = plan_dedup(old + new)
        self.assertEqual(len(plan.drop), 1)
        self.assertEqual(len(plan.attention), 1)
        self.assertIn("only in full.csv: 2025-02-03 BUYSELL XYZ.TO 100",
                      plan.attention[0])
        self.assertIn("only in h1.csv: 2025-02-03 BUYSELL XYZ.TO 100",
                      plan.attention[0])
        self.assertIn("keep only the newer file", plan.attention[0])


# ------------------------------------------------------------ end to end

_TOML = """[settings]
year = 2025
country = "canada"
province = "ON"
base_currency = "CAD"
source_currencies = []
tax_date = "settle"
option_grant_timing_since = 2025

[accounts.margin]
type = "taxable"
"""
_QH = ("Transaction Date,Settlement Date,Action,Symbol,Description,Quantity,"
       "Price,Gross Amount,Commission,Net Amount,Currency,Account #,"
       "Activity Type,Account Type")


def _qbuy(d, s, acct):
    return (f"{d} 12:00:00 AM,{s} 12:00:00 AM,Buy,XEI.TO,ISHARES TEST HIGH "
            f"DIV,100,25,-2500,-4.95,-2504.95,CAD,{acct},Trades,"
            f"Individual margin")


def _qdiv(m, acct):
    return (f"2025-{m:02d}-28 12:00:00 AM,2025-{m:02d}-28 12:00:00 AM,DIV,"
            f"XEI.TO,ISHARES TEST HIGH DIV DIST ON 100 SHS REC {m:02d}/24/25 "
            f"PAY {m:02d}/28/25,0,0,0,0,9.00,CAD,{acct},Dividends,"
            f"Individual margin")


_RH = ('"Date","Activity","Symbol","Symbol Description","Quantity","Price",'
       '"Settlement Date","Account","Value","Currency","Description"')


def _rrow(d, s, sym, qty, price, val, acct):
    return (f'"{d}","Buy","{sym}","{sym} CORP","{qty}","{price}","{s}",'
            f'"{acct}","{val}","CAD","{sym} CORP"')


def _rbc(rows):
    return ('"Activity Export as of Jan 5, 2026 at 8:59:00 am ET"\n\n'
            + _RH + "\n" + "\n".join(rows) + "\n")


def _project(files):
    root = Path(tempfile.mkdtemp())
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "taxjson.toml").write_text(_TOML)
    for fn, body in files.items():
        (root / "inputs" / "margin" / fn).write_text(body)
    r = subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         "run", "--no-input"], stdin=subprocess.DEVNULL,
        capture_output=True, text=True, env=_env())
    return root, r


class TestCrossAccountEndToEnd(unittest.TestCase):

    def _base(self, files):
        root, r = _project(files)
        self.addCleanup(shutil.rmtree, root, True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        base = json.loads((root / "work" / "margin_base.json").read_text())
        work = "".join(p.read_text(errors="replace")
                       for p in (root / "work").iterdir() if p.is_file())
        # The broker account id itself never reaches work/ files.
        self.assertNotIn("55500001", work)  # pii-ok
        self.assertNotIn("55500002", work)  # pii-ok
        return base["transactions"], r

    def test_questrade_two_accounts_distributions_all_booked(self):
        a = [_qbuy("2025-01-10", "2025-01-13", "55500001")] + [  # pii-ok
            _qdiv(m, "55500001") for m in range(2, 13)]  # pii-ok
        b = [_qbuy("2025-01-20", "2025-01-21", "55500002")] + [  # pii-ok
            _qdiv(m, "55500002") for m in range(2, 13)]  # pii-ok
        txs, r = self._base({"questrade_a.csv": _QH + "\n" + "\n".join(a) + "\n",
                             "questrade_b.csv": _QH + "\n" + "\n".join(b) + "\n"})
        divs = [t for t in txs if t["action"] == "DIVIDEND"]
        self.assertEqual(len(divs), 22)
        self.assertAlmostEqual(sum(t["net_amount"] for t in divs), 198.0)
        self.assertEqual(len({t["source_account"] for t in txs}), 2)

    def test_questrade_two_accounts_in_one_export_and_one_alone(self):
        """Per row: the combined export's account-2 rows are not copies
        of the account-1 export's rows."""
        both = [_qbuy("2025-03-03", "2025-03-04", "55500001"),  # pii-ok
                _qbuy("2025-03-03", "2025-03-04", "55500002")]  # pii-ok
        one = [_qbuy("2025-03-03", "2025-03-04", "55500001")]  # pii-ok
        txs, r = self._base({
            "questrade_all.csv": _QH + "\n" + "\n".join(both) + "\n",
            "questrade_one.csv": _QH + "\n" + "\n".join(one) + "\n"})
        self.assertEqual(sum(t["quantity"] for t in txs
                             if t["action"] == "BUYSELL"), 200)

    def test_rbc_two_accounts_identical_buys_all_booked(self):
        ra = [_rrow("May 5, 2025", "May 6, 2025", "XEI", 100, 26, -2604.95,
                    "55500001"),  # pii-ok
              _rrow("March 3, 2025", "March 4, 2025", "XEI", 100, 25,
                    -2504.95, "55500001")]  # pii-ok
        rb = [_rrow("May 5, 2025", "May 6, 2025", "XEI", 100, 26, -2604.95,
                    "55500002"),  # pii-ok
              _rrow("April 4, 2025", "April 7, 2025", "ZEB", 10, 40, -404.95,
                    "55500002"),  # pii-ok
              _rrow("March 3, 2025", "March 4, 2025", "XEI", 100, 25,
                    -2504.95, "55500002")]  # pii-ok
        txs, r = self._base({"rbc_a.csv": _rbc(ra), "rbc_b.csv": _rbc(rb)})
        self.assertEqual(sum(t["quantity"] for t in txs
                             if t["symbol"] == "XEI.TO"), 400)

    def test_same_account_re_export_still_collapses(self):
        a = [_qbuy("2025-03-03", "2025-03-04", "55500001"),  # pii-ok
             _qdiv(4, "55500001")]  # pii-ok
        txs, r = self._base({
            "questrade_a.csv": _QH + "\n" + "\n".join(a) + "\n",
            "questrade_b.csv": _QH + "\n" + "\n".join(a) + "\n"})
        self.assertEqual(len(txs), 2)


class TestSortDedupReadsAccounts(unittest.TestCase):

    def test_merge_then_sort_keeps_two_accounts(self):
        """A2-0297: taxjson-merge | taxjson-sort --dedup applies the
        per-file accounts the parse recorded, as merge2 does."""
        td = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, td, True)
        row = _tx("2025-03-03", 100, "").to_dict()
        for name, h in (("a", "h1"), ("b", "h2")):
            (td / f"{name}.json").write_text(json.dumps({
                "transactions": [dict(row, source=f"{name}.csv")],
                "metadata": {"source_brokerage": "ib",
                             "source_accounts": {f"{name}.csv": [h]}}}))
        m = subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_merge",
             str(td / "a.json"), str(td / "b.json")],
            capture_output=True, text=True, env=_env())
        self.assertEqual(m.returncode, 0, m.stderr)
        s = subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_sort", "--dedup"],
            input=m.stdout, capture_output=True, text=True, env=_env())
        self.assertEqual(s.returncode, 0, s.stderr)
        self.assertEqual(len(json.loads(s.stdout)["transactions"]), 2)
        self.assertIn("different broker accounts", s.stderr)


if __name__ == "__main__":
    unittest.main()


# ------------------------------------------------- IB Ca across accounts

def _ib_head(acct, period):
    return ('Statement,Header,Field Name,Field Value\n'
            'Statement,Data,BrokerName,Interactive Brokers Canada Inc.\n'
            'Statement,Data,Title,Activity Statement\n'
            f'Statement,Data,Period,"{period}"\n'
            'Account Information,Header,Field Name,Field Value\n'
            f'Account Information,Data,Account,{acct}\n')


_IB_TRADES_H = ('Trades,Header,DataDiscriminator,Asset Category,Currency,'
                'Symbol,Date/Time,Quantity,T. Price,C. Price,'
                'Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code\n')
_IB_CA_H = ('Corporate Actions,Header,Asset Category,Currency,Report Date,'
            'Date/Time,Description,Quantity,Proceeds,Value,Realized P/L,'
            'Code\n')


def _ib_trade(sym, when, qty, price, comm=-1.0, code='O', cur='USD'):
    proceeds = round(-qty * price, 6)
    return (f'Trades,Data,Order,Stocks,{cur},{sym},"{when}",{qty},'
            f'{price},{price},{proceeds},{comm},0,0,0,{code}\n')


def _ib_ca(desc, qty, when, value=0, proceeds=0, cur='USD', code=''):
    return (f'Corporate Actions,Data,Stocks,{cur},{when[:10]},"{when}",'
            f'"{desc}",{qty},{proceeds},{value},0,{code}\n')


def _brokerage_ib(files):
    td = Path(tempfile.mkdtemp())
    try:
        paths = []
        for n, body in files.items():
            (td / n).write_text(body)
            paths.append(str(td / n))
        r = subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_brokerage",
             "--brokerage", "ib", "--account", "margin"] + paths,
            capture_output=True, text=True, env=_env())
        txs = json.loads(r.stdout)["transactions"] if r.stdout.strip() else []
        return r, txs
    finally:
        shutil.rmtree(td, True)


_A, _B = "U5550001", "U5550002"  # pii-ok


class TestIbCancellationsStayInTheirAccount(unittest.TestCase):

    def test_corporate_action_ca_is_not_offered_to_another_account(self):
        """A2-1090: B's Ca of its own cash in lieu (B's 2025 statement
        absent) must not undo A's identical CIL."""
        cil = ("ZZT(US0000000001) Cash in Lieu of Fractional Shares "
               "(ZZT, ZZT CORP, US0000000001)")
        a25 = (_ib_head(_A, "January 1, 2025 - December 31, 2025")
               + _IB_TRADES_H
               + _ib_trade("ZZT", "2025-01-10, 10:00:00", 100.5, 20, comm=0)
               + _IB_CA_H + _ib_ca(cil, -0.5, "2025-11-05, 20:25:00",
                                   proceeds=10, value=10))
        b26 = (_ib_head(_B, "January 1, 2026 - March 31, 2026") + _IB_CA_H
               + _ib_ca(cil, 0.5, "2025-11-05, 20:25:00", proceeds=-10,
                        value=-10, code="Ca"))
        r, txs = _brokerage_ib({"ib_A_2025.csv": a25, "ib_B_2026.csv": b26})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue([t for t in txs if t["quantity"] == -0.5], txs)
        self.assertNotIn("is undone", r.stderr)

    def test_trade_ca_is_not_paired_with_another_accounts_fill(self):
        """A2-1095: merge2's trade-Ca pairing compares the broker
        account, not the taxjson label."""
        a25 = (_ib_head(_A, "January 1, 2025 - December 31, 2025")
               + _IB_TRADES_H
               + _ib_trade("QZK", "2025-10-22, 10:00:00", 100, 10, comm=-1))
        b26 = (_ib_head(_B, "January 1, 2026 - March 31, 2026")
               + _IB_TRADES_H
               + _ib_trade("QZK", "2025-10-22, 10:00:00", -100, 10, comm=1,
                           code="Ca"))
        r, txs = _brokerage_ib({"ib_A_2025.csv": a25, "ib_B_2026.csv": b26})
        self.assertEqual(r.returncode, 0, r.stderr)
        td = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, td, True)
        (td / "p.json").write_text(r.stdout)
        m = subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_merge2", "--sort",
             "--dedup", str(td / "p.json")],
            capture_output=True, text=True, env=_env())
        self.assertEqual(m.returncode, 0, m.stderr)
        rows = json.loads(m.stdout)["transactions"]
        self.assertTrue([t for t in rows if t["quantity"] == 100], rows)
        self.assertIn("original fill is in none", m.stderr)


class TestOneBrokerAccountInTwoTaxjsonAccounts(unittest.TestCase):

    def test_same_export_in_two_accounts_is_loud(self):
        """A2-0293 / A2-0630: the same export under two inputs/ folders
        is booked in both — the console names the pair."""
        body = _QH + "\n" + _qbuy("2025-03-03", "2025-03-04",
                                  "55500001") + "\n"  # pii-ok
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        for acct in ("margin", "margin2"):
            (root / "inputs" / acct).mkdir(parents=True)
            (root / "inputs" / acct / "questrade.csv").write_text(body)
        (root / "taxjson.toml").write_text(
            _TOML + '\n[accounts.margin2]\ntype = "taxable"\n')
        r = subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
             str(root), "run", "--no-input"], stdin=subprocess.DEVNULL,
            capture_output=True, text=True, env=_env())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("feeds two taxjson accounts, margin and margin2 "
                      "(1 identical row(s))", r.stdout)
        self.assertNotIn("55500001", r.stdout + r.stderr)  # pii-ok

    def test_different_broker_accounts_are_quiet(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        for acct, num in (("margin", "55500001"), ("margin2", "55500002")):  # pii-ok
            (root / "inputs" / acct).mkdir(parents=True)
            (root / "inputs" / acct / "questrade.csv").write_text(
                _QH + "\n" + _qbuy("2025-03-03", "2025-03-04", num) + "\n")
        (root / "taxjson.toml").write_text(
            _TOML + '\n[accounts.margin2]\ntype = "taxable"\n')
        r = subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
             str(root), "run", "--no-input"], stdin=subprocess.DEVNULL,
            capture_output=True, text=True, env=_env())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("feeds two taxjson accounts", r.stdout)


class TestTransferEvidencePerAccount(unittest.TestCase):

    def test_identical_custody_moves_of_two_accounts_both_kept(self):
        """A2-1093 / A2-1094: the --transfers-out sidecar dedup keys on
        the broker account too."""
        from taxjson.bin.taxjson_brokerage import (_dedup_evidence,
                                                   stamp_source_accounts)
        mv = {"action": "TRANSFER", "date": "2025-04-01", "symbol": "XYZ.US",
              "quantity": 50.0, "currency": "USD", "account": "IB"}
        a, b = [dict(mv)], [dict(mv)]
        stamp_source_accounts(a, {_A})
        stamp_source_accounts(b, {_B})
        self.assertEqual(len(_dedup_evidence([a, b])), 2)
        # A re-download of ONE account still collapses.
        c = [dict(mv)]
        stamp_source_accounts(c, {_A})
        self.assertEqual(len(_dedup_evidence([a, c])), 1)
        # The raw id never stays on a row.
        self.assertNotIn("broker_account", a[0])
        self.assertNotEqual(a[0]["source_account"], _A)


def _brokerage(brokerage, files, *extra):
    td = Path(tempfile.mkdtemp())
    try:
        paths = []
        for n, body in files.items():
            (td / n).write_text(body)
            paths.append(str(td / n))
        r = subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_brokerage",
             "--brokerage", brokerage] + list(extra) + paths,
            capture_output=True, text=True, env=_env(), cwd=str(td))
        txs = json.loads(r.stdout)["transactions"] if r.stdout.strip() else []
        return r, txs
    finally:
        shutil.rmtree(td, True)


class TestZeroTransactionGuard(unittest.TestCase):
    """A2-0301 / A2-0303: a file whose rows are all recognized non-events
    is not the 'NONE of its rows are in the books' regression."""

    def test_questrade_fx_conversion_only(self):
        row = ("2025-03-03 12:00:00 AM,2025-03-03 12:00:00 AM,FXT,,"
               "CONVERSION - USD/CAD,0,0,0,0,-100,USD,55500001,"  # pii-ok
               "FX conversion,Individual margin")
        r, txs = _brokerage("questrade", {"q.csv": _QH + "\n" + row + "\n"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(txs, [])
        self.assertNotIn("parsed to 0 transactions", r.stderr)
        self.assertIn("0 tax objects (1 recognized non-event row(s))",
                      r.stderr)

    def test_an_unclassified_row_still_warns(self):
        row = ("2025-03-03 12:00:00 AM,2025-03-03 12:00:00 AM,ZZZ,XEI.TO,"
               "SOMETHING NEW,0,0,0,0,0,CAD,55500001,Other,"  # pii-ok
               "Individual margin")
        r, txs = _brokerage("questrade", {"q.csv": _QH + "\n" + row + "\n"})
        self.assertIn("parsed to 0 transactions", r.stderr)


class TestSecurityOverrides(unittest.TestCase):

    def _ib(self, rows):
        return (_ib_head(_A, "January 1, 2025 - December 31, 2025")
                + _IB_TRADES_H + "".join(rows))

    def test_one_line_rewriting_two_raw_symbols_is_loud(self):
        """A2-0109: 'LEN' matches the whole word in 'LEN B' too."""
        stmt = self._ib([
            _ib_trade("LEN", "2025-03-03, 10:00:00", 100, 120),
            _ib_trade("LEN B", "2025-03-03, 10:00:00", 100, 110)])
        td = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, td, True)
        (td / "ov.txt").write_text("LEN | USD | LEN.NE\n")
        r, txs = _brokerage("ib", {"ib.csv": stmt}, "--security-overrides",
                            str(td / "ov.txt"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("ATTENTION: security override 'len | USD | LEN.NE' "
                      "rewrote 2 different raw symbols", r.stderr)

    def test_slash_futures_are_never_rewritten(self):
        """A2-1092: every futures spelling is exempt, not only F:."""
        from taxjson.bin.taxjson_brokerage import apply_security_override
        ovr = [("buy", "*", "BUY.US")]
        for sym in ("F:ESZ5.US", "/ESZ5.US", "\\ESZ5.US"):
            tx = {"symbol": sym, "description": "BUY", "currency": "USD"}
            apply_security_override(tx, ovr)
            self.assertEqual(tx["symbol"], sym)
        tx = {"symbol": "XYZ.US", "description": "BUY", "currency": "USD"}
        apply_security_override(tx, ovr)
        self.assertEqual(tx["symbol"], "BUY.US")


class TestKrakenSendNote(unittest.TestCase):

    def test_hybrid_earn_move_is_not_counted_as_a_send(self):
        """A2-1078: crypto-sends decides a Hybrid Earn move `self`; the
        parse NOTE no longer counts it as a possible disposition."""
        head = ('"txid","refid","time","type","subtype","aclass",'
                '"subclass","asset","wallet","amount","fee","balance",'
                '"amountusd","feeusd","balanceusd","feecurrency"\n')
        body = (
            '"L1","FTQZQZQ","2026-05-12 09:14:41","hybridearnwithdrawal",'
            '"","currency","stable_coin","USDC","spot / main",'
            '"-500.00000000","0","0.00000032","-499.9","0","0",""\n'
            '"L2","FTQZQZR","2026-05-13 09:14:41","withdrawal",'
            '"","currency","cryptocurrency","ETH","spot / main",'
            '"-1.00000000","0","0","-3000","0","0",""\n')
        r, _ = _brokerage("kraken", {"kr_ledgers_2026.csv": head + body})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("NOTE: 1 crypto withdrawal/send(s)", r.stderr)


class TestStrictNumbersAndStrikes(unittest.TestCase):

    def test_stacked_currency_signs_are_refused(self):
        """A2-0633: at most one currency sign."""
        from taxjson.lib.brokerages.base import (BrokerageParseError,
                                                 parse_strict_number)
        for bad in ("$€5", "$-€5", "€$5"):
            with self.assertRaises(BrokerageParseError, msg=bad):
                parse_strict_number(bad)
        self.assertEqual(parse_strict_number("$-12.00"), -12.0)
        self.assertEqual(parse_strict_number("-$12.00"), -12.0)
        self.assertEqual(parse_strict_number("€5"), 5.0)

    def test_a_decimal_comma_strike_is_refused(self):
        """A2-0632: '2,50' / '12,5' / '1,0000' were cut at the comma."""
        from taxjson.lib.brokerages.base import BrokerageParseError
        from taxjson.lib.brokerages.questrade import QuestradeBrokerage
        qt = QuestradeBrokerage()
        for bad in ("2,50", "12,5", "1,0000"):
            with self.assertRaises(BrokerageParseError, msg=bad):
                qt.parse_option_from_description(f"CALL BKQ 06/20/25 {bad}")
        for good, want in (("1,000", "1000"), ("7,500.50", "7500.50"),
                           ("12,345", "12345"), ("150.00", "150.00"),
                           ("30", "30")):
            got = qt.parse_option_from_description(
                f"CALL BKQ 06/20/25 {good} BKQ HOLDINGS")
            self.assertEqual(got["strike"], want, good)


from tax_rules import rule  # noqa: E402


class TestFarLateSettleDate(unittest.TestCase):

    @rule("CA-DATE-03")
    @rule("US-DATE-04")
    def test_a_settle_date_a_year_out_is_flagged(self):
        """A2-0104: a printed settle date 366 days after the trade moved
        the sale into the next tax year with no console line."""
        sell = ("2025-12-15 12:00:00 AM,2026-12-16 12:00:00 AM,Sell,XEI.TO,"
                "XEI CORP,-100,30,3000,-4.95,2995.05,CAD,55500001,Trades,"  # pii-ok
                "Individual margin")
        ok = ("2025-12-15 12:00:00 AM,2025-12-16 12:00:00 AM,Sell,XEI.TO,"
              "XEI CORP,-100,30,3000,-4.95,2995.05,CAD,55500001,Trades,"  # pii-ok
              "Individual margin")
        r, txs = _brokerage("questrade", {"q.csv": _QH + "\n" + sell + "\n"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(txs[0]["date_settle"], "2026-12-16")
        self.assertIn("warning: ATTENTION:", r.stderr)
        self.assertIn("366 days after the trade date", r.stderr)
        r, txs = _brokerage("questrade", {"q.csv": _QH + "\n" + ok + "\n"})
        self.assertNotIn("days after the trade date", r.stderr)


class TestCutShortExport(unittest.TestCase):
    """A2-0110: a last line cut short was booked as a smaller amount."""

    DEMO = Path(__file__).resolve().parents[1] / "examples"

    def test_cut_number_is_flagged(self):
        body = (self.DEMO / "rbc_direct_demo.csv").read_text()
        cut = body.rstrip("\n")[:-4]          # ...,183.00 -> ...,18
        self.assertTrue(cut.endswith(",18"))
        r, txs = _brokerage("rbc_direct", {"rbc.csv": cut})
        self.assertIn("warning: ATTENTION: rbc.csv: the file does not end "
                      "with a line break", r.stderr)
        r, txs = _brokerage("rbc_direct", {"rbc.csv": body})
        self.assertNotIn("does not end with a line break", r.stderr)

    def test_open_quote_is_refused(self):
        body = (self.DEMO / "coinbase_demo.csv").read_text().rstrip("\n")
        cut = body[:body.rindex(",") + 1] + '"Sold 0.25 ETH'
        r, txs = _brokerage("coinbase", {"cb.csv": cut})
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("ends inside a quoted cell", r.stderr)

    def test_a_quoted_last_cell_without_line_break_is_whole(self):
        body = (self.DEMO / "coinbase_demo.csv").read_text().rstrip("\n")
        whole = body[:body.rindex(",") + 1] + '"Sold 0.25 ETH for $967.20 USD"'
        r, txs = _brokerage("coinbase", {"cb.csv": whole})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("line break", r.stderr)


class TestWebullAndGenericAccountsThroughBrokerage(unittest.TestCase):
    """A2-0286 / A2-1085: the parser-side broker accounts reach the
    books through taxjson-brokerage (hashed) and keep two accounts'
    identical rows."""

    def _merge(self, brokerage, files):
        r, txs = _brokerage(brokerage, files)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("unknown field 'broker_account'", r.stderr)
        accts = {t.get("source_account") for t in txs}
        self.assertEqual(len(accts), 2, txs)
        for t in txs:
            self.assertNotIn("broker_account", t)
        plan = plan_dedup(txs)
        return plan, txs

    def test_two_webull_accounts(self):
        from test_fix_a2_webull import _H25, _BUY
        def pre(acct):
            return (",,,,,,,,,\n"
                    f"Account Number / Numéro de compte:,,,,,,,{acct},,\n"
                    "Year / Année:,,,,,,,2024,,\n"
                    "Report / Rapport:,,,,,,,TRADING SUMMARY / RÉSUMÉ DES "
                    "TRANSACTIONS,,\n")
        plan, txs = self._merge("webull", {
            "wb_a.csv": pre("55500001") + _H25 + _BUY,  # pii-ok
            "wb_b.csv": pre("55500002") + _H25 + _BUY})  # pii-ok
        self.assertEqual(plan.drop, [])

    def test_two_generic_accounts(self):
        mapping = ('[columns]\ndate = "Date"\naction = "Type"\n'
                   'symbol = "Ticker"\nquantity = "Qty"\nprice = "Price"\n'
                   'amount = "Amount"\naccount = "Acct"\n'
                   '[actions]\n"BUY" = "buy"\n[defaults]\ncurrency = "CAD"\n')
        row = "2025-03-03,BUY,XYZ.TO,10,5,-50,{}\n"
        hdr = "Date,Type,Ticker,Qty,Price,Amount,Acct\n"
        td = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, td, True)
        (td / "generic.toml").write_text(mapping)
        files = {"gen_a.csv": hdr + row.format("55500001"),  # pii-ok
                 "gen_b.csv": hdr + row.format("55500002")}  # pii-ok
        paths = []
        for n, b in files.items():
            (td / n).write_text(b)
            paths.append(str(td / n))
        r = subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_brokerage",
             "--brokerage", "generic"] + paths,
            capture_output=True, text=True, env=_env(), cwd=str(td))
        self.assertEqual(r.returncode, 0, r.stderr)
        txs = json.loads(r.stdout)["transactions"]
        self.assertEqual(len({t["source_account"] for t in txs}), 2)
        self.assertEqual(plan_dedup(txs).drop, [])


class TestZeroCostBuy(unittest.TestCase):
    """A2-0619 (Questrade and RBC halves; Webull refuses it): a share buy
    at $0 price and $0 cash is flagged, as the generic importer refuses
    it."""

    def test_questrade(self):
        row = ("2025-03-03 12:00:00 AM,2025-03-04 12:00:00 AM,Buy,XEI.TO,"
               "XEI CORP,100,0,0,0,0,CAD,55500001,Trades,"  # pii-ok
               "Individual margin")
        r, txs = _brokerage("questrade", {"q.csv": _QH + "\n" + row + "\n"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("at ZERO cost", r.stderr)
        r, txs = _brokerage("questrade", {"q.csv": _QH + "\n" + _qbuy(
            "2025-03-03", "2025-03-04", "55500001") + "\n"})  # pii-ok
        self.assertNotIn("at ZERO cost", r.stderr)

    def test_rbc(self):
        r, txs = _brokerage("rbc_direct", {"rbc.csv": _rbc([_rrow(
            "March 3, 2025", "March 4, 2025", "XEI", 100, 0, 0,
            "55500001")])})  # pii-ok
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("at ZERO cost", r.stderr)
