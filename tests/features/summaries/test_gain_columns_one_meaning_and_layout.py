"""One meaning of every money column and one layout in Canada's three
summary tables (backlog TJ-5336), and `tjs gains-cra` always the whole
text (TJ-5337, TJ-5342): src/taxjson/lib/gain_columns.py (`table`,
`return_table`, `label`, `sum_legend`, `denied_forms`, `fit_legend`),
src/taxjson/lib/gains_cra.py `render`, `_table_lines`, `_wrapped`,
src/taxjson/lib/gains_base.py `render`, `_totals_ca`, `_legend`,
src/taxjson/cli/gains_filing.py `_gains_cra_everything`,
src/taxjson/cli/estimate.py `_sum_return_ca`, `_sum_tables`.

The columns, in this order: PROCEEDS, COST(ACB) as filed, OUTLAYS, TOTAL
COST, GAIN as filed, DENIED (INFO) last (its heading on two lines): every
loss denied (`denied_all`, TJ-5341): the superficial losses, deferred and
for good, and an in-kind contribution's (CA-INKIND-03; gains-base marks
its row `†`, TJ-5340), information only, COST(ACB) already reduced by
them. Every row and total foots PROCEEDS − COST(ACB) − OUTLAYS = PROCEEDS
− TOTAL COST = GAIN for every kind of denial. With no
wider cell of their own each money column ends at the same offset in
`tjs sum`'s FOR THE RETURN, `tjs gains-cra`'s ALL SALES and FOR THE
RETURN and `tjs gains-base`'s TOTALS; gains-cra's two always line up
(each sized from the other's figures). A column the width leaves out is
said in a `! ` line; no legend line is wider than the width (TJ-5343).

The demo (`tjs init --demo`); the mixed USD/CAD project with a loss
denied for good (a tfsa buy-back), an in-kind contribution's denied loss
(an IB transfer-out declared to a TFSA outside the project) and a put
sold at nothing added; the mixed project with a CAD loss and a USD gain
wider than any Schedule 3 line's; the mixed project as it is.
"""
import csv
import re
import subprocess
import unittest
from decimal import Decimal
from pathlib import Path

from _support.filing_mixed_currency_project import MARGIN_TT, TFSA_TT, TOML, Project, cents
from _support.transfer_in_kind import _ib
from _tmpfiles import private_dir

from taxjson.lib import gain_columns as GCOL
from taxjson.lib import gains_cra as GC
from taxjson.lib.dispatch import python_module_argv

_MONEY = re.compile(r"-?[\d,]+\.\d\d")
# The CSV's header row as it was before TJ-5336: a file's header is the contract.
_CSV_HEADER = ["Sale date", "Settle date", "Description", "Symbol", "Qty", "Currency",
               "Proceeds (native)", "FX", "Proceeds (CAD)", "ACB (CAD)", "Commissions (CAD)",
               "Gain/Loss (CAD)", "Denied (CAD)", "Account", "Notes"]
_INFO = "tjs gains-cra always prints everything: --details is no longer needed"
_KEYS = ("proceeds", "acb", "outlays", "total_cost", "gain", "denied_all")
# Each money heading's first line (DENIED (INFO) is on two).
_TOP = tuple(h.split(" (INFO)")[0] for h in GCOL.HEADS)
_MARK = re.compile(r" \[[\d,]+\]")
_DATE = re.compile(r"\d{4}-\d\d-\d\d")


def _after(text, heading, prefix):
    """The first line starting with `prefix` after the line starting with
    `heading`."""
    lines = text.splitlines()
    i = next(n for n, ln in enumerate(lines) if ln.startswith(heading))
    return next(ln for ln in lines[i:] if ln.startswith(prefix))


def _return_rows(sm, cra, base):
    """The RETURN row of sum's FOR THE RETURN, gains-cra's FOR THE RETURN
    and gains-base's TOTALS."""
    return (_after(sm, "FOR THE RETURN", "RETURN "),
            _after(cra, "FOR THE RETURN", "RETURN "),
            _after(base, "TOTALS", ("THE RETURN (tjs sum) ", "RETURN ")))


def _edges(line):
    """The offset where each money cell of `line` ends."""
    return [m.end() for m in _MONEY.finditer(line)]


def _cells(line):
    return [cents(m.group().replace(",", "")) for m in _MONEY.finditer(line)]


def _head_edges(head):
    """The offset where each money heading of a header line ends."""
    return [head.index(h) + len(h) for h in _TOP]


def _first_row(lines, i):
    """The index of the first row of the table whose heading starts at
    `lines[i]`: after the heading's lines and the rule under them."""
    return next(n for n in range(i, len(lines)) if set(lines[n]) == {"-"}) + 1


def _currency_table(text, cur):
    """gains-cra's table of `cur`: its heading's lines, its rows (each a
    sale's first line and its continuation lines) and the DESCRIPTION
    column's offset; None where it is one record per sale."""
    lines = text.splitlines()
    i = lines.index(f"SALES IN {cur}")
    if _DATE.match(lines[i + 1]):
        return None
    j = _first_row(lines, i)
    end = next(n for n in range(j, len(lines)) if set(lines[n]) == {"-"})
    desc = lines[j - 2].index("DESCRIPTION")
    rows = []
    for ln in lines[j:end]:
        if ln[:desc].strip():
            rows.append([ln])
        else:
            rows[-1].append(ln)
    return lines[i + 1:j - 1], rows, desc


def _description(row, desc):
    """A sale's Description as its lines show it, marks taken out."""
    first = row[0][desc:].split("  ")[0]
    return _MARK.sub("", " ".join([first] + [ln.strip() for ln in row[1:]]))


def _all_within(test, text, width):
    test.assertEqual([ln for ln in text.splitlines() if len(ln) > width], [])


def _foots(test, rec, where, gain="gain"):
    test.assertEqual(cents(rec["total_cost"]), cents(rec["acb"]) + cents(rec["outlays"]), where)
    test.assertEqual(cents(rec["proceeds"]) - cents(rec["total_cost"]), cents(rec[gain]), where)


class TestTheDemoLinesUp(unittest.TestCase):
    """The demo at TAXJSON_WIDTH=0, 120 and 100: the RETURN row of the
    three summary tables puts each money column's right edge at the same
    offset, with the same cells, sum's `--json` RETURN row."""

    @classmethod
    def setUpClass(cls):
        cls.p = Project(files={}, planted=False)
        demo = Path(private_dir()) / "demo"
        r = subprocess.run(python_module_argv("taxjson.bin.taxjson_run")
                           + ["init", "--demo", str(demo)], capture_output=True, text=True,
                           env=cls.p.env, stdin=subprocess.DEVNULL, timeout=600)
        assert r.returncode == 0, r.stderr[-2000:]
        cls.p.root = sorted(d for d in demo.iterdir() if d.is_dir() and d.name.isdigit())[0]
        cls.p.run()
        cls.totals = cls.p.json("sum")["filing"]["totals"]

    def _texts(self, width):
        out = []
        for cmd in ("sum", "gains-cra", "gains-base"):
            r = self.p.tjs(cmd, TAXJSON_WIDTH=width)
            self.assertEqual(r.returncode, 0, r.stderr)
            out.append(r.stdout)
        return out

    def test_the_return_rows_line_up(self):
        want = [cents(self.totals[k]) for k in _KEYS]
        self.assertTrue(want[-1], "the demo has a superficial loss denied")
        for width in ("0", "120", "117", "100"):
            sm, cra, base = self._texts(width)
            rows = _return_rows(sm, cra, base)
            with self.subTest(width=width):
                self.assertEqual([_cells(ln) for ln in rows], [want] * 3, rows)
                self.assertEqual(len({tuple(_edges(ln)) for ln in rows}), 1, "\n".join(rows))
                heads = (_after(sm, "FOR THE RETURN", "SCHEDULE 3 LINE"),
                         _after(cra, "FOR THE RETURN", "SCHEDULE 3 LINE"),
                         _after(base, "TOTALS", "ACCOUNT"))
                self.assertEqual(len({tuple(_head_edges(h)) for h in heads}), 1, "\n".join(heads))
                self.assertEqual(_head_edges(heads[0]), _edges(rows[0]))
                if width != "0":
                    self.assertEqual([h for h in heads if len(h) > int(width)], [])

    def test_the_line_labels_are_sum_s(self):
        for width in ("0", "100"):
            sm, cra, _base = self._texts(width)
            with self.subTest(width=width):
                self.assertEqual(self._labels(cra), self._labels(sm))

    def _labels(self, text):
        lines = text.splitlines()
        j = _first_row(lines, next(n for n, ln in enumerate(lines)
                                   if ln.startswith("SCHEDULE 3 LINE")))
        return [_MONEY.split(ln, 1)[0].rstrip() for ln in lines[j:j + 3]]

    def test_sum_s_labels_are_whole_at_117(self):
        """DENIED (INFO) on two lines gives the labels their room back."""
        lines = self.p.json("sum")["filing"]["lines"]
        self.assertEqual(len(lines), 3)
        out = self.p.tjs("sum", TAXJSON_WIDTH="117").stdout
        self.assertEqual(self._labels(out), [GCOL.label(ln) for ln in lines])
        block = out[out.index("FOR THE RETURN"):].split("\n\n")[0].splitlines()
        self.assertEqual([ln for ln in block if len(ln) > 117], [])
        self.assertIn("(INFO)", block[block.index(next(ln for ln in block
                                                       if ln.startswith("SCHEDULE 3 LINE"))) + 1])

    def test_a_column_left_out_is_said(self):
        """gains-base's per-sale table leaves out DAYS, then QTY; sum's
        account tables FEES, then PIL: each time a `! ` line says which
        and where to see it."""
        said = {}
        for width in ("0", "117", "112", "100"):
            out = self.p.tjs("gains-base", TAXJSON_WIDTH=width).stdout
            lines = out.splitlines()
            head = next((n for n, ln in enumerate(lines) if ln.startswith("ACCT ")), None)
            words = set() if head is None else set(lines[head].split() + lines[head + 1].split())
            gone = [] if head is None else [h for h in ("DAYS", "QTY") if h not in words]
            got = [ln for ln in lines if "left out of the sales" in ln]
            with self.subTest(width=width):
                self.assertEqual(got, [f"! {' and '.join(gone)} left out of the sales at this "
                                       f"width — tjs gains-base --json"] if gone else [])
            said[width] = gone
        self.assertEqual(said["112"], ["DAYS", "QTY"])
        self.assertEqual(said["0"], [])
        for width in ("0", "70"):
            out = self.p.tjs("sum", TAXJSON_WIDTH=width).stdout
            heads = [ln for ln in out.splitlines() if ln.startswith("ACCOUNT ")]
            gone = [h for h in ("FEES", "PIL") if not all(h in ln.split() for ln in heads)]
            got = [ln for ln in out.splitlines() if "left out of the account tables" in ln]
            with self.subTest(width=width):
                self.assertEqual(got, [f"! {' and '.join(gone)} left out of the account tables "
                                       f"at this width — tjs sum --json"] if gone else [])
            said[f"sum{width}"] = gone
        self.assertEqual(said["sum70"][:1], ["FEES"])
        # --details prints no `! ` lines: a line under the tables says it.
        det = " ".join(self.p.tjs("sum", "--details", TAXJSON_WIDTH="70").stdout.split())
        self.assertIn(f"{' and '.join(said['sum70'])} left out of the account tables at this "
                      f"width: each account's is in tjs sum --json.", det)

    def test_no_legend_line_is_wider_than_100_at_100(self):
        for cmd in ("gains-cra", "gains-base", "sum"):
            with self.subTest(cmd=cmd):
                _all_within(self, self.p.tjs(cmd, TAXJSON_WIDTH="100").stdout, 100)


# A loss denied for good (the tfsa buys QZPG back within 30 days) and an
# in-kind contribution's loss (100 QZK.TO out of the ib account at 600,
# declared to a TFSA outside the project: 400 denied, s.40(2)(g)(iv)).
_FOR_GOOD = ("BUYSELL 2025-02-03 10:00:00 QZPG.TO 20 CAD 30.00 600.00 0\n"
             "BUYSELL 2025-05-20 10:00:00 QZPG.TO -20 CAD 25.00 500.00 0\n")
_TFSA_BUY = "BUYSELL 2025-05-25 10:00:00 QZPG.TO 20 CAD 26.00 520.00 0\n"


# A put bought and sold for nothing (no FX on its proceeds).
_NOFX = ("BUYSELL 2025-03-03 10:00:00 QZRX251017P00015000.US 1 USD 0.40 41.00 1.00\n"
         "BUYSELL 2025-10-17 16:00:00 QZRX251017P00015000.US -1 USD 0 0 0\n")


class TestEveryKindOfDenial(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = Project({"margin": MARGIN_TT + _FOR_GOOD + _NOFX, "tfsa": TFSA_TT + _TFSA_BUY},
                        toml=TOML + '[accounts.ib]\ntype = "taxable"\n')
        ib = cls.p.root / "inputs" / "ib"
        ib.mkdir()
        (ib / "ib.csv").write_text(_ib([("2025-01-10", 100, 10.0)], [("2025-03-14", -100, 600)]),
                                   encoding="utf-8")
        (ib / "inkind.tt").write_text("INKIND 2025-03-14 QZK.TO -100 CAD 6 plan=tfsa\n",
                                      encoding="utf-8")
        cls.p.run()
        cls.sum = cls.p.json("sum")["filing"]
        cls.base = cls.p.json("gains-base")
        cls.cra = cls.p.json("gains-cra")
        cls.text = {c: cls.p.tjs(c, TAXJSON_WIDTH="0").stdout
                    for c in ("sum", "gains-cra", "gains-base")}

    def _row(self, symbol):
        return next(r for r in self.base["rows"] if r["symbol"] == symbol)

    def test_the_three_kinds(self):
        deferred, for_good, inkind = (self._row(s) for s in ("QZLS.TO", "QZPG.TO", "QZK.TO"))
        self.assertEqual([deferred[k] for k in ("denied", "permanently_denied",
                                                "denied_contribution")], [100.0, 0.0, 0.0])
        self.assertEqual([for_good[k] for k in ("denied", "permanently_denied",
                                                "denied_contribution")], [100.0, 100.0, 0.0])
        self.assertEqual([inkind[k] for k in ("denied", "permanently_denied",
                                              "denied_contribution")], [0.0, 0.0, 400.0])
        # Each one's loss is out of COST(ACB) already: the gain filed is nil.
        for r in (deferred, for_good, inkind):
            self.assertEqual((r["allowed"], r["acb"]), (0.0, r["proceeds"]), r["symbol"])

    def test_every_row_and_total_foots(self):
        t = self.base["totals"]
        for r in self.base["rows"] + t["accounts"] + [t["taxable"], t["sheltered"], t["return"]]:
            where = r.get("symbol") or r.get("account") or "total"
            _foots(self, r, where, gain="allowed")
            self.assertEqual(cents(r["acb"]), cents(r["cost"]) - cents(r["denied"]), where)
            self.assertEqual(cents(r["gain"]), cents(r["allowed"]) - cents(r["denied"]), where)
        for r in self.cra["rows"]:
            self.assertEqual(cents(r["proceeds"]) - cents(r["acb"]) - cents(r["outlays"]),
                             cents(r["gain"]), r["symbol"])
        for rec in (self.cra["tables"] + [self.cra["rows_total"], self.cra["totals"]]
                    + self.cra["lines"] + [ln["rows_sum"] for ln in self.cra["lines"]]):
            _foots(self, rec, rec.get("currency") or rec.get("key") or "total")
        for rec in self.sum["lines"] + [self.sum["totals"]]:
            _foots(self, rec, rec.get("key") or "RETURN")

    def test_denied_info_adds_every_kind(self):
        """TJ-5341: DENIED (INFO), `denied_all`, is the superficial losses
        (200.00, `denied`, as before) and the contribution's (400.00)."""
        want, superficial = Decimal("600.00"), Decimal("200.00")
        self.assertEqual(cents(self.sum["totals"]["denied"]), superficial)
        self.assertEqual(cents(self.sum["totals"]["denied_contribution"]), Decimal("400.00"))
        self.assertEqual(cents(self.sum["totals"]["denied_all"]), want)
        self.assertEqual(cents(self.base["totals"]["return"]["denied"]), superficial)
        self.assertEqual(cents(self.base["totals"]["return"]["denied_all"]), want)
        self.assertEqual(cents(self.cra["totals"]["denied"]), superficial)
        self.assertEqual(cents(self.cra["totals"]["denied_all"]), want)
        self.assertEqual(cents(self.cra["rows_total"]["denied"]), superficial)
        self.assertEqual(cents(self.cra["rows_total"]["denied_all"]), want)
        self.assertEqual(sum((cents(t["denied_all"]) for t in self.cra["tables"]), Decimal(0)),
                         want)
        self.assertEqual(cents(self.cra["rows_total"]["denied_contribution"]), Decimal("400.00"))

    def test_the_return_rows_cell_for_cell(self):
        rows = _return_rows(self.text["sum"], self.text["gains-cra"], self.text["gains-base"])
        want = [cents(self.sum["totals"][k]) for k in _KEYS]
        self.assertEqual([_cells(ln) for ln in rows], [want] * 3, rows)
        self.assertEqual(len({tuple(_edges(ln)) for ln in rows}), 1, "\n".join(rows))
        # gains-base's per-sale rows: sum's meanings, the text's columns.
        self.assertEqual(self.base["text_columns"],
                         ["proceeds", "acb", "outlays", "total_cost", "allowed", "denied_all"])
        line = next(ln for ln in self.text["gains-base"].splitlines() if "QZPG.TO" in ln)
        r = self._row("QZPG.TO")
        self.assertEqual(_cells(line), [cents(r[k]) for k in self.base["text_columns"]])
        self.assertIn("100.00*", line)

    def test_all_sales_lines_up_with_for_the_return(self):
        for width in ("0", "120"):
            out = self.p.tjs("gains-cra", TAXJSON_WIDTH=width).stdout
            total = _after(out, "ALL SALES", "TOTAL ")
            ret = _after(out, "FOR THE RETURN", "RETURN ")
            sm = _after(self.p.tjs("sum", TAXJSON_WIDTH=width).stdout, "FOR THE RETURN",
                        "RETURN ")
            with self.subTest(width=width):
                self.assertEqual(_edges(total)[-6:], _edges(ret))
                self.assertEqual(_edges(ret), _edges(sm))
                self.assertEqual(_cells(total)[-6:],
                                 [cents(self.cra["rows_total"][k]) for k in _KEYS])

    def test_gains_cra_s_denied_info_column(self):
        lines = self.text["gains-cra"].splitlines()
        i = lines.index("SALES IN CAD")
        # Its heading on three lines: DENIED, (INFO), (CAD), the last column.
        edge = lines[i + 1].index("DENIED") + len("DENIED")
        cell = slice(lines[i + 2].index("GAIN") + len("GAIN"), edge)
        for k, word in ((1, "DENIED"), (2, "(INFO)"), (3, "(CAD)")):
            self.assertEqual(lines[i + k].rstrip()[-len(word):], word)
            self.assertEqual(len(lines[i + k].rstrip()), edge)

        def row(symbol):
            r = next(x for x in self.cra["rows"] if x["symbol"] == symbol)
            return next(ln for ln in lines[i:] if ln.startswith(r["date"]))
        self.assertEqual(row("QZLS.TO")[cell].strip(), "100.00")
        self.assertEqual(row("QZPG.TO")[cell].strip(), "100.00")
        self.assertEqual(row("QZNW.TO")[cell].strip(), "")
        # The contribution's loss is in the column (TJ-5341), as in the
        # CSV: its own mark says its kind.
        inkind = row("QZK.TO")
        self.assertEqual(inkind[cell].strip(), "400.00")
        mark = re.search(r"\[(\d+)\]", inkind).group(1)
        note = " ".join(_after(self.text["gains-cra"], "SALES IN CAD", f"[{mark}]  ").split())
        self.assertIn("in-kind contribution", note)
        self.assertIn("(1 row, 400.00 CAD; s.40(2)(g)(iv))", note)
        self.assertNotIn("DENIED (INFO)", note)
        foot = _after(self.text["gains-cra"], "SALES IN CAD", "SUBTOTAL")
        self.assertEqual(foot[cell].strip(), "600.00")
        self.assertEqual(foot.rstrip(), foot[:edge])

    def test_gains_base_marks_an_in_kind_contribution_s_row(self):
        """TJ-5340, TJ-5341: the row's DENIED (INFO) cell holds the loss,
        marked `†` (not `*`), and a legend line says what the mark is; no
        line of its own under TOTALS, whose DENIED (INFO) adds it."""
        r = self._row("QZK.TO")
        self.assertEqual((r["denied"], r["permanently_denied"], r["denied_contribution"]),
                         (0.0, 0.0, 400.0))
        for width in ("0", "117", "100"):
            out = self.p.tjs("gains-base", TAXJSON_WIDTH=width).stdout
            text = " ".join(out.split())
            with self.subTest(width=width):
                # The row (one record per sale where the table does not fit).
                lines = out.splitlines()
                n = next(n for n, ln in enumerate(lines) if "QZK.TO" in ln)
                line = " ".join(lines[n:n + 3]) if width == "100" else lines[n]
                self.assertIn("400.00†", line)
                self.assertNotIn("*", line)
                self.assertEqual(_cells(line), [cents(r[k]) for k in self.base["text_columns"]])
                self.assertIn("†: an in-kind contribution", text)
                self.assertNotIn("not in DENIED (INFO)", text)
                tail = " ".join(out[out.index("TOTALS"):].split())
                self.assertNotIn("†", tail)
                # DENIED (INFO)'s total adds every kind: 200.00 + 400.00.
                ret = _after(out, "TOTALS", ("THE RETURN (tjs sum) ", "RETURN "))
                self.assertEqual(_cells(ret)[-1], Decimal("600.00"))
        # The legend: the mark's line, within 100 columns.
        line = ("†: an in-kind contribution to a registered plan, its loss denied for good "
                "(s.40(2)(g)(iv)).")
        self.assertIn(line, self.text["gains-base"].splitlines())
        self.assertLessEqual(len(line), 100)

    def test_no_line_is_wider_than_100_at_100(self):
        for cmd in ("gains-cra", "gains-base", "sum"):
            with self.subTest(cmd=cmd):
                _all_within(self, self.p.tjs(cmd, TAXJSON_WIDTH="100").stdout, 100)

    def test_the_headings_are_capitals(self):
        heads = []
        for cmd, starts in (("sum", ("SCHEDULE 3 LINE", "ACCOUNT ")),
                            ("gains-cra", ("SALE ", "TABLE ", "SCHEDULE 3 LINE")),
                            ("gains-base", ("ACCT ", "ACCOUNT "))):
            lines = self.text[cmd].splitlines()
            for n, ln in enumerate(lines):
                if ln.startswith(starts):
                    if ln.startswith("SALE "):
                        heads.append(lines[n - 1])      # a three-line heading's first line
                    k = n
                    while not set(lines[k]) <= {"-"}:   # every line of the heading
                        heads.append(lines[k])
                        k += 1
        self.assertGreaterEqual(len(heads), 12)
        for h in heads:
            self.assertEqual(h, h.upper(), h)

    def test_the_csv_header_is_unchanged(self):
        out = self.p.root / "cra.csv"
        self.assertEqual(self.p.tjs("gains-cra", "--csv", str(out)).returncode, 0)
        with open(out, newline="", encoding="utf-8") as f:
            got = list(csv.reader(f))
        self.assertEqual(got[0], _CSV_HEADER)
        self.assertEqual(list(GC.CSV_COLUMNS), _CSV_HEADER)
        # The Denied column adds every kind, the contribution's too: the
        # text's DENIED (INFO) (TJ-5341).
        col = got[0].index("Denied (CAD)")
        self.assertEqual({r[3]: r[col] for r in got[1:] if r[col]},
                         {"QZLS.TO": "100.0", "QZPG.TO": "100.0", "QZK.TO": "400.0"})

    def test_the_json_keeps_its_keys(self):
        self.assertEqual(self.base["columns"],
                         ["proceeds", "cost", "outlays", "gain", "denied", "allowed"])
        old = {"proceeds", "cost", "outlays", "gain", "denied", "allowed", "permanently_denied",
               "denied_contribution", "gain_unrounded"}
        for r in self.base["rows"]:
            self.assertLessEqual(old | {"acb", "total_cost", "denied_all"}, set(r), r["symbol"])
        for rec in (self.cra["tables"] + [self.cra["rows_total"], self.cra["totals"]]
                    + self.cra["lines"]):
            self.assertIn("total_cost", rec)
            self.assertIn("denied_all", rec)
        for ln in self.cra["lines"]:
            self.assertEqual(GCOL.label(ln), next(GCOL.label(s) for s in self.sum["lines"]
                                                  if s["key"] == ln["key"]))


class TestGainsCraIsAlwaysWhole(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = Project()
        cls.p.run()

    def test_details_changes_nothing_but_one_info_line(self):
        for width in ("0", "120"):
            plain = self.p.tjs("gains-cra", TAXJSON_WIDTH=width)
            det = self.p.tjs("gains-cra", "--details", TAXJSON_WIDTH=width)
            with self.subTest(width=width):
                self.assertEqual((plain.returncode, det.returncode), (0, 0))
                self.assertEqual(det.stdout, plain.stdout)
                self.assertNotIn(_INFO, det.stdout)
                info = [ln for ln in det.stderr.splitlines() if _INFO in ln]
                self.assertEqual(len(info), 1, det.stderr)
                rest = "\n".join(ln for ln in det.stderr.splitlines() if _INFO not in ln)
                self.assertEqual(rest, "\n".join(plain.stderr.splitlines()))
                self.assertNotIn("tjs gains-cra --details", plain.stdout)
        self.assertEqual(info, ["Info: " + _INFO])
        narrow = self.p.tjs("gains-cra", "--details", TAXJSON_WIDTH="60")
        self.assertEqual(narrow.stdout, self.p.tjs("gains-cra", TAXJSON_WIDTH="60").stdout)
        # --json stays clean: the same document, the line on stderr only.
        plain = self.p.tjs("gains-cra", "--json")
        det = self.p.tjs("gains-cra", "--json", "--details")
        self.assertEqual(det.stdout, plain.stdout)
        self.assertIn(_INFO, det.stderr)

    def test_the_help_says_so(self):
        r = self.p.tjs("gains-cra", "-h", TAXJSON_WIDTH="0")
        self.assertEqual(r.returncode, 0, r.stderr)
        text = " ".join(r.stdout.split())
        self.assertIn(f"--details {_INFO}", text)
        self.assertNotIn("the default view leaves out", text)
        other = " ".join(self.p.tjs("sum", "-h", TAXJSON_WIDTH="0").stdout.split())
        self.assertIn("the default view leaves out", other)

    def test_whole_at_any_width(self):
        for width in ("120", "60"):
            out = self.p.tjs("gains-cra", TAXJSON_WIDTH=width).stdout
            with self.subTest(width=width):
                self.assertIn("QZRX $30 call", out)
                self.assertNotIn("…", out[:out.index("FOR THE RETURN")])


class TestTheCurrencyTablesFit(unittest.TestCase):
    """TJ-5342: a currency's table at the width: its headings on three
    lines, a long Description wrapped at word boundaries within its column
    (its marks on the first line, its further lines the Description
    alone), every column and every word; unwrapped at width 0; narrower
    than the narrowest fit, one record per sale."""

    @classmethod
    def setUpClass(cls):
        cls.p = Project()
        cls.p.run()
        cls.doc = cls.p.json("gains-cra")

    def _want(self, cur):
        return [r["description"] for r in self.doc["rows"] if r["currency"] == cur]

    def test_whole_and_on_one_line_unwrapped(self):
        out = self.p.tjs("gains-cra", TAXJSON_WIDTH="0").stdout
        heads, rows, desc = _currency_table(out, "USD")
        self.assertEqual([len(r) for r in rows], [1] * len(rows))
        self.assertEqual([_description(r, desc) for r in rows], self._want("USD"))
        self.assertGreater(max(len(r[0]) for r in rows), 120)
        self.assertEqual([h.split() for h in heads][0], ["DENIED"])

    def test_wrapped_within_the_width(self):
        for width in ("120", "117"):
            out = self.p.tjs("gains-cra", TAXJSON_WIDTH=width).stdout
            with self.subTest(width=width):
                for cur in ("CAD", "USD"):
                    heads, rows, desc = _currency_table(out, cur)
                    self.assertEqual([_description(r, desc) for r in rows], self._want(cur))
                    for r in rows:
                        self.assertEqual([ln for ln in r if len(ln) > int(width)], [])
                        for more in r[1:]:
                            # The Description alone, no mark, no figure.
                            self.assertEqual(more[:desc].strip(), "")
                            self.assertNotRegex(more, r"\[|\d\.\d\d")
                    self.assertEqual([h for h in heads if len(h) > int(width)], [])
                    self.assertEqual(heads[1].split()[-4:],
                                     ["COST(ACB)", "OUTLAYS", "GAIN", "(INFO)"])
                _heads, rows, _desc = _currency_table(out, "USD")
                self.assertTrue(any(len(r) > 1 for r in rows), "a Description wrapped")
                buyback = next(r for r in rows if "[" in r[0] and "call" in r[0])
                self.assertRegex(buyback[0], r"call( exp)? \[\d+\]")

    def test_one_record_per_sale_below_the_narrowest_fit(self):
        out = self.p.tjs("gains-cra", TAXJSON_WIDTH="100").stdout
        self.assertIsNone(_currency_table(out, "USD"))
        lines = out.splitlines()
        i = lines.index("SALES IN USD")
        for want in self._want("USD"):
            self.assertTrue(any(ln.startswith(("2025-")) and want in ln for ln in lines[i:]), want)
        self.assertIn("outlays (cad)", out[out.index("SALES IN USD"):])

    def test_one_name_for_one_figure(self):
        """TJ-5341's P3-10, the owner's call of 2026-11-27: a currency's
        table names its money columns as the totals and `tjs sum` do
        (COST(ACB), OUTLAYS, GAIN; was ACB, COMMISSIONS, GAIN/LOSS), so no
        legend line maps one name to the other; the legend's first line is
        sum's identity. Every column at 117 and 100, each line within the
        width; the CSV header and the JSON keep their names."""
        # A table's second heading line (bottom-aligned on three: DENIED /
        # (INFO) / (CAD)); a record's labels.
        second = ["COST(ACB)", "OUTLAYS", "GAIN", "(INFO)"]
        labels = ["proceeds (cad)", "cost(acb) (cad)", "outlays (cad)", "gain (cad)",
                  "denied (info) (cad)"]
        for width in ("0", "117", "100"):
            out = self.p.tjs("gains-cra", TAXJSON_WIDTH=width).stdout
            with self.subTest(width=width):
                for old in ("GAIN/LOSS", "COMMIS", "tjs sum's columns", "= ACB", "— the ACB"):
                    self.assertNotIn(old, out)
                legend = out.splitlines()[1:3]
                self.assertTrue(legend[0].startswith(GCOL.IDENTITY + "; FX: "), legend)
                self.assertTrue(legend[1].startswith("DENIED (INFO): information only — "
                                                     "COST(ACB) is already reduced by it"), legend)
                if width != "0":
                    _all_within(self, out, int(width))
                for cur, proceeds in (("CAD", ["PROCEEDS"]), ("USD", ["PROCEEDS"] * 2)):
                    table = _currency_table(out, cur)
                    if table is None:           # one record per sale: its labels
                        rest = out[out.index(f"SALES IN {cur}"):]
                        self.assertEqual([x for x in labels if x not in rest], [], cur)
                        continue
                    heads = [h.split() for h in table[0]]
                    self.assertEqual(heads[0], ["DENIED"], cur)
                    self.assertEqual(heads[1], ["SALE", "SETTLE"] + proceeds + second, cur)
        # `tjs gains-cra -h` names them so (TJ-5470).
        usage = " ".join(self.p.tjs("gains-cra", "-h").stdout.split())
        self.assertIn("Schedule 3's PROCEEDS, COST(ACB), OUTLAYS and GAIN in CAD", usage)
        self.assertIn("information only, COST(ACB) is already reduced by it", usage)
        for old in ("GAIN/LOSS", "COMMISSIONS", "the ACB"):
            self.assertNotIn(old, usage)
        self.assertEqual(list(GC.CSV_COLUMNS), _CSV_HEADER)
        self.assertLessEqual({"proceeds", "acb", "outlays", "gain", "denied", "denied_all"},
                             set(self.doc["rows"][0]))

    def test_the_wrap(self):
        text = "QZRX $30 call exp 2025-12-19 (written)"
        self.assertEqual(GC._wrapped(text, "", None), [text])
        self.assertEqual(GC._wrapped(text, " [4]", 60), [text + " [4]"])
        self.assertEqual(GC._wrapped(text, " [4]", 17), ["QZRX $30 call [4]", "exp 2025-12-19",
                                                         "(written)"])
        # A word longer than the room stays whole.
        self.assertEqual(GC._wrapped("QZLONGNAMEDSECURITY x", "", 11),
                         ["QZLONGNAMEDSECURITY", "x"])


# A CAD loss and a USD gain wider than any Schedule 3 line's figure: the
# currency tables' GAIN cells are wider than sum's.
_BIG = ("BUYSELL 2025-01-08 10:00:00 QZBG.TO 1000 CAD 50.00 50000.00 0\n"
        "BUYSELL 2025-10-01 10:00:00 QZBG.TO -1000 CAD 38.00 38000.00 0\n"
        "BUYSELL 2025-01-08 10:00:00 QZUG.US 100 USD 100.00 10000.00 0\n"
        "BUYSELL 2025-10-02 10:00:00 QZUG.US -100 USD 200.00 20000.00 0\n")


class TestAWiderCurrencyTable(unittest.TestCase):
    """gains-cra's ALL SALES and FOR THE RETURN size each money column from
    both tables' figures, so they line up when a currency's cell is wider
    than any line's (lib/gain_columns `table`, `refs`, `left_min`)."""

    @classmethod
    def setUpClass(cls):
        cls.p = Project({"margin": MARGIN_TT + _BIG, "tfsa": TFSA_TT})
        cls.p.run()
        cls.doc = cls.p.json("gains-cra")

    def test_the_shape(self):
        widest = max(len(f"{cents(t['gain']):,.2f}") for t in self.doc["tables"])
        lines = max(len(f"{cents(x['gain']):,.2f}")
                    for x in self.doc["lines"] + [self.doc["totals"]])
        self.assertGreater(widest, lines)

    def test_all_sales_and_for_the_return_line_up(self):
        for width in ("0", "120", "117", "100"):
            out = self.p.tjs("gains-cra", TAXJSON_WIDTH=width).stdout
            with self.subTest(width=width):
                edges = {tuple(_edges(_after(out, "ALL SALES", ln))[-6:])
                         for ln in ("Sales in CAD", "Sales in USD", "TOTAL ")}
                edges |= {tuple(_edges(_after(out, "FOR THE RETURN", ln)))
                          for ln in ("Line 4", "Line 6", "RETURN ")}
                self.assertEqual(len(edges), 1, out)
                heads = (_after(out, "ALL SALES", "TABLE "),
                         _after(out, "FOR THE RETURN", "SCHEDULE 3 LINE"))
                self.assertEqual(_head_edges(heads[0]), _head_edges(heads[1]))
                self.assertEqual(list(edges)[0], tuple(_head_edges(heads[0])))
                if width != "0":
                    _all_within(self, out, int(width))

    def test_a_row_s_own_note_has_its_date_and_description(self):
        row = {"date": "2025-03-10", "description": "QZRX"}
        self.assertEqual(GC._note_lines([(3, row, "a note of its own"), (4, None, "shared")], 0),
                         ["[3]  2025-03-10 QZRX: a note of its own", "[4]  shared"])


class TestTheLabels(unittest.TestCase):
    """A Schedule 3 line's one label, shortened to the width as `tjs sum`
    shortens it (lib/gain_columns `label`)."""
    LINE = {"short": "options, crypto & other properties (P2)", "line": "",
            "proceeds_code": "15199", "gain_code": "15300"}

    def test_each_form(self):
        self.assertEqual(GCOL.label(self.LINE), "options, crypto & other properties (P2) "
                                                "(15199/15300)")
        self.assertEqual(GCOL.label(self.LINE, 42), "options, crypto & other (P2) (15199/15300)")
        self.assertEqual(GCOL.label(self.LINE, 35), "options, crypto… (P2) (15199/15300)")
        self.assertEqual(GCOL.label(self.LINE, 27), "options… (P2) (15199/15300)")
        self.assertEqual(GCOL.label(self.LINE, 10), "(P2) 15199/15300")

    def test_sum_s_legend_at_each_width(self):
        """One line unwrapped; two wider than 100 columns; within 100 its
        first line alone (the concise budget, test_concise_sum)."""
        one = GCOL.sum_legend(0)
        self.assertEqual(one, [GCOL.FOOTING[:-1] + "; " + GCOL.denied_forms()[0][0]])
        for width in (160, 120, 117, 101, 100, 90):
            with self.subTest(width=width):
                got = GCOL.sum_legend(width)
                self.assertEqual(len(got), 2 if width > 100 else 1)
                # Narrower than its short form, sum wraps it (`_para`).
                self.assertEqual([t for t in got if len(t) > max(width, 99)], [])
                self.assertIn("DENIED (INFO): information only — COST(ACB) is already reduced by "
                              "it", got[0])
                self.assertIn("PROCEEDS − TOTAL COST", got[0])
        self.assertEqual(GCOL.sum_legend(80, denied=False), [GCOL.FOOTING])

    def test_denied_info_has_one_sense(self):
        """The three reports' DENIED (INFO) line, long and short: COST(ACB)
        (gains-cra's per-sale tables' too, TJ-5341's P3-10) is already
        reduced by it; a deferred one is added to the replacement's ACB,
        one denied for good is not."""
        for star in ("", "*"):
            forms = GCOL.denied_forms(star)
            for form in forms:
                text = " ".join(form)
                self.assertIn("information only — COST(ACB) is already reduced by it", text)
                self.assertIn("a deferred one is added to the replacement's acb", text.lower())
                self.assertIn("one denied for good", text)
                self.assertIn("is not", text)
                self.assertEqual("*" in text, star == "*")
            self.assertEqual(GCOL.fit_legend(forms, 0), forms[0])
            self.assertEqual(GCOL.fit_legend(forms, 100), forms[1])
            self.assertTrue(all(len(t) <= 100 for t in forms[1]))


if __name__ == "__main__":
    unittest.main()
