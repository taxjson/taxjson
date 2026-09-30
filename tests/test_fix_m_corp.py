"""Regression tests for the 2026-09 audit's MEDIUM corporate-action and
distributions.map findings (area `corp`, medium round). All data
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


def _run_cli(root, *args):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args],
        cwd=REPO_ROOT, capture_output=True, text=True,
        stdin=subprocess.DEVNULL)


def _quiet(fn, *a, **kw):
    buf = io.StringIO()
    with redirect_stderr(buf):
        out = fn(*a, **kw)
    return out, buf.getvalue()


# ======================================================= distributions.map
class TestDistributionsMap(unittest.TestCase):
    def _apply(self, txs, rows, **kw):
        from taxjson.bin.taxjson_apply_distributions import (
            apply_distributions)
        (doc, n), err = _quiet(apply_distributions,
                               {"transactions": list(txs)}, rows, "m", **kw)
        adj = [t for t in doc["transactions"] if t["action"] == "ADJUST"]
        return adj, n, err

    def test_s000_06_bom_is_stripped(self):
        from taxjson.bin.taxjson_apply_distributions import load_map
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "distributions.map"
            p.write_bytes("XYZ.TO 2025-06-30 0.50\n".encode("utf-8-sig"))
            self.assertEqual(load_map(p), [("XYZ.TO", "2025-06-30", 0.5)])

    def test_s025_13_symbol_case_insensitive(self):
        from taxjson.bin.taxjson_apply_distributions import load_map
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "distributions.map"
            p.write_text("xaw.to 2025-06-30 0.50\n")
            rows = load_map(p)
        self.assertEqual(rows, [("XAW.TO", "2025-06-30", 0.5)])
        adj, n, _ = self._apply(
            [{"action": "BUYSELL", "date": "2025-01-15", "symbol": "XAW.TO",
              "quantity": 100.0}], rows)
        self.assertEqual(n, 1)
        self.assertAlmostEqual(adj[0]["net_amount"], 50.0)

    def test_s000_07_split_inside_settle_lag(self):
        # A pre-split sale traded 06-10 10:00, settling 06-11; the 2:1
        # split posts 06-10 20:25 (IB evening batch). The engine
        # re-denominates the sale to -1000 post-split shares: 1000 held.
        from taxjson.bin.taxjson_apply_distributions import balance_on
        txs = [
            {"action": "BUYSELL", "date": "2026-01-05", "time": "10:00:00",
             "date_settle": "2026-01-06", "symbol": "ABC.TO",
             "quantity": 1000.0},
            {"action": "BUYSELL", "date": "2026-06-10", "time": "10:00:00",
             "date_settle": "2026-06-11", "symbol": "ABC.TO",
             "quantity": -500.0},
            {"action": "SPLIT", "date": "2026-06-10", "time": "20:25:00",
             "symbol": "ABC.TO", "symbol_new": "ABC.TO", "quantity": 2.0},
        ]
        self.assertAlmostEqual(
            balance_on(txs, "ABC.TO", "2026-12-29", "settle"), 1000.0)
        # Between execution and settlement the holder of record still
        # has the pre-split 1000 shares, split to 2000.
        self.assertAlmostEqual(
            balance_on(txs, "ABC.TO", "2026-06-10", "settle"), 2000.0)
        adj, n, _ = self._apply(txs, [("ABC.TO", "2026-12-29", 0.43)])
        self.assertAlmostEqual(adj[0]["net_amount"], 430.0, places=4)

    def test_s025_10_old_ticker_key_lands_on_live_pool(self):
        txs = [
            {"action": "BUYSELL", "date": "2026-01-05", "symbol": "OLD.TO",
             "quantity": 100.0},
            {"action": "SPLIT", "date": "2026-05-01", "symbol": "OLD.TO",
             "symbol_new": "NEW.TO", "quantity": 1.0},
        ]
        adj, n, err = self._apply(txs, [("OLD.TO", "2026-06-30", 1.0)])
        self.assertEqual(n, 1)
        self.assertEqual(adj[0]["symbol"], "NEW.TO", err)
        self.assertAlmostEqual(adj[0]["net_amount"], 100.0)
        # The current ticker keyed BEFORE the rename lands on OLD.TO,
        # the pool live on that date (the rename then carries it).
        adj, n, _ = self._apply(txs, [("NEW.TO", "2026-03-31", 1.0)])
        self.assertEqual((n, adj[0]["symbol"]), (1, "OLD.TO"))

    def test_s025_22_key_goes_through_ticker_map(self):
        from taxjson.bin.taxjson_apply_distributions import main
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            base = t / "margin_base.json"
            base.write_text(json.dumps({"transactions": [
                {"action": "BUYSELL", "date": "2026-01-05",
                 "symbol": "ABC.TO", "quantity": 100.0,
                 "account": "margin"}]}))
            (t / "distributions.map").write_text("ABC.US 2026-06-30 1.00\n")
            (t / "ticker.map").write_text("TOBASE ABC.US ABC.TO\n")
            rc, err = _quiet(main, [str(base), "--map",
                                    str(t / "distributions.map"),
                                    "--ticker-map", str(t / "ticker.map")])
            self.assertEqual(rc, 0)
            doc = json.loads(base.read_text())
        adj = [x for x in doc["transactions"] if x["action"] == "ADJUST"]
        self.assertEqual(len(adj), 1, err)
        self.assertEqual(adj[0]["symbol"], "ABC.TO")
        self.assertAlmostEqual(adj[0]["net_amount"], 100.0)

    def test_s026_00_income_not_counted_is_said(self):
        adj, n, err = self._apply(
            [{"action": "BUYSELL", "date": "2024-01-15", "symbol": "XIC.TO",
              "quantity": 1000.0}], [("XIC.TO", "2024-12-30", 0.5)])
        self.assertEqual(n, 1)
        self.assertIn("not counted as income", err)

    def test_run_passes_ticker_map(self):
        # End to end: TOBASE consolidates ABC.US into ABC.TO; the map
        # row keyed by the broker's listing still raises the ACB.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2026\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                '[accounts.margin]\ntype = "taxable"\n')
            (root / "inputs" / "margin" / "book.tt").write_text(
                "BUYSELL 2026-01-05 10:00:00 ABC.US 100 CAD 10 -1000 0\n"
                "BUYSELL 2026-09-01 10:00:00 ABC.TO -100 CAD 12 1200 0\n")
            (root / "ticker.map").write_text("GLOBAL ABC.US ABC.TO\n")
            (root / "distributions.map").write_text(
                "ABC.US 2026-06-30 1.00\n")
            r = _run_cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertIn("+100.00 ACB adjustment", r.stderr)
            g = json.loads((root / "work" /
                            "margin_gains_wash.json").read_text())
        self.assertAlmostEqual(float(g["summary"]["total_gain"]), 100.0,
                               places=2)


# ================================================================== RBC
_RBC_HEADER = ('"Date","Activity","Symbol","Symbol Description","Quantity",'
               '"Price","Settlement Date","Account","Value","Currency",'
               '"Description"\n')


def _rbc_row(date, activity, sym, symdesc, qty, value, cur, desc,
             price='', acct='55500001'):
    return (f'"{date} 00:00:00","{activity}","{sym}","{symdesc}","{qty}",'
            f'"{price}","{date} 00:00:00","{acct}","{value}","{cur}",'
            f'"{desc}"\n')


def _rbc_file(tmp, name, *rows):
    p = Path(tmp) / name
    p.write_text(_RBC_HEADER + ''.join(rows))
    return p


def _rbc_pairing(tmp, *rows):
    from taxjson.lib.brokerages.rbc_direct import read_rbc_rows
    from taxjson.lib.corp_actions import pair_rbc_reorganizations
    p = _rbc_file(tmp, "rbc.csv", *rows)
    return pair_rbc_reorganizations(read_rbc_rows(p).rows)


def _rbc_events(tmp, *rows, context=()):
    from taxjson.lib.corp_actions import parse_rbc_corporate_actions
    p = _rbc_file(tmp, "rbc.csv", *rows)
    ctx = [_rbc_file(tmp, f"ctx{i}.csv", *c) for i, c in enumerate(context)]
    evs, err = _quiet(parse_rbc_corporate_actions, p, "margin",
                      context_files=[p] + ctx)
    return evs, err


class TestRbcThousandsRatios(unittest.TestCase):
    def test_s073_19_one_for_one_thousand(self):
        from taxjson.lib.corp_actions import _rbc_stated_ratio
        self.assertAlmostEqual(_rbc_stated_ratio(
            "REV - FOO CORP REV SPLIT TO FOO CORP NEW; 1 FOR 1,000"), 0.001)
        self.assertAlmostEqual(_rbc_stated_ratio(
            "MGR - FOO CORP MERGER TO BARCO INC 1 NEW = 1,000 OLD"), 0.001)
        self.assertAlmostEqual(_rbc_stated_ratio(
            "REV - FOO CORP REV SPLIT TO FOO CORP NEW; 1 FOR 10"), 0.1)

    def test_s073_14_merger_ratio_with_comma(self):
        with tempfile.TemporaryDirectory() as tmp:
            evs, err = _rbc_events(
                tmp,
                _rbc_row("2025-06-02", "Buy", "FOOC", "FOO CORP", "1500",
                         "-3009.95", "USD", "FOO CORP BUY", price="2"),
                _rbc_row("2025-09-10", "Reorganization", "F012345",
                         "FOO CORP", "-1500", "0", "USD",
                         "MGR - FOO CORP MERGER TO BARCO INC "
                         "1 NEW = 1,000 OLD"),
                _rbc_row("2025-09-10", "Reorganization", "BARC",
                         "BARCO INC", "1", "0", "USD",
                         "MGR - BARCO INC SHRS RECEIVED THRU MERGER"))
        self.assertEqual(len(evs), 1, err)
        self.assertEqual((evs[0].ratio_new, evs[0].ratio_old), (1.0, 1000.0))
        self.assertIn("BARCO INC", evs[0].raw_descriptions[0])

    def test_phantom_holdings_ratio_regex(self):
        from taxjson.lib.phantom_holdings import _RATIO_RE
        m = _RATIO_RE.search("MERGER TO BARCO INC 1 NEW = 1,000 OLD")
        self.assertEqual(m.group(2).replace(",", ""), "1000")


class TestRbcPairing(unittest.TestCase):
    def test_s071_19_xch_pairs_by_strike(self):
        rows = [
            _rbc_row("2024-11-15", "Reorganization", "8AAAAA1", "", "-1",
                     "0", "CAD", "XCH - CALL .TUX   03/21/25    64 TUX "
                     "CORP ADJ: SPCL CASH DIVD"),
            _rbc_row("2024-11-15", "Reorganization", "8AAAAA2", "", "-1",
                     "0", "CAD", "XCH - CALL .TUX   03/21/25    70 TUX "
                     "CORP ADJ: SPCL CASH DIVD"),
            # the 69.50 receipt listed first
            _rbc_row("2024-11-15", "Reorganization", "8BBBBB2", "", "1",
                     "0", "CAD", "XCH - CALL .TUX   03/21/25    69.50 TUX "
                     "CORP ADJ: SPCL CASH DIVD"),
            _rbc_row("2024-11-15", "Reorganization", "8BBBBB1", "", "1",
                     "0", "CAD", "XCH - CALL .TUX   03/21/25    63.50 TUX "
                     "CORP ADJ: SPCL CASH DIVD"),
        ]
        import itertools
        for perm in itertools.permutations(rows):
            with tempfile.TemporaryDirectory() as tmp:
                p = _rbc_pairing(tmp, *perm)
            pairs = sorted((e.removal.symbol, e.receipt.symbol)
                           for e in p.events if e.kind == 'option_adjust')
            self.assertEqual(pairs, [("8AAAAA1", "8BBBBB1"),
                                     ("8AAAAA2", "8BBBBB2")])

    def test_s071_20_no_lone_candidate_guess(self):
        rows = [
            _rbc_row("2025-12-29", "Reorganization", "A012345",
                     "ALPHA WIDGETS INC", "-100", "0", "USD", "MGR - "),
            _rbc_row("2025-12-30", "Reorganization", "B054321",
                     "BETA MINING CORP", "-50", "0", "USD",
                     "MGR - BETA MINING CORP TO BETA MINING CORP NEW"),
            _rbc_row("2025-12-30", "Reorganization", "BMC",
                     "BETA MINING CORP NEW", "50", "0", "USD",
                     "MGR - BETA MINING CORP NEW SHRS RECEIVED THRU MERGER"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            p = _rbc_pairing(tmp, *rows)
        pairs = [(e.removal.symbol, e.receipt.symbol) for e in p.events]
        self.assertEqual(pairs, [("B054321", "BMC")])
        self.assertEqual([u.symbol for u in p.unmatched], ["A012345"])

    def test_s071_20_half_token_is_not_a_name_match(self):
        rows = [
            _rbc_row("2025-12-29", "Reorganization", "A012345",
                     "ALPHA RESOURCES INC", "-100", "0", "USD", "MGR - "),
            _rbc_row("2025-12-30", "Reorganization", "AGN",
                     "ALPHA GOLD CORP", "50", "0", "USD",
                     "MGR - ALPHA GOLD CORP SHRS RECEIVED THRU MERGER"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            p = _rbc_pairing(tmp, *rows)
        self.assertEqual(p.events, [])
        self.assertEqual(len(p.unmatched), 2)

    def test_s071_24_roc_word_in_company_name_is_not_roc(self):
        rows = [
            _rbc_row("2025-05-01", "Reorganization", "R012345",
                     "ROC OIL CORP", "-100", "500.00", "CAD",
                     "MER - ROC OIL CORP DEFAULT: C$5.00 + .5 NEW SHS PER "
                     "1 OLD"),
            _rbc_row("2025-05-01", "Reorganization", "ROX", "ROC OIL CORP",
                     "50", "0", "CAD",
                     "MGR - ROC OIL CORP NEW SHRS RECEIVED THRU MER"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            p = _rbc_pairing(tmp, *rows)
        self.assertEqual(len(p.events), 1)
        self.assertEqual(p.events[0].roc_amount, 0.0)
        from taxjson.lib.corp_actions import _RBC_ROC_RE
        self.assertTrue(_RBC_ROC_RE.search(
            "MER - THOMSON REUTERS CORP COM NEW DEFAULT: ROC OF C$6.1585 "
            "+ .963957 NEW SHS PER 1 OLD"))

    def test_s072_00_cross_issuer_xch_to_needs_election(self):
        with tempfile.TemporaryDirectory() as tmp:
            evs, err = _rbc_events(
                tmp,
                _rbc_row("2025-01-10", "Buy", "MPLE", "MAPLE ENERGY CORP",
                         "500", "-10009.95", "CAD", "MAPLE ENERGY CORP",
                         price="20"),
                _rbc_row("2025-06-02", "Reorganization", "M012345",
                         "MAPLE ENERGY CORP", "-500", "0", "CAD",
                         "MGR - MAPLE ENERGY CORP XCH TO OVERSEAS ENERGY "
                         "INC; 1 FOR 5"),
                _rbc_row("2025-06-02", "Reorganization", "OVRX",
                         "OVERSEAS ENERGY INC", "100", "0", "CAD",
                         "MGR - OVERSEAS ENERGY INC SHRS RECEIVED THRU "
                         "MERGER"))
        self.assertEqual(len(evs), 1, err)
        ev = evs[0]
        self.assertEqual(ev.action_type, "merger")
        self.assertEqual((ev.source_symbol, ev.target_symbol),
                         ("MPLE.TO", "OVRX.TO"))
        self.assertAlmostEqual(ev.ratio, 0.2)

    def test_same_issuer_exchange_stays_a_reorg(self):
        rows = [
            _rbc_row("2024-04-30", "Reorganization", "C005166",
                     "CELESTA INC SUBORD VTG SHS", "-175", "0", "CAD",
                     "MGR - CELESTA INC SUBORD VTG SHS XCH TO CELESTA INC "
                     "1 FOR 1"),
            _rbc_row("2024-04-30", "Reorganization", "CLX",
                     "CELESTA INC COM", "175", "0", "CAD",
                     "MGR - CELESTA INC COM SHRS RECEIVED THRU MERGER"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            p = _rbc_pairing(tmp, *rows)
        self.assertEqual([e.kind for e in p.events], ["reorg"])

    def test_s071_22_short_merger_is_refused(self):
        rows = [
            _rbc_row("2025-03-03", "Reorganization", "O000001",
                     "OLDR CORPORATION", "100", "0", "CAD",
                     "MGR - OLDR CORPORATION MERGER TO NEWR CORPORATION "
                     "1 NEW = 1 OLD"),
            _rbc_row("2025-03-03", "Reorganization", "NEWR",
                     "NEWR CORPORATION", "-100", "0", "CAD",
                     "MGR - NEWR CORPORATION SHRS RECEIVED THRU MERGER"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            p = _rbc_pairing(tmp, *rows)
            evs, err = _rbc_events(tmp, *rows)
        self.assertEqual(p.events, [])
        self.assertEqual(len(p.unmatched), 2)
        self.assertEqual(evs, [])
        self.assertIn("SHORT", err)


class TestRbcSymbolResolution(unittest.TestCase):
    _SPIN = ("DIS - NEWCO HOLDINGS INC SPINOFF ON 120 SHS FROM SEC# P000001 "
             "{parent} REC 03/01/25 PAY 03/05/25")

    def _spin(self, parent, qty="30", cur="USD", date="2025-03-05"):
        return _rbc_row(date, "Reorganization", "NEWC", "NEWCO HOLDINGS INC", qty,
                        "0", cur, self._SPIN.format(parent=parent))

    def test_s019_05_parent_listing_is_its_own(self):
        # Parent bought on the TSX; the spun-off shares arrive in USD.
        with tempfile.TemporaryDirectory() as tmp:
            evs, err = _rbc_events(
                tmp,
                _rbc_row("2024-05-01", "Buy", "PAR", "PARENTCO INC", "120",
                         "-6000", "CAD", "PARENTCO INC", price="50"),
                self._spin("PARENTCO INC"))
        self.assertEqual(evs[0].source_symbol, "PAR.TO", err)
        self.assertEqual(evs[0].target_symbol, "NEWC.US")

    def test_s019_06_option_code_is_never_the_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            evs, err = _rbc_events(
                tmp,
                _rbc_row("2025-01-10", "Sell", "8ABCDE1",
                         "CALL .ACME 01/15/27 50 ACME TECHNOLOGIES INC",
                         "-1", "300", "USD",
                         "CALL .ACME 01/15/27 50 ACME TECHNOLOGIES INC",
                         price="3"),
                self._spin("ACME TECHNOLOGIES INC"))
        self.assertNotIn("8ABCDE1", evs[0].source_symbol)
        self.assertIn("not traded", err)

    def test_s072_02_fuzzy_parent_needs_both_names_to_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            evs, err = _rbc_events(
                tmp,
                _rbc_row("2025-01-10", "Buy", "BEPC",
                         "BROOKFIELD RENEWABLE CORP", "100", "-3000", "USD",
                         "BROOKFIELD RENEWABLE CORP", price="30"),
                self._spin("BROOKFIELD CORP"))
        self.assertNotEqual(evs[0].source_symbol, "BEPC.US")
        self.assertIn("not traded", err)

    def test_fuzzy_parent_still_resolves_real_shapes(self):
        # The real 2024 shapes: 'GE AEROSPACE' -> the 'GE AEROSPACE
        # COMMON STOCK' line; 'GRAYSCALE ETHEREUM TR ETH' -> ETHE.
        for parent, sym, symdesc in (
                ("GE AEROSPACE", "GEX", "GE AEROSPACE COMMON STOCK"),
                ("GRAYSCALE ETHEREUM TR ETH", "ETHX",
                 "GRAYSCALE ETHEREUM TR ETF SHS")):
            with tempfile.TemporaryDirectory() as tmp:
                evs, err = _rbc_events(
                    tmp,
                    _rbc_row("2024-01-10", "Buy", sym, symdesc, "120",
                             "-3000", "USD", symdesc, price="25"),
                    self._spin(parent))
            self.assertEqual(evs[0].source_symbol, f"{sym}.US", err)

    def test_s074_09_merger_source_by_removal_currency(self):
        # B2GOLD-style: one company traded as BTOX (CAD) and BTGX (USD);
        # the USD removal under a temporary code is the USD holding,
        # whatever the file order.
        rows = [
            _rbc_row("2025-01-10", "Buy", "BTGX", "BTWO GOLD CORP", "200",
                     "-600", "USD", "BTWO GOLD CORP", price="3"),
            _rbc_row("2025-02-10", "Buy", "BTOX", "BTWO GOLD CORP", "100",
                     "-400", "CAD", "BTWO GOLD CORP", price="4"),
            _rbc_row("2025-02-11", "Sell", "BTOX", "BTWO GOLD CORP", "-100",
                     "410", "CAD", "BTWO GOLD CORP", price="4.1"),
            _rbc_row("2025-06-02", "Reorganization", "H012345",
                     "BTWO GOLD CORP", "-200", "0", "USD",
                     "MGR - BTWO GOLD CORP MERGER TO BIGCO INC "
                     "1 NEW = 2 OLD"),
            _rbc_row("2025-06-02", "Reorganization", "BIGCO", "BIGCO INC",
                     "100", "0", "USD",
                     "MGR - BIGCO INC SHRS RECEIVED THRU MERGER"),
        ]
        for order in (rows, list(reversed(rows))):
            with tempfile.TemporaryDirectory() as tmp:
                evs, err = _rbc_events(tmp, *order)
            self.assertEqual(evs[0].source_symbol, "BTGX.US", err)

    def test_s072_04_spinoff_on_short_parent_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            evs, err = _rbc_events(
                tmp,
                _rbc_row("2025-01-10", "Sell", "PRB", "PARENTCO INC",
                         "-100", "6000", "CAD", "PARENTCO INC", price="60"),
                self._spin("PARENTCO INC", qty="-20", cur="CAD"))
        self.assertEqual(evs, [])
        self.assertIn("SHORT", err)


# ============================================================= Questrade
_QT_H = ('Transaction Date,Settlement Date,Action,Symbol,Description,'
         'Quantity,Price,Gross Amount,Commission,Net Amount,Currency,'
         'Activity Type,Account #,Account Type\n')


def _qt(date, action, sym, desc, qty, cur, activity, price='0',
        net='0'):
    return (f'{date} 12:00:00 AM,{date} 12:00:00 AM,{action},{sym},{desc},'
            f'{qty},{price},{net},0,{net},{cur},{activity},55500001,'  # pii-ok
            f'Individual margin\n')


def _qt_events(tmp, *files):
    from taxjson.bin.taxjson_corp_actions import extract_events
    from taxjson.lib.corp_actions import parse_questrade_corporate_actions
    paths = []
    for i, (header, rows) in enumerate(files):
        p = Path(tmp) / f"qt_{i}.csv"
        p.write_text(header + ''.join(rows))
        paths.append(p)
    return _quiet(extract_events, parse_questrade_corporate_actions,
                  paths, 'margin')


_SPIN = ('WTS {tgt} SPINOFF ON 100 SHS FROM SEC# J000001 {parent} '
         'REC 03/01/25 PAY 03/05/25')


class TestQuestradeSpinoffSymbols(unittest.TestCase):
    def test_r1_141_dotted_target_gets_market_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            evs, err = _qt_events(tmp, (_QT_H, [
                _qt('2025-01-10', 'Buy', 'ABC', 'ABC CORP', '100', 'USD',
                    'Trades', '10', '-1000'),
                _qt('2025-03-05', 'DIS', 'ABC.WS',
                    _SPIN.format(tgt='ABC CORP', parent='ABC CORP'), '10',
                    'USD', 'Dividends')]))
        self.assertEqual((evs[0].source_symbol, evs[0].target_symbol),
                         ('ABC.US', 'ABC.WS.US'), err)

    def test_s074_07_venture_listing_like_the_parser(self):
        from taxjson.lib.brokerages.questrade import QuestradeBrokerage
        with tempfile.TemporaryDirectory() as tmp:
            evs, err = _qt_events(tmp, (_QT_H, [
                _qt('2025-01-10', 'Buy', 'ABC.VN', 'ABC MINING CORP', '100',
                    'CAD', 'Trades', '10', '-1000'),
                _qt('2025-03-05', 'DIS', 'NEWCO.VN',
                    _SPIN.format(tgt='NEWCO', parent='ABC MINING CORP'),
                    '10', 'CAD', 'Dividends')]))
        q = QuestradeBrokerage()
        self.assertEqual(
            (evs[0].source_symbol, evs[0].target_symbol),
            (q.apply_currency_suffix('ABC.VN', 'CAD'),
             q.apply_currency_suffix('NEWCO.VN', 'CAD')), err)

    def test_s020_02_padded_header(self):
        padded = ', '.join(_QT_H.strip().split(',')) + '\n'
        with tempfile.TemporaryDirectory() as tmp:
            evs, err = _qt_events(tmp, (padded, [
                _qt('2025-01-10', 'Buy', 'ABC', 'ABC CORP', '100', 'USD',
                    'Trades', '10', '-1000'),
                _qt('2025-03-05', 'DIS', 'ABCW',
                    _SPIN.format(tgt='ABC CORP', parent='ABC CORP'), '10',
                    'USD', 'Dividends')]))
        self.assertEqual(len(evs), 1, err)
        self.assertEqual(evs[0].source_symbol, 'ABC.US')

    def test_s073_05_s074_08_parent_is_the_listing_held(self):
        # QUUX bought and sold on the TSX, then bought on the NYSE: the
        # parent of a USD spin-off is the NYSE line, in either row order.
        rows = [
            _qt('2025-01-05', 'Buy', 'QUUX.TO', 'QUUX CORP', '100', 'CAD',
                'Trades', '10', '-1000'),
            _qt('2025-01-20', 'Sell', 'QUUX.TO', 'QUUX CORP', '-100', 'CAD',
                'Trades', '11', '1100'),
            _qt('2025-02-03', 'Buy', 'QUUX', 'QUUX CORP', '100', 'USD',
                'Trades', '8', '-800'),
            _qt('2025-03-05', 'DIS', 'QUUXW',
                _SPIN.format(tgt='QUUX CORP', parent='QUUX CORP'), '10',
                'USD', 'Dividends'),
        ]
        for order in (rows, list(reversed(rows))):
            with tempfile.TemporaryDirectory() as tmp:
                evs, err = _qt_events(tmp, (_QT_H, order))
            self.assertEqual(evs[0].source_symbol, 'QUUX.US', err)

    def test_s074_08_two_live_listings_not_guessed(self):
        rows = [
            _qt('2025-01-05', 'Buy', 'BTO.TO', 'BTWO GOLD CORP', '100',
                'CAD', 'Trades', '4', '-400'),
            _qt('2025-01-06', 'Buy', 'BTG', 'BTWO GOLD CORP', '100', 'USD',
                'Trades', '3', '-300'),
            _qt('2025-03-05', 'DIS', 'NEWC',
                _SPIN.format(tgt='NEWC', parent='BTWO GOLD CORP'), '10',
                'USD', 'Dividends'),
        ]
        ids = set()
        for order in (rows, list(reversed(rows))):
            with tempfile.TemporaryDirectory() as tmp:
                evs, err = _qt_events(tmp, (_QT_H, order))
            self.assertNotIn(evs[0].source_symbol, ('BTO.TO', 'BTG.US'))
            self.assertIn('several listings', err)
            ids.add(evs[0].event_id)
        self.assertEqual(len(ids), 1)

    def test_s073_09_chain_across_the_year_boundary(self):
        placeholder = _qt('2025-12-30', 'DIS', '',
                          'WTS QZD CORP SPINOFF ON 1000 SHS FROM SEC# '
                          'J000002 QZD CORP REC 12/29/25 PAY 12/30/25',
                          '100', 'USD', 'Dividends')
        release = _qt('2026-01-02', 'DIS', 'QZDW',
                      'WTS QZD CORP SPINOFF ON 1000 SHS REC 12/29/25 PAY '
                      '12/30/25 RELEASING AS RIGHTS DIST', '-100', 'USD',
                      'Dividends')
        repost = _qt('2026-01-02', 'DIS', 'QZDW',
                     'QZD CORP PENDING RTS DIST ON 1000 SHS REC 12/29/25 '
                     'PAY 12/30/25', '100', 'USD', 'Dividends')
        buy = _qt('2025-06-02', 'Buy', 'QZD', 'QZD CORP', '1000', 'USD',
                  'Trades', '1', '-1000')
        with tempfile.TemporaryDirectory() as tmp:
            one, _ = _qt_events(tmp, (_QT_H, [buy, placeholder, release,
                                             repost]))
        with tempfile.TemporaryDirectory() as tmp:
            split, err = _qt_events(tmp, (_QT_H, [buy, placeholder]),
                                    (_QT_H, [release, repost]))
        from taxjson.lib.corp_actions import combine_broker_copies
        split = combine_broker_copies(split, stream=io.StringIO())
        self.assertEqual(len(one), 1)
        self.assertEqual([(e.event_id, e.qty_received) for e in split],
                         [(one[0].event_id, 100.0)], err)
        self.assertNotIn('SKIPPED', err)


if __name__ == "__main__":
    unittest.main()
