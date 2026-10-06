"""`taxjson ticker-map --suggest`: a parser hint phrased as a condition
("Only if the position is really held under the other listing …, add to
ticker.map: TOBASE QZLR.US QZLR.TO") is offered only when the project's
books hold every symbol the line names. RBC books a dividend on a symbol
no file of the account trades under the payment currency's listing and
says how to move it to the other listing; for a Nasdaq stock that listing
does not exist, and `--suggest --write` wrote the wrong line. A suggestion
that another suggestion covers is listed under its own heading, not as
"answered by ticker.map". Synthetic data only (invented QZ tickers).
"""
import json
import tempfile
import unittest
from pathlib import Path

from taxjson.lib import ticker_map_suggest as TS

from test_fix_rbc import parse_files, row as rbc_row

DIV = rbc_row("June 2, 2025", "Dividend", "QZLR", "QZLR RESEARCH CORP", "",
              "", "12.00", "USD", "QZLR RESEARCH CORP CASH DIV")


def _book(work: Path, name: str, rows) -> None:
    (work / name).write_text(json.dumps(
        {"metadata": {"source_brokerage": "x"}, "transactions": rows}))


def _project(td: str, other_rows=None, holdings=None) -> Path:
    """work/ as `taxjson run` leaves it: the RBC account's parse and its
    .diag (the parser's own output for a dividend-only USD symbol), plus
    another account's parse when given."""
    root = Path(td)
    work = root / "work"
    work.mkdir()
    txs, err, _ = parse_files({"rbc.csv": DIV})
    assert {t["symbol"] for t in txs} == {"QZLR.US"}, txs
    assert "TOBASE QZLR.US QZLR.TO" in err, err
    _book(work, "margin_rbc_direct.json", txs)
    (work / "margin_rbc_direct.json.diag").write_text(err)
    if other_rows is not None:
        _book(work, "tfsa_ib.json", other_rows)
    cfg = '[settings]\nyear = 2025\ncountry = "canada"\n'
    if holdings:
        (root / "h.toml").write_text(holdings)
        cfg += '\n[accounts.tfsa]\nholdings = ["h.toml"]\n'
    (root / "taxjson.toml").write_text(cfg)
    return root


def _tx(sym, qty=10, action="BUYSELL"):
    return {"date": "2025-03-03", "time": "10:00:00", "action": action,
            "symbol": sym, "quantity": qty, "price": 10.0,
            "currency": "CAD"}


class TestConditionalHint(unittest.TestCase):
    def test_gather_still_reads_it(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            s, = [s for s in TS.gather(root)
                  if s.line == "TOBASE QZLR.US QZLR.TO"]
            self.assertTrue(s.conditional)

    def test_no_other_listing_in_the_books_no_suggestion(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, other_rows=[_tx("QZLR.US", 5)])
            offer, skipped = TS.pending(root)
        self.assertEqual([s.line for s in offer], [])
        self.assertEqual(skipped, [])

    def test_other_listing_in_another_account_is_offered(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, other_rows=[_tx("QZLR.TO")])
            offer, _ = TS.pending(root)
        self.assertEqual([s.line for s in offer], ["TOBASE QZLR.US QZLR.TO"])

    def test_an_option_on_the_other_listing_is_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, other_rows=[_tx("QZLR270115C00050000.TO",
                                                1)])
            offer, _ = TS.pending(root)
        self.assertEqual([s.line for s in offer], ["TOBASE QZLR.US QZLR.TO"])

    def test_an_opening_balance_is_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            _book(root / "work", "tfsa_tt_start.json",
                  [_tx("QZLR.TO", 20, action="OPENING")])
            offer, _ = TS.pending(root)
        self.assertEqual([s.line for s in offer], ["TOBASE QZLR.US QZLR.TO"])

    def test_a_holdings_file_is_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, holdings=(
                '[[holding]]\nsymbol = "QZLR.TO"\nquantity = 20\n'))
            offer, _ = TS.pending(root)
        self.assertEqual([s.line for s in offer], ["TOBASE QZLR.US QZLR.TO"])

    def test_a_derived_book_is_not_evidence(self):
        # The merged books carry ticker.map's renames: never evidence.
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            _book(root / "work", "tfsa_base.json", [_tx("QZLR.TO")])
            offer, _ = TS.pending(root)
        self.assertEqual(offer, [])

    def test_cli_shows_nothing(self):
        from tax_rules.dual import cli
        with tempfile.TemporaryDirectory() as td:
            root = _project(td)
            r = cli(root, "ticker-map", "--suggest")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("— 0 from the last run", r.stdout)
            self.assertNotIn("QZLR", r.stdout)
            js = json.loads(cli(root, "ticker-map", "--suggest",
                                "--json").stdout)
            self.assertEqual(js, {"suggestions": [], "skipped": []})


class TestOtherConditionalSources(unittest.TestCase):
    """Every source that turns a conditional hint into a suggestion."""

    def _pending(self, diag, rows):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "work").mkdir()
            (root / "work" / "margin_x.json.diag").write_text(diag)
            _book(root / "work", "margin_x.json", [_tx(s) for s in rows])
            return [s.line for s in TS.pending(root)[0]]

    def test_rbc_option_adjustment_needs_the_new_description(self):
        diag = ("note: option adjustment 2025-05-01: QZOP250620C00010000.US "
                "continues as QZOP250620C00010000.US (RBC now describes it "
                "as QZOQ250620C00010000); if a later export closes it as "
                "QZOQ250620C00010000, add to ticker.map:  GLOBAL "
                "QZOQ250620C00010000.US QZOP250620C00010000.US — nothing "
                "to book\n")
        self.assertEqual(self._pending(diag, ["QZOP250620C00010000.US"]), [])
        self.assertEqual(
            self._pending(diag, ["QZOP250620C00010000.US",
                                 "QZOQ250620C00010000.US"]),
            ["GLOBAL QZOQ250620C00010000.US QZOP250620C00010000.US"])

    def test_ib_currency_tag_hint_needs_the_listing(self):
        diag = ("warning: IB corporate action 1: symbol 'QZTG.USD' ends in "
                "the currency/venue tag .USD — booked as QZTG.USD.US, a "
                "security of its own apart from QZTG. If it is the same "
                "security, join it in ticker.map with the listing that "
                "holds it (e.g. `GLOBAL QZTG.USD.US QZTG.US` or "
                "`QZTG.TO`).\n")
        self.assertEqual(self._pending(diag, ["QZTG.USD.US"]), [])
        self.assertEqual(self._pending(diag, ["QZTG.USD.US", "QZTG.US"]),
                         ["GLOBAL QZTG.USD.US QZTG.US"])

    def test_trailing_condition(self):
        diag = ("warning: q.csv: TFI row keeps internal symbol code — add "
                "`GLOBAL X000002.TO QZN.TO` to ticker.map if it is the same "
                "security.\n")
        self.assertEqual(self._pending(diag, ["X000002.TO"]), [])
        self.assertEqual(self._pending(diag, ["X000002.TO", "QZN.TO"]),
                         ["GLOBAL X000002.TO QZN.TO"])

    def test_a_rename_hint_names_two_symbols_of_the_books(self):
        diag = ("warning: ATTENTION: q.csv: Questrade symbol QZSA.TO looks "
                "renamed to QZSB.TO — if they are one security add to "
                "ticker.map:  GLOBAL QZSA.TO QZSB.TO  — QZSA.TO stops on "
                "2025-02-03 with 5 share(s) still open.\n")
        self.assertEqual(self._pending(diag, ["QZSA.TO", "QZSB.TO"]),
                         ["GLOBAL QZSA.TO QZSB.TO"])

    def test_an_unconditional_hint_is_offered_as_before(self):
        diag = ("warning: ATTENTION: x: IB lists one stock (contract id 1) "
                "under several symbols: QZOA, QZNB — a ticker change. Each "
                "symbol is booked as its own security until ticker.map "
                "records the change as a dated event, e.g. `RENAME QZOA.US "
                "QZNB.US 2025-05-12`.\n")
        self.assertEqual(self._pending(diag, []),
                         ["RENAME QZOA.US QZNB.US 2025-05-12"])

    def test_extract_and_crypto_lines_are_not_listing_pairs(self):
        # An EXTRACT line creates its symbol (its evidence is the row's
        # description); a CRYPTO line's id is a Yahoo id.
        diag = ("warning: ATTENTION: rbc.csv: QZD reads as the US-dollar "
                "class of a TSX-listed fund. If it trades on the TSX, add "
                "to ticker.map:  EXTRACT QZ US DLR FUND | USD | QZD.U.TO\n"
                "warning: crypto id: QZC has no CRYPTO line. If the "
                "numbered id is your coin, add to ticker.map:\n"
                "    CRYPTO QZC QZC12345\n")
        self.assertEqual(self._pending(diag, []),
                         ["EXTRACT QZ US DLR FUND | USD | QZD.U.TO",
                          "CRYPTO QZC QZC12345"])

    def test_symbol_codes_detail(self):
        from taxjson.lib import symbol_codes as SC
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            work = root / "work"
            work.mkdir()
            (work / f"qt{SC.SUFFIX}").write_text(json.dumps({
                "format": SC.FORMAT, "account": "qt", "resolved": {},
                "mapped": {}, "unresolved": {"X000003": {
                    "reason": "no_evidence",
                    "detail": "looks like QZE.TO by name, not applied — "
                              "add `GLOBAL X000003.TO QZE.TO` to "
                              "ticker.map if right"}}}))
            _book(work, "qt_questrade.json", [_tx("X000003.TO")])
            self.assertEqual(TS.pending(root)[0], [])
            _book(work, "rrsp_ib.json", [_tx("QZE.TO")])
            self.assertEqual([s.line for s in TS.pending(root)[0]],
                             ["GLOBAL X000003.TO QZE.TO"])


class TestCoveredHeading(unittest.TestCase):
    def test_superseded_is_not_answered_by_the_map(self):
        from tax_rules.dual import cli
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "work").mkdir()
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n')
            (root / "work" / "margin_rbc.json.diag").write_text(
                "warning: x: add to ticker.map:  EXTRACT QZ DLR FUND | USD "
                "| QZD.U.TO\n"
                "warning: y: add to ticker.map:  EXTRACT QZ US DLR FUND "
                "UNITS | USD | QZD.U.TO\n")
            offer, skipped = TS.pending(root)
            self.assertEqual(len(offer), 1)
            (s, why), = skipped
            self.assertTrue(TS.covered_by_suggestion(why))
            r = cli(root, "ticker-map", "--suggest")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("Already answered by ticker.map", r.stdout)
            self.assertIn("Covered by another suggestion (1)", r.stdout)
            js = json.loads(cli(root, "ticker-map", "--suggest",
                                "--json").stdout)
            self.assertEqual([k["by"] for k in js["skipped"]],
                             ["suggestion"])
            (root / "ticker.map").write_text(
                "EXTRACT QZ DLR FUND | USD | QZD.U.TO\n")
            r = cli(root, "ticker-map", "--suggest")
            self.assertIn("Already answered by ticker.map (2)", r.stdout)
            self.assertNotIn("Covered by another suggestion", r.stdout)


if __name__ == "__main__":
    unittest.main()
