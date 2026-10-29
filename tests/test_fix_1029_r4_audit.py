"""Backlog group `audit`, fix round 4 of 2026-10-29: `tjs audit` traces
the rows that are not trades.

TJ-0359  the audited securities' rows that are not trades — return of
         capital and the other cost adjustments, splits and ticker
         changes, corporate-action rows, openings, their dividends and
         withholding — are traced to their parsed broker row (or .tt
         line) as their trades are: the amount compared (a dividend's
         gross), a mismatch fails the audit. The trades under an earlier
         ticker and across a corporate action are traced too, a
         corporate action's rows (no id in their file) join their file,
         and a row with no parsed row says what made it.

Synthetic books only: made-up tickers (ZZQ.TO, ZZR.TO, A012345.US ...),
account names (margin), amounts under 1,000.
"""
import contextlib
import copy
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.bin import taxjson_audit as AUDIT
from taxjson.lib.core import (TaxTransaction, coerce_transaction_row,
                              get_tax_rules, load_transactions)
from taxjson.lib.dispatch import python_module_argv

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"


def _env() -> dict:
    return dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
                TAXJSON_WIDTH="0")


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    """`taxjson -C root args...` from this tree's sources (width 0)."""
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run")
        + ["-C", str(root), *args],
        capture_output=True, text=True, env=_env(),
        stdin=subprocess.DEVNULL, timeout=600)


def _audit(argv):
    """taxjson-audit in process: (rc, the --json document, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    rc = None
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = AUDIT.main(list(argv) + ["--json"])
        except SystemExit as e:
            rc = e.code
    text = out.getvalue()
    return rc, (json.loads(text) if text.strip() else {}), err.getvalue()


def _row(**kw) -> dict:
    """One parsed row with the id the parsers give it (its content)."""
    kw.setdefault("account", "margin")
    kw.setdefault("date_settle", kw["date"])
    return TaxTransaction(**kw).to_dict()


def _by(doc, key="action"):
    return {(r[key], r["date"]): r
            for r in doc["pool_other_rows"]["rows"]}


class TestOtherPoolRows(unittest.TestCase):
    """The books' rows that are not trades were never compared with the
    rows they came from: a return of capital whose amount differed from
    the broker's changed the cost the audit re-ran and the gain it tied,
    and passed; the buy booked under the ticker before a rename was not
    traced either. Both countries (one audit tool)."""

    def _audit(self, country="canada", change=None):
        us = country == "usa"
        cur = "USD" if us else "CAD"
        old, new = ("ZZQ.US", "ZZR.US") if us else ("ZZQ.TO", "ZZR.TO")
        parsed = [
            _row(action="BUYSELL", date="2026-01-05", symbol=old,
                 quantity=100, price=5.0, net_amount=500.0, currency=cur),
            _row(action="ADJUST", date="2026-02-02", symbol=old,
                 net_amount=-50.0, currency=cur, type="roc"),
            _row(action="DIVIDEND", date="2026-02-16", symbol=old,
                 quantity=100, gross_amount=10.0, net_amount=10.0,
                 currency=cur, type="dividend"),
            _row(action="TAX", date="2026-02-16", symbol=old,
                 net_amount=1.5, currency=cur, type="tax"),
            _row(action="SPLIT", date="2026-03-02", symbol=old,
                 quantity=2.0),
            _row(action="BUYSELL", date="2026-05-04", symbol=new,
                 quantity=-200, price=4.0, net_amount=800.0,
                 currency=cur),
        ]
        books = copy.deepcopy(parsed)
        # The merge nets the withholding into the dividend's net amount
        # (taxjson_merge2.reconcile_dividend_tax): not a difference.
        books[2]["net_amount"] = 8.5
        # Rows the run made: a .tt RENAME's ticker change, a
        # [[distributions]] table's adjustment.
        books.insert(5, _row(
            action="SPLIT", date="2026-04-01", time="00:00:00",
            symbol=old, symbol_new=new, quantity=1.0, source="renames.tt",
            event_source="tt", description=f"Ticker change {old}->{new}"))
        books.insert(6, {
            "action": "ADJUST", "date": "2026-04-20",
            "date_settle": "2026-04-20", "time": "23:59:58",
            "symbol": new, "quantity": 0.0, "currency": cur,
            "net_amount": 25.0, "gross_amount": 0.0, "type": "dist",
            "record_date": "2026-04-20", "account": "margin",
            "id": f"DIST-{new}-2026-04-20-margin",
            "description": "reinvested distribution (ACB up): 100 sh x "
                           "0.25/sh per [[distributions]]"})
        if change:
            change(books)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "margin_tt_trades.json").write_text(json.dumps(
                {"transactions": parsed,
                 "metadata": {"source_file": "inputs/margin/trades.tt"}}))
            (p / "base.json").write_text(json.dumps(
                {"transactions": books}))
            (p / "dated_events.state").write_text(json.dumps(
                {"format": "dated_events/1", "journals": [], "renames": [
                    {"old": old, "new": new, "date": "2026-04-01",
                     "source": "tt",
                     "where": ["inputs/margin/renames.tt:1"]}]}))
            saved = get_tax_rules(country).compute_gains(
                load_transactions(p / "base.json"))
            (p / "check.json").write_text(json.dumps(
                {"transactions": saved["transactions"]}, default=str))
            return _audit(["--country", country, "--year", "2026",
                           "--base", str(p / "base.json"),
                           "--source", str(p / "margin_tt_trades.json"),
                           "--check", str(p / "check.json"),
                           "--dated-events", str(p / "dated_events.state"),
                           "--base-currency", cur])

    def test_tj0359_rows_that_are_not_trades_are_traced(self):
        for country in ("canada", "usa"):
            with self.subTest(country=country):
                rc, d, err = self._audit(country)
                self.assertEqual(rc, 0, (d.get("reconciliation_failures"),
                                         err))
                o = d["pool_other_rows"]
                self.assertEqual(
                    {k: o[k] for k in ("checked", "tied", "untraced")},
                    {"checked": 4, "tied": 4, "untraced": 2})
                rows = _by(d)
                div = rows[("DIVIDEND", "2026-02-16")]
                # The gross: the book's net is the broker's less the
                # withholding.
                self.assertEqual((div["field"], div["amount"], div["ties"]),
                                 ("gross_amount", 10.0, True))
                self.assertEqual(div["source"],
                                 "margin_tt_trades.json (trades.tt)")
                self.assertIs(rows[("ADJUST", "2026-02-02")]["ties"], True)
                self.assertIs(rows[("TAX", "2026-02-16")]["ties"], True)
                # A row the run made says what made it.
                ren = rows[("SPLIT", "2026-04-01")]
                self.assertIsNone(ren["ties"])
                self.assertEqual(
                    ren["origin"], "a .tt RENAME line (inputs/margin/"
                    "renames.tt:1): a ticker change, booked by the run")
                self.assertTrue(rows[("ADJUST", "2026-04-20")]["origin"]
                                .startswith("a [[distributions]] table in "
                                            "taxjson.toml (reinvested "
                                            "distribution"))
                # The buy under the ticker before the rename is traced.
                self.assertEqual(
                    {k: d["pool_trades"][k]
                     for k in ("checked", "tied", "untraced")},
                    {"checked": 1, "tied": 1, "untraced": 0})

    def test_tj0359_an_adjustment_unlike_its_parsed_row_fails(self):
        def roc_40(books):
            books[1]["net_amount"] = -40.0

        def gross_12(books):
            books[2]["gross_amount"] = 12.0
        for country in ("canada", "usa"):
            for change, action, text in (
                    (roc_40, "ADJUST", "AMOUNT cross-check FAILED: the "
                                       "parsed row carries -50.00"),
                    (gross_12, "DIVIDEND", "AMOUNT cross-check FAILED "
                                           "(gross amount): the parsed "
                                           "row carries 10.00")):
                with self.subTest(country=country, row=action):
                    rc, d, err = self._audit(country, change=change)
                    self.assertEqual(rc, 1, err)
                    self.assertTrue(d["failed"])
                    [m] = d["pool_other_rows"]["mismatched"]
                    self.assertEqual(m["action"], action)
                    self.assertTrue(m["failure"].startswith(text), m)
                    self.assertTrue(any(
                        f.startswith(f"pool row {m['id'][:12]} ({action} ")
                        for f in d["reconciliation_failures"]),
                        d["reconciliation_failures"])

    def test_tj0359_details_names_what_made_each_row(self):
        events = [{"id": "s1", "symbol": "ZZR.TO", "date": "2026-05-04",
                   "tie_out": {"ties": True}, "fx": None}]
        other = {"checked": 0, "tied": 0, "untraced": 1, "mismatched": [],
                 "rows": [{"id": "DIST-ZZR.TO-2026-04-20-margin",
                           "action": "ADJUST", "symbol": "ZZR.TO",
                           "date": "2026-04-20", "quantity": 0.0,
                           "amount": 25.0, "ties": None,
                           "origin": "a [[distributions]] table in "
                                     "taxjson.toml"}]}
        brief = AUDIT.render_reconciliation(events, [], False, brief=True,
                                            pool={}, other=other)
        self.assertIn("  other pool rows          0 tied, 0 mismatched, 1 "
                      "with no parsed row  ", brief)
        self.assertFalse(any("With no parsed row" in ln for ln in brief))
        full = AUDIT.render_reconciliation(events, [], False, brief=False,
                                           pool={}, other=other)
        self.assertIn("  With no parsed row (re-run, not traced):", full)
        self.assertIn("    2026-04-20 ADJUST ZZR.TO 25.00 — a "
                      "[[distributions]] table in taxjson.toml", full)


class TestCorporateActionRows(unittest.TestCase):
    """A corporate action's rows have no id in the corp-actions file
    (the merge gives them one): the audit skipped them, so each read as
    a row with no parsed row, and the shares the action replaced were
    not traced at all."""

    CSV = ('"Date","Activity","Symbol","Symbol Description","Quantity",'
           '"Price","Settlement Date","Account","Value","Currency",'
           '"Description"\n'
           '"2025-03-17","Reorganization","A012345","ABC CORPORATION",'
           '"-12","","2025-03-17","1","0","USD","MGR - ABC CORPORATION '
           'MERGER TO ABDCO CORPORATION 1.05 NEW = 1 OLD"\n'
           '"2025-03-17","Reorganization","ABD","ABDCO CORPORATION","12",'
           '"","2025-03-17","1","0","USD","MGR - ABDCO CORPORATION SHRS '
           'RECEIVED THRU MERGER"\n')

    def test_tj0359_corporate_action_rows_join_their_file(self):
        from taxjson.lib.corp_actions import parse_rbc_corporate_actions
        rate = 1.4
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            csv = p / "margin.csv"
            csv.write_text(self.CSV)
            with contextlib.redirect_stderr(io.StringIO()):
                ev = parse_rbc_corporate_actions(csv, "margin")[0]
            (p / "m.json").write_text(json.dumps({"elections": {
                ev.event_id: {"summary": ev.summary(),
                              "election": "rollover_s_85_1_5"}}}))
            res = subprocess.run(
                python_module_argv("taxjson.bin.taxjson_corp_actions")
                + ["--brokerage", "rbc_direct", "--country", "canada",
                   "--account-name", "margin", "--manifest",
                   str(p / "m.json"), str(csv)],
                capture_output=True, text=True, env=_env(),
                stdin=subprocess.DEVNULL, timeout=120)
            self.assertEqual(res.returncode, 0, res.stderr)
            corp = json.loads(res.stdout)
            # The statement the events were read from, by its name.
            self.assertEqual(corp["metadata"]["statements"], ["margin.csv"])
            self.assertTrue(corp["transactions"])
            self.assertFalse(any(r.get("id")
                                 for r in corp["transactions"]))
            (p / "margin_rbc_direct_corp.json").write_text(json.dumps(corp))
            parsed = [
                _row(action="BUYSELL", date="2025-01-10",
                     symbol="A012345.US", quantity=12, price=50.0,
                     net_amount=600.0, currency="USD"),
                _row(action="BUYSELL", date="2025-04-15", symbol="ABD.US",
                     quantity=-12, price=60.0, net_amount=720.0,
                     currency="USD")]
            (p / "margin_rbc_direct.json").write_text(json.dumps(
                {"transactions": parsed,
                 "metadata": {"input_files": [str(csv)]}}))
            books = []
            for i, r in enumerate(parsed + corp["transactions"]):
                b = coerce_transaction_row(dict(r), i, "test").to_dict()
                for f in ("price", "net_amount", "gross_amount"):
                    b[f] = float(b.get(f) or 0.0) * rate
                b["currency"] = "CAD"
                books.append(b)
            (p / "base.json").write_text(json.dumps({"transactions": books}))
            (p / "rates.csv").write_text("".join(
                f"{day} 12:00:00 USD CAD {rate}\n"
                for day in ("2025-01-10", "2025-03-17", "2025-04-15")))
            saved = get_tax_rules("canada").compute_gains(
                load_transactions(p / "base.json"))
            (p / "check.json").write_text(json.dumps(
                {"transactions": saved["transactions"]}, default=str))
            rc, d, err = _audit([
                "--country", "canada", "--year", "2025",
                "--base", str(p / "base.json"),
                "--source", str(p / "margin_rbc_direct.json"),
                "--source", str(p / "margin_rbc_direct_corp.json"),
                "--check", str(p / "check.json"),
                "--rates", str(p / "rates.csv"), "--base-currency", "CAD"])
        self.assertEqual(rc, 0, (d.get("reconciliation_failures"), err))
        [split] = [r for r in d["pool_other_rows"]["rows"]
                   if r["action"] == "SPLIT"]
        self.assertEqual(split["symbol_new"], "ABD.US")
        self.assertIs(split["ties"], True)
        self.assertEqual(split["source"],
                         "margin_rbc_direct_corp.json (corporate actions "
                         "from margin.csv)")
        # The old shares' buy, through the action, is traced.
        self.assertEqual(d["pool_trades"]["checked"], 1)
        self.assertEqual(d["pool_trades"]["untraced_rows"], [])


class TestRowOrigin(unittest.TestCase):
    """What made a row with no parsed row, from what the row says."""

    def test_tj0359_each_kind_is_named(self):
        from taxjson.lib import core
        cases = [
            ({"id": "DIST-ZZR.TO-2026-04-20-margin",
              "description": "return of capital (ACB down): 10 sh x "
                             "-0.5/sh per [[distributions]]"},
             "a [[distributions]] table in taxjson.toml (return of "
             "capital (ACB down): 10 sh x -0.5/sh)"),
            ({"corp_event_id": "20260301-zzq-zzr-0001",
              "corp_election": "rollover_s_85_1_5"},
             "corporate action 20260301-zzq-zzr-0001 (rollover_s_85_1_5): "
             "no row of the corp-actions file has its id — re-run "
             "`taxjson run`"),
            ({"event_source": "map", "symbol": "ZZQ.TO",
              "symbol_new": "ZZR.TO", "date": "2026-04-01"},
             "a dated RENAME line of ticker.map: a ticker change, booked "
             "by the run"),
            ({"event_source": "ib-conid"},
             "IB shows one contract id under two symbols: a ticker "
             "change, booked by the run"),
            ({"action": "OPENING_BALANCE",
              "description": "MISSING-HISTORY opening (inputs/margin/"
                             "missing_history.tt:2) — bought before the "
                             "data, cost unknown (qty=10.0)"},
             "an OPENING ... cost=unknown line (inputs/margin/"
             "missing_history.tt:2): units held before the data, cost "
             "unknown (missing history)"),
            ({"type": core.TRANSFER_BOOK_VALUE_TYPE,
              "source": "ib_2026.csv"},
             "a transfer in, booked by the run at the book value the "
             "broker states (ib_2026.csv)"),
            ({"type": core.LOT_MOVE_TYPE},
             "a move between your own accounts, booked by the run"),
            ({"source": "trades.tt"},
             "a row of trades.tt that no parsed file holds now — stale "
             "work/ files: re-run `taxjson run`"),
            ({}, "made by the run (the row names no stage)"),
        ]
        for row, want in cases:
            with self.subTest(row=row):
                self.assertEqual(AUDIT.row_origin(row), want)

    def test_tj0359_an_id_less_row_joins_under_the_books_id(self):
        row = {"action": "BUYSELL", "type": "transfer_book_value",
               "date": "2026-02-03", "symbol": "ZZQ.TO", "quantity": 10.0,
               "currency": "CAD", "price": 9.0, "net_amount": 90.0,
               "account": "margin"}
        want = coerce_transaction_row(dict(row), 0, "test").id
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "margin_transfer_costs.json"
            f.write_text(json.dumps({"transactions": [row], "metadata": {
                "kind": "transfer_book_value", "account": "margin"}}))
            index = AUDIT.build_source_index([f])
        self.assertEqual(list(index), [want])
        self.assertEqual(index[want][0]["label"],
                         "margin_transfer_costs.json (transfers in and "
                         "in-kind moves, booked by the run)")


class TestTtLines(unittest.TestCase):

    def test_tj0359_a_tt_row_keeps_its_line(self):
        from taxjson.bin.taxjson_convert_tt import tt_to_json
        with tempfile.TemporaryDirectory() as td:
            tt = Path(td) / "trades.tt"
            tt.write_text(
                "# a note\n"
                "BUYSELL 2026-01-05 09:30:00 ZZQ.TO 10 CAD 5.00 50.00\n"
                "\n"
                "ADJUST 2026-02-02 09:30:00 ZZQ.TO CAD -5.00 type=roc\n")
            doc = tt_to_json(tt, "margin", equity=True)
            (Path(td) / "x.json").write_text(json.dumps(doc))
            index = AUDIT.build_source_index([Path(td) / "x.json"])
        ids = [r["id"] for r in doc["transactions"]]
        self.assertEqual(doc["metadata"]["source_lines"],
                         {ids[0]: 2, ids[1]: 4})
        # The audit's copy of each row carries its line ...
        self.assertEqual([index[i][0]["row"]["source_line"] for i in ids],
                         [2, 4])
        # ... the file's rows do not: a converted row still builds a
        # TaxTransaction from its keys, with its id.
        for r in doc["transactions"]:
            self.assertNotIn("source_line", r)
            self.assertEqual(TaxTransaction(**r).id, r["id"])


class TestMergedJson(unittest.TestCase):

    def test_tj0359_books_merge_their_pool_rows(self):
        from taxjson.bin.taxjson_run import _merge_audit_json
        a = {"events": [], "pool_trades": {
                 "checked": 1, "tied": 1, "untraced": 1, "mismatched": [],
                 "untraced_rows": [{"id": "t1"}]},
             "pool_other_rows": {"checked": 2, "tied": 1, "untraced": 1,
                                 "mismatched": [{"id": "r1"}],
                                 "rows": [{"id": "r1"}, {"id": "r2"}]}}
        b = {"events": [], "pool_trades": {
                 "checked": 0, "tied": 0, "untraced": 0, "mismatched": [],
                 "untraced_rows": []},
             "pool_other_rows": {"checked": 1, "tied": 1, "untraced": 0,
                                 "mismatched": [], "rows": [{"id": "r3"}]}}
        d = _merge_audit_json([a, b], "CAD", "canada")
        self.assertEqual(d["pool_trades"]["untraced_rows"], [{"id": "t1"}])
        o = d["pool_other_rows"]
        self.assertEqual((o["checked"], o["tied"], o["untraced"]),
                         (3, 2, 1))
        self.assertEqual([r["id"] for r in o["rows"]], ["r1", "r2", "r3"])
        self.assertEqual(o["mismatched"], [{"id": "r1"}])


class TestProjectAudit(unittest.TestCase):
    """`tjs audit` of a project: the .tt rows with their lines, and the
    rows the run made (a .tt RENAME, a [[distributions]] table, an
    OPENING ... cost=unknown line) each named with its declaration."""

    def test_tj0359_project_rows_are_traced_or_named(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            acct = root / "inputs" / "margin"
            acct.mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nlocal_timezone = "America/Toronto"\n'
                'year = 2026\ncountry = "canada"\nbase_currency = "CAD"\n'
                'source_currencies = []\n'
                '\n[accounts.margin]\ntype = "taxable"\n'
                '\n[[distributions]]\nper_share = 0.25\n'
                'record_date = 2026-04-20\nsymbol = "ZZR.TO"\n')
            (acct / "trades.tt").write_text(
                "BUYSELL 2026-01-05 09:30:00 ZZQ.TO 100 CAD 5.00 500.00\n"
                "ADJUST 2026-02-02 09:30:00 ZZQ.TO CAD -50.00 type=roc\n"
                "SPLIT 2026-03-02 09:30:00 ZZQ.TO ZZQ.TO 2\n"
                "BUYSELL 2026-05-04 09:30:00 ZZR.TO -50 CAD 4.00 200.00\n"
                "BUYSELL 2026-05-04 09:30:00 ZZM.TO -10 CAD 3.00 30.00\n")
            (acct / "renames.tt").write_text(
                "RENAME 2026-04-01 ZZQ.TO ZZR.TO\n")
            (acct / "missing_history.tt").write_text(
                "OPENING 2025-12-30 ZZM.TO 10 cost=unknown\n")
            r = _cli(root, "run")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = _cli(root, "audit", "--json", "--no-trace")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            d = json.loads(r.stdout)
            rows = _by(d)
            roc = rows[("ADJUST", "2026-02-02")]
            self.assertEqual((roc["source"], roc["line"], roc["ties"]),
                             ("margin_tt_trades.json (trades.tt)", 2, True))
            self.assertEqual(rows[("SPLIT", "2026-03-02")]["line"], 3)
            self.assertEqual(
                rows[("SPLIT", "2026-04-01")]["origin"],
                "a .tt RENAME line (inputs/margin/renames.tt:1): a ticker "
                "change, booked by the run")
            self.assertTrue(rows[("ADJUST", "2026-04-20")]["origin"]
                            .startswith("a [[distributions]] table"))
            self.assertEqual(
                rows[("OPENING_BALANCE", "2025-12-30")]["origin"],
                "an OPENING ... cost=unknown line (inputs/margin/"
                "missing_history.tt:1): units held before the data, cost "
                "unknown (missing history)")
            # The ZZQ.TO buy, before the rename, is a pool trade.
            self.assertEqual(d["pool_trades"]["checked"], 1)
            r = _cli(root, "audit", "--details")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("  other pool rows          2 tied, 0 mismatched, "
                          "3 with no parsed row", r.stdout)
            self.assertIn("  With no parsed row (re-run, not traced):\n"
                          "    2025-12-30 OPENING_BALANCE 10 ZZM.TO — an "
                          "OPENING ... cost=unknown line", r.stdout)
            # The sale's own .tt line, beside its row.
            self.assertIn("2026-05-04 SELL 50 ZZR.TO @ 4.00 — net 200.00 "
                          "CAD  [line 4]", r.stdout)


if __name__ == "__main__":
    unittest.main()
