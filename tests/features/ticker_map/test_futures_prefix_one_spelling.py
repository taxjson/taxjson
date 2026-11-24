"""One spelling for a future (backlog TJ-5150).

A future may be written `F:QZM26.US`, `/QZM26.US` or `\\QZM26.US`; the
brokers' parsers write `F:`. A .tt line, a ticker.map or tobase.map line
(every keyword), a holdings file, a hand-made JSON book (TJ-5160) and a
command's symbol argument kept the spelling typed: a .tt `/QZM26.US` was a pool apart from the broker's
`F:QZM26.US`, its row id hashed from the `/`, and a ticker.map line
naming `/QZ.US` never applied to an `F:` row. Every reader now spells
the future `F:` (lib/futures_prefixes `canonical_futures_symbol`), the
underlying of an option on a future included; a symbol with no leading
futures prefix is left as written. A closed year's record (filed/YEAR.json)
an older taxjson wrote with `/` is read `F:` too, and a `/` or `\\`
symbol that names no contract once folded (`//QZM26.US`, `/F:QZM26.US`,
`/.US`) is refused, naming its line. Synthetic data only.
"""
import dataclasses
import json
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.tt_format import compute_tt_id, parse_tt_line, tt_to_json
from taxjson.model.transaction import TaxTransaction
from tax_rules.dual import cli
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


SLASHES = ("/", "\\")
OPT = "QZ260620C00010000.US"        # a call on the future QZ.US
_FIELDS = {f.name for f in dataclasses.fields(TaxTransaction)}


def _line(sym, date="2025-03-03", qty=1, price="1.00", net="100"):
    return f"BUYSELL {date} 10:00:00 {sym} {qty} USD {price} {net} 0"


def _tt(text, name="hand.tt"):
    """The rows tt_to_json reads from `text` (a folder of its own)."""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / name
        p.write_text(text, encoding="utf-8")
        return tt_to_json(p, "margin", equity=True)["transactions"]


def _tx(row, **kw):
    return TaxTransaction(**{**{k: v for k, v in row.items()
                                if k in _FIELDS and k != "id"}, **kw})


class TestTtLinesReadF(unittest.TestCase):
    """The .tt reader: the symbol and the id of the `F:` spelling."""

    def test_three_spellings_one_symbol(self):
        # The repro: one contract's lines spelled `/`, `\` and `F:` were
        # three symbols, so three pools.
        rows = _tt("".join(_line(p + OPT, date=d) + "\n" for p, d in (
            ("/", "2025-03-03"), ("\\", "2025-03-04"), ("F:", "2025-03-05"))))
        self.assertEqual({r["symbol"] for r in rows}, {"F:" + OPT})
        # One id per content: each row's id is the one its `F:` line has.
        for r, d in zip(rows, ("2025-03-03", "2025-03-04", "2025-03-05")):
            self.assertEqual(r["id"],
                             parse_tt_line(_line("F:" + OPT, date=d), "margin")["id"])

    def test_the_id_is_the_F_spelling_id(self):
        f_line = parse_tt_line(_line("F:QZM26.US"), "margin")
        for pre in SLASHES:
            with self.subTest(prefix=pre):
                row = parse_tt_line(_line(pre + "QZM26.US"), "margin")
                self.assertEqual(row["symbol"], "F:QZM26.US")
                self.assertEqual(row["id"], f_line["id"])
                # Not the id the spelling typed hashed to before.
                self.assertNotEqual(row["id"], compute_tt_id(
                    dict(row, symbol=pre + "QZM26.US")))

    def test_an_option_on_a_future_in_each_spelling(self):
        from taxjson.lib.contract_size import option_contract_size
        from taxjson.lib.option_symbols import parse_option_underlying
        for pre in ("F:",) + SLASHES:
            with self.subTest(prefix=pre):
                row = parse_tt_line(_line(pre + OPT), "margin")
                self.assertEqual(row["symbol"], "F:" + OPT)
                self.assertEqual(parse_option_underlying(row["symbol"]), "F:QZ.US")
                # One contract of the future per option, whatever the spelling.
                self.assertEqual(option_contract_size(_tx(row)), 1.0)

    def test_a_refused_rename_shows_F_and_quotes_the_line(self):
        from taxjson.lib.tt_format import parse_rename_line
        for pre in SLASHES:
            with self.subTest(prefix=pre):
                line = f"RENAME 2025-06-02 {pre}QZM26.US {pre}QXM26.US"
                with self.assertRaises(ValueError) as cm:
                    parse_rename_line(line, "hand.tt")
                self.assertIn("a future (F:QZM26.US) into a future (F:QXM26.US)",
                              str(cm.exception))
                self.assertIn(repr(line), str(cm.exception))

    def test_other_symbols_are_left_as_written(self):
        from taxjson.lib.futures_prefixes import canonical_futures_symbol
        # A slash-like character anywhere but first, a share, a coin, a
        # prefix with nothing after it: no future.
        for s in ("ZZB/U.TO", "ZZQ\\X.US", "XYZ.TO", "BTC", "/", "\\", "",
                  "F:QZM26.US", " /QZM26.US"):
            with self.subTest(symbol=s):
                self.assertEqual(canonical_futures_symbol(s), s)
        self.assertIsNone(canonical_futures_symbol(None))
        # A share's .tt row keeps its symbol and its id.
        row = parse_tt_line(_line("XYZ.US"), "margin")
        self.assertEqual(row["symbol"], "XYZ.US")
        self.assertEqual(row["id"], compute_tt_id(row))


class TestPrefixOnlyDifferentLines(unittest.TestCase):
    """Two .tt lines that differ only in the prefix spelling are one
    content: treated as two identical lines are (the split-fill rule,
    test_tt_identical_lines_distinct_ids; across files, the
    dedup plan's rule, test_cross_file_dedup)."""

    def test_in_one_file_both_are_booked_as_fills(self):
        from taxjson.bin.taxjson_sort import deduplicate
        for pre in SLASHES:
            with self.subTest(prefix=pre):
                rows = _tt(_line(pre + "QZM26.US") + "\n"
                           + _line("F:QZM26.US") + "\n")
                self.assertEqual(len(rows), 2)
                self.assertEqual(rows[0]["id"],
                                 parse_tt_line(_line("F:QZM26.US"), "margin")["id"])
                self.assertIn("[fill #2]", rows[1]["description"])
                self.assertNotEqual(rows[0]["id"], rows[1]["id"])
                self.assertEqual(len(deduplicate([_tx(r) for r in rows])), 2)

    def test_in_two_files_both_are_booked_with_attention(self):
        from taxjson.bin.taxjson_sort import plan_dedup
        a = _tt(_line("/QZM26.US") + "\n", "m1.tt")
        b = _tt(_line("F:QZM26.US") + "\n", "m2.tt")
        rows = [_tx(a[0], source="m1.tt"), _tx(b[0], source="m2.tt")]
        self.assertEqual(rows[0].id, rows[1].id)
        plan = plan_dedup(rows)
        self.assertEqual(plan.keep, [0, 1])
        self.assertEqual(len(plan.attention), 1)
        self.assertIn("m1.tt and m2.tt", plan.attention[0])


_RENAMES = ("GLOBAL {p}QZ.US {p}QX.US\n"
            "TOBASE {p}QA.US {p}QA.TO\n"
            "JOURNAL {p}QB.US {p}QB.TO\n"
            "DELETE {p}QC.US\n"
            "DISTINCT {p}QD.US {p}QD.TO\n"
            "RENAME {p}QE.US {p}QF.US\n")
_LOOKUPS = ("QUOTE {p}QG.US QGZ\n"
            "T1135 {p}QH.US USA\n"
            "MULT {p}QI 50\n"
            "MULT {p}QJM26.US 20\n"
            "EXTRACT qz june future | USD | {p}QKM26.US\n"
            "CRYPTO {p}QL ZZCOIN\n"
            "STABLE {p}QM USD\n")


class TestMapLinesReadF(unittest.TestCase):
    """A ticker.map (or tobase.map) line written `/` or `\\` names the
    `F:` row: of a broker's parser and of a .tt line alike."""

    def _map(self, td, text):
        p = Path(td) / "ticker.map"
        p.write_text(text, encoding="utf-8")
        return p

    def test_each_rename_keyword(self):
        from taxjson.bin.taxjson_ticker_map import apply_drops, apply_mapping
        from taxjson.lib.map_file import load_map_file, map_file_problems
        from taxjson.lib.ticker_map_rules import merge_renames
        for pre in SLASHES:
            with self.subTest(prefix=pre), tempfile.TemporaryDirectory() as td:
                p = self._map(td, _RENAMES.format(p=pre))
                self.assertEqual(map_file_problems(p), [])
                tm = load_map_file(p)
                ren = merge_renames(tm, to_base=True)
                for frm, to in (("F:QZ.US", "F:QX.US"), ("F:" + OPT, "F:QX" + OPT[2:]),
                                ("F:QA.US", "F:QA.TO"), ("F:QB.US", "F:QB.TO"),
                                ("F:QE.US", "F:QF.US")):
                    self.assertEqual(apply_mapping(TaxTransaction(
                        action="BUYSELL", date="2025-03-03", symbol=frm,
                        quantity=1, price=1.0, net_amount=100.0, currency="USD",
                        account="margin"), ren).symbol, to)
                self.assertEqual(set(tm.delete), {"F:QC.US"})
                self.assertIn(frozenset({"F:QD.US", "F:QD.TO"}),
                              {frozenset(d) for d in tm.distinct})
                # A .tt row spelled the other way is the same row.
                tt = [_tx(r) for r in _tt(
                    _line("\\QZ.US" if pre == "/" else "/QZ.US") + "\n"
                    + _line(pre + "QC.US", date="2025-03-04") + "\n")]
                self.assertEqual([t.symbol for t in apply_drops(tt, tm.delete)],
                                 ["F:QZ.US"])
                self.assertEqual(apply_mapping(tt[0], ren).symbol, "F:QX.US")

    def test_each_lookup_keyword(self):
        from taxjson.lib import markets
        from taxjson.lib.ticker_map import side_rules_from_text
        for pre in SLASHES:
            with self.subTest(prefix=pre), tempfile.TemporaryDirectory() as td:
                text = _LOOKUPS.format(p=pre)
                side = side_rules_from_text(text)
                self.assertEqual(side.problems, [])
                self.assertEqual(side.quote, {"F:QG.US": ("QGZ", 1.0)})
                self.assertEqual(side.t1135, {"F:QH.US": "USA"})
                self.assertEqual(side.mult, {"F:QI": 50.0, "F:QJM26.US": 20.0})
                self.assertEqual([e[2] for e in side.extract], ["F:QKM26.US"])
                self.assertEqual(set(side.crypto), {"F:QL"})
                self.assertEqual(set(side.stable), {"F:QM"})
                # MULT's contract size reaches an `F:` option and future.
                markets.use_ticker_map(self._map(td, text))
                try:
                    self.assertEqual(markets.option_contract_size(
                        "F:QI260620C00010000.US", "F:QI.US"), 50.0)
                    self.assertEqual(markets.contract_size("F:QJM26.US"), 20.0)
                finally:
                    markets.use_ticker_map(None)

    def test_a_tobase_map_line(self):
        from taxjson.lib.ticker_map_rules import _parse_map_text, merge_renames
        for pre in SLASHES:
            with self.subTest(prefix=pre):
                tm, problems, _notes = _parse_map_text(
                    f"TOBASE {pre}QA.US {pre}QA.TO\n", "tobase.map")
                self.assertEqual(problems, [])
                self.assertEqual(merge_renames(tm, to_base=True),
                                 {"F:QA.US": "F:QA.TO"})

    def test_two_spellings_of_one_key_contradict_loudly(self):
        from taxjson.lib.map_file import map_file_problems
        from taxjson.lib.ticker_map import side_rules_from_text
        side = side_rules_from_text("MULT /QI 50\nMULT F:QI 100\n")
        self.assertEqual(len(side.problems), 1, side.problems)
        self.assertIn("MULT F:QI is given twice", side.problems[0])
        # The line is quoted as written.
        self.assertIn("'MULT F:QI 100'", side.problems[0])
        with tempfile.TemporaryDirectory() as td:
            probs = map_file_problems(self._map(
                td, "GLOBAL /QZ.US F:QX.US\nGLOBAL F:QZ.US F:QY.US\n"))
        self.assertEqual(len(probs), 1, probs)
        self.assertIn("ticker.map:2", probs[0])

    def test_a_refusal_shows_F_and_quotes_the_line(self):
        from taxjson.lib.ticker_map import side_rules_from_text
        side = side_rules_from_text("SIMILAR \\QZ.US ZZSP.US\n")
        self.assertEqual(len(side.problems), 1, side.problems)
        self.assertIn("SIMILAR symbol 'F:QZ.US' must be a share listing",
                      side.problems[0])
        self.assertIn(repr("SIMILAR \\QZ.US ZZSP.US"), side.problems[0])


class TestHoldingsFileReadsF(unittest.TestCase):

    def test_the_positions_reader(self):
        from taxjson.lib.positions_reports import read_positions
        for pre in SLASHES:
            with self.subTest(prefix=pre), tempfile.TemporaryDirectory() as td:
                p = Path(td) / "h.toml"
                # (a TOML literal string: a backslash is no escape there)
                p.write_text('schema_version = "1.0"\n[meta]\naccount = "ACCT1"\n\n'
                             f"[[holding]]\nsymbol = '{pre}QZM26.US'\nquantity = 1\n"
                             'asset_type = "equity"\n', encoding="utf-8")
                rows = read_positions(p).rows
                self.assertEqual([r.symbol for r in rows], ["F:QZM26.US"])
                self.assertEqual([r.raw_symbol for r in rows], [pre + "QZM26.US"])


def _json_row(sym, date="2025-01-06", qty=2.0, price=100.0, net=-200.0, **kw):
    return dict(action="BUYSELL", date=date, date_settle=date, time="10:00:00",
                symbol=sym, quantity=qty, price=price, net_amount=net,
                gross_amount=abs(net), currency="CAD", account="margin", **kw)


class TestJsonBookReadsF(unittest.TestCase):
    """A hand-made JSON book, read by any stand-alone tool through the
    one row funnel (lib/tx_load coerce_transaction_row): its futures are
    `F:` too (backlog TJ-5160)."""

    def test_the_row_funnel(self):
        from taxjson.lib.tx_load import coerce_transaction_row
        f_row = coerce_transaction_row(_json_row("F:QZM26.US"), 0, "book.json")
        for pre in SLASHES:
            with self.subTest(prefix=pre):
                tx = coerce_transaction_row(_json_row(pre + "QZM26.US"), 0, "book.json")
                self.assertEqual(tx.symbol, "F:QZM26.US")
                # An id computed from the content hashes the `F:` spelling.
                self.assertEqual(tx.id, f_row.id)
                # An id the row states is kept.
                given = coerce_transaction_row(
                    _json_row(pre + "QZM26.US", id="99900001aa"), 0, "book.json")
                self.assertEqual((given.symbol, given.id), ("F:QZM26.US", "99900001aa"))
                opt = coerce_transaction_row(dict(
                    _json_row(pre + OPT), symbol_new=pre + "QX" + OPT[2:],
                    exercise_of=pre + "QZM26.US"), 0, "book.json")
                self.assertEqual((opt.symbol, opt.symbol_new, opt.exercise_of),
                                 ("F:" + OPT, "F:QX" + OPT[2:], "F:QZM26.US"))
        share = coerce_transaction_row(_json_row("ZZB/U.TO"), 0, "book.json")
        self.assertEqual(share.symbol, "ZZB/U.TO")

    def test_one_pool_through_taxjson_gains(self):
        import io
        import sys
        from unittest.mock import patch
        from taxjson.bin.taxjson_gains import main
        rows = [_json_row("/QZM26.US"),
                _json_row("F:QZM26.US", date="2025-06-02", qty=-1.0, price=300.0,
                          net=300.0)]
        out = io.StringIO()
        with patch.object(sys, "argv", ["taxjson-gains", "--country", "canada", "--taxable"]), \
                patch.object(sys, "stdin", io.StringIO(json.dumps({"transactions": rows}))), \
                patch.object(sys, "stdout", out):
            main()
        doc = json.loads(out.getvalue())
        self.assertEqual([(i["symbol"], i["qty"]) for i in doc["inventory"]],
                         [("F:QZM26.US", 1.0)])
        self.assertEqual([(t["symbol"], t["qty"], t["gain"]) for t in doc["transactions"]
                          if "gain" in t], [("F:QZM26.US", 1.0, 200.0)])


_CSV = ("Date,Type,Ticker,Qty,Price,Amount,Currency\n"
        "2025-01-06,BUY,F:QZM26.US,2,100,-20000.00,USD\n")
_MAPPING = ('[columns]\ndate = "Date"\naction = "Type"\nsymbol = "Ticker"\n'
            'quantity = "Qty"\nprice = "Price"\namount = "Amount"\n'
            'currency = "Currency"\n[actions]\n"BUY" = "buy"\n"SELL" = "sell"\n')


class TestOnePoolInAProject(unittest.TestCase):
    """A .tt sale spelled `/` of the contract a broker export bought as
    `F:`: one symbol in `tjs trades`, and `tjs sanity` finds no
    difference against the broker's `F:` holding."""

    def test_tt_slash_row_beside_a_broker_F_row(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = ["USD"]\n'
                'local_timezone = "America/Toronto"\n'
                '[accounts.margin]\ntype = "taxable"\ncrypto = false\n')
            d = root / "inputs" / "margin"
            d.mkdir(parents=True)
            (d / "generic_2025.csv").write_text(_CSV)
            (d / "generic_2025.csv.toml").write_text(_MAPPING)
            (d / "hand.tt").write_text(
                "BUYSELL 2025-06-02 10:00:00 /QZM26.US -1 USD 300 30000 0 x100\n")
            r = cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
            r = cli(root, "trades", "--json")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            rows = json.loads(r.stdout)["rows"]
            self.assertEqual(sorted((x["source"], x["symbol"]) for x in rows),
                             [("generic_2025.csv", "F:QZM26.US"),
                              ("hand.tt", "F:QZM26.US")])
            (root / "h.toml").write_text(
                'schema_version = "1.0"\n[meta]\naccount = "ACCT1"\n\n'
                '[[holding]]\nsymbol = "F:QZM26.US"\nquantity = 1\n'
                'asset_type = "equity"\n')
            r = cli(root, "sanity", f"margin={root / 'h.toml'}", "--json")
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-2000:])
            doc = json.loads(r.stdout)
            self.assertTrue(doc["clean"], doc["discrepancies"])
            self.assertEqual(doc["discrepancies"], [])



_NO_CONTRACT = ("//QZM26.US", "/F:QZM26.US", "\\\\QZM26.US", "/.US", "\\.US")


class TestNoContractRefused(unittest.TestCase):
    """A `/` or `\\` symbol that names no contract once folded is refused,
    naming its line: `F:/QZM26.US`, `F:F:QZM26.US` or `F:.US` would each be
    a pool of their own."""

    def test_the_helper(self):
        from taxjson.lib.futures_prefixes import (FuturesSymbolError,
                                                  canonical_futures_symbol)
        for s in _NO_CONTRACT:
            with self.subTest(symbol=s), self.assertRaises(FuturesSymbolError) as cm:
                canonical_futures_symbol(s)
            self.assertIn(repr(s), str(cm.exception))
        with self.assertRaises(FuturesSymbolError) as cm:
            canonical_futures_symbol("/F:QZM26.US")
        self.assertIn("write it 'F:QZM26.US'", str(cm.exception))
        # An `F:` symbol is the books' own spelling: not judged here.
        self.assertEqual(canonical_futures_symbol("F:/QZM26.US"), "F:/QZM26.US")

    def test_a_tt_line(self):
        for sym in _NO_CONTRACT:
            with self.subTest(symbol=sym), self.assertRaises(ValueError) as cm:
                _tt("# a note\n" + _line(sym) + "\n")
            self.assertIn("hand.tt:2:", str(cm.exception))
            self.assertIn(repr(_line(sym)), str(cm.exception))
        with self.assertRaises(ValueError) as cm:
            _tt('ALLOWLOSS 2025-03-03 //QZM26.US reason="x"\n')
        self.assertIn("hand.tt:1:", str(cm.exception))

    def test_a_map_line_is_a_problem_and_its_rule_dropped(self):
        from taxjson.lib.ticker_map_rules import _parse_map_text
        for line in ("GLOBAL //QZ.US F:QX.US", "DELETE /.US",
                     "MULT /F:QI 50", "STABLE //QM USD", "T1135 //QH.US USA",
                     "QUOTE //QG.US QGZ", "CRYPTO //QL ZZCOIN",
                     "EXTRACT qz june future | USD | //QKM26.US"):
            with self.subTest(line=line):
                tm, problems, _notes = _parse_map_text("# rules\n" + line + "\n")
                self.assertEqual(len(problems), 1, problems)
                self.assertTrue(problems[0].startswith("ticker.map:2: "), problems[0])
                self.assertIn("not a futures symbol", problems[0])
                self.assertIn(repr(line), problems[0])
                self.assertEqual((tm.glob, set(tm.delete)), ({}, set()))
        # A free-text EXTRACT description may hold a `//` word.
        _tm, problems, _notes = _parse_map_text(
            "EXTRACT qz // june future | USD | /QKM26.US\n")
        self.assertEqual(problems, [])

    def test_the_other_readers_name_the_entry(self):
        from taxjson.lib import cg_dividends as CG
        from taxjson.lib.handoff import load_filed_dispositions
        from taxjson.lib.positions_reports import PositionsReportError, read_positions
        from taxjson.lib.project_tables import distribution_rows
        from taxjson.lib.tx_load import coerce_transaction_row
        with self.assertRaises(ValueError) as cm:
            coerce_transaction_row(_json_row("//QZM26.US"), 3, "book.json")
        self.assertIn("book.json: transaction at index 3", str(cm.exception))
        with self.assertRaises(CG.CgDividendMapError) as cm:
            CG.parse_map("ZZB.TO 2025 all\n/.US 2025 all\n")
        self.assertIn("line 2:", str(cm.exception))
        with self.assertRaises(CG.CgDividendMapError) as cm:
            CG.entries_from_config({CG.TABLE: [
                {"symbol": "//QZM26.US", "year": 2025, "amount": "all"}]})
        self.assertIn("#1:", str(cm.exception))
        _rows, problems, _w = distribution_rows({"distributions": [
            {"symbol": "//QZM26.US", "record_date": "2025-12-29", "per_share": 0.1}]})
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("#1:", problems[0])
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "h.toml"
            p.write_text('schema_version = "1.0"\n[meta]\naccount = "ACCT1"\n\n'
                         '[[holding]]\nsymbol = "//QZM26.US"\nquantity = 1\n',
                         encoding="utf-8")
            with self.assertRaises(PositionsReportError) as cm:
                read_positions(p)
            self.assertIn("[[holding]] #1", str(cm.exception))
            c = Path(td) / "filed.csv"
            c.write_text("symbol,date,qty,proceeds,cost,gain\n"
                         "//QZM26.US,2025-03-03,1,300,100,200\n", encoding="utf-8")
            with self.assertRaises(ValueError) as cm:
                load_filed_dispositions(c, 2025)
            self.assertIn("filed.csv:2:", str(cm.exception))

    def test_the_capital_gains_dividends_map_folds(self):
        from taxjson.lib import cg_dividends as CG
        for pre in SLASHES:
            with self.subTest(prefix=pre):
                self.assertEqual(CG.parse_map(f"{pre}QZM26.US 2025 all\n")[0].symbol,
                                 "F:QZM26.US")


def _lock_text(doc):
    return json.dumps(doc, indent=1)


class TestClosedYearRecordReadsF(unittest.TestCase):
    """A year an older taxjson closed holds a .tt line's `/QZM26.US` in its
    record (filed/YEAR.json); the next year's books spell it `F:`. Every
    reader of a lock folds the record's symbols as it reads them, so the
    hand-off compares one contract (lib/filed_locks fold_lock_symbols)."""

    CFG = {"settings": {"country": "canada", "year": 2026},
           "accounts": {"margin": {"type": "taxable"}}}

    def _record(self, td, doc):
        from taxjson.lib.filed_locks import load_lock_json
        p = Path(td) / "2025.json"
        p.write_text(_lock_text(doc), encoding="utf-8")
        return load_lock_json(p)

    def _work(self, td, base_rows, gains_rows):
        work = Path(td) / "work"
        work.mkdir()
        (work / "margin_base.json").write_text(json.dumps({"transactions": base_rows}))
        (work / "margin_gains_wash.json").write_text(
            json.dumps({"transactions": gains_rows}))

    def test_two_spellings_in_the_record_are_one_security(self):
        with tempfile.TemporaryDirectory() as td:
            rec = self._record(td, {"year": 2025, "year_end": {"equity": {
                "/QZM26.US": {"qty": 1.0, "acb": 100.0, "deferred": 0.0},
                "F:QZM26.US": {"qty": 2.0, "acb": 50.0, "deferred": 5.0},
                "ZZB/U.TO": {"qty": 3.0, "acb": 1.0, "deferred": 0.0}}},
                "year_end_lots": {"equity": {
                    "\\QZ.US": [{"qty": 1.0, "basis": 1.0, "date": "2025-03-01",
                                  "account": "a"}],
                    "F:QZ.US": [{"qty": 1.0, "basis": 2.0, "date": "2025-01-01",
                                 "account": "a"}]}},
                "year_end_shorts": {"equity": {"/QX.US": {"qty": -1.0, "acb": 9.0}}},
                "settle_next_year": [{"symbol": "/QZM26.US", "qty": 1.0}]})
        self.assertEqual(rec["year_end"]["equity"], {
            "F:QZM26.US": {"qty": 3.0, "acb": 150.0, "deferred": 5.0},
            "ZZB/U.TO": {"qty": 3.0, "acb": 1.0, "deferred": 0.0}})
        self.assertEqual([(x["date"], x["basis"]) for x in
                          rec["year_end_lots"]["equity"]["F:QZ.US"]],
                         [("2025-01-01", 2.0), ("2025-03-01", 1.0)])
        self.assertEqual(list(rec["year_end_shorts"]["equity"]), ["F:QX.US"])
        self.assertEqual(rec["settle_next_year"][0]["symbol"], "F:QZM26.US")

    def test_a_filed_sale_spelled_slash_is_this_year_s_F_sale(self):
        from taxjson.lib import handoff
        with tempfile.TemporaryDirectory() as td:
            self._work(td, [], [
                {"symbol": "F:QZM26.US", "date": "2026-01-02",
                 "date_settle": "2026-01-02", "qty": 1.0, "proceeds": 300.0,
                 "cost": 100.0, "gain": 200.0}])
            rec = self._record(td, {"year": 2025, "year_end": {},
                                    "boundary_rows": [], "dispositions": [],
                                    "filed_dispositions": [
                {"symbol": "/QZM26.US", "date": "2025-12-31", "qty": 1.0,
                 "proceeds": 300.0, "cost": 100.0, "gain": 200.0}]})
            rep = handoff.check(Path(td), self.CFG, rec, {})
        self.assertEqual([d["symbol"] for d in rep["double"]], ["F:QZM26.US"])

    def test_a_boundary_row_spelled_slash_is_this_year_s_F_row(self):
        from taxjson.lib import handoff
        with tempfile.TemporaryDirectory() as td:
            self._work(td, [
                {"action": "BUYSELL", "symbol": "F:QZM26.US", "date": "2025-12-31",
                 "date_settle": "2025-12-31", "time": "10:00:00", "quantity": -1.0,
                 "net_amount": 300.0, "currency": "CAD", "id": "99900001aa"}], [])
            rec = self._record(td, {"year": 2025, "year_end": {}, "dispositions": [],
                                    "boundary_rows": [
                {"symbol": "/QZM26.US", "action": "BUYSELL", "quantity": -1.0,
                 "net": 300.0, "date": "2025-12-31", "tax_date": "2025-12-31"}]})
            rep = handoff.check(Path(td), self.CFG, rec, {})
        self.assertEqual(rep["boundary"], [])

    def test_the_next_year_hands_off_without_a_re_close(self):
        # A 2025 the books hold as the broker's `F:QZM26.US` (bought 2,
        # sold 1), its record spelled `/QZM26.US` as an older taxjson wrote
        # a .tt line's symbol: the 2026 hand-off finds the one position,
        # once.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            d = root / "inputs" / "margin"
            d.mkdir(parents=True)
            (d / "generic_2025.csv").write_text(
                _CSV + "2025-06-02,SELL,F:QZM26.US,-1,300,30000.00,USD\n")
            (d / "generic_2025.csv.toml").write_text(_MAPPING)
            head = ('[settings]\ncountry = "canada"\nbase_currency = "CAD"\n'
                    'source_currencies = ["USD"]\nlocal_timezone = "America/Toronto"\n'
                    'inputs_dir = "../inputs"\n')
            acct = '[accounts.margin]\ntype = "taxable"\ncrypto = false\n'
            (root / "2025").mkdir()
            (root / "2025" / "taxjson.toml").write_text(
                head.replace("[settings]\n", "[settings]\nyear = 2025\n") + acct)
            (root / "2026").mkdir()
            (root / "2026" / "taxjson.toml").write_text(
                head.replace("[settings]\n", "[settings]\nyear = 2026\n")
                + 'prior_year_record = "../2025/filed/2025.json"\n' + acct)
            r = cli(root / "2025", "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
            r = cli(root / "2025", "close-year", "--yes")
            self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
            lock = root / "2025" / "filed" / "2025.json"
            text = lock.read_text(encoding="utf-8")
            self.assertIn('"F:QZM26.US"', text)
            lock.write_text(text.replace('"F:QZM26.US"', '"/QZM26.US"'),
                            encoding="utf-8")
            r = cli(root / "2026", "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
            r = cli(root / "2026", "handoff")
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-2000:])
            self.assertIn("Everything the closed year carried forward is here, once.",
                          r.stdout)


if __name__ == "__main__":
    unittest.main()
