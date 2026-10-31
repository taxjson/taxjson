"""Fix round 8, the books group (synthetic data only)."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from _qa_project import gains, tj
from tax_rules import rule
from tax_rules.dual import tx
from taxjson.lib import stage_msg

_QH = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
       "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
       "Activity Type,Account #,Account Type\n")


class TestOldNameShortenerGone(unittest.TestCase):
    """TJ-1346: nothing prints the old command name, so the helper that
    shortened it is gone and each caller uses its line as it is."""

    def test_the_helper_is_gone(self):
        self.assertFalse(hasattr(stage_msg, "shorten_commands"))

    def test_an_over_wide_line_is_still_one_cut_line(self):
        long = "warning: " + "x" * 200 + " — `tjs sanity`"
        lines = stage_msg.console_lines(long, width_=80, concise=True)
        self.assertEqual(len(lines), 1)
        self.assertLessEqual(len(lines[0]), 80)

    def test_a_command_is_still_named_from_the_detail(self):
        self.assertEqual(stage_msg.detail_command(["see `tjs sanity --x`"]),
                         "tjs sanity --x")
        # A line quoted from an older project file can hold the old name.
        self.assertEqual(
            stage_msg.detail_command(["see `taxjson format-map`"]),
            "taxjson format-map")


def _ca_project(td, accounts, trades, year=2025):
    root = Path(td) / "p"
    (root / "inputs/margin").mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        '[settings]\nlocal_timezone = "America/Toronto"\n'
        f'year = {year}\ncountry = "canada"\nbase_currency = "CAD"\n'
        'source_currencies = []\n'
        + "".join(accounts))
    (root / "ticker.map").write_text("")
    (root / "inputs/margin/trades.tt").write_text(trades)
    return root


_MARGIN = '[accounts.margin]\ntype = "taxable"\n'
_PLAN = '[accounts.plan]\ntype = "sheltered"\nplan = "tfsa"\n'


class TestInKindLossIsNoBrokenInvariant(unittest.TestCase):
    """TJ-1316: a loss denied for good by an in-kind contribution raises
    taxable_gain with no wash sale; the engine's check allows for it."""

    def _inkind(self, td):
        root = _ca_project(td, [_MARGIN, _PLAN], (
            "BUYSELL 2025-01-02 09:30:00 SYN.TO 10 CAD 10 100 0\n"
            "INKIND 2025-06-03 SYN.TO -10 CAD 9 plan=tfsa\n"))
        (root / "inputs/plan").mkdir()
        (root / "inputs/margin/qt.csv").write_text(
            _QH + "2025-06-03,2025-06-03,TF6,SYN.TO,SYN CORP TRANSFER OUT,"
            "-10,0,0,0,0,CAD,Transfers,99900001,Margin\n")
        (root / "inputs/plan/qt.csv").write_text(
            _QH + "2025-06-03,2025-06-03,TF6,SYN.TO,SYN CORP TRANSFER IN,"
            "10,0,0,0,0,CAD,Transfers,99900002,TFSA\n")
        return root

    def test_an_in_kind_loss_warns_of_no_broken_invariant(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._inkind(td)
            tj(root, "run", "--no-input")
            r = tj(root, "carryover")
            self.assertNotIn("invariant broken", r.stdout + r.stderr)
            self.assertNotIn("invariant broken",
                             (root / "reports/margin.sum").read_text())
            self.assertIn("2025            0.00", r.stdout)

    def test_a_superficial_loss_still_passes_the_check(self):
        """Control: the same loss, the shares rebought in the window."""
        with tempfile.TemporaryDirectory() as td:
            root = _ca_project(td, [_MARGIN], (
                "BUYSELL 2025-01-02 09:30:00 SYN.TO 10 CAD 10 100 0\n"
                "BUYSELL 2025-06-03 09:30:00 SYN.TO -10 CAD 9 90 0\n"
                "BUYSELL 2025-06-10 09:30:00 SYN.TO 10 CAD 9 90 0\n"))
            tj(root, "run", "--no-input")
            r = tj(root, "carryover")
            self.assertNotIn("invariant broken", r.stdout + r.stderr)
            sale = [g for g in gains(root) if g.get("action") != "DIVIDEND"][0]
            self.assertAlmostEqual(sale["disallowed_amount"], 10.0)


_IB = ("Statement,Header,Field Name,Field Value\n"
       "Statement,Data,BrokerName,Interactive Brokers\n"
       "Account Information,Header,Field Name,Field Value\n"
       "Account Information,Data,Account,{acct}\n"
       "Trades,Header,DataDiscriminator,Asset Category,Currency,Symbol,"
       "Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,"
       "Realized P/L,MTM P/L,Code\n")


def _ib_row(cur, day, qty, price, code):
    return (f'Trades,Data,Order,Stocks,{cur},SYN,"{day}, 10:00:00",{qty},'
            f"{price},{price},{-qty * price},0,0,0,0,{code}\n")


# A buy coded closing with no short in the rows, then a documented long.
_COVER_THEN_LONG = [("2025-02-03", 10, 50, "C"), ("2025-03-03", 10, 60, "O"),
                    ("2025-04-03", -10, 70, "C")]


def _ib_project(td, country, books, year=2025):
    root = Path(td) / "p"
    head = ('[settings]\nlocal_timezone = "America/Toronto"\n'
            f'year = {year}\ncountry = "canada"\nbase_currency = "CAD"\n'
            'source_currencies = []\n'
            if country == "canada" else
            '[settings]\nlocal_timezone = "America/New_York"\n'
            f'year = {year}\ncountry = "usa"\nbase_currency = "USD"\n'
            'source_currencies = []\n')
    cur = "CAD" if country == "canada" else "USD"
    body = ""
    for i, (acct, rows) in enumerate(books.items()):
        body += f'[accounts.{acct}]\ntype = "taxable"\n'
        (root / "inputs" / acct).mkdir(parents=True)
        (root / "inputs" / acct / "ib.csv").write_text(
            _IB.format(acct=f"U123456{i}")
            + "".join(_ib_row(cur, *r) for r in rows))
    (root / "taxjson.toml").write_text(head + body)
    (root / "ticker.map").write_text("")
    return root


def _gains_doc(root, account="margin"):
    p = root / "work" / f"{account}_gains_wash.json"
    return json.loads(p.read_text())


def _issues(root):
    r = tj(root, "form-export", "--json")
    return [i["kind"] for i in json.loads(r.stdout)["readiness"]["issues"]]


def _sale(root, account="margin"):
    return [(g["qty"], round(g["cost"], 2), round(g["gain"], 2))
            for g in gains(root, account) if g.get("action") != "DIVIDEND"]


class TestBuyPastAShortLine(unittest.TestCase):
    """A plain buy larger than a .tt short line covers it and buys long with
    the rest: the later sale keeps its known cost (TJ-0844)."""

    def _book(self, sell, proceeds):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                '[settings]\ncountry = "canada"\nyear = 2025\n'
                'base_currency = "CAD"\n\n'
                '[accounts.margin]\ntype = "taxable"\n')
            (root / "inputs/margin").mkdir(parents=True)
            (root / "inputs/margin/t.tt").write_text(
                "OPENING 2024-12-31 SYN.TO -4 cost=unknown\n"
                "BUYSELL 2025-03-03 09:30:00 SYN.TO 10 CAD 40 400 0\n"
                f"BUYSELL 2025-05-05 09:30:00 SYN.TO {sell} CAD 50 "
                f"{proceeds} 0\n")
            tj(root, "run", "--no-input")
            return _sale(root)

    @rule("CA-ACB-11")
    def test_the_sale_of_the_long_part_is_realized(self):
        self.assertEqual(self._book(-6, 300), [(6.0, 240.0, 60.0)])

    @rule("CA-ACB-11")
    def test_part_of_the_long_is_sold(self):
        self.assertEqual(self._book(-3, 150), [(3.0, 120.0, 30.0)])


class TestUnbackedCover(unittest.TestCase):
    """TJ-0844: a buy the broker codes closing with no short held is said
    until the user answers it; the answer is a .tt short line."""

    _SYM = {"canada": "SYN.TO", "usa": "SYN.US"}

    def _flagged(self, country, cost, gain):
        with tempfile.TemporaryDirectory() as td:
            root = _ib_project(td, country, {"margin": _COVER_THEN_LONG})
            r = tj(root, "run", "--no-input")
            self.assertIn("1 cover of a short opened before your files",
                          r.stdout)
            # The figures do not move until the user answers.
            self.assertEqual(_sale(root), [(10.0, cost, gain)])
            self.assertEqual(_issues(root), ["unbacked-cover"])
            self.assertIn("unbacked-cover", [
                i["kind"] for i in json.loads(
                    tj(root, "sum", "--json").stdout)["readiness"]["issues"]])
            self.assertIn("1 cover of a short opened before your files",
                          tj(root, "form-export").stdout)

    @rule("CA-ACB-11")
    def test_canada_flagged_figures_unchanged(self):
        self._flagged("canada", 550.0, 150.0)

    @rule("US-BASIS-04")
    def test_usa_flagged_figures_unchanged(self):
        self._flagged("usa", 500.0, 200.0)

    def _answered(self, country):
        with tempfile.TemporaryDirectory() as td:
            root = _ib_project(td, country, {"margin": _COVER_THEN_LONG})
            tj(root, "run", "--no-input")
            r = tj(root, "find-missing-history", "--write-missing-history")
            self.assertIn("A negative quantity is a short held", r.stderr)
            tt = (root / "inputs/margin/missing_history.tt").read_text()
            self.assertIn(
                f"OPENING 2025-02-02 {self._SYM[country]} -10 cost=unknown",
                tt)
            r = tj(root, "run", "--no-input")
            self.assertNotIn("rows never go short", r.stdout + r.stderr)
            self.assertNotIn("cover of a short", r.stdout)
            # The later sale's documented cost; the cover by hand; no long.
            self.assertEqual(_sale(root), [(10.0, 600.0, 100.0)])
            doc = _gains_doc(root)
            self.assertEqual(
                [(m["direction"], m["qty"], m["proceeds"])
                 for m in doc["manual_reporting_required"]],
                [("SHORT", 10.0, -500.0)])
            self.assertEqual(doc.get("inventory_year_end") or [], [])
            self.assertEqual(_issues(root), ["unknown-cost"])
            out = tj(root, "find-missing-history").stdout
            self.assertNotIn("COVERS OF A SHORT", out)
            self.assertNotIn("STALE", out)
            self.assertIn("No open positions", tj(root, "list").stdout)
            self.assertIn("[x] 10. missing-history",
                          tj(root, "checklist", "--run", check=False).stdout)
            # A second write adds nothing.
            r = tj(root, "find-missing-history", "--write-missing-history")
            self.assertEqual(
                (root / "inputs/margin/missing_history.tt").read_text(), tt)

    @rule("CA-ACB-11")
    def test_canada_the_line_answers_it(self):
        self._answered("canada")

    @rule("US-BASIS-04")
    def test_usa_the_line_answers_it(self):
        self._answered("usa")

    @rule("CA-ACB-11")
    def test_a_short_line_keeps_another_accounts_long_cost(self):
        """Canada's pool spans the taxable accounts: the short is not
        netted against a long another account holds."""
        with tempfile.TemporaryDirectory() as td:
            root = _ib_project(td, "canada", {
                "margin": [("2025-02-03", 10, 50, "C")],
                "other": [("2025-01-10", 100, 40, "O"),
                          ("2025-06-02", -50, 45, "C")]})
            (root / "inputs/margin/missing_history.tt").write_text(
                "OPENING 2025-01-31 SYN.TO -10 cost=unknown\n")
            tj(root, "run", "--no-input")
            self.assertEqual(_sale(root, "other"), [(50.0, 2000.0, 250.0)])
            self.assertEqual(
                [m["direction"] for m in _gains_doc(root)
                 ["manual_reporting_required"]], ["SHORT"])

    def test_a_cover_of_an_earlier_year_is_flagged_a_later_one_is_not(self):
        rows = [("2024-02-05", 10, 50, "C")]
        for year, flagged, heading in (
                (2025, True, "AFFECTS 2025 - an earlier year's cover"),
                (2023, False, "NOT relevant to 2023 - later covers")):
            with self.subTest(year=year), tempfile.TemporaryDirectory() as td:
                root = _ib_project(td, "canada", {"margin": rows}, year=year)
                tj(root, "run", "--no-input")
                self.assertEqual("unbacked-cover" in _issues(root), flagged)
                self.assertIn(heading,
                              tj(root, "find-missing-history").stdout)

    def test_a_sale_past_the_answered_cover_is_still_no_purchase(self):
        with tempfile.TemporaryDirectory() as td:
            root = _ib_project(td, "canada", {"margin": [
                ("2025-02-03", 10, 50, "C"), ("2025-04-03", -10, 70, "")]})
            (root / "inputs/margin/missing_history.tt").write_text(
                "OPENING 2025-01-31 SYN.TO -10 cost=unknown\n")
            r = tj(root, "run", "--no-input")
            self.assertIn("1 sale with no purchase", r.stdout)
            self.assertIn("AFFECTS 2025",
                          tj(root, "find-missing-history").stdout)

    def test_a_long_and_a_short_of_one_symbol_are_not_written(self):
        """One line per symbol per account: neither side is guessed."""
        with tempfile.TemporaryDirectory() as td:
            root = _ib_project(td, "canada", {"margin": [
                ("2025-02-03", -10, 70, ""), ("2025-02-10", 10, 50, "O"),
                ("2025-03-03", 5, 50, "C")]})
            tj(root, "run", "--no-input")
            r = tj(root, "find-missing-history", "--write-missing-history")
            self.assertIn("not written: 1 symbol(s) with both", r.stderr)

    def test_a_cover_of_a_short_in_the_rows_is_not_flagged(self):
        """Control: the short is in the files."""
        with tempfile.TemporaryDirectory() as td:
            root = _ib_project(td, "canada", {"margin": [
                ("2025-02-03", -10, 65, "O"), ("2025-02-10", 10, 50, "C"),
                ("2025-03-03", 10, 60, "O"), ("2025-04-03", -10, 70, "C")]})
            r = tj(root, "run", "--no-input")
            self.assertNotIn("cover of a short", r.stdout)
            self.assertEqual(_issues(root), [])
            self.assertEqual(sorted(_sale(root)),
                             [(10.0, -650.0, 150.0), (10.0, 600.0, 100.0)])
            r = tj(root, "find-missing-history", "--write-missing-history")
            self.assertFalse(
                (root / "inputs/margin/missing_history.tt").exists())

    def test_a_long_line_is_as_it_was(self):
        """Control: a sale with no purchase and its positive line."""
        with tempfile.TemporaryDirectory() as td:
            root = _ib_project(td, "canada", {"margin": [
                ("2025-04-03", -10, 70, "C")]})
            tj(root, "run", "--no-input")
            tj(root, "find-missing-history", "--write-missing-history")
            self.assertIn(
                "SYN.TO 10 cost=unknown",
                (root / "inputs/margin/missing_history.tt").read_text())
            tj(root, "run", "--no-input")
            self.assertEqual(
                [m["direction"] for m in _gains_doc(root)
                 ["manual_reporting_required"]], ["LONG"])
            self.assertEqual(_issues(root), ["unknown-cost"])

    def test_refused_quantities(self):
        for line, want in (
                ("OPENING 2025-01-31 SYN.TO 0 cost=unknown\n",
                 "negative for a short"),
                ("OPENING 2025-01-31 SYN251219C00010000.TO -1 cost=unknown\n",
                 "a short of shares, not an option contract")):
            with self.subTest(line=line), tempfile.TemporaryDirectory() as td:
                root = _ib_project(td, "canada", {"margin": [
                    ("2025-02-03", 10, 50, "C")]})
                (root / "inputs/margin/missing_history.tt").write_text(line)
                r = tj(root, "check-tt", check=False)
                self.assertEqual(r.returncode, 1)
                self.assertIn(want, r.stdout + r.stderr)


class TestSharedTobaseAfterTheClose(unittest.TestCase):
    """TJ-0712: a filed year names a shared tobase.map pair that touches a
    symbol it has rows of; a master's other pairs say nothing."""

    def _closed(self, td, ticker=""):
        top = Path(td)
        y = top / "2024"
        y.mkdir()
        (top / "inputs/margin").mkdir(parents=True)
        (top / "inputs/margin/t.tt").write_text(
            "BUYSELL 2024-01-02 10:00:00 ZQA.TO 10 CAD 10 100 0\n"
            "BUYSELL 2024-01-05 10:00:00 ZQA.US 10 CAD 20 200 0\n"
            "BUYSELL 2024-06-03 10:00:00 ZQA.TO -10 CAD 15 150 0\n")
        (top / "tobase.map").write_text(
            "# master-generated: 2024-01-01\nTOBASE ZQB.US ZQB.TO\n")
        (y / "taxjson.toml").write_text(
            '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2024\n'
            'country = "canada"\nbase_currency = "CAD"\n'
            'source_currencies = []\ninputs_dir = "../inputs"\n'
            'tobase_map = "../tobase.map"\n'
            '[accounts.margin]\ntype = "taxable"\n')
        (y / "ticker.map").write_text(ticker)
        tj(y, "run", "--no-input")
        tj(y, "close-year", "--yes")
        return top, y

    def _say(self, y):
        r = tj(y, "check-filed")
        return r.stdout + r.stderr

    def _add(self, top, line):
        with (top / "tobase.map").open("a") as f:
            f.write(line)

    def test_a_pair_of_the_years_symbols_is_named(self):
        with tempfile.TemporaryDirectory() as td:
            top, y = self._closed(td)
            before = json.loads((y / "filed/2024.json").read_text())["totals"]
            self._add(top, "TOBASE ZQA.US ZQA.TO\nTOBASE ZQC.US ZQC.TO\n")
            out = self._say(y)
            self.assertIn("by 1 pair naming a symbol with rows of 2024: "
                          "TOBASE ZQA.US ZQA.TO", out)
            self.assertNotIn("ZQC", out)
            self.assertIn("filed 2024: OK", out)
            self.assertEqual(
                json.loads((y / "filed/2024.json").read_text())["totals"],
                before)
            r = tj(top, "years", "--json")
            row = json.loads(r.stdout)["years"][0]
            self.assertEqual(row["shared_exports_changed"], ["tobase.map"])

    def test_a_removed_pair_is_named(self):
        with tempfile.TemporaryDirectory() as td:
            top, y = self._closed(td)
            (y / "tobase.map").write_text(
                "# master-generated: 2024-01-01\nTOBASE ZQA.US ZQA.TO\n"
                "TOBASE ZQB.US ZQB.TO\n")
            self.assertIn("TOBASE ZQA.US ZQA.TO", self._say(y))

    def test_other_pairs_and_a_rewrite_say_nothing(self):
        """Control: a master update's other pairs, a comment, another
        order: the case a byte comparison would mark on every update."""
        with tempfile.TemporaryDirectory() as td:
            top, y = self._closed(td)
            (top / "tobase.map").write_text(
                "# master-generated: 2026-01-01\n# a comment\n"
                "TOBASE ZQD.US ZQD.TO\nTOBASE ZQC.US ZQC.TO\n"
                "TOBASE ZQB.US ZQB.TO  # master:BBG000000000\n")
            out = self._say(y)
            self.assertNotIn("tobase.map", out)
            row = json.loads(tj(top, "years", "--json").stdout)["years"][0]
            self.assertEqual(row["shared_exports_changed"], [])
            self.assertEqual(row["shared_not_compared"], [])

    def test_a_pair_ticker_map_decides_says_nothing(self):
        """Control: the year's ticker.map keeps the listings apart, so the
        pair would not apply."""
        with tempfile.TemporaryDirectory() as td:
            top, y = self._closed(td, "DISTINCT ZQA.US ZQA.TO\n")
            self._add(top, "TOBASE ZQA.US ZQA.TO\n")
            self.assertNotIn("tobase.map", self._say(y))


class TestMigrateLeftAtTheTop(unittest.TestCase):
    """TJ-1264: a folder a kept link reads is not 'no year reads them'."""

    def _migrate(self, td, link=True):
        root = Path(td)
        (root / "taxjson.toml").write_text(
            '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\n'
            'country = "canada"\nbase_currency = "CAD"\n'
            'source_currencies = []\n[accounts.margin]\ntype = "taxable"\n')
        (root / "ticker.map").write_text("")
        (root / "inputs/margin").mkdir(parents=True)
        (root / "inputs/margin/t.tt").write_text(
            "BUYSELL 2025-01-02 10:00:00 SYN.TO 10 CAD 10 100 0\n"
            "BUYSELL 2025-02-03 10:00:00 SYN.TO -10 CAD 15 150 0\n")
        (root / "maps").mkdir()
        (root / "maps/more.tt").write_text(
            "BUYSELL 2025-03-02 10:00:00 SYN.TO 10 CAD 10 100 0\n")
        (root / "other").mkdir()
        (root / "other/note.txt").write_text("a note\n")
        if link:
            (root / "inputs/margin/more.tt").symlink_to("../../maps/more.tt")
        tj(root, "run", "--no-input")
        tj(root, "close-year", "--yes")
        r = tj(root, "migrate", "--to-years")
        return next(ln for ln in r.stdout.splitlines()
                    if ln.startswith("Left at the top"))

    def test_a_folder_a_kept_link_reads_is_not_listed(self):
        with tempfile.TemporaryDirectory() as td:
            line = self._migrate(td)
            self.assertNotIn("maps", line)
            self.assertIn("other", line)

    def test_with_no_link_the_folder_is_listed(self):
        """Control: nothing reads it, so the line says so."""
        with tempfile.TemporaryDirectory() as td:
            line = self._migrate(td, link=False)
            self.assertIn("maps", line)
            self.assertIn("other", line)


class TestNewYearKeepsALinkedHoldingsFolder(unittest.TestCase):
    """TJ-1156: a plainly named holdings_dir whose folder is a link to a
    shared one stays shared in the new year."""

    def _top(self, td, link=True):
        top = Path(td)
        (top / "inputs/margin").mkdir(parents=True)
        (top / "inputs/margin/t.tt").write_text(
            "BUYSELL 2025-01-02 10:00:00 SYN.TO 10 CAD 10 100 0\n")
        y = top / "2025"
        y.mkdir()
        (y / "taxjson.toml").write_text(
            '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\n'
            'country = "canada"\nbase_currency = "CAD"\n'
            'source_currencies = []\ninputs_dir = "../inputs"\n'
            'holdings_dir = "snapshots"\n'
            '[accounts.margin]\ntype = "taxable"\n')
        (y / "ticker.map").write_text("")
        if link:
            (top / "snapshots_all").mkdir()
            (top / "snapshots_all/margin_holdings.toml").write_text(
                '[[position]]\nsymbol = "SYN.TO"\nqty = 10\n')
            (y / "snapshots").symlink_to("../snapshots_all")
        else:
            (y / "snapshots").mkdir()
        return top

    def test_the_new_year_gets_the_same_link(self):
        with tempfile.TemporaryDirectory() as td:
            top = self._top(td)
            out = tj(top, "new-year", "2026").stdout
            new = top / "2026/snapshots"
            self.assertTrue(new.is_symlink())
            self.assertEqual(new.resolve(), (top / "snapshots_all").resolve())
            self.assertIn("snapshots_all/ (shared)", out)
            self.assertIn('holdings_dir', (top / "2026/taxjson.toml").read_text())
            tj(top / "2026", "run", "--no-input")
            r = tj(top / "2026", "sanity", check=False)
            self.assertNotIn("UNCHECKED", r.stdout + r.stderr)
            # The same word in both years: nothing for align to bring over.
            d = tj(top, "years", "--json")
            for row in json.loads(d.stdout)["years"]:
                self.assertNotIn("holdings_dir",
                                 json.dumps(row["differs_from_newest"]))

    def test_a_year_of_its_own_gets_an_empty_folder(self):
        """Control: a real folder of the year stays the year's own."""
        with tempfile.TemporaryDirectory() as td:
            top = self._top(td, link=False)
            out = tj(top, "new-year", "2026").stdout
            new = top / "2026/snapshots"
            self.assertTrue(new.is_dir())
            self.assertFalse(new.is_symlink())
            self.assertIn("2026/snapshots/", out)


class TestDearShareSoldToATinyRest(unittest.TestCase):
    """TJ-1062, TJ-1141: a position above the tolerance sold to a rest
    under it that carries real cost: the rest is held and its sale is
    realized, instead of a stranded cost and a silent dust short."""

    Z = "ZQD.US"

    def _canada(self, book):
        from taxjson.lib.pipeline import GainsRequest, run_gains
        err = io.StringIO()
        with contextlib.redirect_stderr(err), \
                contextlib.redirect_stdout(io.StringIO()):
            res = run_gains(book, [], [], req=GainsRequest(
                country="canada", taxable=True, year=2025,
                phantom_hint=False))
        return res, err.getvalue()

    def _first(self):
        return [tx("BUYSELL", "2025-01-06", self.Z, 0.001, 900.0),
                tx("BUYSELL", "2025-06-03", self.Z, -0.0009995, 950.0)]

    @rule("CA-ACB-01")
    def test_the_rest_is_held_with_its_cost(self):
        res, err = self._canada(self._first())
        self.assertNotIn("stranded", err)
        held = {i["symbol"]: i for i in res["inventory"]}
        self.assertAlmostEqual(held[self.Z]["total_cost"], 0.45, places=4)
        self.assertAlmostEqual(res["summary"]["total_gain"], 950 - 899.55,
                               places=4)

    @rule("CA-ACB-01")
    def test_its_sale_is_realized_and_opens_no_short(self):
        res, err = self._canada(self._first() + [
            tx("BUYSELL", "2025-07-03", self.Z, -0.0000005, 0.75)])
        self.assertEqual(res["inventory"], [])
        self.assertNotIn("stranded", err)
        self.assertAlmostEqual(res["summary"]["total_gain"],
                               950.75 - 900.0, places=4)

    def test_an_ordinary_rest_still_drains(self):
        """Control: rounding residue of an ordinary share: no holding, no
        warning, the gain as ever."""
        res, err = self._canada(
            [tx("BUYSELL", "2025-01-06", "ZQE.US", 10.0, 500.0),
             tx("BUYSELL", "2025-06-03", "ZQE.US", -9.9999995, 600.0)])
        self.assertEqual(res["inventory"], [])
        self.assertNotIn("stranded", err)
        self.assertAlmostEqual(res["summary"]["total_gain"], 100.0, places=2)

    def test_a_full_sale_leaves_nothing(self):
        """Control: the same dear share sold whole."""
        res, err = self._canada(
            [tx("BUYSELL", "2025-01-06", self.Z, 0.001, 900.0),
             tx("BUYSELL", "2025-06-03", self.Z, -0.001, 950.0)])
        self.assertEqual(res["inventory"], [])
        self.assertAlmostEqual(res["summary"]["total_gain"], 50.0, places=4)


class TestOpenShortIsNamed(unittest.TestCase):
    """TJ-1138: a short sale with no cover and no broker mark: the run and
    sum say it can be a short still open, not only a missing purchase."""

    def _project(self, td, country, rows):
        root = Path(td)
        cur = "USD" if country == "usa" else "CAD"
        (root / "taxjson.toml").write_text(
            f'[settings]\nlocal_timezone = "America/Toronto"\nyear = 2024\n'
            f'country = "{country}"\nbase_currency = "{cur}"\n'
            f'source_currencies = []\n[accounts.margin]\ntype = "taxable"\n')
        (root / "ticker.map").write_text("")
        (root / "inputs/margin").mkdir(parents=True)
        (root / "inputs/margin/t.tt").write_text(rows.replace("CUR", cur))
        r = tj(root, "run", "--no-input")
        return root, r.stdout + r.stderr

    SALE = "BUYSELL 2024-12-16 10:00:00 ZRS.TO -30 CUR 100.00 2999.00 1\n"

    def test_the_message_says_it_may_be_a_short_still_open(self):
        for country in ("usa", "canada"):
            with self.subTest(country=country), \
                    tempfile.TemporaryDirectory() as td:
                root, out = self._project(td, country, self.SALE)
                self.assertIn("A short sale still open reads the same",
                              tj(root, "run", "--no-input",
                                 "--details").stdout.replace("\n", " "))
                self.assertIn("these totals, or a short", tj(root, "sum").stdout)
                d = tj(root, "find-missing-history", "--details").stdout
                self.assertIn("A short sale you made and have not covered", d)

    def test_a_covered_short_does_not_say_so(self):
        """Control: covered by a later purchase, the sale is in the totals."""
        with tempfile.TemporaryDirectory() as td:
            root, out = self._project(
                td, "usa", self.SALE +
                "BUYSELL 2024-12-20 10:00:00 ZRS.TO 30 CUR 90.00 2700.00 1\n")
            self.assertNotIn("or a short", out)
            self.assertNotIn("or a short", tj(root, "sum").stdout)

    def test_a_sale_of_what_was_bought_says_nothing(self):
        """Control: an ordinary buy and sale."""
        with tempfile.TemporaryDirectory() as td:
            _root, out = self._project(
                td, "canada",
                "BUYSELL 2024-01-16 10:00:00 ZRS.TO 30 CUR 90.00 2700.00 1\n"
                + self.SALE)
            self.assertNotIn("no purchase", out)


class TestLockRecordsAShortBesideALong(unittest.TestCase):
    """TJ-1134: close-year records a short beside another account's long,
    and the hand-off compares it, as it does a short alone."""

    def _closed(self, td, books):
        root = _ib_project(td, "canada", books)
        tj(root, "run", "--no-input")
        tj(root, "close-year", "--yes", "--force")
        return root, json.loads((root / "filed/2025.json").read_text())

    LONG = [("2025-01-06", 10, 50, "O")]
    SHORT = [("2025-03-06", -10, 65, "O")]

    def test_the_lock_records_it(self):
        with tempfile.TemporaryDirectory() as td:
            _root, rec = self._closed(td, {"a": self.LONG, "b": self.SHORT})
            self.assertEqual(rec["year_end"]["equity"]["SYN.TO"]["qty"], 10.0)
            self.assertEqual(rec["year_end_shorts"]["equity"]["SYN.TO"],
                             {"qty": -10.0, "acb": -650.0})

    def test_a_short_alone_stays_the_pools_own_row(self):
        """Control: the short alone is in year_end, as it was."""
        with tempfile.TemporaryDirectory() as td:
            _root, rec = self._closed(td, {"b": self.SHORT})
            self.assertEqual(rec["year_end"]["equity"]["SYN.TO"]["qty"], -10.0)
            self.assertEqual(rec["year_end_shorts"]["equity"], {})

    def test_a_long_alone_records_no_short(self):
        """Control."""
        with tempfile.TemporaryDirectory() as td:
            _root, rec = self._closed(td, {"a": self.LONG})
            self.assertEqual(rec["year_end_shorts"]["equity"], {})

    def _check(self, rec, now, opening_shorts):
        from taxjson.lib import handoff
        with tempfile.TemporaryDirectory() as td:
            cfg = {"settings": {"year": 2026, "country": "canada"},
                   "accounts": {}}
            return handoff.check(Path(td), cfg, dict(rec, year=2025), now,
                                 opening_shorts=opening_shorts)["positions"]

    def test_the_handoff_names_a_changed_short(self):
        rec = {"year_end": {"equity": {"SYN.TO": {
            "qty": 10.0, "acb": 500.0, "deferred": 0.0}}},
            "year_end_shorts": {"equity": {"SYN.TO": {
                "qty": -10.0, "acb": -650.0}}}}
        now = rec["year_end"]
        said = self._check(rec, now, {"equity": {"SYN.TO": {
            "qty": -10.0, "acb": -700.0}}})
        self.assertEqual(len(said), 1)
        self.assertEqual(said[0]["acb_diff"], -50.0)
        self.assertIn("the short beside a long", said[0]["why"])
        # The same short: nothing to say.
        self.assertEqual(self._check(rec, now, rec["year_end_shorts"]), [])
        # An older record has no short to compare.
        old = {"year_end": rec["year_end"]}
        self.assertEqual(self._check(old, now, rec["year_end_shorts"]), [])

    def test_a_damaged_record_is_refused(self):
        from taxjson.lib import handoff
        self.assertIn("year_end_shorts", handoff._shorts_problem(
            {"equity": {"SYN.TO": {"qty": "x", "acb": 1.0}}}))
        self.assertIsNone(handoff._shorts_problem({"equity": {}}))


class TestDatedListShowsEachAccountsPremium(unittest.TestCase):
    """TJ-1251: under close timing the dated list and the year-end pools
    show each account's written premium, as plain `list` does."""

    OPT = "SYQ261218P00010000.TO"

    def _project(self, td, a, b, timing="close"):
        root = Path(td)
        (root / "taxjson.toml").write_text(
            '[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\n'
            'country = "canada"\nbase_currency = "CAD"\n'
            f'source_currencies = []\noption_premium_timing = "{timing}"\n'
            '[accounts.a]\ntype = "taxable"\n'
            '[accounts.b]\ntype = "taxable"\n')
        (root / "ticker.map").write_text("")
        for acct, rows in (("a", a), ("b", b)):
            (root / "inputs" / acct).mkdir(parents=True)
            (root / "inputs" / acct / "t.tt").write_text(rows)
        tj(root, "run", "--no-input")
        return root

    def _a(self):
        return f"BUYSELL 2025-01-02 10:00:00 {self.OPT} -1 CAD 1 100\n"

    def _b(self):
        return f"BUYSELL 2025-01-03 10:00:00 {self.OPT} -2 CAD 3 600\n"

    def _listed(self, root, *args):
        return tj(root, "list", *args).stdout

    @rule("CA-OPT-06")
    def test_list_date_agrees_with_list(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, self._a(), self._b())
            for args in ((), ("--date", "2025-06-30")):
                text = self._listed(root, *args)
                self.assertRegex(text, r"a\s+\S+\s+-1\s+-100\.00", args)
                self.assertRegex(text, r"b\s+\S+\s+-2\s+-600\.00", args)

    @rule("CA-OPT-06")
    def test_each_day_shows_what_was_open_then(self):
        """After a's buy-back only b's contracts are open; before it, both
        at their own premium."""
        with tempfile.TemporaryDirectory() as td:
            root = self._project(
                td, self._a()
                + f"BUYSELL 2025-06-02 10:00:00 {self.OPT} 1 CAD 0.5 50\n",
                self._b())
            before = self._listed(root, "--date", "2025-03-03")
            self.assertRegex(before, r"a\s+\S+\s+-1\s+-100\.00")
            self.assertRegex(before, r"b\s+\S+\s+-2\s+-600\.00")
            after = self._listed(root, "--date", "2025-07-02")
            self.assertRegex(after, r"b\s+\S+\s+-2\s+-600\.00")
            self.assertNotRegex(after, r"\ba\s+SYQ")

    @rule("CA-OPT-06")
    def test_the_year_end_pools_too(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, self._a(), self._b())
            cost = {}
            for acct in "ab":
                doc = json.loads(
                    (root / f"work/{acct}_gains_wash.json").read_text())
                cost[acct] = [i["total_cost"]
                              for i in doc["inventory_year_end"]]
            self.assertEqual(cost, {"a": [-100.0], "b": [-600.0]})

    def test_one_account_writes_no_premium_part(self):
        """Control: a single account, and two accounts' long shares, keep
        the timeline as it was."""
        from taxjson.lib import cost_timeline as CT
        with tempfile.TemporaryDirectory() as td:
            root = self._project(
                td, "BUYSELL 2025-01-02 10:00:00 SYN.TO 10 CAD 10 100 0\n",
                "BUYSELL 2025-01-03 10:00:00 SYN.TO 10 CAD 30 300 0\n")
            text = self._listed(root, "--date", "2025-06-30")
            self.assertRegex(text, r"a\s+SYN\.TO\s+10\s+200\.00")
            self.assertRegex(text, r"b\s+SYN\.TO\s+10\s+200\.00")
            tls = list((root / "work").glob(".*.timeline.json"))
            self.assertTrue(tls)
            for p in tls:
                doc = json.loads(p.read_text())
                self.assertIsNone(CT.check(doc))
                self.assertNotIn("account_premiums", doc)

    def test_a_damaged_part_is_refused(self):
        from taxjson.lib import cost_timeline as CT
        doc = {"version": CT.VERSION, "fields": list(CT.FIELDS), "rows": [],
               "account_premiums": {"7": {}}}
        self.assertIn("account_premiums", CT.check(doc))
        doc["account_premiums"] = {}
        self.assertIsNone(CT.check(doc))


if __name__ == "__main__":
    unittest.main()
