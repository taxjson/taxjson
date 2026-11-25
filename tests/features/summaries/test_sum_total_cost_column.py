"""`tjs sum`'s FOR THE RETURN block in Canada: TOTAL COST between
OUTLAYS and GAIN, COST(ACB) + OUTLAYS added from the cells as shown, so
every Schedule 3 line and the RETURN row foot, PROCEEDS − TOTAL COST =
GAIN; `--json`'s `total_cost` on each line, account and the totals (src/
taxjson/cli/estimate.py `_return_figures`, `_sum_return_ca`). A US
project's Form 8949 block has no such column: its (e) cost already
holds the costs of the sale. No money column but DENIED is dropped for
width: too wide, the SCHEDULE 3 LINE labels shorten (their line codes
always whole), then DENIED drops with a `! ` line giving its total, then
one record per line, TOTAL COST in each (`_s3_block`, `_s3_label`;
backlog TJ-5333).

The mixed USD/CAD project, the style project and the recorded golden books.
"""
import json
import re
import unittest
from decimal import Decimal

import _golden
from _style import project
from _support.filing_mixed_currency_project import Project, cents

LEGEND = ("GAIN = PROCEEDS − COST(ACB) − OUTLAYS = PROCEEDS − TOTAL COST, the allowed gain; "
          "DENIED = superficial losses denied.")
_MONEY = re.compile(r"-?[\d,]+\.\d\d")


def _foots(test, rec, where):
    test.assertEqual(cents(rec["total_cost"]), cents(rec["acb"]) + cents(rec["outlays"]), where)
    test.assertEqual(cents(rec["proceeds"]) - cents(rec["total_cost"]), cents(rec["gain"]), where)


def _filing_foots(test, filing):
    for ln in filing["lines"]:
        _foots(test, ln, ln.get("title") or ln["key"])
    for a in filing["accounts"]:
        _foots(test, a, a["account"])
    _foots(test, filing["totals"], "RETURN")


def _table(text):
    """The block's header and its rows (each a list of its money cells)."""
    lines = text.splitlines()
    i = next(n for n, ln in enumerate(lines) if ln.startswith("SCHEDULE 3 LINE"))
    rows = []
    for ln in lines[i + 2:]:
        if not ln.strip():
            break
        if set(ln) == {"-"}:
            continue
        rows.append([Decimal(m.replace(",", "")) for m in _MONEY.findall(ln)])
    return lines[i], lines[i - 2:i], rows


def _legend(text):
    """The block's text above its table, its wrapping undone."""
    block = text[text.index("FOR THE RETURN"):]
    return " ".join(block[:block.index("SCHEDULE 3 LINE")].split())


def _block_lines(text):
    """The block's lines from its heading to the end of its table (the
    `! ` lines after it are never wrapped: docs/output-style.md)."""
    lines = text.splitlines()
    i = next(n for n, ln in enumerate(lines) if ln.startswith("FOR THE RETURN"))
    j = next(n for n, ln in enumerate(lines) if ln.startswith("SCHEDULE 3 LINE"))
    k = next(n for n in range(j, len(lines)) if not lines[n].strip())
    return lines[i:k]


class TestTheMixedProject(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = Project()
        cls.p.run()
        cls.sum = cls.p.json("sum")

    def test_every_line_and_the_return_foot_in_the_json(self):
        _filing_foots(self, self.sum["filing"])
        self.assertEqual(len(self.sum["filing"]["lines"]), 2)

    def test_the_text_block(self):
        for width in ("0", "120"):
            with self.subTest(width=width):
                r = self.p.tjs("sum", TAXJSON_WIDTH=width)
                self.assertEqual(r.returncode, 0, r.stderr)
                head, above, rows = _table(r.stdout)
                self.assertEqual(head.split(), ["SCHEDULE", "3", "LINE", "PROCEEDS", "COST(ACB)",
                                                "OUTLAYS", "TOTAL", "COST", "GAIN", "DENIED"])
                self.assertIn(LEGEND, above)
                self.assertLessEqual(len(LEGEND), 120)
                self.assertEqual(len(rows), 3)
                for cells in rows:
                    proceeds, acb, outlays, total, gain, _denied = cells
                    self.assertEqual(acb + outlays, total)
                    self.assertEqual(proceeds - total, gain)
                self.assertEqual([ln for ln in r.stdout.splitlines() if len(ln) > 120], [])

    def test_the_return_row_is_the_json_s(self):
        _h, _a, rows = _table(self.p.tjs("sum").stdout)
        t = self.sum["filing"]["totals"]
        self.assertEqual(rows[-1][:5], [cents(t[k]) for k in ("proceeds", "acb", "outlays",
                                                               "total_cost", "gain")])


class TestTheNarrowWidths(unittest.TestCase):
    """The style project's block (2024: two periods; its widest label,
    53, makes the table 116 wide): every money column at every width the
    table fits, the labels shortened first, then DENIED dropped and said,
    then records; the cells are `sum --json`'s at every width."""
    HEAD_ALL = ["SCHEDULE", "3", "LINE", "PROCEEDS", "COST(ACB)", "OUTLAYS", "TOTAL", "COST",
                "GAIN", "DENIED"]
    CELLS = ("proceeds", "acb", "outlays", "total_cost", "gain", "denied")
    TOTAL = "= PROCEEDS − TOTAL COST"
    # The widest label at each width: the words, the short words, cut
    # after a whole word, then the period mark and the codes alone.
    WIDEST = {"0": "options, crypto & other properties (P2) (15199/15300)",
              "160": "options, crypto & other properties (P2) (15199/15300)",
              "120": "options, crypto & other properties (P2) (15199/15300)",
              "117": "options, crypto & other properties (P2) (15199/15300)",
              "116": "options, crypto & other properties (P2) (15199/15300)",
              "100": "options, crypto… (P2) (15199/15300)",
              "90": "options… (P2) (15199/15300)",
              "80": "(P1) 10689/10690"}

    @classmethod
    def setUpClass(cls):
        cls.proj = project("canada")
        r = cls.proj.run("sum", "--json", TAXJSON_WIDTH="0")
        assert r.returncode == 0, r.stderr
        cls.filing = json.loads(r.stdout)["filing"]
        cls.totals = cls.filing["totals"]

    def _sum(self, width, *extra):
        r = self.proj.run("sum", *extra, TAXJSON_WIDTH=width)
        self.assertEqual(r.returncode, 0, r.stderr)
        return str(r.stdout)

    def _labels(self, out):
        lines = out.splitlines()
        i = next(n for n, ln in enumerate(lines) if ln.startswith("SCHEDULE 3 LINE"))
        rows = [ln for ln in lines[i + 2:i + 2 + len(self.filing["lines"])]]
        return [_MONEY.split(ln, 1)[0].rstrip() for ln in rows]

    def _codes_whole(self, labels):
        for lab, ln in zip(labels, self.filing["lines"]):
            self.assertIn(f"{ln['proceeds_code']}/{ln['gain_code']}", lab)
            self.assertIn(f"(P{ln['period']})", lab)

    def test_every_money_column_where_the_table_fits(self):
        for width, widest in self.WIDEST.items():
            with self.subTest(width=width):
                out = self._sum(width)
                head, _above, rows = _table(out)
                self.assertEqual(head.split(), self.HEAD_ALL)
                self.assertIn(self.TOTAL, _legend(out))
                self.assertEqual(rows[-1], [cents(self.totals[k]) for k in self.CELLS])
                for cells, ln in zip(rows, self.filing["lines"]):
                    self.assertEqual(cells, [cents(ln[k]) for k in self.CELLS])
                labels = self._labels(out)
                self.assertEqual(max(labels, key=len), widest)
                self._codes_whole(labels)
                self.assertFalse([ln for ln in out.splitlines() if ln.startswith("! DENIED")])
                if width != "0":
                    self.assertEqual([ln for ln in _block_lines(out) if len(ln) > int(width)], [])
        self.assertIn(LEGEND, self._sum("116"))

    def test_then_denied_drops_and_says_so(self):
        out = self._sum("75")
        head, _above, rows = _table(out)
        self.assertEqual(head.split(), self.HEAD_ALL[:-1])
        self.assertIn(self.TOTAL, _legend(out))
        self.assertEqual(rows[-1], [cents(self.totals[k]) for k in self.CELLS[:-1]])
        self.assertEqual(self._labels(out), ["(P1) 10689/10690", "(P2) 13199/13200",
                                             "(P2) 15199/15300"])
        denied = f"{Decimal(str(self.totals['denied'])):,.2f}"
        self.assertEqual([ln for ln in out.splitlines() if "DENIED" in ln and ln.startswith("! ")],
                         [f"! DENIED left out at this width (superficial losses denied: {denied})"
                          " — tjs sum --details"])
        self.assertEqual([ln for ln in _block_lines(out) if len(ln) > 75], [])
        # --details keeps DENIED: one record per line where the table does not fit.
        det = self._sum("75", "--details")
        self.assertNotIn("DENIED left out", det)
        block = det[det.index("FOR THE RETURN"):]
        self.assertIn(f"denied {denied}", block)

    def test_records_carry_total_cost(self):
        out = self._sum("60")
        self.assertFalse(any(ln.startswith("SCHEDULE 3 LINE") for ln in out.splitlines()))
        block = out[out.index("FOR THE RETURN"):]
        block = block[:block.index("\n! ")]
        recs = [r for r in block.split("\n\n") if "  proceeds " in r]
        self.assertEqual(len(recs), len(self.filing["lines"]) + 1)
        for rec, want in zip(recs, self.filing["lines"] + [self.totals]):
            got = {k: v for k, v in re.findall(r"(proceeds|cost\(acb\)|outlays|total cost|gain"
                                               r"|denied) (-?[\d,]+\.\d\d)", rec)}
            self.assertEqual({k: cents(v.replace(",", "")) for k, v in got.items()},
                             {"proceeds": cents(want["proceeds"]), "cost(acb)": cents(want["acb"]),
                              "outlays": cents(want["outlays"]),
                              "total cost": cents(want["total_cost"]),
                              "gain": cents(want["gain"]), "denied": cents(want["denied"])})
        self.assertTrue(recs[-1].startswith("RETURN\n"))
        self.assertIn(self.TOTAL, " ".join(block.split()))
        self.assertNotIn("DENIED left out", out)


class TestAUsProject(unittest.TestCase):
    def test_form_8949_s_block_is_unchanged(self):
        toml = ('[settings]\nlocal_timezone = "America/New_York"\nyear = 2025\n'
                'country = "usa"\nbase_currency = "USD"\nsource_currencies = []\n'
                '[accounts.margin]\ntype = "taxable"\n')
        p = Project(files={"margin": "BUYSELL 2025-01-06 10:00:00 QZNW 10 USD 10 100 1\n"
                                     "BUYSELL 2025-03-06 10:00:00 QZNW -10 USD 12 119 1\n"},
                    toml=toml, planted=False)
        p.run()
        filing = p.json("sum")["filing"]
        self.assertNotIn("total_cost", filing["totals"])
        self.assertFalse([ln for ln in filing["parts_8949"] if "total_cost" in ln])
        self.assertNotIn("TOTAL COST", p.tjs("sum").stdout)


class TestTheGoldenBooks(unittest.TestCase):
    """Each Canadian golden book's recorded `sum --json`: every line, account
    and the RETURN row foot on TOTAL COST."""

    def test_each_book(self):
        for book in ("demo", "style-canada", "evidence"):
            p = (_golden.GOLDEN / book / "expected" / "_views"
                 / f"{_golden.view_name({'argv': 'sum --json'})}.stdout.json")
            with self.subTest(book=book):
                _filing_foots(self, json.loads(p.read_text(encoding="utf-8"))["filing"])


if __name__ == "__main__":
    unittest.main()
