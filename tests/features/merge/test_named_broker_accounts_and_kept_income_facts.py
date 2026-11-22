"""Parser, import and dedup fixes of fix round 7 (wave C, group
parsers2): RBC holdings with a CASH ticker, dotted preferred series L
and V, the generic importer's fee-only and zero-net rows and its named
brokers, a Webull class-share MULT, a mapped warrant exercise target
and dedup keeping a copy's income facts. Every fixture is SYNTHETIC:
fake account ids (99900001, U1234567), invented tickers.

Formerly: test_fix_1029_r7c_parsers2
"""
import json
import tempfile
import unittest

from _qa_project import project, tj


def _totals(root):
    """`tjs sum --json`'s filing totals of a project."""
    return json.loads(tj(root, "sum", "--json").stdout)["filing"]["totals"]


def _toml_2025(root):
    """The project's year set to 2025 (the fixtures' trades)."""
    p = root / "taxjson.toml"
    p.write_text(p.read_text().replace("year = 2024", "year = 2025")
                 .replace("option_grant_timing_since = 2024",
                          "option_grant_timing_since = 2025"))


class TestGenericNamedBrokers(unittest.TestCase):
    """TJ-0975: one account number at two named brokers is two
    accounts; their identical trades are both booked.

    Formerly: test_fix_1029_r7c_parsers2.TestGenericNamedBrokers"""

    MAP = ('[columns]\ndate="Date"\naction="Action"\nsymbol="Symbol"\n'
           'quantity="Quantity"\nprice="Price"\namount="Amount"\n'
           '[defaults]\ncurrency="CAD"\n[actions]\nBUY="buy"\nSELL="sell"\n'
           '[broker]\nname="{broker}"\naccount="99900001"\n')
    CSV = ("Date,Action,Symbol,Quantity,Price,Amount\n"
           "2025-01-02,BUY,SYN.TO,10,10,-100\n"
           "2025-06-02,SELL,SYN.TO,-10,20,200\n")

    def test_run_books_both_brokers(self):
        files = {}
        for b in ("alpha", "beta"):
            files[f"margin/generic_{b}.csv"] = self.CSV
            files[f"margin/generic_{b}.csv.toml"] = self.MAP.format(broker=b)
        with tempfile.TemporaryDirectory() as td:
            root = project(td, "p", files)
            _toml_2025(root)
            tj(root, "run", "--strict", "--no-input")
            t = _totals(root)
        self.assertEqual((t["proceeds"], t["acb"], t["gain"]),
                         (400.0, 200.0, 200.0))

    def test_plan_dedup_named_brokers(self):
        from taxjson.bin.taxjson_sort import plan_dedup
        rows = [{"id": "x1", "source": "a.csv", "source_account": "h1",
                 "date": "2025-01-02"},
                {"id": "x1", "source": "b.csv", "source_account": "h1",
                 "date": "2025-01-02"}]
        self.assertEqual(plan_dedup(rows, {}).drop, [1])
        plan = plan_dedup(rows, {}, {"a.csv": "generic:alpha",
                                     "b.csv": "generic:beta"})
        self.assertEqual(plan.drop, [])
        plan = plan_dedup(rows, {}, {"a.csv": "generic:alpha",
                                     "b.csv": "generic:alpha"})
        self.assertEqual(plan.drop, [1])


_IB_HEAD = ("Statement,Header,Field Name,Field Value\n"
            "Statement,Data,BrokerName,{broker}\n"
            "Statement,Data,Title,Activity Statement\n")
_IB_PIL = ("Dividends,Header,Currency,Account,Date,Description,Amount\n"
           "Dividends,Data,CAD,U1234567,2025-06-02,SYN(CA9990000001) "
           "Payment in Lieu of Dividend CAD 1 per Share,100\n")


class TestDedupKeepsIncomeFacts(unittest.TestCase):
    """TJ-0980: the kept copy of a duplicate keeps an income fact only
    the dropped copy states.

    Formerly: test_fix_1029_r7c_parsers2.TestDedupKeepsIncomeFacts"""

    def test_plan_fills_missing_fact(self):
        from taxjson.bin.taxjson_sort import deduplicate
        from taxjson.model.transaction import TaxTransaction

        def row(src, dealer):
            return TaxTransaction(
                id="x1", action="DIVIDEND_IN_LIEU", date="2025-06-02",
                time="", symbol="SYN.TO", quantity=0, currency="CAD",
                price=0, net_amount=100, gross_amount=100, account="m",
                source=src, dealer_country=dealer)
        kept = deduplicate([row("a.csv", ""), row("b.csv", "CA")])
        self.assertEqual([(t.source, t.dealer_country) for t in kept],
                         [("a.csv", "CA")])
        kept = deduplicate([row("a.csv", "US"), row("b.csv", "CA")])
        self.assertEqual([t.dealer_country for t in kept], ["US"])

    def test_run_keeps_the_canadian_dealer(self):
        files = {"margin/a_plain.csv": _IB_HEAD.format(
                     broker="Interactive Brokers") + _IB_PIL,
                 "margin/b_full.csv": _IB_HEAD.format(
                     broker="Interactive Brokers Canada Inc.") + _IB_PIL}
        with tempfile.TemporaryDirectory() as td:
            root = project(td, "p", files)
            _toml_2025(root)
            tj(root, "run", "--strict", "--no-input")
            s = json.loads(tj(root, "sum", "--json").stdout)
        self.assertEqual(s["totals"].get("dividend"), 100.0)
        self.assertFalse(s["totals"].get("pil"))


if __name__ == "__main__":
    unittest.main()
