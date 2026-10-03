"""Re-audit-2 errors (errA-d): one-line errors instead of tracebacks in the
checklist / watch state, close-year / handoff, export,
some parsers and taxjson-sum-income."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ENV = dict(os.environ, TAXJSON_OFFLINE="1", PYTHONPATH=str(REPO / "src"),
           NO_COLOR="1")

TOML = ('[settings]\nyear = 2025\ncountry = "canada"\n'
        'base_currency = "CAD"\nsource_currencies = []\n'
        'option_grant_timing_since = 2025\n'
        '[accounts.margin]\ntype = "taxable"\n')


def tj(root, *args):
    return subprocess.run([sys.executable, "-m", "taxjson.bin.taxjson_run",
                           "-C", str(root), *args], capture_output=True,
                          text=True, env=ENV, stdin=subprocess.DEVNULL)


def tool(module, *args, cwd=None, env=None):
    return subprocess.run([sys.executable, "-c",
                           "import sys; from taxjson.bin._entry import "
                           f"{module} as m; sys.exit(m())", *args],
                          capture_output=True, text=True, cwd=cwd,
                          env=env or ENV, stdin=subprocess.DEVNULL)


def no_tb(tc, r):
    tc.assertNotIn("Traceback", r.stderr, r.stderr[-2000:])


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)

    def tearDown(self):
        for p in self.root.rglob("*"):
            try:
                if p.is_dir() and not p.is_symlink():
                    p.chmod(0o755)
                else:
                    p.chmod(0o644)
            except OSError:
                pass
        self._td.cleanup()


# ------------------------------------------------------------- checklist
class TestChecklistState(_Tmp):
    """A2-0768, A2-0789, A2-1393, A2-1414, A2-1430 (checklist half)."""

    def project(self):
        (self.root / "taxjson.toml").write_text(TOML)
        return self.root

    def test_state_is_directory_load_refused(self):
        from taxjson.lib import checklist as cl
        root = self.project()
        (root / cl.STATE_FILE).mkdir()
        with self.assertRaises(cl.StateFileError):
            cl.load_state(root)

    def test_state_symlink_loop_load_refused(self):
        from taxjson.lib import checklist as cl
        root = self.project()
        os.symlink(cl.STATE_FILE, root / cl.STATE_FILE)
        with self.assertRaises(cl.StateFileError):
            cl.load_state(root)

    def test_wrong_shape_override_entry_refused(self):
        from taxjson.lib import checklist as cl
        root = self.project()
        for bad in ('"x"', "7", "[1]", '{"status": 5}'):
            (root / cl.STATE_FILE).write_text(
                '{"overrides": {"elections": %s}}' % bad)
            with self.assertRaises(cl.StateFileError) as cm:
                cl.load_state(root)
            self.assertIn("elections", str(cm.exception))

    def test_bom_state_loads(self):
        """A2-0776: a hand-edited checklist.json saved with a BOM."""
        from taxjson.lib import checklist as cl
        root = self.project()
        (root / cl.STATE_FILE).write_bytes(
            b'\xef\xbb\xbf{"overrides": {"elections": {"status": "done"}}}')
        self.assertEqual(cl.load_state(root)["overrides"]["elections"]
                         ["status"], "done")

    def test_done_on_directory_one_line(self):
        from taxjson.lib import checklist as cl
        root = self.project()
        (root / cl.STATE_FILE).mkdir()
        r = tj(root, "checklist", "--done", "elections")
        no_tb(self, r)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("checklist.json", r.stderr)

    def test_reset_on_directory_one_line(self):
        from taxjson.lib import checklist as cl
        root = self.project()
        (root / cl.STATE_FILE).mkdir()
        r = tj(root, "checklist", "--reset")
        no_tb(self, r)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("checklist.json", r.stderr)

    @unittest.skipIf(os.geteuid() == 0, "root ignores permissions")
    def test_done_read_only_file_one_line_and_kept(self):
        from taxjson.lib import checklist as cl
        root = self.project()
        st = root / cl.STATE_FILE
        st.write_text('{"overrides": {}}\n')
        root.chmod(0o555)
        try:
            r = tj(root, "checklist", "--done", "elections")
        finally:
            root.chmod(0o755)
        no_tb(self, r)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("cannot write", r.stderr)
        self.assertEqual(st.read_text(), '{"overrides": {}}\n')
        self.assertEqual([p.name for p in root.iterdir()
                          if p.name.endswith(".part")], [])

    def test_save_failure_leaves_old_file_and_no_part(self):
        from taxjson.lib import checklist as cl
        root = self.project()
        st = root / cl.STATE_FILE
        st.write_text('{"overrides": {}}\n')
        state = cl.load_state(root)
        state["bad"] = object()          # json.dumps fails part-way
        with self.assertRaises((cl.StateFileError, TypeError)):
            cl.save_state(root, state, 2025)
        self.assertEqual(st.read_text(), '{"overrides": {}}\n')
        self.assertEqual([p.name for p in root.iterdir()
                          if p.name.endswith(".part")], [])


class TestWatchStateInner(unittest.TestCase):
    """A2-1430 (watch half): wrong-shape inner entries re-baseline."""

    def test_bad_inner_rebaselines(self):
        from taxjson.bin import taxjson_watch as w
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".watch_state.json"
            for radar, hn in (([1], None), ({"SHOP.TO": "x"}, None),
                              ({"SHOP.TO": {"category": 5}}, None),
                              ({}, "abc"), ({}, True)):
                doc = {"schema_version": w.STATE_VERSION, "as_of": "x",
                       "radar": radar}
                if hn is not None:
                    doc["harvest_now"] = hn
                p.write_text(json.dumps(doc))
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    self.assertIsNone(w.load_state(p), (radar, hn))
                self.assertIn("NEW baseline", err.getvalue())

    def test_good_state_kept(self):
        from taxjson.bin import taxjson_watch as w
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".watch_state.json"
            doc = {"schema_version": w.STATE_VERSION, "as_of": "x",
                   "radar": {"SHOP.TO": {"category": "LOCKED",
                                         "advisory": "a",
                                         "clears_at": "2025-01-01"}},
                   "harvest_now": -12.5}
            p.write_text(json.dumps(doc))
            self.assertEqual(w.load_state(p)["harvest_now"], -12.5)


# --------------------------------------------------- handoff / close-year
GOOD_LOCK = {
    "schema_version": 2, "record_version": 2, "year": 2024,
    "country": "canada", "date_basis": "settle", "closed_at": "2025-04-01",
    "dispositions": [{"account": "margin", "symbol": "XYZ.TO",
                      "date": "2024-05-05", "date_settle": "2024-05-06",
                      "qty": 10.0, "proceeds": 120.0, "cost": 100.0,
                      "gain": 20.0, "denied": 0.0}],
    "settle_next_year": [{"group": "equity", "account": "margin",
                          "symbol": "XYZ.TO", "date": "2024-12-31",
                          "date_settle": "2025-01-02", "qty": 5.0,
                          "net": -50.0}],
    "year_end": {"equity": {"XYZ.TO": {"qty": 5.0, "acb": 50.0,
                                       "deferred": 0.0}}},
    "boundary_rows": [],
}


class TestHandoffRecord(_Tmp):
    """A2-0803: field-level damage in the prior-year lock is one line."""

    def test_good_lock_validates(self):
        from taxjson.lib import handoff
        handoff.validate_record(json.loads(json.dumps(GOOD_LOCK)), "f")

    def test_wrong_field_shapes_refused(self):
        from taxjson.lib import handoff
        cases = [("dispositions", "x"), ("dispositions", 7),
                 ("dispositions", {"a": 1}), ("schema_version", "x"),
                 ("schema_version", [1]), ("settle_next_year", "x"),
                 ("settle_next_year", 7), ("year_end", [1]),
                 ("year", "x")]
        for k, v in cases:
            rec = json.loads(json.dumps(GOOD_LOCK))
            rec[k] = v
            with self.assertRaises(handoff.RecordError, msg=(k, v)) as cm:
                handoff.validate_record(rec, "filed/2024.json")
            self.assertIn("filed/2024.json", str(cm.exception))
        for lst, field in (("dispositions", None),
                           ("dispositions", "gain"),
                           ("dispositions", "symbol"),
                           ("settle_next_year", None),
                           ("settle_next_year", "symbol"),
                           ("settle_next_year", "qty")):
            for v in ("x", 7, None, [1], {"a": 1}, {}):
                rec = json.loads(json.dumps(GOOD_LOCK))
                if field is None:
                    if v == {}:
                        continue
                    rec[lst][0] = v
                else:
                    if field != "symbol" and v == 7:
                        continue
                    if field == "symbol" and v == "x":
                        continue
                    rec[lst][0][field] = v
                with self.assertRaises(handoff.RecordError,
                                       msg=(lst, field, v)):
                    handoff.validate_record(rec, "f")

    def test_bom_lock_loads(self):
        from taxjson.lib import handoff
        p = self.root / "2024.json"
        p.write_bytes(b"\xef\xbb\xbf" + json.dumps(GOOD_LOCK).encode())
        self.assertEqual(handoff.load_record(p)["year"], 2024)

    def test_handoff_cli_one_line_exit_2(self):
        (self.root / "taxjson.toml").write_text(TOML)
        (self.root / "filed").mkdir()
        rec = json.loads(json.dumps(GOOD_LOCK))
        rec["dispositions"] = "x"
        (self.root / "filed" / "2024.json").write_text(json.dumps(rec))
        r = tj(self.root, "handoff")
        no_tb(self, r)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("dispositions", r.stderr)


class TestFiledDispositionsCsv(_Tmp):
    """A2-0769, A2-1397: short rows, blank symbols, a directory."""

    def load(self, text):
        from taxjson.lib import handoff
        p = self.root / "filed.csv"
        p.write_text("symbol,date,qty,proceeds,cost,gain\n" + text)
        return handoff.load_filed_dispositions(p)

    def test_short_row(self):
        with self.assertRaisesRegex(ValueError, r"filed.csv:2: bad row"):
            self.load("AAPL.US,2024-05-14\n")

    def test_blank_symbol(self):
        with self.assertRaisesRegex(ValueError, r"filed.csv:2: .*blank"):
            self.load(",2024-05-14,1,2,3,4\n")

    def test_non_finite(self):
        with self.assertRaisesRegex(ValueError, r"filed.csv:2"):
            self.load("AAPL.US,2024-05-14,nan,nan,inf,-inf\n")

    def test_directory(self):
        from taxjson.lib import handoff
        (self.root / "d.csv").mkdir()
        with self.assertRaises(ValueError):
            handoff.load_filed_dispositions(self.root / "d.csv")

    def test_good_row(self):
        self.assertEqual(self.load("AAPL.US,2024-05-14,1,2,3,4\n")[0]
                         ["symbol"], "AAPL.US")


class TestHandoffGainsShape(_Tmp):
    """A2-1396 / A2-0794 (handoff part): a wrong-shape gains file."""

    def test_dispositions_and_check_refuse(self):
        from taxjson.lib import handoff
        p = self.root / "brk_gains.json"
        for bad in ("[]", '{"transactions": ["x"]}',
                    '{"transactions": 5}'):
            p.write_text(bad)
            if bad == "[]":
                # a bare list is a transaction book: no sales, no crash
                self.assertEqual(handoff.dispositions({"brk": p}, 2024), [])
                continue
            with self.assertRaises(handoff.BooksError):
                handoff.dispositions({"brk": p}, 2024)

    def test_rows_wrong_row_type(self):
        from taxjson.lib import handoff
        p = self.root / "m_base.json"
        p.write_text('{"transactions": [{"symbol": "A", "quantity": "x"}]}')
        with self.assertRaises(handoff.BooksError):
            handoff._rows(p)


# --------------------------------------------------------------- export
def export(*args, cwd=None):
    return subprocess.run([sys.executable, "-m", "taxjson.bin.taxjson_export",
                           *args], capture_output=True, text=True, cwd=cwd,
                          env=ENV, stdin=subprocess.DEVNULL)


class TestExport(_Tmp):
    """A2-0806, A2-1410, A2-1442, A2-1443 (tv-map BOM), A2-1441."""

    def test_tv_map_bom_keeps_first_rule(self):
        g = self.root / "g.json"
        g.write_text('{"inventory":[{"symbol":"XYZ.TO","qty":10,'
                     '"total_cost":100.0,"currency":"CAD"}]}')
        m = self.root / "bom.map"
        m.write_bytes(b"\xef\xbb\xbfXYZ NEO\n")
        r = export("--tradingview", "--tv-map", str(m), str(g))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("NEO:XYZ", r.stdout)

    def test_gains_rows_wrong_types_refused(self):
        """A2-0793 (export part)."""
        g = self.root / "margin_gains_wash.json"
        for inv in ({"symbol": "x", "qty": "x", "total_cost": "x",
                     "currency": "x"},
                    {"symbol": 5, "qty": 1, "total_cost": 1}):
            g.write_text(json.dumps({"transactions": [], "inventory": [inv]}))
            for mode in ("--report", "--seekingalpha", "--holdings-toml"):
                r = export(mode, str(g))
                no_tb(self, r)
                self.assertEqual(r.returncode, 2, (mode, r.stderr))
                self.assertIn("margin_gains_wash.json", r.stderr)

    def test_holdings_toml_bad_quantity_refused(self):
        h = self.root / "h.toml"
        for bad in ('quantity = "abc"\ntotal_cost = 10.0',
                    'quantity = 5\ntotal_cost = "x"'):
            h.write_text('[meta]\nschema_version = 1\n[[holding]]\n'
                         'symbol = "XEI.TO"\n' + bad +
                         '\ncurrency = "CAD"\n')
            for mode in ("--report", "--seekingalpha", "--tradingview"):
                r = export(mode, str(h))
                no_tb(self, r)
                self.assertEqual(r.returncode, 2, (mode, r.stderr))
                self.assertIn("[[holding]] 1 (XEI.TO)", r.stderr)


# ------------------------------------------------------------- parsers
class TestIbIncomeTicker(unittest.TestCase):
    """A2-0780: an IB income row with no TICKER(ISIN) token is refused,
    never booked on UNKNOWN.US or a word of the text."""

    def parse(self, body):
        from test_fix_ibparse import HEAD, _parse_ib
        return _parse_ib(HEAD + body)

    def test_clean_row_booked(self):
        from test_fix_ibparse import DIV_H
        _p, txs, _e = self.parse(
            DIV_H + 'Dividends,Data,CAD,2024-02-01,"QZRY (CA9990000017) '
            'CASH DIVIDEND CAD 1.38 PER SHARE",13.80\n')
        self.assertEqual([t["symbol"] for t in txs
                          if t["action"] == "DIVIDEND"], ["QZRY.TO"])

    def test_no_token_refused(self):
        from taxjson.lib.brokerages.base import BrokerageParseError
        from test_fix_ibparse import DIV_H, WHT_H
        for body in (
                DIV_H + 'Dividends,Data,CAD,2024-02-01,,13.80\n',
                DIV_H + 'Dividends,Data,CAD,2024-02-01,"CASH DIVIDEND '
                'CAD 1.38 PER SHARE",13.80\n',
                DIV_H + 'Dividends,Data,CAD,2024-02-01,"qzry (ca9990000017) '
                'cash dividend",13.80\n',
                WHT_H + 'Withholding Tax,Data,USD,2024-02-01,"CASH '
                'DIVIDEND USD 0.24",-1.20\n'):
            with self.assertRaisesRegex(BrokerageParseError,
                                        r"line \d+ .*TICKER \(ISIN\)"):
                self.parse(body)

    def test_interest_withholding_on_cash(self):
        from test_fix_ibparse import WHT_H
        _p, txs, _e = self.parse(
            WHT_H + 'Withholding Tax,Data,USD,2024-06-03,"Withholding @ '
            '20% on Credit Interest for MAY-2024",-1.20\n')
        self.assertEqual([(t["action"], t["symbol"]) for t in txs],
                         [("TAX", "CASH")])


WB = ('Webull Securities (Canada) Ltd.\nSynthetic Demo Statement\n'
      'Date Range: January 1 2024 - December 31 2024\n\n'
      '"Currency","Date","Action Code","Symbol","Security Description",'
      '"Type Code","Quantity","Price","Proceeds"\n'
      'USD,15-01-2024,BUY,@AAPL,APPLE INC,EQ,100,185.00,"(18,500.00)"\n')


def wb_parse(text, name="wb_demo.csv", encoding="utf-8"):
    import contextlib
    import io
    from taxjson.lib.brokerages.webull import WebullBrokerage
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / name
        p.write_bytes(text.encode(encoding) if encoding != "utf-16"
                      else b"\xff\xfe" + text.encode("utf-16-le"))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            return WebullBrokerage().parse_file(p)


class TestWebullBlankAction(unittest.TestCase):
    """A2-0788."""

    def test_blank_action_trade_cells_refused(self):
        from taxjson.lib.brokerages.base import BrokerageParseError
        with self.assertRaisesRegex(BrokerageParseError,
                                    r"line \d+: .*blank Action Code"):
            wb_parse(WB + 'USD,10-05-2024,,@AAPL,APPLE INC,EQ,75,200.00,'
                          '"14,995.05"\n')

    def test_blank_continuation_row_ok(self):
        txs = wb_parse(WB + ',,,,,,,,\n')
        self.assertEqual(len(txs), 1)

    def test_utf16_webull_read(self):
        """A2-0805 (Webull half)."""
        self.assertEqual(len(wb_parse(WB, encoding="utf-16")), 1)


class TestGenericSidecar(unittest.TestCase):
    """A2-0801 (already fixed by A2-1081; pinned), A2-1452."""

    CSV = ("Date,Type,Ticker,Qty,Price,Amount,Fee,Currency\n"
           "2024-11-04,BUY,XEI,50,20.00,-1009.95,9.95,CAD\n")

    def test_non_string_date_format(self):
        from test_fix_a2_generic import _parse, _toml
        for bad in ("7", "[1]", "{a=1}", "true"):
            for key in ("date", "settle"):
                with self.assertRaisesRegex(
                        ValueError, rf"\[formats\]\.{key} must be a "
                                    r"quoted string"):
                    _parse(self.CSV, _toml(extra=f"[formats]\n{key} = "
                                                 f"{bad}\n"))

    def test_non_utf8_mapping_named(self):
        from test_fix_a2_generic import _toml
        from taxjson.lib.brokerages.generic import GenericBrokerage
        with tempfile.TemporaryDirectory() as td:
            c = Path(td) / "generic_ws.csv"
            c.write_text(self.CSV)
            c.with_name(c.name + ".toml").write_bytes(
                _toml().encode() + b"# \xff\xfe\n")
            with self.assertRaisesRegex(ValueError,
                                        r"generic_ws\.csv\.toml: not UTF-8"):
                GenericBrokerage().parse_file(c)


class TestIbNoTzdata(unittest.TestCase):
    """A2-1447: no tz database -> one-line parse error, not a traceback."""

    def test_missing_zone_is_parse_error(self):
        from unittest import mock
        import zoneinfo
        from taxjson.lib.brokerages import ib_extractor as ib
        from taxjson.lib.brokerages.base import BrokerageParseError

        def boom(name):
            raise zoneinfo.ZoneInfoNotFoundError(f"No time zone found "
                                                 f"with key {name}")
        with mock.patch.object(zoneinfo, "ZoneInfo", boom):
            with self.assertRaisesRegex(BrokerageParseError, "tzdata"):
                ib._ib_market_trade_date("2025-06-30", "09:30:00",
                                         "Stocks", "AUD", "AX", "QZBHP")

    def test_with_zone_ok(self):
        from taxjson.lib.brokerages import ib_extractor as ib
        self.assertEqual(ib._ib_market_trade_date(
            "2025-06-30", "21:30:00", "Stocks", "AUD", "AX", "QZBHP")[0],
            "2025-07-01")


class TestTtBareEquitySymbol(_Tmp):
    """A2-0777: a suffix-less symbol in an equity account is said."""

    def make(self, crypto=False):
        (self.root / "taxjson.toml").write_text(
            TOML + ("crypto = true\n" if crypto else ""))
        d = self.root / "inputs" / "margin"
        d.mkdir(parents=True)
        (d / "hand.tt").write_text(
            "BUYSELL 2025-02-03 10:00:00 QZMS.TO 10 CAD 400 4001 1\n"
            "BUYSELL 2025-06-03 10:00:00 QZMS -5 CAD 420 2099 1\n")
        return d / "hand.tt"

    def test_warned_in_equity_account(self):
        tt = self.make()
        r = tj(self.root, "run", "--no-input")
        no_tb(self, r)
        # The channel of the unknown-suffix warning (S028-19): the
        # stage .diag and the .sum DIAGNOSTICS.
        self.assertIn("symbol QZMS has no market suffix",
                      (self.root / "reports" / "margin.sum").read_text())
        self.assertTrue(tt.exists())

    def test_not_warned_in_crypto_account(self):
        import contextlib
        import io
        from taxjson.bin.taxjson_convert_tt import tt_to_json
        tt = self.make(crypto=True)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            tt_to_json(tt, "margin")
        self.assertNotIn("no market suffix", err.getvalue())

    def test_outside_project_not_warned(self):
        import contextlib
        import io
        from taxjson.bin.taxjson_convert_tt import tt_to_json
        tt = self.root / "x.tt"
        tt.write_text("BUYSELL 2025-02-03 10:00:00 QZMS 10 USD 400 4001 1\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            tt_to_json(tt, "default")
        self.assertNotIn("no market suffix", err.getvalue())


# ----------------------------------------------------------- sum-income
class TestSumIncomeRows(_Tmp):
    """A2-1448: rows go through the gains loader's checks."""

    def test_bad_rows_one_line_exit_2(self):
        good = {"action": "DIVIDEND", "date": "2024-02-15", "time":
                "09:30:00", "symbol": "QZRY.TO", "quantity": 0.0,
                "currency": "CAD", "net_amount": 24.69, "type": "dividend",
                "account": "margin", "id": "d1"}
        for field, val in (("date", "2024-02-30"),
                           ("net_amount", float("nan")),
                           ("net_amount", float("inf")),
                           ("net_amount", "abc")):
            row = dict(good, **{field: val})
            f = self.root / "b.json"
            f.write_text(json.dumps({"transactions": [row]}))
            r = tool("taxjson_sum_income", "--year", "2024",
                     "--country", "canada", str(f))
            no_tb(self, r)
            self.assertEqual(r.returncode, 2, (field, val, r.stderr,
                                               r.stdout))
            self.assertIn("b.json", r.stderr)
        f.write_text(json.dumps({"transactions": [good]}))
        r = tool("taxjson_sum_income", "--year", "2024", "--country",
                 "canada", str(f))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("24.69", r.stdout)


if __name__ == "__main__":
    unittest.main()
