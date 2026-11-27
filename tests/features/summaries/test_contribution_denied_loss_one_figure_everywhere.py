"""An in-kind contribution's denied loss is one DENIED (INFO) figure in
every report (backlog TJ-5341, the owner's decision of 2026-11-26): its
row shows the same denied amount in `tjs gains-cra`'s text, its `--csv`
Denied column, `tjs gains-base` and `tjs sum` (scoped to the account the
sale is in), and gains-cra's SUBTOTALs add to sum's DENIED (INFO).

DENIED (INFO) adds every kind of denied loss, as the CSV always has: the
superficial losses (deferred and for good) and an in-kind contribution's
(s.40(2)(g)(iv)). `--json` keeps `denied` (the superficial losses) and
`denied_contribution` and adds `denied_all`, the column's figure; the
CSV is unchanged. src/taxjson/lib/gain_columns.py `KEYS`;
src/taxjson/lib/gains_cra.py `_table_lines`, `report`, `csv_rows`;
src/taxjson/lib/gains_base.py `row_ca`, `_cells`, `_legend`;
src/taxjson/cli/estimate.py `_return_figures`, `_sum_return_ca`.

The project: an IB account (taxable) whose 100 QZK.TO, bought at 10.00,
move out in kind to a TFSA outside the project, valued 600 (400.00
denied for good), and a margin account (taxable) with a superficial loss
of 100.00 deferred (QZLS.TO sold at a loss, bought back within 30 days)
and a USD sale at a gain (a second currency's table, its SUBTOTAL's
DENIED (INFO) 0.00), in a HOME whose Bank of Canada rates are planted.
The other classes: RRSP and USD contributions with the cents a total of
the lines' figures and the gains files' unrounded one part on (TJ-5450);
three superficial losses' cents beside a contribution; and a year folder
closed, whose lock records the superficial losses alone (close-year and
`tjs years` name them so). Made-up tickers and ids.
"""
import csv
import json
import re
import unittest
from decimal import Decimal

from _support.filing_mixed_currency_project import Project, cents
from _support.transfer_in_kind import _ib

_TOML = ('[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\n'
         'country = "canada"\nbase_currency = "CAD"\nsource_currencies = ["USD"]\n'
         '[accounts.ib]\ntype = "taxable"\n[accounts.margin]\ntype = "taxable"\n')
_MARGIN = ("BUYSELL 2025-02-03 10:00:00 QZLS.TO 10 CAD 50.00 500.00 0\n"
           "BUYSELL 2025-05-05 10:00:00 QZLS.TO -10 CAD 40.00 400.00 0\n"
           "BUYSELL 2025-05-12 10:00:00 QZLS.TO 10 CAD 41.00 410.00 0\n"
           "BUYSELL 2025-01-06 10:00:00 QZRX.US 10 USD 20.00 200.00 0\n"
           "BUYSELL 2025-03-10 10:00:00 QZRX.US -10 USD 25.00 250.00 0\n")
_MONEY = re.compile(r"-?[\d,]+\.\d\d")
_HEAD = ('[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\n'
         'country = "canada"\nbase_currency = "CAD"\nsource_currencies = ["USD"]\n')
_INKIND, _DEFERRED, _BOTH = Decimal("400.00"), Decimal("100.00"), Decimal("500.00")


def _cells(line):
    return [cents(m.group().replace(",", "")) for m in _MONEY.finditer(line)]


def _after(text, heading, prefix):
    """The first line starting with `prefix` after the line starting with
    `heading`."""
    lines = text.splitlines()
    i = next(n for n, ln in enumerate(lines) if ln.startswith(heading))
    return next(ln for ln in lines[i:] if ln.startswith(prefix))


def _denied_cell(text, date):
    """gains-cra's DENIED (INFO) cell of the CAD table's row of `date`
    (its heading on three lines: DENIED, (INFO), (CAD), the last column;
    the cell between GAIN's end and DENIED's)."""
    lines = text.splitlines()
    i = lines.index("SALES IN CAD")
    cell = slice(lines[i + 2].index("GAIN") + len("GAIN"),
                 lines[i + 1].index("DENIED") + len("DENIED"))
    row = next(ln for ln in lines[i:] if ln.startswith(date))
    foot = next(ln for ln in lines[i:] if ln.startswith("SUBTOTAL"))
    return row[cell].strip(), foot[cell].strip()


def _taxable(*names):
    return _HEAD + "".join(f'[accounts.{n}]\ntype = "taxable"\n' for n in names)


def _contribute(p, account, sym, cur, buy, out, inkind):
    """An IB account of `p` whose `sym` (bought: `buy`, (date, qty,
    price)) moves out in kind (`out`, (date, qty, market value)) to the
    plan its INKIND line names."""
    d = p.root / "inputs" / account
    d.mkdir(parents=True)
    (d / "ib.csv").write_text(_ib([buy], [out], cur=cur, exch="NASDAQ" if cur == "USD" else "TSE",
                                  sym=sym), encoding="utf-8")
    (d / "inkind.tt").write_text(inkind + "\n", encoding="utf-8")


def _cra_row(text, currency, ticker):
    """gains-cra's row of `ticker` (its Description's word) in the table
    of `currency`."""
    block = text[text.index(f"SALES IN {currency}"):]
    block = block[:block.index("\nSUBTOTAL")]
    return next(ln for ln in block.splitlines() if re.search(rf"\s{ticker}\s", ln))


def _subtotals(text):
    """Each currency table's SUBTOTAL DENIED (INFO), its last cell (the
    subtotal always shows its figure), by currency."""
    lines = text.splitlines()
    out = {}
    for n, ln in enumerate(lines):
        if ln.startswith("SALES IN "):
            foot = next(x for x in lines[n:] if x.startswith("SUBTOTAL"))
            out[ln[len("SALES IN "):]] = _cells(foot)[-1]
    return out


class TestAnInKindContributionHasOneDeniedFigure(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = Project({"margin": _MARGIN}, toml=_TOML)
        _contribute(cls.p, "ib", "QZK", "CAD", ("2025-01-10", 100, 10.0),
                    ("2025-03-14", -100, 600), "INKIND 2025-03-14 QZK.TO -100 CAD 6 plan=tfsa")
        cls.p.run()
        cls.cra = cls.p.json("gains-cra")
        cls.base = cls.p.json("gains-base")
        cls.sum = cls.p.json("sum")["filing"]
        cls.text = {c: cls.p.tjs(c, TAXJSON_WIDTH="0").stdout
                    for c in ("sum", "gains-cra", "gains-base")}

    def _row(self, doc, symbol):
        return next(r for r in doc["rows"] if r["symbol"] == symbol)

    def test_the_project(self):
        r = self._row(self.base, "QZK.TO")
        self.assertEqual([r[k] for k in ("denied", "permanently_denied", "denied_contribution")],
                         [0.0, 0.0, 400.0])
        r = self._row(self.base, "QZLS.TO")
        self.assertEqual([r[k] for k in ("denied", "permanently_denied", "denied_contribution")],
                         [100.0, 0.0, 0.0])

    def test_gains_cra_s_text_shows_the_csv_s_figure(self):
        out = self.p.root / "cra.csv"
        self.assertEqual(self.p.tjs("gains-cra", "--csv", str(out)).returncode, 0)
        with open(out, newline="", encoding="utf-8") as f:
            csv_rows = {r["Symbol"]: r for r in csv.DictReader(f)}
        for symbol, want in (("QZK.TO", _INKIND), ("QZLS.TO", _DEFERRED)):
            r = self._row(self.cra, symbol)
            with self.subTest(symbol=symbol):
                cell, _foot = _denied_cell(self.text["gains-cra"], r["date"])
                self.assertEqual(cents(csv_rows[symbol]["Denied (CAD)"]), want)
                self.assertEqual(cell, f"{want:,.2f}")
                self.assertEqual(cents(r.get("denied_all", 0)), want)

    def test_every_report_shows_the_contribution_s_figure(self):
        """The sale alone: its gains-cra cell, its CSV figure (above),
        its gains-base row, and `tjs sum ib`'s RETURN row (the account's
        only sale), at every width the owner's terminal has."""
        r = self._row(self.base, "QZK.TO")
        for width in ("0", "117", "100"):
            with self.subTest(width=width):
                cra = self.p.tjs("gains-cra", TAXJSON_WIDTH=width).stdout
                block = cra[cra.index("SALES IN CAD"):cra.index("FOR THE RETURN")]
                row = next(ln for ln in block.splitlines() if ln.startswith(r["date"]))
                self.assertEqual(_cells(row)[-1], _INKIND, row)
                base = self.p.tjs("gains-base", TAXJSON_WIDTH=width).stdout
                self.assertIn("400.00†", base)
                lines = base.splitlines()
                n = next(n for n, ln in enumerate(lines) if "QZK.TO" in ln)
                self.assertEqual(_cells(" ".join(lines[n:n + 3]))[5], _INKIND, lines[n:n + 3])
                sm = self.p.tjs("sum", "ib", TAXJSON_WIDTH=width).stdout
                ret = _after(sm, "FOR ACCOUNT ib", "RETURN ")
                self.assertEqual(_cells(ret)[-1], _INKIND, ret)
        self.assertEqual(cents(r.get("denied_all", 0)), _INKIND)
        self.assertEqual(cents(self.p.json("sum", "ib")["filing"]["totals"].get("denied_all", 0)),
                         _INKIND)

    def test_a_width_that_leaves_denied_info_out_gives_its_figure(self):
        """The `! ` line under sum's FOR THE RETURN and gains-base's TOTALS
        at the first width (from 100 down) that leaves DENIED (INFO) out
        gives its figure, every kind."""
        want = f"! DENIED (INFO) left out at this width (losses denied: {_BOTH:,.2f})"
        for cmd in ("sum", "gains-base"):
            with self.subTest(cmd=cmd):
                said = []
                for width in range(100, 59, -4):
                    out = self.p.tjs(cmd, TAXJSON_WIDTH=str(width)).stdout
                    said = [ln for ln in out.splitlines()
                            if "left out at this width" in ln and "DENIED" in ln]
                    if said:
                        break
                self.assertEqual(said, [want + f" — tjs {cmd} --details"])

    def test_each_account_s_figure(self):
        """Each account's DENIED (INFO): sum's `filing.accounts`, gains-base's
        TOTALS rows (text and `totals.accounts`)."""
        want = {"ib": _INKIND, "margin": _DEFERRED}
        self.assertEqual({a["account"]: cents(a["denied_all"]) for a in self.sum["accounts"]}, want)
        self.assertEqual({a["account"]: cents(a["denied_all"])
                          for a in self.base["totals"]["accounts"]}, want)
        for acct, fig in want.items():
            self.assertEqual(_cells(_after(self.text["gains-base"], "TOTALS", acct + " "))[-1], fig)

    def test_an_account_list_adds_its_accounts(self):
        """`ib,margin`: sum's, gains-cra's and gains-base's RETURN row for
        the two accounts, the JSON's `denied_all`, and each account alone."""
        sm = self.p.tjs("sum", "ib,margin").stdout
        cra = self.p.tjs("gains-cra", "ib,margin").stdout
        base = self.p.tjs("gains-base", "ib,margin").stdout
        self.assertEqual([_cells(_after(sm, "FOR ACCOUNTS ib, margin", "RETURN "))[-1],
                          _cells(_after(cra, "FOR ACCOUNTS ib, margin", "RETURN "))[-1],
                          _cells(_after(base, "TOTALS", "tjs sum ib,margin "))[-1]], [_BOTH] * 3)
        self.assertEqual(cents(self.p.json("sum", "ib,margin")["filing"]["totals"]["denied_all"]),
                         _BOTH)
        self.assertEqual(cents(self.p.json("gains-base", "ib,margin")["totals"]["return"]
                               ["denied_all"]), _BOTH)
        self.assertEqual(cents(self.p.json("sum", "margin")["filing"]["totals"]["denied_all"]),
                         _DEFERRED)

    def test_the_subtotals_add_to_sum_s_denied_info(self):
        subtotals = _subtotals(self.text["gains-cra"])
        self.assertEqual(subtotals, {"CAD": _BOTH, "USD": Decimal("0.00")})
        sm = _after(self.text["sum"], "FOR THE RETURN", "RETURN ")
        cra = _after(self.text["gains-cra"], "FOR THE RETURN", "RETURN ")
        every = _after(self.text["gains-cra"], "ALL SALES", "TOTAL ")
        base = _after(self.text["gains-base"], "TOTALS", "THE RETURN (tjs sum) ")
        # The SUBTOTALs add to sum's DENIED (INFO), as the CSV's Denied
        # column adds; gains-cra's ALL SALES and FOR THE RETURN and
        # gains-base's THE RETURN say the same figure.
        self.assertEqual([sum(subtotals.values(), Decimal(0))]
                         + [_cells(ln)[-1] for ln in (sm, cra, every, base)], [_BOTH] * 5)
        tables = sum((cents(t["denied_all"]) for t in self.cra["tables"]), Decimal(0))
        self.assertEqual(tables, cents(self.sum["totals"]["denied_all"]))
        self.assertEqual(cents(self.cra["totals"]["denied_all"]), _BOTH)
        self.assertEqual(cents(self.base["totals"]["return"]["denied_all"]), _BOTH)
        self.assertEqual(cents(self.base["totals"]["taxable"]["denied_all"]), _BOTH)

    def test_the_figure_is_shown_once(self):
        """The contribution's own mark says its kind, never the figure a
        second time beside a total: gains-base's TOTALS has no line of its
        own for it, and no report says it is outside DENIED (INFO)."""
        base = self.text["gains-base"]
        self.assertIn("†: an in-kind contribution to a registered plan, its loss denied for good "
                      "(s.40(2)(g)(iv)).", base.splitlines())
        totals = base[base.index("TOTALS"):]
        self.assertNotIn("†", totals)
        self.assertNotIn("contribution", totals)
        # 400.00 only on an account's row: no line of its own.
        self.assertEqual([ln for ln in totals.splitlines()
                          if "400.00" in ln and not ln.startswith(("ib ", "margin "))], [])
        self.assertEqual(_cells(_after(base, "TOTALS", "ib "))[-1], _INKIND)
        note = _after(self.text["gains-cra"], "SALES IN CAD", "[1]  ")
        self.assertIn("in-kind contribution", note)
        self.assertIn("(1 row, 400.00 CAD; s.40(2)(g)(iv))", note)
        det = " ".join(self.p.tjs("sum", "--details", TAXJSON_WIDTH="0").stdout.split())
        self.assertIn("DENIED (INFO) adds them to the superficial losses (500.00 in all).", det)
        for cmd, text in self.text.items():
            with self.subTest(cmd=cmd):
                self.assertNotIn("not in DENIED (INFO)", " ".join(text.split()))

    def test_the_json_keeps_its_keys(self):
        """`denied` keeps the superficial losses, `denied_contribution` the
        contribution's; `denied_all` is added; gains-base's text columns
        name it."""
        for name, t in (("sum", self.sum["totals"]), ("gains-cra", self.cra["totals"]),
                        ("gains-base", self.base["totals"]["return"])):
            with self.subTest(report=name):
                self.assertEqual(cents(t["denied"]), _DEFERRED)
                self.assertEqual(cents(t["denied_all"]), _BOTH)
        self.assertEqual(cents(self.sum["totals"]["denied_contribution"]), _INKIND)
        self.assertEqual(cents(self.cra["rows_total"]["denied_contribution"]), _INKIND)
        self.assertEqual(self.base["text_columns"][-1], "denied_all")
        self.assertEqual(self.base["columns"],
                         ["proceeds", "cost", "outlays", "gain", "denied", "allowed"])
        for ln in self.sum["lines"]:
            self.assertEqual(cents(ln["denied_all"]),
                             cents(ln["denied"]) + cents(ln["denied_contribution"]))
        self.assertEqual([cents(ln["denied_all"]) for ln in self.cra["lines"]],
                         [cents(ln["denied_all"]) for ln in self.sum["lines"]])
        self.assertEqual([cents(ln["rows_sum"]["denied_all"]) for ln in self.cra["lines"]],
                         [_BOTH])



class TestEveryPlanAndTheCentsTheTotalsAddUp(unittest.TestCase):
    """RRSP and USD contributions beside a TFSA one: each row's one
    figure, and the RETURN row's DENIED (INFO), the lines' cents added
    (TJ-5450): QZJ.TO and QZM.TO, 3 shares each bought at 10.00 and
    valued 19.995, each lose 10.005 (10.01 on its line; the gains files'
    total 20.01), so the lines' contributions are a cent over
    `denied_contribution`, and `tjs sum --details` gives the lines' part
    and says the cent. QZU.US: 30 bought at 10.00 USD, valued 6 USD each,
    its CAD loss at the planted rates (whole cents)."""

    @classmethod
    def setUpClass(cls):
        cls.p = Project({"margin": _MARGIN[:_MARGIN.index("BUYSELL 2025-01-06")]},
                        toml=_taxable("ib", "ibr", "ibs", "ibu", "margin"))
        _contribute(cls.p, "ib", "QZK", "CAD", ("2025-01-10", 100, 10.0),
                    ("2025-03-14", -100, 600), "INKIND 2025-03-14 QZK.TO -100 CAD 6 plan=tfsa")
        for acct, sym in (("ibr", "QZJ"), ("ibs", "QZM")):
            _contribute(cls.p, acct, sym, "CAD", ("2025-01-10", 3, 10.0),
                        ("2025-03-14", -3, 19.995),
                        f"INKIND 2025-03-14 {sym}.TO -3 CAD 6.665 plan=rrsp")
        _contribute(cls.p, "ibu", "QZU", "USD", ("2025-01-10", 30, 10.0),
                    ("2025-04-14", -30, 180), "INKIND 2025-04-14 QZU.US -30 USD 6 plan=rrsp")
        cls.p.run()
        cls.cra = cls.p.json("gains-cra")
        cls.base = cls.p.json("gains-base")
        cls.sum = cls.p.json("sum")["filing"]
        cls.text = {c: cls.p.tjs(c).stdout for c in ("gains-cra", "gains-base")}
        cls.det = " ".join(cls.p.tjs("sum", "--details").stdout.split())
        # The USD loss in CAD from the CSV, unchanged by TJ-5341.
        out = cls.p.root / "cra.csv"
        assert cls.p.tjs("gains-cra", "--csv", str(out)).returncode == 0
        with open(out, newline="", encoding="utf-8") as f:
            cls.csv = {r["Symbol"]: r for r in csv.DictReader(f)}
        cls.usd = cents(cls.csv["QZU.US"]["Denied (CAD)"])

    def test_each_plan_s_row_has_one_figure(self):
        csv_rows = self.csv
        self.assertGreater(self.usd, 0)
        for sym, cur, want in (("QZK.TO", "CAD", _INKIND), ("QZJ.TO", "CAD", Decimal("10.01")),
                               ("QZM.TO", "CAD", Decimal("10.01")), ("QZU.US", "USD", self.usd)):
            with self.subTest(symbol=sym):
                row = _cra_row(self.text["gains-cra"], cur, sym.split(".")[0])
                lines = self.text["gains-base"].splitlines()
                base = next(ln for ln in lines if f" {sym} " in ln)
                self.assertEqual([_cells(row)[-1], _cells(base)[5],
                                  cents(csv_rows[sym]["Denied (CAD)"])]
                                 + [cents(next(r for r in d["rows"] if r["symbol"] == sym)
                                          ["denied_all"]) for d in (self.cra, self.base)],
                                 [want] * 5)
        # The USD table holds QZU.US alone: its SUBTOTAL is the row's.
        self.assertEqual(_subtotals(self.text["gains-cra"])["USD"], self.usd)

    def test_the_details_items_add_to_denied_info(self):
        lines = sum((cents(ln["denied_all"]) for ln in self.sum["lines"]), Decimal(0))
        every = cents(self.sum["totals"]["denied_all"])
        part = every - _DEFERRED
        self.assertEqual(lines, every)
        self.assertEqual(part, Decimal("420.02") + self.usd)
        # `denied_contribution` keeps its meaning: the gains files' total,
        # cut to the cent once (20.01 for the two 10.005 losses).
        self.assertEqual(cents(self.sum["totals"]["denied_contribution"]),
                         part - Decimal("0.01"))
        self.assertEqual(cents(self.sum["engine_denied_unrounded"]), _DEFERRED)
        self.assertIn(f"where a superficial loss was denied ({_DEFERRED:,.2f} in all)", self.det)
        self.assertIn(f"Denied: contribution to a registered plan — {part:,.2f} of losses",
                      self.det)
        self.assertIn(f"adds them to the superficial losses ({every:,.2f} in all).", self.det)
        sm = self.p.tjs("sum").stdout
        self.assertEqual(_cells(_after(sm, "FOR THE RETURN", "RETURN "))[-1], every)
        self.assertIn("Rows are rounded to the cent, as filed: DENIED (INFO)'s unrounded total "
                      "in the gains files (every loss denied) is "
                      f"{every - Decimal('0.01'):,.2f} (+0.01 on the RETURN row).", self.det)
        self.assertEqual(cents(self.base["totals"]["return"]["denied_all"]), every)
        self.assertEqual(cents(self.cra["totals"]["denied_all"]), every)


class TestTheSuperficialLossesCentsBesideAContribution(unittest.TestCase):
    """Three superficial losses of 1.004 (each line 1.00: 3.00; the gains
    files 3.012) beside a contribution's 400.00: the rounding note
    compares DENIED (INFO), 403.00, with the gains files' 403.01, never
    the superficial losses' 3.01 under it (TJ-5450)."""

    @classmethod
    def setUpClass(cls):
        tt = "".join(f"BUYSELL 2025-02-03 10:00:00 {s}.TO 1 CAD 11.004 11.004 0\n"
                     f"BUYSELL 2025-05-05 10:00:00 {s}.TO -1 CAD 10.00 10.00 0\n"
                     f"BUYSELL 2025-05-12 10:00:00 {s}.TO 1 CAD 10.00 10.00 0\n"
                     for s in ("QZA", "QZB", "QZC"))
        cls.p = Project({"margin": tt}, toml=_taxable("ib", "margin"))
        _contribute(cls.p, "ib", "QZK", "CAD", ("2025-01-10", 100, 10.0),
                    ("2025-03-14", -100, 600), "INKIND 2025-03-14 QZK.TO -100 CAD 6 plan=tfsa")
        cls.p.run()

    def test_the_rounding_note_compares_denied_info(self):
        det = " ".join(self.p.tjs("sum", "--details").stdout.split())
        sm = self.p.tjs("sum").stdout
        self.assertEqual(_cells(_after(sm, "FOR THE RETURN", "RETURN "))[-1], Decimal("403.00"))
        self.assertIn("DENIED (INFO)'s unrounded total in the gains files (every loss denied) "
                      "is 403.01 (-0.01 on the RETURN row).", det)
        self.assertNotIn("is 3.01", det)
        self.assertIn("where a superficial loss was denied (3.00 in all)", det)
        self.assertIn("— 400.00 of losses", det)
        f = self.p.json("sum")["filing"]
        self.assertEqual([cents(f["totals"][k]) for k in ("denied", "denied_all")],
                         [Decimal("3.00"), Decimal("403.00")])
        self.assertEqual(cents(f["engine_denied_unrounded"]), Decimal("3.01"))


class TestTheYearsNameTheLocksFigure(unittest.TestCase):
    """A year closed: the lock records the superficial losses alone
    (`disallowed`, 100.00), never an in-kind contribution's loss, so
    close-year's CLOSED block and `tjs years` name the figure for what it
    is ("Superficial losses", SUPERFICIAL), never DENIED beside `tjs
    sum`'s DENIED (INFO) of 500.00 (TJ-5341). The lock is unchanged."""

    @classmethod
    def setUpClass(cls):
        cls.p = Project({"margin": _MARGIN}, toml=_TOML)
        year = cls.p.root.parent / "yrs" / "2025"
        year.parent.mkdir()
        cls.p.root.rename(year)
        cls.p.root = year
        _contribute(cls.p, "ib", "QZK", "CAD", ("2025-01-10", 100, 10.0),
                    ("2025-03-14", -100, 600), "INKIND 2025-03-14 QZK.TO -100 CAD 6 plan=tfsa")
        cls.p.run()
        cls.sum = cls.p.tjs("sum").stdout
        cls.close = cls.p.tjs("close-year", "--yes")

    def test_close_year_names_the_superficial_losses(self):
        self.assertEqual(self.close.returncode, 0, self.close.stderr[-2000:])
        self.assertEqual(_cells(_after(self.sum, "FOR THE RETURN", "RETURN "))[-1], _BOTH)
        lines = [ln for ln in self.close.stdout.splitlines() if "Superficial losses:" in ln]
        self.assertEqual(len(lines), 1, self.close.stdout)
        self.assertEqual(_cells(lines[0]), [_DEFERRED])
        self.assertNotIn("Disallowed", self.close.stdout)
        lock = json.loads((self.p.root / "filed" / "2025.json").read_text(encoding="utf-8"))
        self.assertEqual([cents(lock["totals"]["disallowed"]),
                          cents(lock["return_row"]["disallowed"])], [_DEFERRED] * 2)

    def test_years_names_the_superficial_losses(self):
        self.assertEqual(self.close.returncode, 0, self.close.stderr[-2000:])
        for width in ("0", "117", "100"):
            with self.subTest(width=width):
                out = self.p.tjs("years", TAXJSON_WIDTH=width).stdout.splitlines()
                head = next(ln for ln in out if ln.split()[:2] == ["YEAR", "STATE"])
                self.assertEqual(head.split()[:4], ["YEAR", "STATE", "REALIZED", "SUPERFICIAL"])
                self.assertNotIn("DENIED", head)
                row = next(ln for ln in out if ln.startswith("2025 "))
                self.assertEqual(_cells(row)[1], _DEFERRED, row)
                self.assertEqual(row[head.index("SUPERFICIAL"):][:len("SUPERFICIAL")].strip(),
                                 f"{_DEFERRED:,.2f}")


if __name__ == "__main__":
    unittest.main()
