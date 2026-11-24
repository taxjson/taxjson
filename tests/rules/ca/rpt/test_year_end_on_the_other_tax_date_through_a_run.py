"""TJ-4984 end to end: `tjs run` on a generic CSV with a settlement-date
column, in a project whose tax_date is not its engine's walk date (US
"settle", Canada "trade"). The year-end positions in the gains file hold
the row of the year that comes after a next-year row in the walk, and a
position whose own rows cross the two date orders is said on the run's
console, headline first, its detail lines under it.

Made-up symbols (ZZQ, ZZR) and amounts only.
"""
import json
import tempfile
import unittest

from tax_rules import rule
from tax_rules.dual import cli, projects_both
from taxjson.rules import ids as R

_MAP = '''[columns]
date = "Date"
settle = "Settled"
action = "Type"
symbol = "Symbol"
quantity = "Quantity"
price = "Price"
amount = "Amount"
currency = "Currency"
[formats]
date = "%Y-%m-%d"
[actions]
"BUY" = "buy"
"SELL" = "sell"
'''
_HEAD = "Date,Settled,Type,Symbol,Quantity,Price,Amount,Currency\n"
_ROWS = {
    # ZZQ sold Dec 29 settling Jan 2 (next year's on settlement dates),
    # then bought again and ZZR bought, both settled Dec 30.
    "usa": ("USD", "settle", [
        "2025-01-06,2025-01-07,BUY,ZZQ,10,10,-100",
        "2025-12-29,2026-01-02,SELL,ZZQ,-10,12,120",
        "2025-12-30,2025-12-30,BUY,ZZQ,5,12,-60",
        "2025-12-30,2025-12-30,BUY,ZZR,10,20,-200"]),
    # ZZQ bought Jan 2 and settled that day (next year's on trade
    # dates) before the Dec 29 sale of ZZQ and purchase of ZZR settle.
    "canada": ("CAD", "trade", [
        "2025-06-02,2025-06-03,BUY,ZZQ,10,10,-100",
        "2025-12-29,2026-01-05,SELL,ZZQ,-5,20,100",
        "2025-12-29,2026-01-05,BUY,ZZR,10,20,-200",
        "2026-01-02,2026-01-02,BUY,ZZQ,10,20,-200"]),
}


class TestYearEndThroughARun(unittest.TestCase):
    def _run(self, country):
        cur, tax_date, rows = _ROWS[country]
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = projects_both(
            td.name, accounts='[accounts.gen]\ntype = "taxable"\n',
            **{country: {"tax_date": tax_date}}, files={
                "inputs/gen/activity.csv.toml": _MAP,
                "inputs/gen/activity.csv": _HEAD + "".join(
                    f"{r},{cur}\n" for r in rows)})[country]
        r = cli(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-3000:])
        doc = json.loads((root / "work" / "gen_gains_wash.json").read_text())
        self.assertEqual(doc["inventory_year_end_basis"], tax_date)
        self._gates(root)
        return ([(h["symbol"], h["qty"], h["total_cost"])
                 for h in doc["inventory_year_end"]],
                (r.stdout + r.stderr).splitlines())

    def _gates(self, root):
        # Review 2 (call d): `tjs checklist` lists the position in its
        # run step, and `tjs run --strict` stops on it.
        ck = " ".join((lambda r: r.stdout + r.stderr)(
            cli(root, "checklist", "--run")).split())
        self.assertIn("1 year-end position(s) not exact on the project's "
                      "tax_date (", ck)
        self.assertIn("! 8 run-clean: 1 year-end position(s) not exact", ck)
        r = cli(root, "run", "--no-input", "--strict")
        self.assertEqual(r.returncode, 1, (r.stdout + r.stderr)[-2000:])
        self.assertIn("tjs run: error: --strict: 1 year-end position(s) "
                      "are not exact on the project's tax_date — aborting",
                      r.stdout + r.stderr)

    def _said(self, lines, head, detail, check):
        # Once, under the account's gains step: the headline, then the
        # detail lines as its continuation.
        at = [i for i, ln in enumerate(lines) if "ATTENTION" in ln]
        self.assertEqual(len(at), 1, lines)
        self.assertEqual(lines[at[0]].strip(), head)
        self.assertTrue(lines[at[0] + 1].strip().startswith(detail),
                        lines[at[0] + 1])
        self.assertTrue(lines[at[0] + 2].strip().startswith(check),
                        lines[at[0] + 2])
        self.assertEqual(lines[at[0] - 1], "==> Calculating capital gains")

    @rule(R.US_RPT_13)
    def test_usa_on_settlement_dates(self):
        ye, lines = self._run("usa")
        self.assertEqual(ye, [("ZZQ.US", 10.0, 100.0),
                              ("ZZR.US", 10.0, 200.0)])
        self._said(
            lines, "warning: ATTENTION: year end: gen: ZZQ.US: the lots at the "
            "end of 2025-12-31 (by settlement date) are not exact",
            "A row that settles after that day stands before 1 day(s) of "
            "rows settled by it (2025-12-30) in trade order",
            "Check these lots (`tjs edge-cases`, the closed year's "
            "record) against the broker's statement.")

    @rule(R.CA_RPT_07)
    def test_canada_on_trade_dates(self):
        ye, lines = self._run("canada")
        self.assertEqual(ye, [("ZZQ.TO", 5.0, 50.0),
                              ("ZZR.TO", 10.0, 200.0)])
        self._said(
            lines, "warning: ATTENTION: year end: ZZQ.TO: the pool at the end "
            "of 2025-12-31 (by trade date) is not exact",
            "A row traded after that day settles before a row traded by "
            "it, and both touch this pool",
            "Check this year-end pool (`tjs edge-cases`) against your "
            "records.")


if __name__ == "__main__":
    unittest.main()
