"""Fix round 8, the transfers group: the export-coverage warning of an
account holding two brokers or two IB accounts (TJ-1286, TJ-1314, TJ-1315,
TJ-1338). A data check: no figure of the books moves (synthetic data only;
account ids are fake, pii-ok)."""
import tempfile
import unittest
from datetime import date
from pathlib import Path

from _qa_project import tj
from test_fix_1029_r7c_coverage import (_GENERIC_MAP, _cfg, _checklist, _ib,
                                        _project)
from test_fix_1029_r7e_cash import _generic
from test_fix_1029_r7e_ib import _A, _B

from taxjson.lib import export_coverage as EC


def _root(td, year=2025):
    return _project(Path(td) / "p", year, {"margin": "taxable"},
                    extra="combined_broker_accounts = true\n")


def _generic_files(root, **brokers):
    for broker, (acct, rows) in brokers.items():
        f = root / "inputs/margin" / f"generic_{broker}.csv"
        f.write_text(_generic(rows))
        f.with_name(f.name + ".toml").write_text(
            _GENERIC_MAP.format(broker=broker, account=acct))


class TestTJ1286RenameOrSplitBetweenABuyAndItsSale(unittest.TestCase):
    """A .tt SPLIT or RENAME between a broker's own buy and its sale: the sale
    is of its own units, and closes nothing at the other broker."""

    BUY = ("2025-03-03", "BUY", "SYN.TO", 10)
    TAIL = ("2025-12-15", "BUY", "ZZZ.TO", 5)

    def _gaps(self, beta_sale, tt):
        with tempfile.TemporaryDirectory() as td:
            root = _root(td)
            _generic_files(root, alpha=("99900001", [self.BUY]),
                           beta=("99900002", [self.BUY, beta_sale, self.TAIL]))
            (root / "inputs/margin/x.tt").write_text(tt)
            tj(root, "run", "--no-input")
            gaps = EC.find_gaps(root, _cfg(root), today=date(2026, 3, 1))
            return [(g.broker, g.end, [q for _s, q in g.positions])
                    for g in gaps if not g.info]

    def test_a_rename(self):
        self.assertEqual(
            self._gaps(("2025-05-01", "SELL", "SYQ.TO", -10),
                       "RENAME 2025-04-01 SYN.TO SYQ.TO\n"),
            [("generic-alpha", "2025-03-03", [10.0])])

    def test_a_split_line_that_renames(self):
        self.assertEqual(
            self._gaps(("2025-05-01", "SELL", "SYQ.TO", -10),
                       "SPLIT 2025-04-01 09:30:00 SYN.TO SYQ.TO 1\n"),
            [("generic-alpha", "2025-03-03", [10.0])])

    def test_a_split(self):
        self.assertEqual(
            self._gaps(("2025-05-01", "SELL", "SYN.TO", -20),
                       "SPLIT 2025-04-01 09:30:00 SYN.TO SYN.TO 2\n"),
            [("generic-alpha", "2025-03-03", [20.0])])

    def test_control_no_event_between(self):
        # TJ-1158's own case.
        self.assertEqual(
            self._gaps(("2025-05-01", "SELL", "SYN.TO", -10), "# nothing\n"),
            [("generic-alpha", "2025-03-03", [10.0])])

    def test_control_a_sale_beyond_its_own_units_still_closes_the_rest(self):
        self.assertEqual(
            self._gaps(("2025-05-01", "SELL", "SYQ.TO", -14),
                       "RENAME 2025-04-01 SYN.TO SYQ.TO\n"),
            [("generic-alpha", "2025-03-03", [6.0])])


class TestTJ1314AnotherUnitsBuyInTheGap(unittest.TestCase):
    """A purchase inside the gap is not units the short export still held."""

    TAIL = ("2026-10-20", "BUY", "ZZZ.TO", 5)

    def _ib(self, acct, day, qty):
        return ('Statement,Header,Field Name,Field Value\n'
                'Statement,Data,BrokerName,Interactive Brokers\n'
                'Account Information,Header,Field Name,Field Value\n'
                f'Account Information,Data,Account,{acct}\n'
                'Account Information,Data,Base Currency,CAD\n'
                'Trades,Header,DataDiscriminator,Asset Category,Currency,'
                'Symbol,Date/Time,Quantity,T. Price,C. Price,Proceeds,'
                'Comm/Fee,Basis,Realized P/L,MTM P/L,Code\n'
                f'Trades,Data,Order,Stocks,CAD,ZQP,"{day}, 10:00:00",{qty},60,'
                f'60,{-60 * qty}.00,0,{60 * qty}.00,0,0,O\n')

    def test_two_ib_accounts(self):
        with tempfile.TemporaryDirectory() as td:
            root = _root(td, 2026)
            (root / "inputs/margin/ib_a.csv").write_text(
                self._ib(_A, "2026-01-05", 100))
            (root / "inputs/margin/ib_b.csv").write_text(
                self._ib(_B, "2026-03-05", 50))
            tj(root, "run", "--no-input")
            gaps = EC.find_gaps(root, _cfg(root), today=date(2026, 10, 30))
        self.assertEqual(sorted((g.end, g.positions) for g in gaps),
                         [("2026-01-05", [("ZQP.TO", 100.0)]),
                          ("2026-03-05", [("ZQP.TO", 50.0)])])

    def _generic_gaps(self, alpha, beta):
        with tempfile.TemporaryDirectory() as td:
            root = _root(td, 2026)
            _generic_files(root, alpha=("99900001", alpha),
                           beta=("99900002", beta))
            tj(root, "run", "--no-input")
            gaps = EC.find_gaps(root, _cfg(root), today=date(2026, 10, 30))
            return sorted((g.broker, g.end, g.positions) for g in gaps)

    def test_two_generic_brokers(self):
        self.assertEqual(
            self._generic_gaps([("2026-01-05", "BUY", "ZQP.TO", 100)],
                               [("2026-03-05", "BUY", "ZQP.TO", 50)]),
            [("generic-alpha", "2026-01-05", [("ZQP.TO", 100.0)]),
             ("generic-beta", "2026-03-05", [("ZQP.TO", 50.0)])])

    def test_control_a_later_sale_still_closes_it_after_the_buy(self):
        # 100 + 50 bought by beta, beta sells 120: its own 50, then 70 of
        # alpha's; 30 of alpha's are left.
        self.assertEqual(
            self._generic_gaps([("2026-01-05", "BUY", "ZQP.TO", 100)],
                               [("2026-03-05", "BUY", "ZQP.TO", 50),
                                ("2026-04-05", "SELL", "ZQP.TO", -120),
                                self.TAIL]),
            [("generic-alpha", "2026-01-05", [("ZQP.TO", 30.0)])])

    def test_control_a_tt_buy_then_a_sale_as_before(self):
        kept = EC.closed_later(
            [("SYN", 10.0)],
            [{"action": "BUYSELL", "date": "2025-05-01", "symbol": "SYN",
              "quantity": 5},
             {"action": "BUYSELL", "date": "2025-06-01", "symbol": "SYN",
              "quantity": -12}])
        self.assertEqual(kept, ([("SYN", 3.0)], [], []))

    def test_a_sale_then_a_buy_leaves_what_the_sale_left(self):
        kept = EC.closed_later(
            [("SYN", 10.0)],
            [{"action": "BUYSELL", "date": "2025-05-01", "symbol": "SYN",
              "quantity": -8},
             {"action": "BUYSELL", "date": "2025-06-01", "symbol": "SYN",
              "quantity": 5}])
        self.assertEqual(kept, ([("SYN", 2.0)], [], []))

    def test_a_written_option_too(self):
        kept = EC.closed_later(
            [("SYN260116C00010000", -3.0)],
            [{"action": "BUYSELL", "date": "2025-05-01",
              "symbol": "SYN260116C00010000", "quantity": -2}])
        self.assertEqual(kept, ([("SYN260116C00010000", -3.0)], [], []))


class TestTJ1315SpinOffRowOfBothAccounts(unittest.TestCase):
    """The spin-off row IB's two statements report is one merged row with no
    broker account: it dates both accounts' exports."""

    @staticmethod
    def _ib(acct, buy, spun, value):
        return ('Statement,Header,Field Name,Field Value\n'
                'Statement,Data,BrokerName,Interactive Brokers\n'
                'Account Information,Header,Field Name,Field Value\n'
                f'Account Information,Data,Account,{acct}\n'
                'Account Information,Data,Base Currency,CAD\n'
                'Trades,Header,DataDiscriminator,Asset Category,Currency,'
                'Symbol,Date/Time,Quantity,T. Price,C. Price,Proceeds,'
                'Comm/Fee,Basis,Realized P/L,MTM P/L,Code\n'
                f'Trades,Data,Order,Stocks,CAD,PARNT,"2026-01-05, 10:00:00",'
                f'{buy},60,60,{-60 * buy}.00,0,{60 * buy}.00,0,0,O\n'
                'Corporate Actions,Header,Asset Category,Currency,Report Date,'
                'Date/Time,Description,Quantity,Proceeds,Value,Realized P/L,'
                'Code\n'
                'Corporate Actions,Data,Stocks,CAD,2026-04-01,'
                '"2026-04-01, 20:25:00","PARNT(CA0000000777) Spinoff  1 for 5 '
                f'(SPNCO, SPINCO CORP, CA0000000778)",{spun},0,{value},0,\n')

    def _gaps(self, a, b):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", 2026, {"margin": "taxable"})
            (root / "inputs/margin/ib_a.csv").write_text(a)
            (root / "inputs/margin/ib_b.csv").write_text(b)
            tj(root, "elect", "margin", "--set",
               "20260401-parnt-spnco-94ba=taxable_deemed_dividend",
               check=False)
            r = tj(root, "run", "--no-input", check=False)
            gaps = EC.find_gaps(root, _cfg(root), today=date(2026, 10, 30))
            return r.stdout + r.stderr, [(g.end, g.positions) for g in gaps]

    def test_one_warning_at_the_statements_last_row(self):
        _out, gaps = self._gaps(self._ib(_A, 100, 20, 900),
                                self._ib(_B, 50, 10, 450))
        self.assertEqual(gaps, [("2026-04-01", [("PARNT.TO", 150.0),
                                                ("SPNCO.TO", 30.0)])])

    def test_control_a_row_in_one_statement_dates_that_one_only(self):
        # B's statement holds no spin-off row: its export ends with its trade.
        b = self._ib(_B, 50, 10, 450).split("Corporate Actions,Header")[0]
        _out, gaps = self._gaps(self._ib(_A, 100, 20, 900), b)
        self.assertEqual(sorted(g[0] for g in gaps),
                         ["2026-01-05", "2026-04-01"])


class TestTJ1338MergedGapAfterLaterRows(unittest.TestCase):
    """Two IB accounts' gaps of one end: each takes from the account's holding
    at the end, and later rows scale what each took: the figures one
    broker-wide unit gave."""

    def _after(self, tt):
        with tempfile.TemporaryDirectory() as td:
            root = _root(td)
            (root / "inputs/margin/a.csv").write_text(
                _ib(_A, "March 3, 2025", "ZQP", 100))
            (root / "inputs/margin/b.csv").write_text(
                _ib(_B, "March 3, 2025", "ZQP", 50))
            (root / "inputs/margin/x.tt").write_text(tt)
            tj(root, "run", "--no-input")
            gaps = EC.find_gaps(root, _cfg(root), today=date(2026, 3, 1))
            return [g.positions for g in gaps]

    SALE = "BUYSELL 2025-03-03 15:00:00 ZQP.TO -30 CAD 15 450 0\n"

    def test_a_rename_with_a_ratio(self):
        self.assertEqual(
            self._after("SPLIT 2025-06-02 09:30:00 ZQP.TO ZQN.TO 2\n"),
            [[("ZQP.TO", 300.0)]])

    def test_a_sale_then_a_rename_with_a_ratio(self):
        self.assertEqual(
            self._after(self.SALE
                        + "SPLIT 2025-06-02 09:30:00 ZQP.TO ZQN.TO 2\n"),
            [[("ZQP.TO", 240.0)]])

    def test_a_split_then_a_rename(self):
        self.assertEqual(
            self._after("SPLIT 2025-05-01 09:30:00 ZQP.TO ZQP.TO 2\n"
                        "SPLIT 2025-06-02 09:30:00 ZQP.TO ZQN.TO 1\n"),
            [[("ZQP.TO", 300.0)]])

    def test_a_sale_then_a_reverse_split(self):
        self.assertEqual(
            self._after(self.SALE
                        + "SPLIT 2025-06-02 09:30:00 ZQP.TO ZQP.TO 0.1\n"),
            [[("ZQP.TO", 12.0)]])

    def test_control_the_holding_at_the_end_caps_the_sum(self):
        # TJ-1313's own case.
        self.assertEqual(self._after(self.SALE), [[("ZQP.TO", 120.0)]])

    def test_control_two_brokers_each_keep_their_own_cap(self):
        with tempfile.TemporaryDirectory() as td:
            root = _root(td)
            _generic_files(
                root,
                alpha=("99900001", [("2025-03-03", "BUY", "ZQP.TO", 100)]),
                beta=("99900002", [("2025-03-03", "BUY", "ZQP.TO", 50)]))
            tj(root, "run", "--no-input")
            gaps = EC.find_gaps(root, _cfg(root), today=date(2026, 3, 1))
        self.assertEqual(sorted((g.broker, g.positions) for g in gaps),
                         [("generic-alpha", [("ZQP.TO", 100.0)]),
                          ("generic-beta", [("ZQP.TO", 50.0)])])


class TestChecklistKeepsAsking(unittest.TestCase):
    def test_a_rename_gap_needs_attention(self):
        with tempfile.TemporaryDirectory() as td:
            root = _root(td)
            buy = ("2025-03-03", "BUY", "SYN.TO", 10)
            _generic_files(
                root, alpha=("99900001", [buy]),
                beta=("99900002", [buy, ("2025-05-01", "SELL", "SYQ.TO", -10),
                                   ("2025-12-15", "BUY", "ZZZ.TO", 5)]))
            (root / "inputs/margin/x.tt").write_text(
                "RENAME 2025-04-01 SYN.TO SYQ.TO\n")
            tj(root, "run", "--no-input")
            self.assertEqual(_checklist(root).status, "attention")


if __name__ == "__main__":
    unittest.main()
