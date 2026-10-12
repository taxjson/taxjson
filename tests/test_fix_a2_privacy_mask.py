"""Re-audit-2 fixes: id masking in diagnostics (fix list privacy-02).

A2-0756 / A2-0757 / A2-1381 (every Kraken refid / txid in a note, an
error or a skip summary is masked to its first 2 characters + ***),
A2-0461 (the file-name account-id mask is pinned at the IB and generic
broker diagnostics). Synthetic data only.
"""
import os
import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from taxjson.lib.brokerages.kraken import KrakenBrokerage


def setUpModule():
    # Crypto UTC stamps need a named zone (no default since the 2026-10
    # generalisation): the parsers outside a project read
    # TAXJSON_LOCAL_TZ; the project fixtures here set local_timezone.
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"

_KT_H = ("txid,ordertxid,pair,time,type,ordertype,price,cost,fee,vol,"
         "margin,misc,ledgers\n")
_KL_H = ("txid,refid,time,type,subtype,aclass,asset,wallet,amount,fee,"
         "balance\n")

# Synthetic ids, long enough that a leak is unmistakable.
_REF = "TSECRETREFAAAA"
_TX = "LSECRETTXIDBBBB"


def _kraken(files, which):
    """Parse; return (txs or exception, stderr, extractor)."""
    with tempfile.TemporaryDirectory() as td:
        for name, text in files.items():
            (Path(td) / name).write_text(text)
        k = KrakenBrokerage()
        k.stablecoins_as_cash = True
        buf = io.StringIO()
        out = None
        with redirect_stderr(buf):
            try:
                out = k.parse_file(Path(td) / which)
            except ValueError as e:
                out = e
        return out, buf.getvalue(), k


class TestKrakenIdsMasked(unittest.TestCase):
    """A2-0756 / A2-0757 / A2-1381."""

    def _assert_masked(self, text):
        self.assertNotIn(_REF, text)
        self.assertNotIn(_TX, text)
        self.assertNotIn(_REF[2:6], text)
        self.assertNotIn(_TX[2:6], text)

    def _everything(self, out, err, k):
        skips = " ".join(getattr(k, "_skip_counts", {}) or {})
        return f"{err}\n{out if isinstance(out, Exception) else ''}\n{skips}"

    def test_multi_leg_note_masks_the_refid(self):
        led = _KL_H + "".join(r + "\n" for r in [
            f"L1,{_REF},2025-03-01 12:00:00,spend,,currency,ADA,spot,-10,0,0",
            f"L2,{_REF},2025-03-01 12:00:00,spend,,currency,DOT,spot,-1,0,0",
            f"L3,{_REF},2025-03-01 12:00:00,receive,,currency,XETH,spot,"
            "0.004,0,0.004"])
        out, err, k = _kraken({"kr_ledgers.csv": led}, "kr_ledgers.csv")
        self.assertIsInstance(out, list)
        self.assertIn("TS***", err)
        self._assert_masked(self._everything(out, err, k))
        # The refid stays the work-JSON id (data, not a message).
        self.assertTrue(any(_REF in (t.get("id") or "") for t in out))

    def test_both_sides_many_legs_error_masks_the_refid(self):
        led = _KL_H + "".join(r + "\n" for r in [
            f"L1,{_REF},2025-03-01 12:00:00,spend,,currency,ADA,spot,-10,0,0",
            f"L2,{_REF},2025-03-01 12:00:00,spend,,currency,DOT,spot,-1,0,0",
            f"L3,{_REF},2025-03-01 12:00:00,receive,,currency,XETH,spot,"
            "0.004,0,0.004",
            f"L4,{_REF},2025-03-01 12:00:00,receive,,currency,SOL,spot,"
            "0.1,0,0.1"])
        out, err, k = _kraken({"kr_ledgers.csv": led}, "kr_ledgers.csv")
        self.assertIsInstance(out, ValueError)
        self.assertIn("TS***", str(out))
        self._assert_masked(self._everything(out, err, k))

    def test_orphan_leg_skip_category_masks_the_refid(self):
        led = _KL_H + (f"L1,{_REF},2025-03-01 12:00:00,spend,,currency,"
                       "ADA,spot,-10,0,0\n")
        out, err, k = _kraken({"kr_ledgers.csv": led}, "kr_ledgers.csv")
        self.assertIn("orphan spend (refid TS***)", k._skip_counts)
        self._assert_masked(self._everything(out, err, k))

    def test_ledger_parse_error_masks_the_txid(self):
        led = _KL_H + (f"{_TX},{_REF},2025-03-01 12:00:00,deposit,,currency,"
                       "ADA,spot,abc,0,0\n")
        out, err, k = _kraken({"kr_ledgers.csv": led}, "kr_ledgers.csv")
        self.assertIsInstance(out, ValueError)
        self.assertIn("LS***", str(out))
        self._assert_masked(self._everything(out, err, k))

    def test_trades_parse_error_masks_the_txid(self):
        t = _KT_H + (f"{_TX},O1,XBT/CAD,2025-06-02 16:00:00,buy,limit,"
                     "abc,9000,10,0.1,,,\n")
        out, err, k = _kraken({"kr_trades.csv": t}, "kr_trades.csv")
        self.assertIsInstance(out, ValueError)
        self.assertIn("LS***", str(out))
        self._assert_masked(self._everything(out, err, k))

    def test_trade_ledger_join_error_masks_the_txid(self):
        t = _KT_H + (f"{_TX},O1,XBT/CAD,2025-06-02 16:00:00,buy,limit,"
                     "90000,9000,10,0.1,,,\n")
        led = _KL_H + "".join(r + "\n" for r in [
            f"L1,{_TX},2025-06-02 16:00:00,trade,,currency,ZCAD,spot,"
            "-9010,0,0",
            f"L2,{_TX},2025-06-02 16:00:00,trade,,currency,ADA,spot,"
            "5,0,5"])
        out, err, k = _kraken({"kr_trades.csv": t, "kr_ledgers.csv": led},
                              "kr_trades.csv")
        self.assertIsInstance(out, ValueError)
        self.assertIn("LS***", str(out))
        self._assert_masked(self._everything(out, err, k))


# ------------------------------------------------------------ A2-0461
_REPO = Path(__file__).resolve().parent.parent
_ID = "U5550001"  # pii-ok (synthetic)
_IB_NAME = f"{_ID}_20240101_20241231.csv"


def _ib_demo():
    return (_REPO / "examples" / "ib_demo.csv").read_text(encoding="utf-8")


def _brokerage(name, text, *extra):
    import os
    import subprocess
    import sys
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / name
        p.write_text(text, encoding="utf-8")
        env = dict(os.environ, PYTHONPATH=str(_REPO / "src"))
        r = subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_brokerage", *extra,
             str(p)], capture_output=True, text=True, env=env,
            stdin=subprocess.DEVNULL)
    return r.returncode, r.stdout, r.stderr


def _ib_variant(kind):
    t = _ib_demo()
    if kind == "no_cash_report":        # ATTENTION: not reconciled
        return t
    if kind == "title_refused":         # Realized Summary refusal
        return t.replace("Title,Activity Statement",
                         "Title,Realized Summary")
    if kind == "title_warning":         # unknown title: warning
        return t.replace("Title,Activity Statement",
                         "Title,Custom Statement")
    cash = ("Cash Report,Header,Currency Summary,Currency,Total,\n"
            "Cash Report,Data,Starting Cash,Base Currency Summary,0,\n")
    if kind == "cash_mismatch":         # parsed vs Cash Report
        return t + cash + "Cash Report,Data,Dividends,USD,999.00,\n"
    if kind == "cash_unreadable":       # Cash Report total not a number
        return t + cash + "Cash Report,Data,Dividends,USD,abc,\n"
    if kind == "row_cut_short":         # a Trades row missing cells
        return t.replace(',-1.00,1481.00,0,0,O', ',-1.00', 1)
    raise AssertionError(kind)


class TestFileNameIdMasked(unittest.TestCase):
    """A2-0461 (S027-02 pin): a broker's default download name carries
    the account id; every diagnostic shows it masked (U5***)."""

    def test_ib_diagnostics_never_print_the_id(self):
        for kind in ("no_cash_report", "title_refused", "title_warning",
                     "cash_mismatch", "cash_unreadable", "row_cut_short"):
            with self.subTest(kind=kind):
                rc, out, err = _brokerage(_IB_NAME, _ib_variant(kind),
                                         "--brokerage", "ib")
                self.assertIn("U5***", err)
                self.assertNotIn(_ID, err)
                if kind not in ("no_cash_report", "title_warning"):
                    self.assertNotEqual(rc, 0, err)

    def test_generic_parser_diagnostics_never_print_the_id(self):
        # The questrade demo under an account-numbered name: the
        # per-file summary and lint lines name the file.
        qt = (_REPO / "examples" / "questrade_demo.csv").read_text(
            encoding="utf-8")
        name = "55500001_activity.csv"  # pii-ok (synthetic)
        rc, out, err = _brokerage(name, qt, "--brokerage", "questrade",
                                  "--lint")
        self.assertEqual(rc, 0, err)
        self.assertIn("55***", err)
        self.assertNotIn("55500001", err)  # pii-ok
        rc, out, err = _brokerage(name, qt.replace(",", ";", 3),
                                  "--brokerage", "questrade")
        self.assertNotEqual(rc, 0)
        self.assertNotIn("55500001", err)  # pii-ok

    def test_kraken_orphan_warning_masks_the_refid(self):
        led = _KL_H + (f"L1,{_REF},2025-03-01 12:00:00,spend,,currency,"
                       "ADA,spot,-10,0,0\n")
        rc, out, err = _brokerage("kr_ledgers.csv", led,
                                  "--brokerage", "kraken")
        self.assertIn("TS***", err)
        self.assertNotIn(_REF, err)
        self.assertNotIn(_REF, out)


# ------------------------------------------------------------ A2-0159
_TT_BUY = "BUYSELL 2025-03-04 10:00:00 XEI.TO 100 CAD 25.00 2500.00 9.96\n"
_TT_SELL = "BUYSELL 2025-06-04 10:00:00 XEI.TO -200 CAD 30.00 6000.00 9.96\n"


class TestMaskedSourceNamesStayDistinct(unittest.TestCase):
    """A2-0159: two .tt / generic files whose names differ only in an
    account-number token used to share the masked `source`
    ('manual_55***.tt'), so dedup read them as one file repeating
    itself and dropped a real trade silently."""

    def _tt_rows(self, files):
        from taxjson.bin.taxjson_convert_tt import tt_to_json
        rows = []
        with tempfile.TemporaryDirectory() as td:
            for name, text in files.items():
                p = Path(td) / name
                p.write_text(text)
                rows += tt_to_json(p, "margin")["transactions"]
        return rows

    def _plan(self, rows, accts=None):
        from taxjson.bin.taxjson_sort import plan_dedup
        return plan_dedup(rows, accts)

    def test_two_tt_files_named_after_account_numbers_both_book(self):
        rows = self._tt_rows({
            "manual_55500001.tt": _TT_BUY,              # pii-ok
            "manual_55500002.tt": _TT_BUY + _TT_SELL})  # pii-ok
        self.assertEqual(rows[0]["source"], "manual_55***.tt")
        plan = self._plan(rows)
        self.assertEqual(plan.drop, [])
        self.assertEqual(len(plan.keep), 3)
        text = " ".join(plan.attention + plan.notes)
        self.assertIn("Hand-kept .tt files are separate records", text)
        self.assertIn("manual_55***.tt", text)
        self.assertNotIn("55500001", text)  # pii-ok
        self.assertNotIn("55500002", text)  # pii-ok

    def test_run_books_both_buys_and_prints_no_id(self):
        import json
        from test_fix_a2_pipecmd import _cli, _tt_project
        with tempfile.TemporaryDirectory() as td:
            root, home = _tt_project(td, [_TT_BUY.strip()], year=2025)
            d = root / "inputs" / "m"
            (d / "a.tt").rename(d / "manual_55500001.tt")   # pii-ok
            (d / "manual_55500002.tt").write_text(          # pii-ok
                _TT_BUY + _TT_SELL)
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            both = r.stdout + r.stderr
            self.assertNotIn("5550000", both)
            self.assertIn("Hand-kept .tt files are separate records", both)
            s = _cli(root, home, "sum", "--json")
            acct = json.loads(s.stdout)["filing"]["accounts"][0]
            self.assertAlmostEqual(acct["acb"], 5000.0)   # both buys
            self.assertAlmostEqual(acct["gain"], 1000.0)

    def test_same_file_twice_still_collapses(self):
        rows = self._tt_rows({"manual_55500001.tt": _TT_BUY})  # pii-ok
        plan = self._plan(rows + [dict(r) for r in rows])
        self.assertEqual(len(plan.drop), 1)

    def test_unmasked_names_keep_their_old_shape(self):
        rows = self._tt_rows({"margin_start.tt": _TT_BUY})
        self.assertEqual(rows[0]["source"], "margin_start.tt")
        self.assertNotIn("source_key", rows[0])

    def test_generic_files_of_two_numbered_names_warn(self):
        # One shared row and one row each: too thin an overlap to call
        # a re-export, so the shared row is kept once WITH the ATTENTION.
        def row(i, src, key):
            return {"id": f"r{i}", "date": f"2025-01-0{i}",
                    "action": "BUYSELL", "symbol": "XEI.TO",
                    "quantity": 100, "source": src, "source_key": key}
        a = [row(1, "generic_55***.csv", "k1"), row(2, "generic_55***.csv",
                                                     "k1")]
        b = [row(1, "generic_55***.csv", "k2"), row(3, "generic_55***.csv",
                                                     "k2")]
        plan = self._plan(a + b)
        self.assertEqual(plan.drop, [2])
        self.assertTrue(any("identical row" in x for x in plan.attention),
                        plan.attention)

    def test_brokerage_stamps_a_key_for_a_masked_name(self):
        import json
        qt = (_REPO / "examples" / "questrade_demo.csv").read_text(
            encoding="utf-8")
        keys = []
        for name in ("55500001_activity.csv",   # pii-ok
                     "55500002_activity.csv"):  # pii-ok
            rc, out, err = _brokerage(name, qt, "--brokerage", "questrade")
            self.assertEqual(rc, 0, err)
            doc = json.loads(out)
            tx = doc["transactions"][0]
            self.assertEqual(tx["source"], "55***_activity.csv")
            keys.append(tx["source_key"])
            self.assertNotIn(name[:8], json.dumps(doc["transactions"]))
            self.assertNotIn(name[:8], json.dumps(
                doc["metadata"].get("source_accounts") or {}))
            for k in (doc["metadata"].get("source_accounts") or {}):
                self.assertIn(tx["source_key"], k)
        self.assertNotEqual(keys[0], keys[1])


# ------------------------------------------------- A2-1380 / A2-1392
class TestSanityMasksPaths(unittest.TestCase):
    """A2-1380: sanity --json and the duplicate-file note/error name a
    holdings file masked, like the text listing. A2-1392: a symlink
    loop given as a file is one clean line, not a traceback."""

    _HOLD = ('[[holding]]\nsymbol = "XEI.TO"\nquantity = 100\n')

    def _proj(self, tmp):
        from test_fix_l_runcore_b import _held_project
        root = _held_project(tmp, "")
        f = root / "hold" / "U5550001_holdings.toml"  # pii-ok
        f.write_text(self._HOLD)
        return root, str(f)

    def test_json_paths_are_masked(self):
        import json
        from test_fix_l_runcore_b import _run_cli
        with tempfile.TemporaryDirectory() as tmp:
            root, f = self._proj(tmp)
            r = _run_cli(root, "sanity", "margin", f, "--json")
            self.assertNotIn("5550001", r.stdout + r.stderr)
            j = json.loads(r.stdout)
            self.assertTrue(j["files"][0]["file"].endswith(
                "U5***_holdings.toml"), j["files"][0]["file"])

    def test_file_given_twice_note_is_masked(self):
        from test_fix_l_runcore_b import _run_cli
        with tempfile.TemporaryDirectory() as tmp:
            root, f = self._proj(tmp)
            r = _run_cli(root, "sanity", "margin", f, f)
            self.assertIn("U5***_holdings.toml", r.stderr)
            self.assertNotIn("5550001", r.stdout + r.stderr)
            r = _run_cli(root, "sanity", f"margin={f}", "tfsa", f)
            self.assertIn("U5***_holdings.toml", r.stderr + r.stdout)
            self.assertNotIn("5550001", r.stdout + r.stderr)

    def test_symlink_loop_is_one_line(self):
        import os
        from test_fix_l_runcore_b import _run_cli
        with tempfile.TemporaryDirectory() as tmp:
            root, f = self._proj(tmp)
            l1, l2 = root / "hold" / "l1.toml", root / "hold" / "l2.toml"
            os.symlink(l2, l1)
            os.symlink(l1, l2)
            for args in (("margin", str(l1)), (f"margin={l1}",)):
                with self.subTest(args=args):
                    r = _run_cli(root, "sanity", *args)
                    self.assertNotEqual(r.returncode, 0)
                    self.assertNotIn("Traceback", r.stderr)
                    self.assertIn("l1.toml", r.stderr)


if __name__ == "__main__":
    unittest.main()
