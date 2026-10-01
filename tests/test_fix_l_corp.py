"""Regression tests for the 2026-09 audit's LOW corporate-action and
distributions.map findings (area `corp`, low round). All data
synthetic: fake tickers, fake ISINs, fake broker account ids."""
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _quiet(fn, *a, **kw):
    buf = io.StringIO()
    with redirect_stderr(buf):
        out = fn(*a, **kw)
    return out, buf.getvalue()


def _map_file(tmp, text):
    p = Path(tmp) / "distributions.map"
    p.write_text(text)
    return p


# ======================================================= distributions.map
class TestDistributionsMapLow(unittest.TestCase):
    def _apply(self, txs, rows, **kw):
        from taxjson.bin.taxjson_apply_distributions import (
            apply_distributions)
        (doc, n), err = _quiet(apply_distributions,
                               {"transactions": list(txs)}, rows, "m", **kw)
        adj = [t for t in doc["transactions"] if t["action"] == "ADJUST"]
        return doc, adj, n, err

    def test_s025_11_adjust_follows_a_record_date_sale(self):
        # Hold 100; sell 50 at 10:00 ON the record date (settled that
        # day); +1.00/sh reinvested on the 50 left. The ADJUST is stamped
        # at the END of the record date, so the whole +50 lands on the
        # 50 still held: gains 250 then 200 (at 00:00:00 the bump was
        # split 225 / 225 with shares already sold).
        from dataclasses import fields
        from taxjson.lib.core import CanadaTaxRules, TaxTransaction as T
        txs = [
            {"action": "BUYSELL", "date": "2025-01-10", "time": "10:00:00",
             "date_settle": "2025-01-10", "symbol": "XAW.TO",
             "quantity": 100.0, "currency": "CAD", "net_amount": -1000.0,
             "account": "m"},
            {"action": "BUYSELL", "date": "2025-06-30", "time": "10:00:00",
             "date_settle": "2025-06-30", "symbol": "XAW.TO",
             "quantity": -50.0, "currency": "CAD", "net_amount": 750.0,
             "account": "m"},
            {"action": "BUYSELL", "date": "2025-09-02", "time": "10:00:00",
             "date_settle": "2025-09-02", "symbol": "XAW.TO",
             "quantity": -50.0, "currency": "CAD", "net_amount": 750.0,
             "account": "m"},
        ]
        doc, adj, n, _ = self._apply(txs, [("XAW.TO", "2025-06-30", 1.0)])
        self.assertEqual((n, adj[0]["time"]), (1, "23:59:58"))
        self.assertAlmostEqual(adj[0]["net_amount"], 50.0)
        names = {f.name for f in fields(T)}
        book = [T(**{k: v for k, v in t.items() if k in names})
                for t in doc["transactions"]]
        res, _ = _quiet(CanadaTaxRules().compute_gains, book,
                        sheltered_transactions=[], detect_wash_sales=False)
        gains = [round(x["gain"], 2) for x in res["transactions"]
                 if x.get("action") is None and "gain" in x]
        self.assertEqual(gains, [250.0, 200.0])

    def test_s025_12_unknown_account_refused(self):
        from taxjson.bin.taxjson_apply_distributions import main
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "margin_base.json"
            base.write_text(json.dumps({"transactions": [
                {"action": "BUYSELL", "date": "2025-01-05",
                 "symbol": "XAW.TO", "quantity": 100.0,
                 "account": "margin"}]}))
            m = _map_file(tmp, "XAW.TO 2025-06-30 0.50\n")
            for bad in ("Margin", "bogus"):
                rc, err = _quiet(main, [str(base), "--map", str(m),
                                        "--account", bad])
                self.assertEqual(rc, 2, err)
                self.assertIn("is not an account of", err)
            # Untouched by the refusals; the book's own label works.
            self.assertNotIn("ADJUST", base.read_text())
            rc, err = _quiet(main, [str(base), "--map", str(m),
                                    "--account", "margin"])
            self.assertEqual(rc, 0, err)
            adj = [t for t in json.loads(base.read_text())["transactions"]
                   if t["action"] == "ADJUST"]
        self.assertEqual([a["account"] for a in adj], ["margin"])

    def test_s025_14_amount_must_be_a_plain_decimal(self):
        from taxjson.bin.taxjson_apply_distributions import load_map
        with tempfile.TemporaryDirectory() as tmp:
            for bad in ("nan", "inf", "-inf", "1e309", "1_0", "0x10",
                        "1e2", "+", "."):
                p = _map_file(tmp, f"XYZ.TO 2025-03-31 {bad}\n")
                with self.assertRaises(SystemExit) as cm:
                    load_map(p)
                self.assertIn("bad per-share amount", str(cm.exception))
            for ok, val in (("0.10", 0.1), ("-0.12", -0.12), (".5", 0.5),
                            ("+2", 2.0), ("3.", 3.0)):
                p = _map_file(tmp, f"XYZ.TO 2025-03-31 {ok}\n")
                self.assertEqual(load_map(p), [("XYZ.TO", "2025-03-31",
                                                val)])

    def test_s025_19_date_shape(self):
        from taxjson.bin.taxjson_apply_distributions import load_map
        with tempfile.TemporaryDirectory() as tmp:
            for bad in ("2025/06/19", "2025-06/19", "2025/06-19",
                        "20250619", "2025-6-19", "2025-06-1x",
                        "2025-02-30", "2025-13-01"):
                p = _map_file(tmp, f"XYZ.TO {bad} 0.10\n")
                with self.assertRaises(SystemExit) as cm:
                    load_map(p)
                self.assertIn("bad date", str(cm.exception), bad)

    def test_s025_16_repeated_key_warned_and_ids_unique(self):
        from taxjson.bin.taxjson_apply_distributions import load_map
        with tempfile.TemporaryDirectory() as tmp:
            p = _map_file(tmp, "ABC.TO 2026-06-30 1.00\n"
                               "abc.to 2026-06-30 1.00\n")
            rows, err = _quiet(load_map, p)
        self.assertEqual(len(rows), 2)
        self.assertIn("repeats line 1", err)
        _, adj, n, _ = self._apply(
            [{"action": "BUYSELL", "date": "2026-01-05",
              "symbol": "ABC.TO", "quantity": 100.0}], rows)
        self.assertEqual(n, 2)
        ids = [a["id"] for a in adj]
        self.assertEqual(len(set(ids)), 2, ids)
        self.assertEqual(ids[0], "DIST-ABC.TO-2026-06-30-m")
        # Regenerating keeps the same ids (still deterministic).
        doc2, adj2, _, _ = self._apply(
            [{"action": "BUYSELL", "date": "2026-01-05",
              "symbol": "ABC.TO", "quantity": 100.0}] + adj, rows)
        self.assertEqual([a["id"] for a in adj2], ids)

    def test_s025_23_zero_is_a_placeholder(self):
        _, adj, n, err = self._apply(
            [{"action": "BUYSELL", "date": "2025-01-05",
              "symbol": "XAW.TO", "quantity": 200.0}],
            [("XAW.TO", "2025-12-29", 0.0)])
        self.assertEqual((n, adj), (0, []))
        self.assertIn("placeholder, NOT applied", err)
        self.assertNotIn("return of capital", err)


# ========================================================== manifests
def _run_cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args],
        cwd=REPO_ROOT, capture_output=True, text=True,
        stdin=subprocess.DEVNULL)


def _project(tmp):
    root = Path(tmp)
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2025\ncountry = "canada"\n'
        'base_currency = "CAD"\nsource_currencies = []\n'
        '[accounts.margin]\ntype = "taxable"\n')
    return root


class TestManifestErrors(unittest.TestCase):
    def test_s072_05_elect_on_a_broken_manifest_is_one_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            man = root / "inputs" / "margin" / "manifest.json"
            for content in (b"{not json", b'{"elections": {"x": 1}}\xe9',
                            b"[]", b'{"elections": [1]}',
                            b'{"elections": {"E1": "rollover_s_85_1_5"}}',
                            b'{"elections": {"E1": {"election": 5}}}',
                            b'{"elections": {"E1": {"election": "ignore",'
                            b' "hints": [1]}}}'):
                man.write_bytes(content)
                for args in (("elect",), ("elect", "margin")):
                    r = _run_cli(root, *args)
                    self.assertNotIn("Traceback", r.stderr, content)
                    self.assertNotEqual(r.returncode, 0, content)
                    self.assertIn("taxjson elect: error: manifest at",
                                  r.stderr, content)

    def test_s072_16_bad_record_refused_by_load(self):
        from taxjson.lib.corp_actions import Manifest, ManifestError
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "m.json"
            p.write_text('{"elections": {"E1": "taxable_disposition"}}')
            with self.assertRaises(ManifestError) as cm:
                Manifest.load(p)
            self.assertIn("election E1 must be a JSON object",
                          str(cm.exception))

    def test_s072_16_typo_election_flagged_by_elect(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project(tmp)
            (root / "inputs" / "margin" / "manifest.json").write_text(
                json.dumps({"elections": {
                    "20250101-abc-xyz-0000": {
                        "election": "taxable_dispostion"},
                    "20250102-abc-xyz-0001": {
                        "election": "taxable_disposition"}}}))
            r = _run_cli(root, "elect", "margin")
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        bad = [ln for ln in lines if "taxable_dispostion" in ln]
        good = [ln for ln in lines if "taxable_disposition" in ln]
        self.assertIn("UNKNOWN election", bad[0])
        self.assertNotIn("UNKNOWN", good[0])


# ================================================================== RBC
_RBC_HEAD = ('"Date","Activity","Symbol","Symbol Description","Quantity",'
             '"Price","Settlement Date","Account","Value","Currency",'
             '"Description"\n')


def _rbc(date, act, sym, symdesc, qty, price, val, cur, desc):
    return (f'"{date}","{act}","{sym}","{symdesc}","{qty}","{price}",'
            f'"{date}","55500001","{val}","{cur}","{desc}"\n')  # pii-ok


class TestRbcLow(unittest.TestCase):
    def _parse(self, *rows):
        from taxjson.lib.brokerages.rbc_direct import RbcBrokerage
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "rbc.csv"
            p.write_text(_RBC_HEAD + "".join(rows))
            return _quiet(RbcBrokerage().parse_file, p)

    def _arc_with_other_cil(self, other, osym):
        return self._parse(
            _rbc("November 20, 2025", "Reorganization", osym, other, "0", "",
                 "7", "CAD", f"CIL - {other} CASH IN LIEU OF FRACTIONAL "
                             f"SHARES"),
            _rbc("November 10, 2025", "Reorganization", "ARC",
                 "ALPHA RESOURCES CORP", "0", "", "4", "CAD",
                 "CIL - ALPHA RESOURCES CORP CASH IN LIEU OF FRACTIONAL "
                 "SHARES"),
            _rbc("November 8, 2025", "Reorganization", "ARC",
                 "ALPHA RESOURCES CORP NEW", "10", "", "0", "CAD",
                 "REV - ALPHA RESOURCES CORP NEW RESULT OF REVERSE SPLIT"),
            _rbc("November 8, 2025", "Reorganization", "A012345",
                 "ALPHA RESOURCES CORP", "-105", "", "0", "CAD",
                 "REV - ALPHA RESOURCES CORP REVERSE SPLIT 1 FOR 10"),
            _rbc("January 10, 2025", "Buy", osym, other, "200", "5",
                 "-1000", "CAD", other),
            _rbc("January 10, 2025", "Buy", "ARC", "ALPHA RESOURCES CORP",
                 "105", "2", "-210", "CAD", "ALPHA RESOURCES CORP"))

    def test_s072_01_other_companys_cil_is_not_folded(self):
        for other, osym in (("ALPHA GOLD CORP", "AGC"),
                            ("OMEGA GOLD CORP", "OGC")):
            txs, err = self._arc_with_other_cil(other, osym)
            frac = [t for t in txs if t["action"] == "BUYSELL"
                    and t["date"] == "2025-11-10"]
            self.assertEqual(len(frac), 1, other)
            self.assertAlmostEqual(frac[0]["net_amount"], 4.0)
            self.assertIn("cash-in-lieu row with no reorganization", err)
            self.assertIn(osym, err[err.index("cash-in-lieu row with no"):])

    def test_s072_03_spinoff_temp_code_warning(self):
        from taxjson.lib.corp_actions import parse_rbc_corporate_actions
        rows = (_rbc("2023-09-05 00:00:00", "Reorganization", "C135859",
                     "SPINCO WTS", "10", "", "0", "CAD",
                     "DIS - SPINCO WTS SPINOFF ON 100 SHS FROM SEC# X1 "
                     "PARENTCO INC REC 08/25/23 PAY 08/31/23"),
                _rbc("2023-01-05 00:00:00", "Buy", "PAR", "PARENTCO INC",
                     "100", "10", "-1000", "CAD", "PARENTCO INC"))
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "rbc.csv"
            p.write_text(_RBC_HEAD + "".join(rows))
            evs, err = _quiet(parse_rbc_corporate_actions, p, "margin")
            self.assertEqual(evs[0].target_symbol, "C135859.TO")
            # Named as booked, with the exact line to add.
            self.assertIn("temporary code C135859.TO", err)
            self.assertIn("GLOBAL C135859.TO <TICKER>.TO", err)
            # Once ticker.map renames it, no warning.
            _, err2 = _quiet(parse_rbc_corporate_actions, p, "margin",
                             renames={"C135859.TO": "SPNC.TO"})
            self.assertNotIn("temporary code", err2)
            # Through the CLI flag `taxjson run` passes.
            tm = Path(tmp) / "ticker.map"
            tm.write_text("GLOBAL C135859.TO SPNC.TO\n")
            r = subprocess.run(
                [sys.executable, "-m", "taxjson.bin.taxjson_corp_actions",
                 "--country", "canada", "--brokerage", "rbc", "--list",
                 "--manifest", str(Path(tmp) / "m.json"),
                 "--ticker-map", str(tm), str(p)],
                cwd=REPO_ROOT, capture_output=True, text=True,
                stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("spinoff", r.stdout)
            self.assertNotIn("temporary code", r.stderr)


if __name__ == "__main__":
    unittest.main()
