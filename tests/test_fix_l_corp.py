"""Regression tests for the 2026-09 audit's LOW corporate-action and
distributions.map findings (area `corp`, low round; the map is
taxjson.toml's [[distributions]] now). All data
synthetic: fake tickers, fake ISINs, fake broker account ids."""
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from tax_rules import rule
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()

REPO_ROOT = Path(__file__).resolve().parent.parent


def _quiet(fn, *a, **kw):
    buf = io.StringIO()
    with redirect_stderr(buf):
        out = fn(*a, **kw)
    return out, buf.getvalue()


def _map_file(tmp, *entries):
    """A taxjson.toml holding one [[distributions]] table per (symbol,
    record_date, per_share) — each value is TOML text as written."""
    p = Path(tmp) / "taxjson.toml"
    p.write_text("".join(
        f"[[distributions]]\nsymbol = {sym}\nrecord_date = {d}\n"
        f"per_share = {amt}\n" for sym, d, amt in entries))
    return p


# ======================================================= [[distributions]]
@rule("CA-DIST-01")
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
            m = _map_file(tmp, ('"XAW.TO"', "2025-06-30", "0.50"))
            for bad in ("Margin", "bogus"):
                rc, err = _quiet(main, [str(base), "--config", str(m),
                                        "--account", bad])
                self.assertEqual(rc, 2, err)
                self.assertIn("is not an account of", err)
            # Untouched by the refusals; the book's own label works.
            self.assertNotIn("ADJUST", base.read_text())
            rc, err = _quiet(main, [str(base), "--config", str(m),
                                    "--account", "margin"])
            self.assertEqual(rc, 0, err)
            adj = [t for t in json.loads(base.read_text())["transactions"]
                   if t["action"] == "ADJUST"]
        self.assertEqual([a["account"] for a in adj], ["margin"])

    def test_s025_14_amount_must_be_a_plain_decimal(self):
        # TOML numbers now: a non-finite number, a quoted amount or a
        # boolean is refused naming per_share (S025-14's nan/inf/1e309).
        from taxjson.bin.taxjson_apply_distributions import load_rows
        with tempfile.TemporaryDirectory() as tmp:
            for bad in ("nan", "inf", "-inf", "1e309", '"0.10"', '"1_0"',
                        "true"):
                p = _map_file(tmp, ('"XYZ.TO"', "2025-03-31", bad))
                with self.assertRaises(SystemExit) as cm:
                    _quiet(load_rows, p)
                self.assertIn("per_share must be a number",
                              str(cm.exception), bad)
            for ok, val in (("0.10", 0.1), ("-0.12", -0.12), ("0.5", 0.5),
                            ("+2", 2.0), ("3.0", 3.0)):
                p = _map_file(tmp, ('"XYZ.TO"', "2025-03-31", ok))
                self.assertEqual(load_rows(p), [("XYZ.TO", "2025-03-31",
                                                 val)])

    def test_s025_19_date_shape(self):
        from taxjson.bin.taxjson_apply_distributions import load_rows
        with tempfile.TemporaryDirectory() as tmp:
            for bad in ("2025/06/19", "2025-06/19", "2025/06-19",
                        "20250619", "2025-6-19", "2025-06-1x",
                        "2025-02-30", "2025-13-01"):
                p = _map_file(tmp, ('"XYZ.TO"', f'"{bad}"', "0.10"))
                with self.assertRaises(SystemExit) as cm:
                    _quiet(load_rows, p)
                self.assertIn("record_date must be a date",
                              str(cm.exception), bad)
            # A TOML date and its "YYYY-MM-DD" string read the same.
            for d in ("2025-06-19", '"2025-06-19"'):
                p = _map_file(tmp, ('"XYZ.TO"', d, "0.10"))
                self.assertEqual(load_rows(p)[0][1], "2025-06-19")

    def test_s025_16_repeated_key_warned_and_ids_unique(self):
        from taxjson.bin.taxjson_apply_distributions import load_rows
        with tempfile.TemporaryDirectory() as tmp:
            p = _map_file(tmp, ('"ABC.TO"', "2026-06-30", "1.00"),
                          ('"abc.to"', "2026-06-30", "1.00"))
            rows, err = _quiet(load_rows, p)
        self.assertEqual(len(rows), 2)
        self.assertIn("repeats entry #1", err)
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
        rows = (_rbc("2023-09-05 00:00:00", "Reorganization", "X000006",
                     "SPINCO WTS", "10", "", "0", "CAD",
                     "DIS - SPINCO WTS SPINOFF ON 100 SHS FROM SEC# X1 "
                     "PARENTCO INC REC 08/25/23 PAY 08/31/23"),
                _rbc("2023-01-05 00:00:00", "Buy", "PAR", "PARENTCO INC",
                     "100", "10", "-1000", "CAD", "PARENTCO INC"))
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "rbc.csv"
            p.write_text(_RBC_HEAD + "".join(rows))
            evs, err = _quiet(parse_rbc_corporate_actions, p, "margin")
            self.assertEqual(evs[0].target_symbol, "X000006.TO")
            # Named as booked, with the exact line to add.
            self.assertIn("temporary code X000006.TO", err)
            self.assertIn("GLOBAL X000006.TO <TICKER>.TO", err)
            # Once ticker.map renames it, no warning.
            _, err2 = _quiet(parse_rbc_corporate_actions, p, "margin",
                             renames={"X000006.TO": "SPNC.TO"})
            self.assertNotIn("temporary code", err2)
            # Through the CLI flag `taxjson run` passes.
            tm = Path(tmp) / "ticker.map"
            tm.write_text("GLOBAL X000006.TO SPNC.TO\n")
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


# ===================================================== IB / Questrade
_IB_HEAD = ('Statement,Header,Field Name,Field Value\n'
            'Account Information,Header,Field Name,Field Value\n'
            'Account Information,Data,Account,U5550001\n')  # pii-ok
_IB_MRG = ('SSX(CA0000000001) Merged(Acquisition) WITH US0000000002 1 for 16 '
           '({t}, {n}, {i})')
_IB_OUT = _IB_MRG.format(t='SSX', n='SSX GOLD LTD', i='CA0000000001')
_IB_IN = _IB_MRG.format(t='RGX', n='RGX GOLD INC', i='US0000000002')


def _ib_file(tmp, header_cols, rows):
    p = Path(tmp) / "ib.csv"
    head = ("Corporate Actions,Header," + ",".join(header_cols) + "\n"
            if header_cols else "")
    p.write_text(_IB_HEAD + head + "".join(rows))
    return p


class TestIbQuestradeExtractorsLow(unittest.TestCase):
    # Consolidated layout: an Account column at index 4, Report Date at 5.
    COLS = ['Asset Category', 'Currency', 'Account', 'Report Date',
            'Date/Time', 'Description', 'Quantity', 'Proceeds', 'Value',
            'Realized P/L', 'Code']

    def _rows(self):
        return [f'Corporate Actions,Data,Stocks,{cur},U5550001,2026-01-05,'  # pii-ok
                f'"2025-12-31, 20:25:00","{d}",{q},0,{v},0,\n'
                for d, q, v, cur in ((_IB_OUT, -1600, -25920, 'CAD'),
                                     (_IB_IN, 100, 25840, 'USD'))]

    def test_s072_22_columns_by_name(self):
        from taxjson.lib.brokerages.base import BrokerageParseError
        from taxjson.lib.corp_actions import parse_ib_corporate_actions
        with tempfile.TemporaryDirectory() as tmp:
            evs, err = _quiet(parse_ib_corporate_actions,
                              _ib_file(tmp, self.COLS, self._rows()), "m")
            self.assertEqual([(e.date, e.qty_disposed, e.qty_received)
                              for e in evs],
                             [("2025-12-31", 1600.0, 100.0)], err)
            # A missing header cell used to fall back to position 5 —
            # Report Date in this layout (2026-01-05: the wrong year).
            cols = [c for c in self.COLS if c != 'Date/Time'] + ['X']
            with self.assertRaises(BrokerageParseError) as cm:
                _quiet(parse_ib_corporate_actions,
                       _ib_file(tmp, cols, self._rows()), "m")
            self.assertIn("missing column(s) Date/Time", str(cm.exception))
            # Data rows before any Header: refused, not silently dropped.
            with self.assertRaises(BrokerageParseError) as cm:
                _quiet(parse_ib_corporate_actions,
                       _ib_file(tmp, None, self._rows()), "m")
            self.assertIn("before any Corporate Actions Header",
                          str(cm.exception))

    def test_s073_02_on_shs_with_thousands_comma(self):
        from taxjson.lib.corp_actions import parse_questrade_corporate_actions
        head = ('Transaction Date,Settlement Date,Action,Symbol,Description,'
                'Quantity,Price,Gross Amount,Commission,Net Amount,Currency,'
                'Activity Type,Account #,Account Type\n')

        def row(date, action, sym, desc, qty, act, price='0', net='0'):
            return (f'{date} 12:00:00 AM,{date} 12:00:00 AM,{action},{sym},'
                    f'"{desc}",{qty},{price},{net},0,{net},USD,{act},'
                    f'55500001,Individual margin\n')  # pii-ok
        out = {}
        with tempfile.TemporaryDirectory() as tmp:
            for n in ("1,500", "1500"):
                p = Path(tmp) / "qt.csv"
                p.write_text(head + row('2025-02-05', 'Buy', 'QZD',
                                        'QUUZ CORP', '1500', 'Trades',
                                        '10', '-15000')
                             + row('2025-10-27', 'DIS', 'QZDW',
                                   f'QZD WTS SPINOFF ON {n} SHS FROM SEC# '
                                   f'J0001 QUUZ CORP REC 10/20/25 PAY '
                                   f'10/27/25', '150', 'Dividends'))
                (ev,), err = _quiet(parse_questrade_corporate_actions, p,
                                    "m")
                out[n] = (ev.ratio_old, ev.event_id)
        self.assertEqual(out["1,500"][0], 1500.0)
        self.assertEqual(out["1,500"], out["1500"])


# ========================================================== elections
def _spin(**kw):
    from taxjson.lib.corp_actions import CorporateAction
    base = dict(date='2025-06-02', time='09:30:00', action_type='spinoff',
                source_symbol='PAR.US', source_isin='US0000000901',
                target_symbol='SPN.US', target_isin='US0000000902',
                ratio_new=1, ratio_old=5, qty_disposed=0, qty_received=20,
                fmv=0.0, currency='USD', target_currency='USD',
                account='margin')
    base.update(kw)
    return CorporateAction(**base)


def _merger(**kw):
    return _spin(**dict(dict(action_type='merger', target_symbol='NEW.US',
                             qty_disposed=100, qty_received=50), **kw))


class TestElectionsLow(unittest.TestCase):
    def _zero_alloc(self, country, election, hint):
        from taxjson.lib.corp_actions import resolve_event
        rows, err = _quiet(resolve_event, _spin(), election,
                           country=country, hints={hint: 0.0})
        buy = [r for r in rows if r['action'] == 'BUYSELL']
        self.assertEqual(buy[0]['net_amount'], 0.0)
        self.assertIn("allocated cost of 0", err)
        # `taxjson run` repeats it on every run, into the .sum/checklist.
        from taxjson.bin.taxjson_run import _warn_zero_value_spinoffs
        with tempfile.TemporaryDirectory() as tmp:
            corp = Path(tmp) / "margin_ib_corp.json"
            corp.write_text(json.dumps({"transactions": rows}))
            _, out = _quiet(_warn_zero_value_spinoffs, "margin", True,
                            [corp], Path(tmp))
            diag = (Path(tmp) / "margin_corp_spinoff_value.diag")
            text = diag.read_text()
            self.assertIn("$0 allocated cost", text)
            self.assertIn(f"--hint {hint}=<amount>", text)
            # A real allocation: quiet.
            rows2, err2 = _quiet(resolve_event, _spin(), election,
                                 country=country, hints={hint: 500.0})
            self.assertNotIn("allocated cost of 0", err2)
            corp.write_text(json.dumps({"transactions": rows2}))
            _quiet(_warn_zero_value_spinoffs, "margin", True, [corp],
                   Path(tmp))
            self.assertFalse(diag.exists())

    def test_s073_21_s074_04_zero_allocation_s86_1(self):
        self._zero_alloc('canada', 'rollover_s_86_1', 'allocated_acb_cad')

    def test_s073_21_s074_04_zero_allocation_355(self):
        self._zero_alloc('usa', 'tax_free_355', 'allocated_acb')

    @rule("US-CORP-05")
    def test_s073_22_boot_merger_without_fmv_warns(self):
        from taxjson.lib.corp_actions import resolve_event
        hints = {'cash_boot': 500.0, 'source_basis_total': 1000.0}
        rows, err = _quiet(resolve_event, _merger(qty_received=25),
                           'reorg_368_boot', country='usa',
                           hints=dict(hints, fmv_per_share=0.0))
        self.assertIn("no value for the new shares", err)
        _, err2 = _quiet(resolve_event, _merger(qty_received=25),
                         'reorg_368_boot', country='usa',
                         hints=dict(hints, fmv_per_share=80.0))
        self.assertNotIn("no value for the new shares", err2)

    @rule("US-CORP-04", "US-CORP-07")
    def test_s073_00_us_nonrecognition_is_not_optional(self):
        from taxjson.lib.corp_actions import (FILING_REQUIRED_ELECTIONS,
                                              USA_MERGER, USA_SPINOFF)
        for k in ('reorg_368', 'reorg_368_boot', 'tax_free_355'):
            self.assertNotIn(k, FILING_REQUIRED_ELECTIONS)
        m, sp = dict(USA_MERGER.options), dict(USA_SPINOFF.options)
        self.assertNotIn("aren't claiming", m['taxable_exchange'])
        self.assertIn("not by choice", m['taxable_exchange'])
        self.assertIn("significant holder", m['reorg_368'])
        self.assertIn("§1.368-3", m['reorg_368_boot'])
        self.assertIn("§1.355-5", sp['tax_free_355'])
        self.assertIn("does NOT qualify", sp['taxable_distribution_301'])


# ====================================================== leg ordering
class TestLegTimesLow(unittest.TestCase):
    def test_s074_02_same_symbol_exchange_at_end_of_day(self):
        from dataclasses import fields
        from taxjson.lib.core import CanadaTaxRules, TaxTransaction as T
        from taxjson.lib.corp_actions import resolve_event
        names = {f.name for f in fields(T)}
        for tm in ('10:00:00', '23:59:59', ''):
            ev = _merger(time=tm, source_symbol='ABC.US',
                         target_symbol='ABC.US', qty_disposed=100,
                         qty_received=100, fmv=2000.0, target_fmv=2000.0)
            rows, _ = _quiet(resolve_event, ev, 'taxable_disposition',
                             country='canada')
            sell, buy = rows[0], rows[1]
            self.assertLess(sell['quantity'], 0)
            self.assertLess(sell['time'], buy['time'], tm)
            # Even fed to the engine in the WRONG order, the old shares'
            # disposition uses their own cost.
            book = [T(action='BUYSELL', date='2024-01-10', time='10:00:00',
                      symbol='ABC.US', quantity=100, currency='USD',
                      net_amount=-1000.0, date_settle='2024-01-10',
                      account='margin')]
            book += [T(**{k: v for k, v in r.items() if k in names})
                     for r in (buy, sell)]
            res, _ = _quiet(CanadaTaxRules().compute_gains, book,
                            sheltered_transactions=[],
                            detect_wash_sales=False)
            g = [x for x in res['transactions']
                 if x.get('action') is None and 'gain' in x]
            self.assertEqual([(round(x['cost'], 2), round(x['gain'], 2))
                              for x in g], [(1000.0, 1000.0)], tm)

    def test_s074_02_split_up_spinoff_after_merger(self):
        from taxjson.lib.corp_actions import parse_ib_corporate_actions
        mrg = ('ABC(US0000000001) Merged(Acquisition) WITH US0000000001 '
               '1 for 1, US0000000003 1 for 2 ({t}, {n}, {i})')
        cols = ('Asset Category,Currency,Report Date,Date/Time,Description,'
                'Quantity,Proceeds,Value,Realized P/L,Code').split(',')
        rows = [f'Corporate Actions,Data,Stocks,USD,2025-06-02,'
                f'"2025-06-02, 23:59:59","{d}",{q},0,{v},0,\n'
                for d, q, v in (
                    (mrg.format(t='ABC', n='ABC INC', i='US0000000001'),
                     -40, -4000),
                    (mrg.format(t='ABC', n='ABC INC', i='US0000000001'),
                     40, 3000),
                    (mrg.format(t='ABCS', n='ABC SPINCO',
                                i='US0000000003'), 20, 1000))]
        with tempfile.TemporaryDirectory() as tmp:
            evs, err = _quiet(parse_ib_corporate_actions,
                              _ib_file(tmp, cols, rows), "m")
        kinds = {e.action_type: e.time for e in evs}
        self.assertEqual(set(kinds), {'merger', 'spinoff'}, err)
        self.assertLess(kinds['merger'], kinds['spinoff'])


# ================================================ pinned constants
class TestEmittedRowConstantsLow(unittest.TestCase):
    """G1-14: the corp rows' fee and money constants were unpinned — a
    +1 fee or a +1 value on an IB merger leg survived the suite."""

    CASES = [
        ('canada', 'taxable_disposition', _merger, {}),
        ('canada', 'rollover_s_85_1_5', _merger, {}),
        ('canada', 'taxable_deemed_dividend', _spin,
         {'fmv_per_share': 7.0}),
        ('canada', 'rollover_s_86_1', _spin, {'allocated_acb_cad': 300.0}),
        ('usa', 'taxable_exchange', _merger, {}),
        ('usa', 'reorg_368_boot', _merger,
         {'cash_boot': 500.0, 'source_basis_total': 1000.0}),
        ('usa', 'taxable_distribution_301', _spin, {'fmv_per_share': 7.0}),
        ('usa', 'tax_free_355', _spin, {'allocated_acb': 300.0}),
    ]

    def test_g1_14_every_emitted_trade_row_has_zero_fee(self):
        from taxjson.lib.corp_actions import resolve_event
        for country, el, make, hints in self.CASES:
            ev = make(fmv=2000.0, target_fmv=2000.0)
            rows, _ = _quiet(resolve_event, ev, el, country=country,
                             hints=hints)
            self.assertTrue(rows, el)
            for r in rows:
                if r['action'] == 'BUYSELL':
                    self.assertEqual(r['fee'], 0.0, (el, r))

    def test_g1_14_taxable_exchange_money_exact(self):
        from taxjson.lib.corp_actions import resolve_event
        ev = _merger(fmv=2000.0, target_fmv=1900.0)
        rows, _ = _quiet(resolve_event, ev, 'taxable_disposition',
                         country='canada')
        sell = next(r for r in rows if r['quantity'] < 0)
        buy = next(r for r in rows if r['quantity'] > 0)
        # One fair value for both legs: the consideration received.
        self.assertEqual((sell['net_amount'], sell['fee']), (1900.0, 0.0))
        self.assertEqual((buy['net_amount'], buy['fee']), (1900.0, 0.0))

    def test_g1_14_ib_merger_and_split_up_values_exact(self):
        from taxjson.lib.corp_actions import parse_ib_corporate_actions
        cols = ('Asset Category,Currency,Report Date,Date/Time,Description,'
                'Quantity,Proceeds,Value,Realized P/L,Code').split(',')

        def r(d, q, v, cur='USD'):
            return (f'Corporate Actions,Data,Stocks,{cur},2025-06-02,'
                    f'"2025-06-02, 20:25:00","{d}",{q},0,{v},0,\n')
        mrg = ('ABC(US0000000001) Merged(Acquisition) WITH US0000000001 '
               '1 for 1, US0000000003 1 for 2 ({t}, {n}, {i})')
        with tempfile.TemporaryDirectory() as tmp:
            (ev,), _ = _quiet(parse_ib_corporate_actions, _ib_file(
                tmp, cols, [r(_IB_OUT, -1600, -25920.5, 'CAD'),
                            r(_IB_IN, 100, 25840.25)]), "m")
            self.assertEqual((ev.fmv, ev.target_fmv), (25920.5, 25840.25))
            evs, _ = _quiet(parse_ib_corporate_actions, _ib_file(
                tmp, cols, [
                    r(mrg.format(t='ABC', n='ABC INC', i='US0000000001'),
                      -40, -4000.5),
                    r(mrg.format(t='ABC', n='ABC INC', i='US0000000001'),
                      40, 3000.25),
                    r(mrg.format(t='ABCS', n='ABC SPINCO',
                                 i='US0000000003'), 20, 1000.75)]), "m")
        got = {e.action_type: (e.fmv, e.target_fmv) for e in evs}
        self.assertEqual(got['merger'], (4000.5, 3000.25))
        self.assertEqual(got['spinoff'][0], 1000.75)


if __name__ == "__main__":
    unittest.main()
