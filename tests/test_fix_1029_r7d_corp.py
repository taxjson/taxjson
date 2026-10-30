"""Fix round 7 (2026-10-29, wave D), group corp: corporate actions (backlog
TJ-1022, TJ-1025, TJ-1026, TJ-1117, TJ-1132). Synthetic data only: made-up
tickers and account ids."""
import tempfile
import unittest
from pathlib import Path

from _qa_project import console, flat, tj
from tax_rules import rule
from taxjson.lib.core import TaxTransaction as T
from taxjson.lib.missing_history import (assess_tax_year_relevance,
                                         detect_missing_history,
                                         detect_unbacked_covers,
                                         draft_purchases, synthesize_openings)


def _project(root: Path, country: str, b_files: dict, ticker_map: str = ""):
    tz = "America/Toronto" if country == "canada" else "America/New_York"
    cur = "CAD" if country == "canada" else "USD"
    sym = "SYN.TO" if country == "canada" else "SYN"
    (root / "inputs/a").mkdir(parents=True)
    (root / "inputs/b").mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        f'[settings]\nlocal_timezone = "{tz}"\nyear = 2025\n'
        f'country = "{country}"\nbase_currency = "{cur}"\n'
        'source_currencies = []\n'
        '[accounts.a]\ntype = "sheltered"\n'
        '[accounts.b]\ntype = "taxable"\n')
    (root / "ticker.map").write_text(ticker_map)
    (root / "inputs/a/trades.tt").write_text(
        f"BUYSELL 2024-06-03 09:30:00 {sym} 1 {cur} 10 -10 0\n"
        f"SPLIT 2025-01-02 00:00:00 {sym} {sym} 2\n")
    for name, text in b_files.items():
        (root / "inputs/b" / name).write_text(text)


class TestTJ1022SplitOfAnotherBook(unittest.TestCase):
    """TJ-1022 (a regression of TJ-0832): the engines apply a SPLIT row to
    the shares of that row's book only, so a split only a sheltered or
    affiliated account reports must not re-denominate a taxable account's
    units in the checks and drafts. A split another TAXABLE account
    reports still does (TJ-0832's own tests, test_fix_1029_r7_writers)."""

    TYPES = {"a": True, "b": False}

    def _rows(self, split_account="a"):
        return [
            T(action="BUYSELL", date="2024-06-03", symbol="SYN.US",
              quantity=10, price=10, net_amount=-100, currency="USD",
              account="b"),
            T(action="BUYSELL", date="2024-06-03", symbol="SYN.US",
              quantity=1, price=10, net_amount=-10, currency="USD",
              account=split_account),
            T(action="BUYSELL", date="2025-03-03", symbol="SYN.US",
              quantity=-20, price=10, net_amount=200, currency="USD",
              account="b"),
            T(action="SPLIT", date="2025-01-02", symbol="SYN.US",
              quantity=2.0, account=split_account)]

    def test_sheltered_split_leaves_taxable_short(self):
        c, = detect_missing_history(self._rows(),
                                    registered_accounts=self.TYPES)
        self.assertEqual((c.account, c.peak_short), ("b", -10.0))
        r, = assess_tax_year_relevance(self._rows(), [c], 2025,
                                       registered_accounts=self.TYPES)
        self.assertTrue(r.affects_year)

    def test_label_reads_the_book_when_the_type_is_unknown(self):
        # No configured type: the label heuristic, as the rest of the walk.
        rows = self._rows("MY-TFSA")
        c, = detect_missing_history(rows)
        self.assertEqual((c.account, c.peak_short), ("b", -10.0))

    def test_affiliated_split_leaves_taxable_short(self):
        c, = detect_missing_history(self._rows("spouse"),
                                    registered_accounts={"b": False},
                                    affiliated_accounts={"spouse"})
        self.assertEqual((c.account, c.peak_short), ("b", -10.0))

    def test_taxable_split_still_reaches_taxable(self):
        # TJ-0832 stays fixed, with the types known.
        self.assertEqual(detect_missing_history(
            self._rows("c"), registered_accounts={"b": False, "c": False}),
            [])

    def test_same_book_spread_inside_the_sheltered_book(self):
        # Two sheltered accounts: one's split reaches the other.
        rows = [T(action="BUYSELL", date="2024-06-03", symbol="SYN.US",
                  quantity=10, price=10, net_amount=-100, currency="USD",
                  account="s1"),
                T(action="BUYSELL", date="2024-06-03", symbol="SYN.US",
                  quantity=1, price=10, net_amount=-10, currency="USD",
                  account="s2"),
                T(action="BUYSELL", date="2025-03-03", symbol="SYN.US",
                  quantity=-20, price=10, net_amount=200, currency="USD",
                  account="s1"),
                T(action="SPLIT", date="2025-01-02", symbol="SYN.US",
                  quantity=2.0, account="s2")]
        self.assertEqual(detect_missing_history(
            rows, registered_accounts={"s1": True, "s2": True}), [])

    def test_earlier_sheltered_copy_keeps_the_taxable_event(self):
        # A sheltered copy dated first must not hide the taxable account's
        # own report from a third, taxable account.
        rows = self._rows("c") + [
            T(action="BUYSELL", date="2024-06-03", symbol="SYN.US",
              quantity=1, price=10, net_amount=-10, currency="USD",
              account="a"),
            T(action="SPLIT", date="2025-01-01", symbol="SYN.US",
              quantity=2.0, account="a")]
        self.assertEqual(detect_missing_history(
            rows, registered_accounts={"a": True, "b": False, "c": False}),
            [])

    def test_opening_is_sized_on_the_book(self):
        # Left in opening-date units with the split ignored: 10, not 5.
        new, _log = synthesize_openings(
            self._rows(), pairs={("SYN.US", "b")},
            registered_accounts=self.TYPES)
        self.assertEqual(sum(t.quantity for t in new
                             if t.action == "OPENING_BALANCE"), 10.0)

    def test_cover_walk_uses_the_book(self):
        # A sheltered split doubles no taxable short: a 20 cover of 10
        # short is unbacked by 10.
        rows = [T(action="BUYSELL", date="2024-06-03", symbol="SYN.US",
                  quantity=-10, price=10, net_amount=100, currency="USD",
                  account="b", description="SHORT."),
                T(action="BUYSELL", date="2024-06-03", symbol="SYN.US",
                  quantity=1, price=10, net_amount=-10, currency="USD",
                  account="a"),
                T(action="SPLIT", date="2025-01-02", symbol="SYN.US",
                  quantity=2.0, account="a"),
                T(action="BUYSELL", date="2025-03-03", symbol="SYN.US",
                  quantity=20, price=10, net_amount=-200, currency="USD",
                  account="b", description="COVER SHORT.")]
        c, = detect_unbacked_covers(rows, registered_accounts=self.TYPES)
        self.assertEqual((c.account, c.unbacked_qty), ("b", 10.0))

    def test_draft_ignores_the_sheltered_split(self):
        from test_basis_drafts import _book, _lot, _parsed, _sell
        rows = _book(_parsed(
            _sell("SYN", "2025-03-18, 10:00:00", -10, 15, 100)
            + _lot("SYN", "2024-01-02", 10, 10, 100), account="b"))
        rows = [T(action="SPLIT", date="2025-01-02", symbol="SYN.US",
                  quantity=2.0, account="a"),
                T(action="BUYSELL", date="2024-06-03", symbol="SYN.US",
                  quantity=1, price=10, net_amount=-10, currency="USD",
                  account="a")] + rows
        drafts, _gaps = draft_purchases(rows, country="usa", accounts={"b"},
                                        registered_accounts=self.TYPES)
        self.assertEqual([(d.account, d.quantity, d.cost) for d in drafts],
                         [("b", 10.0, 100.0)])

    def _checks_flag_it(self, country):
        sym = "SYN.TO" if country == "canada" else "SYN"
        cur = "CAD" if country == "canada" else "USD"
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "p"
            _project(root, country, {"trades.tt":
                     f"BUYSELL 2024-06-03 09:30:00 {sym} 10 {cur} 10 -100 0\n"
                     f"BUYSELL 2025-03-03 09:30:00 {sym} -20 {cur} 10 200 0\n"
                     })
            tj(root, "run", "--no-input", check=False)
            fm = tj(root, "find-missing-history", check=False)
            self.assertIn("AFFECTS", fm.stdout)
            self.assertNotIn("No missing-cost-basis issues", fm.stdout)
            cl = tj(root, "checklist", "--run", check=False)
            line, = [ln for ln in cl.stdout.splitlines()
                     if "10. missing-history" in ln]
            self.assertIn("[!]", line)
            if country == "canada":
                self.assertIn("NOT in these totals",
                              flat(console(tj(root, "sum", check=False))))

    def test_checks_flag_the_short_canada(self):
        self._checks_flag_it("canada")

    def test_checks_flag_the_short_usa(self):
        self._checks_flag_it("usa")

    def test_cli_draft_adds_up(self):
        from test_fix_ibparse import HEAD, TRADES_H
        from test_basis_drafts import _lot, _sell
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "p"
            _project(root, "canada", {"ib.csv": (
                HEAD + TRADES_H
                + _sell("SYN", "2025-03-18, 10:00:00", -10, 15, 100, cur="CAD")
                + _lot("SYN", "2024-01-02", 10, 10, 100, cur="CAD"))},
                ticker_map="GLOBAL SYN SYN.TO\n")
            tj(root, "run", "--no-input", check=False)
            tj(root, "find-missing-history", "b", "--write-purchases",
               check=False)
            draft = root / "inputs/b/purchases_draft.tt.txt"
            buys = [ln for ln in draft.read_text().splitlines()
                    if ln.startswith("BUYSELL")]
            self.assertEqual(len(buys), 1, buys)
            self.assertEqual(buys[0].split()[4:5], ["10"], buys)


class TestTJ1025ParentOnlyABrokerCode(unittest.TestCase):
    """TJ-1025 (a regression of TJ-0658): a taxable spin-off whose parent
    is only a Questrade/RBC SEC# code booked its dividend on the bare
    code, so a Canadian dividend read as foreign. TJ-0658's own tests
    (a foreign parent with an ISIN or listing pays the dividend) stand:
    test_fix_1029_r7b_corpib.TestForeignParentSpinoffIncome."""

    def _div(self, source, isin):
        import contextlib
        import io
        from taxjson.lib import corp_actions as CA
        ev = CA.CorporateAction(
            date="2025-08-25", time="09:30:00", action_type="spinoff",
            source_symbol=source, source_isin=isin,
            target_symbol="ZQNEW.TO", target_isin="ZQNEW.TO",
            ratio_new=40, ratio_old=400, qty_disposed=0, qty_received=40,
            fmv=0, currency="CAD", target_currency="CAD", account="a")
        with contextlib.redirect_stderr(io.StringIO()):
            rows = CA.resolve_event(ev, "taxable_deemed_dividend", "canada",
                                    {"fmv_per_share": 2.5})
        return next(r for r in rows if r["action"] == "DIVIDEND")

    @rule("CA-CORP-07")
    def test_code_only_parent_falls_back_to_the_new_shares(self):
        self.assertEqual(self._div("K099999", "K099999")["symbol"], "ZQNEW.TO")

    def test_resolved_parents_still_pay(self):
        for source, isin, country in (
                ("PARENT.TO", "K099999", None),   # traded or mapped: the code differs
                ("PARENT.US", "PARENT.US", None),  # a listing is a listing
                ("US0000000002", "US0000000002", "US"),  # a real ISIN
                ("PARENT", "", None)):            # a hand-named parent
            with self.subTest(source=source):
                d = self._div(source, isin)
                self.assertEqual(d["symbol"], source)
                self.assertEqual(d.get("issuer_country"), country)

    def test_questrade_project_reads_it_as_canadian(self):
        head = Path(__file__).resolve().parent.parent.joinpath(
            "examples/questrade_demo.csv").read_text().splitlines()[0]
        row = ('2025-08-25,2025-08-25,DIS,ZQNEW,"ZQNEW SPINCO LTD SPINOFF ON '
               '400 SHS FROM SEC# K099999 ZQPARENT CORP REC 08/21/25 PAY '
               '08/25/25",40,0.00,0.00,0.00,0.00,CAD,Dividends,99900001,Margin')
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "p"
            tj(Path(d), "init", "--country", "canada", "--single", "--year",
               "2025", str(root), check=False)
            (root / "inputs/margin").mkdir(parents=True, exist_ok=True)
            (root / "inputs/margin/questrade_2025.csv").write_text(
                head + "\n" + row + "\n")
            tj(root, "run", "--no-input", check=False)
            tj(root, "elect", "margin", "--set",
               "20250825-k099999-zqnew-3dfa=taxable_deemed_dividend",
               "--hint", "fmv_per_share=2.5", check=False)
            tj(root, "run", "--no-input", check=False)
            divs = tj(root, "divs-sum", check=False).stdout
            self.assertIn("ZQNEW.TO", divs)
            self.assertNotIn("K099999", divs)
            est = flat(tj(root, "estimate", "--other-income", "80000",
                          "--province", "ON", check=False).stdout)
            self.assertIn("Eligible dividends (grossed) 138.00", est)
            self.assertNotIn("K099999", tj(root, "t1135", check=False).stdout)


class TestTJ1026S861OriginalShares(unittest.TestCase):
    """TJ-1026 (TJ-0842's fix left this): an s.86.1 spin-off after the parent
    was sold reduces the cost of the shares the distribution was made on,
    not a trim from before the record date. TJ-0842's own tests (one sale
    closing the holding; a loss keeps the gain on the distribution; a plain
    return of capital is unchanged) stand: test_fix_1029_r7b_corpib."""

    def _gains(self, book, year):
        import contextlib
        import copy
        import io
        from taxjson.lib.pipeline import GainsRequest, run_gains
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(err):
            g = run_gains(copy.deepcopy(book), req=GainsRequest(
                country="canada", year=year, taxable=True, phantom_hint=False))
        return g, err.getvalue()

    def _event(self, qty=1, ratio_old=5, ratio_new=1):
        from taxjson.lib import corp_actions as CA
        return CA.CorporateAction(
            date="2025-01-02", time="10:00:00", action_type="spinoff",
            source_symbol="PARENT.TO", source_isin="CA0000000001",
            target_symbol="CHILD.TO", target_isin="CA0000000002",
            ratio_new=ratio_new, ratio_old=ratio_old, qty_disposed=0,
            qty_received=qty, fmv=500, target_fmv=500, currency="CAD",
            target_currency="CAD", account="a", fractional_delivery=True)

    def _rows(self, ev):
        import contextlib
        import dataclasses
        import io
        from taxjson.lib import corp_actions as CA
        from taxjson.lib.core import coerce_transaction_row
        with contextlib.redirect_stderr(io.StringIO()):
            rows = CA.resolve_event(ev, "rollover_s_86_1", "canada",
                                    {"allocated_acb_cad": 500})
        fields = {f.name for f in dataclasses.fields(T)}
        return [coerce_transaction_row(
            {k: v for k, v in dict(r, id=f"corp{i}").items() if k in fields},
            i, "synthetic") for i, r in enumerate(rows)]

    @staticmethod
    def _tx(i, day, qty, amount, account="a"):
        return T(id=i, action="BUYSELL", date=day, date_settle=day,
                 symbol="PARENT.TO", quantity=qty, net_amount=amount,
                 currency="CAD", account=account)

    def _total(self, book, year):
        return round(self._gains(book, year)[0]["summary"]["total_gain"], 2)

    @rule("CA-CORP-14")
    def test_a_trim_before_the_record_date_keeps_its_gain(self):
        book = [self._tx("b", "2022-01-03", 10, -2000),
                self._tx("s1", "2023-03-01", -5, 1000),
                self._tx("s2", "2024-12-31", -5, 1000)] + self._rows(
                    self._event())
        self.assertEqual(self._total(book, 2023), 0.0)
        self.assertEqual(self._total(book, 2024), 500.0)
        self.assertEqual(self._total(book, 2025), 0.0)
        _g, log = self._gains(book, 2024)
        self.assertIn("sold 2024-12-31)", flat(log))
        self.assertNotIn("2023-03-01", log)

    @rule("CA-CORP-14")
    def test_the_count_walks_back_over_the_closing_sales(self):
        # 10 shares paid on: all of the 4 and 6 sold after the 2023 trim,
        # by quantity.
        book = [self._tx("b", "2022-01-03", 15, -3000),
                self._tx("s0", "2023-03-01", -5, 1000),
                self._tx("s1", "2024-12-30", -4, 800),
                self._tx("s2", "2024-12-31", -6, 1200)] + self._rows(
                    self._event(qty=2, ratio_old=5, ratio_new=1))
        rows = self._gains(book, 2024)[0]["transactions"]
        self.assertEqual(sorted(round(r["cost"], 2) for r in rows
                                if r["symbol"] == "PARENT.TO"),
                         [600.0, 900.0])
        self.assertEqual(self._total(book, 2023), 0.0)

    @rule("CA-CORP-14")
    def test_a_loss_on_an_earlier_trim_blocks_nothing(self):
        book = [self._tx("b", "2022-01-03", 10, -2000),
                self._tx("s1", "2023-03-01", -5, 900),
                self._tx("s2", "2024-12-31", -5, 1000)] + self._rows(
                    self._event())
        self.assertEqual(self._total(book, 2024), 500.0)
        self.assertEqual(self._total(book, 2025), 0.0)

    def test_no_stated_count_takes_the_closing_date(self):
        # The parser's fallback for a missing parent count (ratio_old 1,
        # ratio_new the delivered shares): the closing sale.
        book = [self._tx("b", "2022-01-03", 10, -2000),
                self._tx("s1", "2023-03-01", -5, 1000),
                self._tx("s2", "2024-12-31", -5, 1000)] + self._rows(
                    self._event(qty=40, ratio_old=1, ratio_new=40))
        self.assertEqual(self._total(book, 2023), 0.0)
        self.assertEqual(self._total(book, 2024), 500.0)

    def test_a_ratio_of_the_events_own_counts_is_the_number(self):
        from taxjson.lib.corp_actions import _spinoff_parent_qty
        self.assertEqual(_spinoff_parent_qty(
            self._event(qty=40, ratio_old=400, ratio_new=40)), 400.0)
        self.assertEqual(_spinoff_parent_qty(
            self._event(qty=40, ratio_old=1, ratio_new=40)), 0.0)
        self.assertEqual(_spinoff_parent_qty(
            self._event(qty=2.5, ratio_old=4, ratio_new=1)), 10.0)

    def test_the_events_own_account_sales_take_it(self):
        book = [self._tx("b", "2022-01-03", 5, -1000),
                self._tx("b2", "2022-01-03", 5, -1000, account="z"),
                self._tx("s1", "2024-06-03", -5, 1000),
                self._tx("s2", "2024-12-31", -5, 1000, account="z")
                ] + self._rows(self._event())
        rows = self._gains(book, 2024)[0]["transactions"]
        got = {r["account"]: round(r["cost"], 2) for r in rows
               if r["symbol"] == "PARENT.TO"}
        self.assertEqual(got, {"a": 500.0, "z": 1000.0})

    def test_an_older_closed_holding_is_not_named(self):
        # Opened and closed in 2020; a 2023 holding crossed to short and
        # covered: the pool's dates still name 2020. The reduction stays
        # a gain on the distribution, with the warning.
        book = [self._tx("b0", "2020-01-03", 10, -1000),
                self._tx("s0", "2020-03-03", -10, 1000),
                self._tx("b", "2023-01-03", 10, -2000),
                self._tx("x", "2024-06-03", -15, 3000),
                self._tx("c", "2024-12-31", 5, -1000)] + self._rows(
                    self._event())
        self.assertEqual(self._total(book, 2020), 0.0)
        g, log = self._gains(book, 2025)
        self.assertEqual(round(g["summary"]["total_gain"], 2), 500.0)
        self.assertIn("cannot move the reduction", log)

    @rule("CA-CORP-14")
    def test_a_questrade_project_keeps_the_trims_gain(self):
        # The parent count reaches the engine through a real run: parser,
        # election, books. 600 bought, 200 trimmed in 2023, the last 400
        # sold in 2024; the 2025 spin-off is on 400 shares.
        import json
        import re
        head = Path(__file__).resolve().parent.parent.joinpath(
            "examples/questrade_demo.csv").read_text().splitlines()[0]

        def row(day, act, sym, desc, qty, price, gross, kind):
            return (f'{day},{day},{act},{sym},"{desc}",{qty},{price},{gross},'
                    f'0.00,{gross},CAD,{kind},99900001,Margin')
        rows = [row("2022-01-04", "Buy", "ZQPAR.TO", "ZQPARENT CORP", 600, 10,
                    -6000, "Trades"),
                row("2023-03-01", "Sell", "ZQPAR.TO", "ZQPARENT CORP", -200,
                    10, 2000, "Trades"),
                row("2024-12-30", "Sell", "ZQPAR.TO", "ZQPARENT CORP", -400,
                    10, 4000, "Trades"),
                row("2025-08-25", "DIS", "ZQNEW", "ZQNEW SPINCO LTD SPINOFF "
                    "ON 400 SHS FROM SEC# K099999 ZQPARENT CORP REC 08/21/25 "
                    "PAY 08/25/25", 40, "0.00", "0.00", "Dividends")]
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "p"
            (root / "inputs/margin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nlocal_timezone = "America/Toronto"\n'
                'year = 2024\ncountry = "canada"\nbase_currency = "CAD"\n'
                'source_currencies = []\n'
                '[accounts.margin]\ntype = "taxable"\n')
            (root / "ticker.map").write_text("")
            (root / "inputs/margin/q.csv").write_text(
                head + "\n" + "\n".join(rows) + "\n")
            out = console(tj(root, "run", "--no-input", check=False))
            eid = re.search(r"20250825-[a-z0-9.-]+-[0-9a-f]{4}", out).group(0)
            tj(root, "elect", "margin", "--set", f"{eid}=rollover_s_86_1",
               "--hint", "allocated_acb_cad=500", check=False)
            tj(root, "run", "--no-input", check=False)
            t = json.loads(tj(root, "sum", "--json").stdout)[
                "filing"]["totals"]
            self.assertEqual((t["acb"], t["gain"]), (3500.0, 500.0))
            base = json.loads((root / "work/margin_base.json").read_text())
            self.assertIn(400.0, [r.get("corp_parent_qty")
                                  for r in base["transactions"]])

    def test_a_row_without_the_count_keeps_its_shape(self):
        # Omitted from to_dict() when 0, so other rows' bytes stay.
        self.assertNotIn("corp_parent_qty",
                         self._tx("b", "2022-01-03", 1, -1).to_dict())


class TestTJ1117CorpProvenanceRoundTrip(unittest.TestCase):
    """TJ-1117: a .tt round trip dropped corp_event_id and corp_election, so
    an s.86.1 reduction written to .tt read as a plain return of capital."""

    def _rows(self):
        from taxjson.lib import corp_actions as CA
        ev = CA.CorporateAction(
            date="2025-01-02", time="10:00:00", action_type="spinoff",
            source_symbol="PARENT.US", source_isin="US0000000001",
            target_symbol="CHILD.US", target_isin="US0000000002",
            ratio_new=1, ratio_old=10, qty_disposed=0, qty_received=1,
            fmv=500, target_fmv=500, currency="CAD", target_currency="CAD",
            account="margin", fractional_delivery=True)
        return CA.resolve_event(ev, "rollover_s_86_1", "canada",
                                {"allocated_acb_cad": 500})

    def test_line_round_trip_keeps_the_fields(self):
        from taxjson.bin.taxjson_convert_tt import (parse_tt_line,
                                                    tx_to_tt_line)
        for row in self._rows():
            with self.subTest(action=row["action"]):
                line = tx_to_tt_line(row)
                self.assertIn(f"corp={row['corp_event_id']}", line)
                self.assertIn("election=rollover_s_86_1", line)
                back = parse_tt_line(line, "margin")
                self.assertEqual(back["corp_event_id"], row["corp_event_id"])
                self.assertEqual(back["corp_election"], "rollover_s_86_1")
                if row["action"] == "ADJUST":
                    self.assertEqual(back["corp_parent_qty"], 10.0)

    def test_a_plain_row_writes_no_token(self):
        from taxjson.bin.taxjson_convert_tt import tx_to_tt_line
        line = tx_to_tt_line(dict(
            action="ADJUST", date="2025-01-02", date_settle="2025-01-02",
            time="10:00:00", symbol="ZZQ.TO", currency="CAD", net_amount=-5))
        self.assertNotIn("corp=", line)
        self.assertNotIn("election=", line)

    def test_a_bad_token_is_refused(self):
        from taxjson.bin.taxjson_convert_tt import parse_tt_line
        base = "ADJUST 2025-01-02 10:00:00 ZZQ.TO CAD -5.00000 "
        for bad in ("election=rollover_s_86_1", "corp=e election=Bad-Name",
                    "corp=e parent_qty=nope", "corp=e parent_qty=0"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_tt_line(base + bad, "margin")
        with self.assertRaises(ValueError):
            parse_tt_line("BUYSELL 2025-01-02 10:00:00 ZZQ.TO 1 CAD 5 5 0 "
                          "corp=e parent_qty=5", "margin")

    def test_the_tt_book_moves_the_reduction_to_the_sale(self):
        from taxjson.bin.taxjson_convert_tt import tx_to_tt_line
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "p"
            (root / "inputs/margin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(
                '[settings]\nlocal_timezone = "America/Toronto"\n'
                'year = 2024\ncountry = "canada"\nbase_currency = "CAD"\n'
                'source_currencies = []\n'
                '[accounts.margin]\ntype = "taxable"\n')
            (root / "ticker.map").write_text("")
            lines = ["BUYSELL 2024-01-02 09:30:00 PARENT.US 10 CAD 100 1000 0",
                     "BUYSELL 2024-12-31 09:30:00 PARENT.US -10 CAD 100 1000 0"]
            lines += [tx_to_tt_line(r) for r in self._rows()]
            (root / "inputs/margin/book.tt").write_text(
                "\n".join(lines) + "\n")
            tj(root, "run", "--no-input", check=False)
            sum24 = flat(tj(root, "sum", check=False).stdout)
            self.assertIn("margin 500.00 0.00 500.00", sum24)
            toml = root / "taxjson.toml"
            toml.write_text(toml.read_text().replace("year = 2024",
                                                     "year = 2025"))
            run25 = tj(root, "run", "--no-input", check=False)
            self.assertNotIn("EMPTY pool", console(run25))
            sum25 = flat(tj(root, "sum", check=False).stdout)
            self.assertIn("margin 0.00 0.00 0.00", sum25)


class TestTJ1132DatedRenameExerciseTarget(unittest.TestCase):
    """TJ-1132: a dated RENAME left a later warrant exercise's exercise_of
    on the old symbol: read as settled in cash, a loss of the warrants'
    cost. TJ-0978 fixed the undated GLOBAL map."""

    HEAD = ("BUYSELL 2025-01-02 10:00:00 SYN.WT.TO 10 CAD 1 10 0\n"
            "BUYSELL 2025-01-03 10:00:00 SYN.TO 5 CAD 4 20 0\n"
            "RENAME 2025-03-01 SYN.TO NEW.TO\n")

    def _project(self, d, tt):
        root = Path(d) / "p"
        (root / "inputs/margin").mkdir(parents=True)
        (root / "taxjson.toml").write_text(
            '[settings]\nlocal_timezone = "America/Toronto"\n'
            'year = 2025\ncountry = "canada"\nbase_currency = "CAD"\n'
            'source_currencies = []\noption_grant_timing_since = 2025\n'
            '[accounts.margin]\ntype = "taxable"\n')
        (root / "ticker.map").write_text("")
        (root / "inputs/margin/book.tt").write_text(tt)
        return root

    def _gain(self, root):
        import json
        tj(root, "run", "--no-input", check=False)
        return json.loads(tj(root, "sum", "--json").stdout)[
            "filing"]["totals"]

    def test_share_leg_on_the_new_symbol(self):
        tt = (self.HEAD
              + "ASSIGN 2025-06-02 10:00:00 SYN.WT.TO -10 CAD 0 0 "
                "exercise_of=SYN.TO\n"
              + "BUYSELL 2025-06-02 10:00:00 NEW.TO 10 CAD 5 50 0\n")
        with tempfile.TemporaryDirectory() as d:
            t = self._gain(self._project(d, tt))
        self.assertEqual((t["proceeds"], t["acb"], t["gain"]), (0, 0, 0))

    def test_no_shares_before_the_rename(self):
        # The account holds only the warrants: no rename row of its own.
        tt = ("BUYSELL 2025-01-02 10:00:00 SYN.WT.TO 10 CAD 1 10 0\n"
              "RENAME 2025-03-01 SYN.TO NEW.TO\n"
              "ASSIGN 2025-06-02 10:00:00 SYN.WT.TO -10 CAD 0 0 "
              "exercise_of=SYN.TO\n"
              "BUYSELL 2025-06-02 10:00:00 NEW.TO 10 CAD 5 50 0\n")
        with tempfile.TemporaryDirectory() as d:
            t = self._gain(self._project(d, tt))
        self.assertEqual((t["proceeds"], t["acb"], t["gain"]), (0, 0, 0))

    def test_library_rules(self):
        from taxjson.lib.renames import DatedRename, apply_dated_renames

        def rows(day):
            return [T(action="BUYSELL", date="2025-01-03", symbol="SYN.TO",
                      quantity=5, currency="CAD", account="m", id="b"),
                    T(action="ASSIGN", date=day, symbol="SYN.WT.TO",
                      quantity=-10, currency="CAD", account="m", id="x",
                      exercise_of="SYN.TO")]

        def run(day, **kw):
            import io
            dr = DatedRename(old="SYN.TO", new="NEW.TO", date="2025-03-01",
                             **kw)
            out = apply_dated_renames(rows(day), [dr], stream=io.StringIO())
            return next(t.exercise_of for t in out if t.id == "x")
        self.assertEqual(run("2025-06-02"), "NEW.TO")
        self.assertEqual(run("2025-03-01"), "NEW.TO")
        # Before the rename its shares were the old symbol's.
        self.assertEqual(run("2025-02-03"), "SYN.TO")

    def test_share_leg_kept_on_the_old_symbol_keeps_the_target(self):
        # late=separate: another company uses the ticker now, and the
        # exercise delivers its shares.
        import io
        from taxjson.lib.renames import DatedRename, apply_dated_renames
        rows = [T(action="BUYSELL", date="2025-01-03", symbol="SYN.TO",
                  quantity=5, currency="CAD", account="m", id="b"),
                T(action="ASSIGN", date="2025-06-02", symbol="SYN.WT.TO",
                  quantity=-10, currency="CAD", account="m", id="x",
                  exercise_of="SYN.TO"),
                T(action="BUYSELL", date="2025-06-02", symbol="SYN.TO",
                  quantity=10, currency="CAD", account="m", id="l")]
        dr = DatedRename(old="SYN.TO", new="NEW.TO", date="2025-03-01",
                         late="separate")
        out = apply_dated_renames(rows, [dr], stream=io.StringIO())
        self.assertEqual(next(t.exercise_of for t in out if t.id == "x"),
                         "SYN.TO")


if __name__ == "__main__":
    unittest.main()
