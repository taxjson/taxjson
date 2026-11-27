"""Fix round 7 (2026-10-29, wave D), group corp: corporate actions (backlog
TJ-1022, TJ-1025, TJ-1026, TJ-1117, TJ-1132). Synthetic data only: made-up
tickers and account ids.

Formerly: test_fix_1029_r7d_corp"""
import tempfile
import unittest
from pathlib import Path

from _qa_project import console, flat, tj
from tax_rules import rule
from taxjson.rules import ids as R
from taxjson.model.transaction import TaxTransaction as T
import _paths


class TestTJ1025ParentOnlyABrokerCode(unittest.TestCase):
    """TJ-1025 (a regression of TJ-0658): a taxable spin-off whose parent
    is only a Questrade/RBC SEC# code booked its dividend on the bare
    code, so a Canadian dividend read as foreign. TJ-0658's own tests
    (a foreign parent with an ISIN or listing pays the dividend) stand:
    TestForeignParentSpinoffIncome in
    features.corporate_actions.test_ib_corporate_action_cancellations_and_deliveries.

    Formerly: test_fix_1029_r7d_corp.TestTJ1025ParentOnlyABrokerCode"""

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

    @rule(R.CA_CORP_07)
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
        head = _paths.REPO.joinpath(
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
            tj(root, "elections", "margin", "--set",
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
    return of capital is unchanged) stand:
    features.corporate_actions.test_ib_corporate_action_cancellations_and_deliveries.

    Formerly: test_fix_1029_r7d_corp.TestTJ1026S861OriginalShares"""

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
        from taxjson.lib.tx_load import coerce_transaction_row
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

    @rule(R.CA_CORP_14)
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

    @rule(R.CA_CORP_14)
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

    @rule(R.CA_CORP_14)
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

    @rule(R.CA_CORP_14)
    def test_a_questrade_project_keeps_the_trims_gain(self):
        # The parent count reaches the engine through a real run: parser,
        # election, books. 600 bought, 200 trimmed in 2023, the last 400
        # sold in 2024; the 2025 spin-off is on 400 shares.
        import json
        import re
        head = _paths.REPO.joinpath(
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
            tj(root, "elections", "margin", "--set", f"{eid}=rollover_s_86_1",
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


if __name__ == "__main__":
    unittest.main()
