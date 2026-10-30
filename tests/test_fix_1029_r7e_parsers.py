"""Parser and stage fixes of fix round 7, wave E (group parsers).

TJ-1173 (a crypto .tt reward with a price and a 0 total), TJ-1202 (a
preferred symbol's explicit listing suffix doubled), TJ-1203 (a bare slip
symbol SYN.PR.L read as series-less), TJ-1224 / TJ-1225 (convert-currency
and sort metadata), TJ-1226 (validate and corp_parent_qty), TJ-1236 (the
redact placeholder read as a path), TJ-1237 / TJ-1238 / TJ-1239 / TJ-1240
(generic importer: fee-only rows, MULT on a low price, a blank fee cell),
TJ-1249 / TJ-1250 (Questrade: splitting one distribution chain).

Every fixture is SYNTHETIC: made-up tickers (SYN, SPNA, QZX, ZZC), accounts
99900001 and U1234567, invented amounts. Each test's files live in a folder
of their own.
"""
import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from _qa_project import project, tj
from tax_rules import rule
from test_fix_1029_r7b_generic import _gains, _mapping, _parse
from test_fix_1029_r7d_rbcqt import QT_BUY, qt_dis
from test_fix_rbcqt import QH, q

from taxjson.bin import fill_crypto_prices as FC
from taxjson.bin import taxjson_convert_tt as CT
from taxjson.bin import taxjson_redact as R
from taxjson.bin import taxjson_reconcile_slips as RS
from taxjson.lib import corp_actions as CA
from taxjson.lib.brokerages.base import BaseBrokerage
from taxjson.lib.brokerages.generic import GenericBrokerage
from taxjson.lib.dispatch import python_module_argv

REPO = Path(__file__).resolve().parent.parent
_AMOUNT = 'amount = "Amount"'
_AMOUNT_FEE = 'amount = "Amount"\nfee = "Fee"'
_CHARGED = 'fee_sign = "charged"'


def _cli(module, args, stdin=None):
    env = dict(os.environ, TAXJSON_WIDTH="0", TAXJSON_OFFLINE="1",
               PYTHONPATH=str(REPO / "src"))
    c = subprocess.run(python_module_argv(f"taxjson.bin.{module}",
                                          [str(a) for a in args]),
                       capture_output=True, text=True, env=env, input=stdin)
    return c.returncode, c.stdout, c.stderr


# ======================================== TJ-1173 a reward's stated price
def _fill_tt(line, cache=None):
    """One .tt line in a crypto account through fill-crypto, no network."""
    row = CT.parse_tt_line(line, "crypto1")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "book.json"
        p.write_text(json.dumps({"transactions": [row]}))
        out, err = io.StringIO(), io.StringIO()
        with patch.object(FC, "load_cache", return_value=dict(cache or {})), \
                patch.object(FC, "save_cache"), \
                patch.object(FC, "get_crypto_price", return_value=0.0), \
                contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            FC._fill(Namespace(input=str(p)))
    return json.loads(out.getvalue())["transactions"][0], err.getvalue()


class TestTJ1173RewardWithAPriceAndNoTotal(unittest.TestCase):
    def test_the_stated_price_values_it(self):
        row, _ = _fill_tt("DIVIDEND 2025-03-03 09:30:00 ZZC 0.5 CAD 40 0")
        self.assertAlmostEqual(row["net_amount"], 20.0)
        self.assertAlmostEqual(row["gross_amount"], 20.0)
        self.assertEqual(row["currency"], "CAD")
        self.assertEqual(row["price"], 40.0)

    def test_a_negative_quantity_is_valued_as_a_lookup_would(self):
        row, _ = _fill_tt("DIVIDEND 2025-03-03 09:30:00 ZZC -0.5 CAD 40 0")
        self.assertAlmostEqual(row["net_amount"], 20.0)

    def test_a_stated_total_stays(self):
        row, _ = _fill_tt("DIVIDEND 2025-03-03 09:30:00 ZZC 0.5 CAD 40 19")
        self.assertAlmostEqual(row["net_amount"], 19.0)

    def test_a_row_fully_withheld_keeps_its_figures(self):
        row, _ = _fill_tt("DIVIDEND 2025-03-03 09:30:00 ZZC 0.5 CAD 40 20 0")
        self.assertAlmostEqual(row["gross_amount"], 20.0)
        self.assertAlmostEqual(row["net_amount"], 0.0)

    def test_no_price_still_goes_to_the_lookup(self):
        row, _ = _fill_tt("DIVIDEND 2025-03-03 09:30:00 ZZC 0.5 CAD 0 0",
                          {"ZZC-2025-03-03": 40.0})
        self.assertAlmostEqual(row["net_amount"], 20.0)
        self.assertEqual(row["currency"], "USD")

    def test_no_quantity_is_nothing_to_value(self):
        row, _ = _fill_tt("DIVIDEND 2025-03-03 09:30:00 ZZC 0 CAD 40 0")
        self.assertEqual(row["net_amount"], 0.0)

    def test_a_buy_at_a_price_with_no_total_is_unchanged(self):
        row, _ = _fill_tt("BUYSELL 2025-03-03 09:30:00 ZZC 0.5 CAD 40 0")
        self.assertEqual(row["net_amount"], 0.0)

    def _run(self, country, base):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            (root / "inputs" / "crypto1").mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nlocal_timezone = "America/Toronto"\n'
                f'year = 2025\ncountry = "{country}"\n'
                f'base_currency = "{base}"\n'
                'source_currencies = []\n'
                '[accounts.crypto1]\ntype = "taxable"\ncrypto = true\n')
            (root / "ticker.map").write_text("")
            (root / "inputs" / "crypto1" / "rewards.tt").write_text(
                f"DIVIDEND 2025-03-03 09:30:00 ZZC 0.5 {base} 40 0\n")
            tj(root, "run", "--strict", "--no-input")
            return tj(root, "divs").stdout

    def test_end_to_end_canada(self):
        self.assertIn("20.00", self._run("canada", "CAD"))

    def test_end_to_end_usa(self):
        self.assertIn("20.00", self._run("usa", "USD"))


# ============================== TJ-1202 an explicit suffix on a preferred
class TestTJ1202ExplicitListingOfAPreferred(unittest.TestCase):
    def test_shared_helper(self):
        b = BaseBrokerage()
        for sym, cur, want in (
                ("SYN.PF.US", "USD", "SYN.PF.US"),
                ("SYN.PR.AX", "AUD", "SYN.PR.AX"),
                ("SYN.PF.L", "GBP", "SYN.PF.L"),
                ("SYN.PF", "USD", "SYN.PF.US"),
                ("SYN.PF.US", "CAD", "SYN.PF.TO"),
                # TJ-0970: a series letter stays a series.
                ("SYN.PR.L", "CAD", "SYN.PR.L.TO"),
                ("SYN.PR.V", "CAD", "SYN.PR.V.TO"),
                ("SYN.PR.L", "USD", "SYN.PR.L.US"),
                ("SYN.PR.A", "GBP", "SYN.PR.A.L"),
                ("SYN.PR.L.TO", "CAD", "SYN.PR.L.TO"),
                ("SYN.PR.CN", "CAD", "SYN.PR.TO")):
            with self.subTest(sym=sym, cur=cur):
                self.assertEqual(b.apply_currency_suffix(sym, cur), want)

    def test_generic(self):
        g = GenericBrokerage()
        for sym, cur, want in (("SYN.PF.US", "USD", "SYN.PF.US"),
                               ("SYN.PR.AX", "AUD", "SYN.PR.AX"),
                               ("SYN.PF.L", "GBP", "SYN.PF.L"),
                               # The listing wins over the row's currency.
                               ("SYN.PF.US", "CAD", "SYN.PF.US"),
                               ("SYN.PR.V", "CAD", "SYN.PR.V.TO")):
            with self.subTest(sym=sym, cur=cur):
                self.assertEqual(g._listing_symbol(sym, cur), want)

    def test_ib(self):
        from taxjson.lib.brokerages.ib_extractor import _ib_stock_symbol
        for sym, cur, want in (("SYN.PF.US", "USD", "SYN.PF.US"),
                               ("SYN.PR.AX", "AUD", "SYN.PR.AX"),
                               ("SYN.PF.L", "GBP", "SYN.PF.L"),
                               ("SYN.PR.L", "CAD", "SYN.PR.L.TO"),
                               ("SYN.PR.V", "CAD", "SYN.PR.V.TO")):
            with self.subTest(sym=sym, cur=cur):
                self.assertEqual(_ib_stock_symbol("Stocks", sym, cur, {}),
                                 want)

    def test_the_two_spellings_share_a_pool(self):
        rows, _ = _parse(
            "Date,Action,Symbol,Qty,Price,Amount\n"
            "2025-01-02,B,SYN.PF.US,10,10,-100\n"
            "2025-06-02,S,SYN.PF,-10,20,200\n",
            '[columns]\ndate="Date"\naction="Action"\nsymbol="Symbol"\n'
            'quantity="Qty"\nprice="Price"\namount="Amount"\n'
            '[defaults]\ncurrency="USD"\n[actions]\nB="buy"\nS="sell"\n')
        self.assertEqual({r["symbol"] for r in rows}, {"SYN.PF.US"})


# ========================================= TJ-1203 a bare slip's series
class TestTJ1203BareSlipSeries(unittest.TestCase):
    def test_split_and_norm(self):
        for sym, want in (("SYN.PR.L", ("SYN.PR.L", "")),
                          ("SYN.PR.V", ("SYN.PR.V", "")),
                          ("SYN.PF.A", ("SYN.PF.A", "")),
                          ("SYN.PR.L.TO", ("SYN.PR.L", "TO")),
                          ("SYN.PR.US", ("SYN.PR", "US")),
                          ("SYN.PR.TO", ("SYN.PR", "TO")),
                          ("SYN.PR.CN", ("SYN.PR", "CN")),
                          ("SYN.L", ("SYN", "L")),
                          ("SYN.V", ("SYN", "V")),
                          ("SYN.TO", ("SYN", "TO"))):
            with self.subTest(sym=sym):
                self.assertEqual(RS.split_listing(sym), want)
        self.assertEqual(RS.norm_symbol("syn.pr.l"), "SYN.PR.L")
        self.assertEqual(RS.norm_symbol("SYN.PR.L.TO"), "SYN.PR.L")
        self.assertEqual(RS.norm_symbol("SYN.L"), "SYN")

    def _reconcile(self, spelled, country="canada"):
        from test_fix_rbcqt import QH as _QH
        rows = [q(td="2025-01-02", action="Buy", sym="SYN.PR.L",
                  desc="SYN PREFERRED SERIES L", qty="10", price="10",
                  gross="-100", comm="0", net="-100", cur="CAD"),
                q(td="2025-06-02", action="Sell", sym="SYN.PR.L",
                  desc="SYN PREFERRED SERIES L", qty="-5", price="30",
                  gross="150", comm="0", net="150", cur="CAD")]
        with tempfile.TemporaryDirectory() as td:
            root = project(td, "p", {"margin/questrade.csv":
                                     _QH + "".join(rows)})
            toml = (root / "taxjson.toml").read_text()
            (root / "taxjson.toml").write_text(
                toml.replace("year = 2024", "year = 2025").replace(
                    "option_grant_timing_since = 2024",
                    "option_grant_timing_since = 2025"))
            tj(root, "run", "--no-input")
            slip = root / "slip.csv"
            slip.write_text("symbol,quantity,proceeds,cost\n"
                            f"{spelled},5,150,50\n")
            return tj(root, "reconcile-slips", str(slip), check=False)

    def test_every_spelling_reconciles(self):
        for spelled in ("SYN.PR.L", "SYN PR L", "SYN.PR.L.TO"):
            with self.subTest(spelled=spelled):
                r = self._reconcile(spelled)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertIn("SYN.PR.L", r.stdout)
                self.assertNotIn("SYN.PR ", r.stdout)


# ================================== TJ-1224 / TJ-1225 stage metadata
_ROW = {"id": "t1", "date": "2024-01-15", "type": "BUYSELL",
        "action": "BUY", "symbol": "SYN", "quantity": 10, "price": 10,
        "currency": "CAD", "fees": 0, "source_file": "a.csv"}


def _book(metadata):
    return {"transactions": [dict(_ROW)], "metadata": metadata}


class TestTJ1224BrokersThroughConversion(unittest.TestCase):
    """Two generic brokers' identical rows, one source account: a dedup
    after convert-currency keeps both, as a direct dedup does."""

    def _files(self, td):
        out = []
        for name in ("alpha", "beta"):
            p = Path(td) / f"generic_{name}.csv"
            p.write_text("Date,Action,Symbol,Qty,Price,Amount,Currency\n"
                         "2025-03-03,BUY,SYN.TO,10,10,-100,CAD\n"
                         "2025-06-03,SELL,SYN.TO,10,20,200,CAD\n")
            p.with_suffix(".csv.toml").write_text(
                '[columns]\ndate="Date"\naction="Action"\nsymbol="Symbol"\n'
                'quantity="Qty"\nprice="Price"\namount="Amount"\n'
                'currency="Currency"\n[actions]\nBUY="buy"\nSELL="sell"\n'
                f'[broker]\nname="{name}"\naccount="99900001"\n')
            rc, o, err = _cli("taxjson_brokerage",
                              ["--brokerage", "generic", p])
            self.assertEqual(rc, 0, err)
            j = Path(td) / f"{name}.json"
            j.write_text(o)
            out.append(j)
        return out

    def test_dedup_after_conversion_keeps_both(self):
        with tempfile.TemporaryDirectory() as td:
            a, b = self._files(td)
            rc, merged, err = _cli("taxjson_merge", [a, b])
            self.assertEqual(rc, 0, err)
            m = Path(td) / "merged.json"
            m.write_text(merged)
            rc, o, err = _cli("taxjson_sort", ["--dedup", m])
            self.assertEqual(rc, 0, err)
            self.assertEqual(len(json.loads(o)["transactions"]), 4)
            rc, c, err = _cli("taxjson_convert_currency", [m, "--to", "CAD"])
            self.assertEqual(rc, 0, err)
            self.assertEqual(sorted(json.loads(c)["metadata"][
                "source_brokers"].values()), ["generic:alpha", "generic:beta"])
            p = Path(td) / "c.json"
            p.write_text(c)
            rc, o, err = _cli("taxjson_sort", ["--dedup", p])
            self.assertEqual(rc, 0, err)
            self.assertEqual(len(json.loads(o)["transactions"]), 4)

    def test_stdin_too(self):
        doc = _book({"source_brokers": {"a.csv": "generic:alpha"}})
        rc, c, err = _cli("taxjson_convert_currency", ["--to", "CAD"],
                          stdin=json.dumps(doc))
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(c)["metadata"]["source_brokers"],
                         {"a.csv": "generic:alpha"})

    def test_none_written_when_the_input_has_none(self):
        rc, c, err = _cli("taxjson_convert_currency", ["--to", "CAD"],
                          stdin=json.dumps(_book({})))
        self.assertEqual(rc, 0, err)
        self.assertNotIn("source_brokers", json.loads(c)["metadata"])


class TestTJ1225UnexpectedMetadataShape(unittest.TestCase):
    SHAPES = ({"sources": 5}, {"sources": "x"}, {"sources": [1, None]},
              {"sources": [{"original_metadata": 5}]},
              {"sources": [{"original_metadata": {"source_accounts": 5}}]},
              {"source_accounts": ["x"]}, {"source_accounts": 5},
              {"source_accounts": {"a.csv": 5}},
              {"source_accounts": {"a.csv": [["nested"]]}},
              {"source_accounts": {"a.csv": [1, "b"]}},
              {"source_brokers": "x"}, {"source_brokers": ["x"]},
              {"source_brokers": {"a.csv": 5}}, "text", 5, [])

    def test_ignored_by_every_pass(self):
        for meta in self.SHAPES:
            for module, args in (("taxjson_sort", []),
                                 ("taxjson_sort", ["--dedup"]),
                                 ("taxjson_convert_currency",
                                  ["--to", "CAD"])):
                with self.subTest(meta=meta, module=module, args=args):
                    rc, o, err = _cli(module, args,
                                      stdin=json.dumps(_book(meta)))
                    self.assertEqual(rc, 0, err)
                    self.assertNotIn("Traceback", err)
                    self.assertEqual(len(json.loads(o)["transactions"]), 1)

    def test_good_entries_beside_a_bad_one_are_kept(self):
        from taxjson.bin.taxjson_sort import (source_accounts_of,
                                              source_brokers_of)
        self.assertEqual(source_accounts_of([{"source_accounts": {
            "a.csv": ["x", "y"], "b.csv": 5}}]), {"a.csv": ["x", "y"]})
        self.assertEqual(source_brokers_of([{"source_brokers": {
            "a.csv": "generic:a"}}, {"source_brokers": ["x"]}]),
            {"a.csv": "generic:a"})

    def test_a_file_input_too(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "book.json"
            p.write_text(json.dumps(_book({"sources": 5})))
            rc, _o, err = _cli("taxjson_sort", [p])
            self.assertEqual(rc, 0, err)


# =================================== TJ-1226 validate and corp_parent_qty
class TestTJ1226ValidateCorpParentQty(unittest.TestCase):
    def _validate(self, value):
        row = {"id": "T1", "action": "BUYSELL", "date": "2025-03-03",
               "date_settle": "2025-03-04", "time": "10:00:00",
               "symbol": "SYN.TO", "quantity": 10, "price": 10,
               "net_amount": 100, "currency": "CAD", "account": "U1234567",
               "type": "STOCK"}
        if value is not self:
            row["corp_parent_qty"] = value
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "book.json"
            p.write_text(json.dumps({"transactions": [row]}))
            return _cli("taxjson_validate", [p])

    def test_malformed_is_an_error(self):
        for value in ("not-a-number", True, None, [1]):
            with self.subTest(value=value):
                rc, o, err = self._validate(value)
                self.assertNotEqual(rc, 0, o + err)
                self.assertIn("corp_parent_qty", o + err)

    def test_a_good_or_absent_one_passes(self):
        for value in (100, 0, 2.5, self):
            with self.subTest(value=value):
                rc, o, err = self._validate(value)
                self.assertEqual(rc, 0, o + err)

    def test_a_tt_parent_qty_stays_valid(self):
        # The .tt reader's `parent_qty=` token is the other way this field
        # reaches a book: it must validate as written.
        row = CT.parse_tt_line("ADJUST 2025-03-03 09:30:00 SYN.TO CAD -10 "
                               "corp=E1 parent_qty=100", "margin")
        self.assertIsInstance(row["corp_parent_qty"], float)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "book.json"
            p.write_text(json.dumps({"transactions": [row]}))
            rc, o, err = _cli("taxjson_validate", [p])
        self.assertNotIn("corp_parent_qty", o + err)
        self.assertEqual(rc, 0, o + err)


# ================================= TJ-1236 the placeholder in a file URL
class TestTJ1236FileUrlPlaceholder(unittest.TestCase):
    def test_second_pass_changes_nothing(self):
        for comment in ("# see file://<redacted path>",
                        "# see FILE://<redacted path>",
                        "# see file:<redacted path>",
                        '# was:"<redacted path>"'):
            with self.subTest(comment=comment):
                self.assertEqual(R._redact_comment_paths(comment),
                                 (comment, 0))

    def test_every_first_pass_is_stable(self):
        for comment in ("# see file:///srv/casey/taxes/inputs",
                        "# see FILE:///srv/casey/taxes/inputs",
                        "# see file://server/share/taxes",
                        "# see file:/srv/casey/taxes",
                        "# see file:///C:/data/casey/taxes"):
            with self.subTest(comment=comment):
                once, n = R._redact_comment_paths(comment)
                self.assertEqual(n, 1)
                self.assertEqual(R._redact_comment_paths(once), (once, 0))

    def test_a_real_share_is_still_replaced(self):
        self.assertEqual(
            R._redact_comment_paths("# see file://server/share/taxes"),
            ("# see file:<redacted path>", 1))
        # A server whose name merely starts as the placeholder does.
        self.assertEqual(
            R._redact_comment_paths("# see file://<redacted/share/x"),
            ("# see file:<redacted path>", 1))

    def test_the_copy_checks_clean(self):
        with tempfile.TemporaryDirectory() as td:
            year = Path(td) / "2025"
            year.mkdir()
            (year / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'inputs_dir = "../inputs"\n'
                '# see file:///srv/casey/taxes/inputs\n'
                '[accounts.margin]\nbroker = "tt"\n')
            (year / "ticker.map").write_text("")
            inputs = Path(td) / "inputs" / "margin"
            inputs.mkdir(parents=True)
            (inputs / "trades.tt").write_text(
                "BUYSELL 2025-01-06 10:00:00 SYN.TO 10 CAD 10 100\n"
                "BUYSELL 2025-02-06 10:00:00 SYN.TO -10 CAD 20 200\n")
            tj(year, "redact", "--no-denylist")
            copy = year / "inputs_redact"
            self.assertIn("file://<redacted path>",
                          (copy / "taxjson.toml").read_text())
            r = tj(copy, "redact", "--check", "--no-denylist", check=False)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


# ================================== TJ-1237 / TJ-1238 fee-only $0 rows
class _FeeOnly:
    SYM = "SYN251231P00100000.TO"
    HEAD = "Date,Action,Symbol,Qty,Price,Amount,Fee,Currency\n"

    def _parse(self, *rows, options=""):
        return _parse(self.HEAD + "".join(r + "\n" for r in rows),
                      _mapping(_AMOUNT_FEE, _CHARGED, options))[0]


class TestTJ1237ShareBuyAtAStatedZero(_FeeOnly, unittest.TestCase):
    def test_a_share_buy_is_refused(self):
        with self.assertRaisesRegex(ValueError, "not less than the buy"):
            self._parse("2025-01-06,B,SYN.TO,100,0,5,5,CAD",
                        "2025-03-06,S,SYN.TO,-100,10,995,5,CAD")

    def test_a_short_cover_of_shares_is_refused_too(self):
        with self.assertRaisesRegex(ValueError, "not less than the buy"):
            self._parse("2025-01-06,S,SYN.TO,-100,10,995,5,CAD",
                        "2025-03-06,B,SYN.TO,100,0,-5,5,CAD")

    def test_a_us_listing_too(self):
        with self.assertRaisesRegex(ValueError, "not less than the buy"):
            self._parse("2025-01-06,B,SYN.US,100,0,5,5,USD")

    def test_the_option_expiry_stays_accepted(self):
        rows = self._parse(f"2025-01-06,S,{self.SYM},-1,1,100,0,CAD",
                           f"2025-12-31,B,{self.SYM},1,0,1,1,CAD")
        self.assertEqual(rows[1]["net_amount"], 1.0)
        self.assertEqual(rows[1]["date_settle"], "2025-12-31")

    def test_a_mini_option_too(self):
        rows = _parse(self.HEAD
                      + f"2025-01-06,S,{self.SYM},-1,1,10,0,CAD\n"
                      + f"2025-12-31,B,{self.SYM},1,0,1,1,CAD\n",
                      _mapping(_AMOUNT_FEE, _CHARGED),
                      ticker_map=f"MULT {self.SYM} 10\n")[0]
        self.assertEqual(rows[1]["net_amount"], 1.0)

    def test_a_worthless_share_sale_stays_accepted(self):
        rows = self._parse("2025-01-06,B,SYN.TO,10,10,100,0,CAD",
                           "2025-03-06,S,SYN.TO,-10,0,-1,1,CAD")
        self.assertEqual(rows[1]["net_amount"], -1.0)


class TestTJ1238FeeOnlyRowIsNoCashSignEvidence(_FeeOnly, unittest.TestCase):
    OTHER = "2025-01-06,B,ZZZ.TO,1,100,{},0,CAD"

    def _file(self, other, amount):
        return self._parse(self.OTHER.format(other),
                           f"2025-01-07,S,{self.SYM},-1,1,99,1,CAD",
                           f"2025-12-31,B,{self.SYM},1,0,{amount},1,CAD")

    @rule("CA-DATE-08")
    def test_positive_file_negative_expiry(self):
        rows = self._file("100", "-1")
        self.assertEqual(rows[2]["net_amount"], 1.0)
        self.assertEqual(rows[2]["date"], "2025-12-31")
        self.assertEqual(rows[2]["date_settle"], "2025-12-31")
        g = _gains([r for r in rows if "ZZZ" not in r["symbol"]], 2025)
        self.assertAlmostEqual(g["summary"]["total_gain"], 98.0)

    def test_cash_signed_file_positive_expiry(self):
        rows = self._file("-100", "1")
        self.assertEqual(rows[2]["net_amount"], 1.0)
        self.assertEqual(rows[2]["date_settle"], "2025-12-31")

    def test_every_sign_agrees_with_the_blank_form(self):
        blank = self._file("100", "")[2]
        for other, amount in (("100", "1"), ("100", "-1"), ("-100", "1"),
                              ("-100", "-1")):
            with self.subTest(other=other, amount=amount):
                row = self._file(other, amount)[2]
                self.assertEqual(row["net_amount"], blank["net_amount"])
                self.assertEqual(row["date_settle"], blank["date_settle"])

    def test_the_no_fee_column_form(self):
        rows = _parse("Date,Action,Symbol,Qty,Price,Amount,Currency\n"
                      "2025-01-06,B,ZZZ.TO,1,100,100,CAD\n"
                      f"2025-01-07,S,{self.SYM},-1,1,99,CAD\n"
                      f"2025-12-31,B,{self.SYM},1,0,-1,CAD\n",
                      _mapping(_AMOUNT))[0]
        self.assertEqual(rows[2]["net_amount"], 1.0)
        self.assertEqual(rows[2]["date_settle"], "2025-12-31")

    def test_a_real_sale_under_a_buy_action_is_still_refused(self):
        with self.assertRaisesRegex(ValueError, "POSITIVE amount"):
            self._parse("2025-01-06,B,ZZZ.TO,1,100,-100,0,CAD",
                        "2025-01-07,B,YYY.TO,1,100,99,1,CAD")

    def test_a_priced_option_buy_is_still_evidence(self):
        with self.assertRaisesRegex(ValueError, "POSITIVE amount"):
            self._parse("2025-01-06,B,ZZZ.TO,1,100,-100,0,CAD",
                        f"2025-01-07,B,{self.SYM},1,1,101,1,CAD")


# ======================================== TJ-1239 MULT on a low price
class TestTJ1239LowPricedRowAtTheDeclaredSize(unittest.TestCase):
    SYM = "QZX1251219C00005000.TO"
    HEAD = "Date,Action,Symbol,Qty,Price,Amount,Currency\n"

    def _one(self, row, mult="50", options=""):
        return _parse(self.HEAD + row + "\n",
                      _mapping(_AMOUNT, options=options),
                      ticker_map=f"MULT QZX1 {mult}\n")[0][0]

    def test_a_sale_at_50(self):
        row = self._one(f"2025-02-06,S,{self.SYM},-1,0.2,10,CAD")
        self.assertEqual(row["net_amount"], 10.0)
        self.assertEqual(row["gross_amount"], 10.0)
        self.assertEqual(row["fee"], 0.0)
        self.assertEqual(row["multiplier"], 50.0)
        self.assertEqual(row["contract_size_basis"], "declared")

    def test_a_buy_at_1000(self):
        row = self._one(f"2025-01-06,B,{self.SYM},1,0.01,10,CAD", "1000")
        self.assertEqual(row["net_amount"], 10.0)
        self.assertEqual(row["fee"], 0.0)
        self.assertEqual(row["multiplier"], 1000.0)

    def test_a_sale_and_a_buy_agree(self):
        rows = _parse(self.HEAD
                      + f"2025-01-06,B,{self.SYM},1,0.2,10,CAD\n"
                      + f"2025-02-06,S,{self.SYM},-1,0.2,10,CAD\n",
                      _mapping(_AMOUNT),
                      ticker_map="MULT QZX1 50\n")[0]
        self.assertEqual([r["multiplier"] for r in rows], [50.0, 50.0])

    def test_an_exact_100_stays_100(self):
        row = self._one(f"2025-01-06,B,{self.SYM},1,0.05,5,CAD")
        self.assertEqual(row["fee"], 0.0)
        self.assertNotIn("multiplier", row)

    def test_a_small_commission_at_100_stays_100(self):
        # 2.00 x 100 less 5 of commission (under the 5% limit): not x 97.5.
        row = self._one(f"2025-02-06,S,{self.SYM},-1,2,195,CAD", "97.5")
        self.assertEqual(row["fee"], 5.0)
        self.assertNotIn("multiplier", row)

    def test_a_row_fitting_no_size_is_still_refused(self):
        with self.assertRaisesRegex(ValueError, "of the gross"):
            self._one(f"2025-02-06,S,{self.SYM},-1,0.2,12,CAD")

    def test_a_fee_column_of_zero_is_unchanged(self):
        rows = _parse("Date,Action,Symbol,Qty,Price,Amount,Fee,Currency\n"
                      f"2025-02-06,S,{self.SYM},-1,0.2,10,0,CAD\n",
                      _mapping(_AMOUNT_FEE),
                      ticker_map="MULT QZX1 50\n")[0]
        self.assertEqual(rows[0]["multiplier"], 50.0)

    def test_no_mult_line_is_unchanged(self):
        with self.assertRaisesRegex(ValueError, "of the gross"):
            _parse(self.HEAD + f"2025-02-06,S,{self.SYM},-1,0.2,10,CAD\n",
                   _mapping(_AMOUNT))


# =================================== TJ-1240 a blank fee cell on an expiry
class TestTJ1240BlankFeeCellOnAnExpiry(unittest.TestCase):
    SYM = "SYN251231P00100000.TO"

    def _rows(self, amount="-1", fee="", day="2025-12-31", target="S",
              qty="-1", opening=""):
        opening = opening or f"2025-01-06,B,{self.SYM},1,1,100,,CAD"
        return _parse("Date,Action,Symbol,Qty,Price,Amount,Fee,Currency\n"
                      f"{opening}\n"
                      f"{day},{target},{self.SYM},{qty},0,{amount},{fee},CAD\n",
                      _mapping(_AMOUNT_FEE, _CHARGED))[0]

    @rule("CA-DATE-08")
    def test_the_loss_is_in_the_expiry_year(self):
        rows = self._rows()
        self.assertEqual(rows[1]["date_settle"], "2025-12-31")
        self.assertEqual(rows[1]["net_amount"], -1.0)
        self.assertEqual(rows[1]["fee"], 1.0)
        self.assertAlmostEqual(_gains(rows, 2025)["summary"]["total_gain"],
                               -101.0)
        self.assertAlmostEqual(_gains(rows, 2026)["summary"]["total_gain"],
                               0.0)

    def test_agrees_with_the_no_fee_column_form(self):
        no_col = _parse("Date,Action,Symbol,Qty,Price,Amount,Currency\n"
                        f"2025-01-06,B,{self.SYM},1,1,100,CAD\n"
                        f"2025-12-31,S,{self.SYM},-1,0,-1,CAD\n",
                        _mapping(_AMOUNT))[0]
        got = self._rows()
        for k in ("date", "date_settle", "net_amount", "fee"):
            self.assertEqual(got[1][k], no_col[1][k], k)

    def test_a_buy_to_close(self):
        rows = self._rows(amount="-1", target="B", qty="1",
                          opening=f"2025-01-06,S,{self.SYM},-1,1,100,,CAD")
        self.assertEqual(rows[1]["date_settle"], "2025-12-31")
        self.assertEqual(rows[1]["net_amount"], 1.0)

    def test_cash_received_stays_proceeds(self):
        rows = self._rows(amount="1")
        self.assertEqual(rows[1]["net_amount"], 1.0)
        self.assertEqual(rows[1]["fee"], 0.0)

    def test_a_stated_fee_of_zero_is_believed(self):
        rows = self._rows(fee="0")
        self.assertEqual(rows[1]["fee"], 0.0)

    def test_a_stated_fee_keeps_its_figures(self):
        rows = self._rows(fee="1")
        self.assertEqual(rows[1]["net_amount"], -1.0)
        self.assertEqual(rows[1]["fee"], 1.0)

    def test_more_than_a_commission_is_not_read_as_one(self):
        rows = self._rows(amount="-400", target="B", qty="1",
                          opening=f"2025-01-06,S,{self.SYM},-1,5,500,,CAD")
        self.assertEqual(rows[1]["fee"], 0.0)
        self.assertEqual(rows[1]["net_amount"], 400.0)

    def test_before_the_expiry_is_an_ordinary_row(self):
        rows = self._rows(day="2025-12-15", amount="1")
        self.assertEqual(rows[1]["fee"], 0.0)
        self.assertNotEqual(rows[1]["date_settle"], "2025-12-15")

    def test_a_share_is_untouched(self):
        rows = _parse("Date,Action,Symbol,Qty,Price,Amount,Fee,Currency\n"
                      "2025-01-06,B,SYN.TO,10,10,100,,CAD\n"
                      "2025-12-30,S,SYN.TO,-10,0,1,,CAD\n",
                      _mapping(_AMOUNT_FEE, _CHARGED))[0]
        self.assertEqual(rows[1]["fee"], 0.0)


# ============================== TJ-1249 / TJ-1250 one chain, its securities
_CIL = q(td="2025-09-02", action="CIL", sym="D012345",
         desc="D012345 CASH IN LIEU OF .5", qty="0", price="0", gross="0",
         comm="0", net="4", cur="CAD", act="Dividends", atype="Cash")


def _chain(*rows):
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "qt.csv"
        f.write_text(QH + QT_BUY + "".join(rows), encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            evs = CA.parse_questrade_corporate_actions(f, "margin")
    return evs, " ".join(err.getvalue().split())


def _got(evs):
    return sorted((e.target_symbol, e.qty_received) for e in evs)


class TestTJ1249NoLegDisappears(unittest.TestCase):
    def test_a_reversal_under_a_third_symbol_offsets_its_posting(self):
        evs, _ = _chain(qt_dis("SPNA", 10), qt_dis("SPNV", 5, "SPNV WT"),
                        qt_dis("SPNW", -5, "SPNW WT"))
        self.assertEqual(_got(evs), [("SPNA.TO", 10)])

    def test_a_code_posting_cancelled_under_the_ticker(self):
        evs, _ = _chain(qt_dis("SPNA", 10), qt_dis("D012345", 5, "SPNW WT"),
                        qt_dis("SPNW", -5, "SPNW WT"))
        self.assertEqual(_got(evs), [("SPNA.TO", 10)])

    def test_two_live_beside_a_cancelled_pair(self):
        evs, _ = _chain(qt_dis("SPNA", 10), qt_dis("SPNB", 7, "SPNB CORP"),
                        qt_dis("SPNV", 5, "SPNV WT"),
                        qt_dis("SPNW", -5, "SPNW WT"))
        self.assertEqual(_got(evs), [("SPNA.TO", 10), ("SPNB.TO", 7)])

    def test_a_net_removal_still_warns(self):
        evs, err = _chain(qt_dis("SPNA", 10), qt_dis("SPNW", 5, "SPNW WT"),
                          qt_dis("SPNX", -20, "SPNX WT"))
        self.assertEqual(evs, [])
        self.assertIn("UNBOOKED", err)
        self.assertIn("nets -5 units", err)

    def test_a_take_back_nothing_explains_is_refused(self):
        evs, err = _chain(qt_dis("SPNA", 10), qt_dis("SPNB", 7, "SPNB CORP"),
                          qt_dis("SPNX", -2, "SPNX WT"))
        self.assertEqual(evs, [])
        self.assertIn("UNBOOKED", err)
        for sym in ("SPNA", "SPNB", "SPNX"):
            self.assertIn(sym, err)

    def test_an_ambiguous_take_back_is_refused(self):
        evs, err = _chain(qt_dis("SPNA", 5), qt_dis("SPNB", 5, "SPNB CORP"),
                          qt_dis("SPNC", 5, "SPNC CORP"),
                          qt_dis("SPNX", -5, "SPNX WT"))
        self.assertEqual(evs, [])
        self.assertIn("UNBOOKED", err)

    def test_units_add_up(self):
        for rows in ((("SPNA", 10), ("SPNV", 5), ("SPNW", -5)),
                     (("SPNA", 10), ("SPNB", 7), ("SPNV", 5), ("SPNW", -5)),
                     (("SPNA", 10), ("SPNA", -10), ("SPNB", 10), ("SPNW", 5),
                      ("SPNW", -5))):
            with self.subTest(rows=rows):
                evs, _ = _chain(*[qt_dis(s, n, f"{s} CORP") for s, n in rows])
                self.assertEqual(sum(e.qty_received for e in evs),
                                 sum(n for _s, n in rows))

    def test_a_reversed_securitys_cash_in_lieu_is_named(self):
        evs, err = _chain(qt_dis("D012345", 10, "SPNB CORP"),
                          qt_dis("D012345", -10, "SPNB CORP"),
                          qt_dis("SPNB", 10, "SPNB CORP"),
                          qt_dis("SPNW", 5, "SPNW WT"), _CIL)
        self.assertEqual(_got(evs), [("SPNB.TO", 10), ("SPNW.TO", 5)])
        self.assertIn("cash in lieu", err)
        self.assertIn("D012345", err)
        self.assertIn("4.00", err)

    def test_no_cash_no_message(self):
        _evs, err = _chain(qt_dis("D012345", 10, "SPNB CORP"),
                           qt_dis("D012345", -10, "SPNB CORP"),
                           qt_dis("SPNB", 10, "SPNB CORP"),
                           qt_dis("SPNW", 5, "SPNW WT"))
        self.assertNotIn("cash in lieu", err)
        self.assertNotIn("UNBOOKED", err)

    def test_one_live_keeps_the_whole_chain_and_its_cash(self):
        evs, err = _chain(qt_dis("D012345", 10, "SPN CORP"),
                          qt_dis("D012345", -10, "SPN CORP"),
                          qt_dis("SPNB", 10, "SPN CORP"), _CIL)
        self.assertEqual(sum(e.qty_received for e in evs), 10)
        self.assertEqual(evs[0].cash_in_lieu, 4.0)
        self.assertNotIn("UNBOOKED", err)

    def test_a_lone_take_back_still_nets(self):
        evs, _ = _chain(qt_dis("SPNA", 10), qt_dis("SPNX", -3, "SPNX WT"))
        self.assertEqual(_got(evs), [("SPNA.TO", 7)])

    def test_everything_taken_back_is_nothing(self):
        evs, err = _chain(qt_dis("SPNA", 10), qt_dis("SPNB", 5, "SPNB CORP"),
                          qt_dis("SPNX", -15, "SPNX WT"))
        self.assertEqual(evs, [])
        self.assertNotIn("UNBOOKED", err)


class TestTJ1250PlaceholderWithFewLiveSymbols(unittest.TestCase):
    def test_two_placeholders_two_reversed_symbols_are_refused(self):
        evs, err = _chain(qt_dis("SPNA", 10), qt_dis("SPNA", -10),
                          qt_dis("", 10, "PENDING"),
                          qt_dis("SPNW", 10, "SPNW WT"),
                          qt_dis("SPNW", -10, "SPNW WT"),
                          qt_dis("", 10, "PENDING"))
        self.assertEqual(evs, [])
        self.assertIn("UNBOOKED", err)
        self.assertIn("SPNA", err)
        self.assertIn("SPNW", err)

    def test_a_placeholder_and_its_reversal_cancel(self):
        evs, err = _chain(qt_dis("", 5, "PENDING"), qt_dis("", -5, "PENDING"),
                          qt_dis("SPNW", 5, "SPNW WT"), qt_dis("SPNA", 10))
        self.assertEqual(_got(evs), [("SPNA.TO", 10), ("SPNW.TO", 5)])
        self.assertNotIn("UNBOOKED", err)

    def test_different_quantities_still_split(self):
        evs, _ = _chain(qt_dis("SPNA", 10), qt_dis("SPNA", -10),
                        qt_dis("", 10, "PENDING"),
                        qt_dis("SPNW", 5, "SPNW WT"),
                        qt_dis("SPNW", -5, "SPNW WT"),
                        qt_dis("", 5, "PENDING"))
        self.assertEqual(_got(evs), [("SPNA.TO", 10), ("SPNW.TO", 5)])

    def test_a_cancelled_code_with_a_placeholder_pair_stays_one_event(self):
        evs, err = _chain(qt_dis("", 10, "PENDING"),
                          qt_dis("", -10, "PENDING"),
                          qt_dis("D012345", 10, "SPN CORP"),
                          qt_dis("D012345", -10, "SPN CORP"),
                          qt_dis("SPNA", 10, "SPN CORP"))
        self.assertEqual(sum(e.qty_received for e in evs), 10)
        self.assertEqual(len(evs), 1)
        self.assertNotIn("UNBOOKED", err)

    def test_one_live_and_an_unexplained_placeholder_is_refused(self):
        evs, err = _chain(qt_dis("SPNA", 10), qt_dis("SPNW", 5, "SPNW WT"),
                          qt_dis("SPNW", -5, "SPNW WT"),
                          qt_dis("", 7, "PENDING"))
        self.assertEqual(evs, [])
        self.assertIn("UNBOOKED", err)

    def test_one_symbol_chains_are_untouched(self):
        evs, _ = _chain(qt_dis("", 10, "PENDING"), qt_dis("", -10, "PENDING"),
                        qt_dis("SPNA", 10))
        self.assertEqual(_got(evs), [("SPNA.TO", 10)])

    def test_a_removal_through_a_placeholder_still_says_removal(self):
        evs, err = _chain(qt_dis("SPNA", 10), qt_dis("SPNB", -10, "SPNB CORP"),
                          qt_dis("", -5, "PENDING"))
        self.assertEqual(evs, [])
        self.assertIn("UNBOOKED", err)


if __name__ == "__main__":
    unittest.main()
