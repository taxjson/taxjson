"""The tjs command audit of 2026-10-27, group diagnostics: the views that
explain a project's books (transfers, renames, check-dates, the
corporate-action views, journals, check-tt, the checklist's format
step) said a check was clean when its evidence was missing or damaged.

TJ-0308  `tjs transfers`: a damaged converted .tt file raised inside the
         arrivals classification, the view swallowed it, and every
         taxable transfer-in read "own move" — as if a transfer-out of
         yours had been matched. Now the read failure is named and each
         transfer-in reads UNCLASSIFIED; "own move" comes only from the
         pairing's own word (transfer_in.arrivals `paired`).
TJ-0309  `tjs renames` in a US project carried the average cost of the
         renamed shares (150), not the FIFO basis the books carry (200):
         the replay is the country's lot rule (country.lot_rule).
TJ-0319  `tjs check-dates` read a parsed source its sources list names
         but work/ lacks as 0 rows checked clean, and `tjs renames` with
         no books said "nothing pending" at exit 0. Now the first is an
         ERROR (`missing`, exit 1) and the second refuses like `splits`.
TJ-0320  `tjs splits` / `spinoffs` / `renames` dropped a book row that
         is not an object (and renames a non-list `transactions`) and
         reported clean; now each refuses naming the file and the row.
TJ-0321  `tjs journals`: a state file with a valid format tag and a
         damaged record list (`"refused": [null]`) read as no journals,
         so a pending review vanished (exit 0, pending 0). Now
         cross_listings.state_problem checks the lists and records.
TJ-0322  `tjs check-tt` said "Every line parses." for two known-cost
         OPENING snapshots of one symbol with two dates, which the run
         refuses (lib/opening.snapshots); now an ERROR of the second
         line, in project mode and for named files alike.
TJ-0250  `tjs checklist`'s format step (TJ-0340, TJ-0112): a tobase.map
         line the run refuses left the step at "not in the template
         layout" (exit 0) or "laid out"; it now counts the problems of
         the map the run reads (ticker.map with its tobase.map) and names
         the first line, and the checks no longer repeat the loader's
         lines below it.

Every fixture is synthetic: invented tickers, amounts and account ids.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from _style import CapturedWidth
from tax_rules import rule

from taxjson.lib import transfer_in as TI
from taxjson.lib.dispatch import python_module_argv

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
_WIDTH = CapturedWidth()


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


def _cli(root: Path, *args: str, width: str = "0"
         ) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH=width)
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *args]),
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        timeout=300)


def _config(root: Path, country: str = "canada", year: int = 2025,
            accounts: str = '[accounts.margin]\ntype = "taxable"\n') -> None:
    cur = "CAD" if country == "canada" else "USD"
    (root / "taxjson.toml").write_text(
        f'[settings]\nyear = {year}\ncountry = "{country}"\n'
        f'base_currency = "{cur}"\nsource_currencies = []\n'
        f'local_timezone = "America/Toronto"\n' + accounts,
        encoding="utf-8")


def _leg(acct, d, q, sym="ZZQ.TO"):
    """One transfer sidecar row as transfer_in.arrivals takes it."""
    return (acct, "questrade",
            dict(action="TRANSFER", symbol=sym, date=d, date_settle=d,
                 time="09:30:00", quantity=q, net_amount=0,
                 currency="CAD", account=acct))


class TestTransfersUnclassified(unittest.TestCase):
    """TJ-0308: a transfer-in is "own move" on the pairing's evidence
    only; a classification that cannot be made is named and reads
    UNCLASSIFIED."""

    def _project(self, td: str) -> Path:
        root = Path(td)
        (root / "inputs" / "margin").mkdir(parents=True)
        (root / "work").mkdir()
        _config(root)
        (root / "inputs" / "margin" / "synthetic.tt").write_text(
            "# synthetic\n", encoding="utf-8")
        row = {"action": "TRANSFER", "date": "2025-01-06",
               "date_settle": "2025-01-06", "symbol": "ZZQ.TO",
               "quantity": 10, "currency": "CAD", "account": "margin",
               "net_amount": 0, "description": "TRANSFER IN",
               "id": "fake-transfer"}
        (root / "work" / "margin_questrade_transfers.json").write_text(
            json.dumps({"transactions": [row],
                        "metadata": {"kind": "transfer_sidecar",
                                     "account": "margin",
                                     "brokerage": "questrade"}}),
            encoding="utf-8")
        (root / "work" / "margin_base.json").write_text(
            '{"transactions": []}', encoding="utf-8")
        return root

    def _arrival(self, root: Path, tt_doc: str):
        (root / "work" / "margin_tt_synthetic.json").write_text(
            tt_doc, encoding="utf-8")
        r = _cli(root, "transfers", "--json")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        doc = json.loads(r.stdout)
        return [t["arrival"] for t in doc["transfers"]], r.stderr

    def test_tj0308_a_valid_cache_keeps_the_arrival_no_cost(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td)
            for good in ('{"transactions": []}', "[]"):
                arrival, err = self._arrival(root, good)
                self.assertEqual(arrival, ["NO COST"], good)
                self.assertNotIn("not classified", err)

    def test_tj0308_a_damaged_cache_is_named_and_never_own_move(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td)
            for bad, why in (('[null]', "must be a list of JSON objects"),
                             ('{"transactions": 42}', "must be a list"),
                             ('{ broken', "not valid JSON")):
                arrival, err = self._arrival(root, bad)
                self.assertEqual(arrival, ["UNCLASSIFIED"], bad)
                self.assertIn("transfer-ins not classified", err)
                self.assertIn("margin_tt_synthetic.json", err)
                self.assertIn(why, err)
            text = _cli(root, "transfers")
            self.assertEqual(text.returncode, 0, text.stderr[-2000:])
            self.assertIn("UNCLASSIFIED", text.stdout)
            self.assertIn("! 1 transfer-in(s) UNCLASSIFIED (ZZQ.TO)",
                          text.stdout)
            self.assertNotIn("own_move", text.stdout)

    def test_tj0308_paired_names_only_the_cancelled_in_legs(self):
        # One move of your own (a -> b, 3 days), one arrival from outside.
        rows = [_leg("a", "2025-03-03", -100), _leg("b", "2025-03-06", 100),
                _leg("b", "2025-05-01", 7, sym="ZZR.TO")]
        paired = []
        found = TI.arrivals(rows, paired=paired)
        self.assertEqual([(a.account, a.symbol) for a in found],
                         [("b", "ZZR.TO")])
        self.assertEqual(paired, [("b", "ZZQ.TO", "2025-03-06", 100.0)])
        # Partly paired: both an arrival (the rest) and paired.
        paired = []
        found = TI.arrivals([_leg("a", "2025-03-03", -60),
                             _leg("b", "2025-03-06", 100)], paired=paired)
        self.assertEqual([a.quantity for a in found], [40.0])
        self.assertEqual(paired, [("b", "ZZQ.TO", "2025-03-06", 100.0)])


class TestRenamesCarryTheCountrysCost(unittest.TestCase):
    """TJ-0309: two buys (100, 200), a sale of one, then a rename: the
    renamed share carries the remaining lot's 200 in the US, the
    pool's average 150 in Canada."""

    def _carried(self, td: str, country: str):
        root = Path(td) / country
        (root / "inputs" / "margin").mkdir(parents=True)
        _config(root, country)
        cur = "CAD" if country == "canada" else "USD"
        sfx = "TO" if country == "canada" else "US"
        (root / "inputs" / "margin" / "trades.tt").write_text(
            f"BUYSELL 2025-01-06 10:00:00 ZZQ.{sfx} 1 {cur} 100 100 0\n"
            f"BUYSELL 2025-02-03 10:00:00 ZZQ.{sfx} 1 {cur} 200 200 0\n"
            f"BUYSELL 2025-06-02 10:00:00 ZZQ.{sfx} -1 {cur} 250 250 0\n"
            f"RENAME 2025-07-01 ZZQ.{sfx} ZZR.{sfx}\n", encoding="utf-8")
        r = _cli(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        r = _cli(root, "renames", "--json")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        doc = json.loads(r.stdout)
        self.assertEqual(len(doc["renames"]), 1, doc)
        return root, doc["renames"][0]["carried"]

    @rule("US-BASIS-01")
    def test_tj0309_us_rename_carries_the_fifo_lot(self):
        with tempfile.TemporaryDirectory() as td:
            root, carried = self._carried(td, "usa")
            self.assertEqual([(c["qty"], c["book_cost"]) for c in carried],
                             [(1.0, 200.0)])
            # The books agree: the one lot left is the second purchase.
            g = json.loads((root / "work" / "margin_gains_wash.json")
                           .read_text(encoding="utf-8"))
            inv = g["inventory"]
            self.assertEqual(round(sum(float(v.get("total_cost") or 0)
                                       for v in inv), 2), 200.0)

    @rule("CA-ACB-01")
    def test_tj0309_canada_rename_keeps_the_average_cost(self):
        with tempfile.TemporaryDirectory() as td:
            _root, carried = self._carried(td, "canada")
            self.assertEqual([(c["qty"], c["book_cost"]) for c in carried],
                             [(1.0, 150.0)])

    def test_tj0309_fifo_walk_short_split_adjust(self):
        from taxjson.lib.renames import _walk_states

        def row(d, a, q, net=0.0, sym="ZZQ.US", **kw):
            return dict(date=d, time="10:00:00", action=a, symbol=sym,
                        quantity=q, net_amount=net, **kw)
        rows = [row("2025-01-02", "BUYSELL", 10, -100.0),
                row("2025-01-03", "BUYSELL", 10, -300.0),
                row("2025-02-01", "BUYSELL", -15, 600.0),
                row("2025-03-01", "SPLIT", 2.0),
                row("2025-04-01", "ADJUST", 0, -50.0),
                row("2025-05-01", "SPLIT", 1.0, symbol_new="ZZR.US")]
        snaps = _walk_states(rows, "usa")
        # Left: 5 of the second lot (150), split 2:1 -> 10 units at 150,
        # then a 50 return of capital -> 100.
        self.assertEqual(snaps[id(rows[3])], (5.0, 150.0))
        self.assertEqual(snaps[id(rows[5])], (10.0, 100.0))
        avg = _walk_states(rows, "canada")
        self.assertEqual(round(avg[id(rows[3])][1], 2), 100.0)


class _RunOnce(unittest.TestCase):
    """One synthetic Canadian project (one purchase in a .tt file), run
    once; each test damages a copy of it."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        cls.base = Path(cls._td.name) / "base"
        (cls.base / "inputs" / "margin").mkdir(parents=True)
        _config(cls.base)
        (cls.base / "inputs" / "margin" / "trades.tt").write_text(
            "BUYSELL 2025-01-06 10:00:00 ZZQ.TO 1 CAD 100 100 0\n",
            encoding="utf-8")
        r = _cli(cls.base, "run", "--no-input")
        assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def copy(self, name: str) -> Path:
        dst = Path(self._td.name) / name
        shutil.copytree(self.base, dst)
        return dst


class TestMissingEvidence(_RunOnce):
    """TJ-0319: evidence the project records is checked or refused,
    never read as an empty, clean check."""

    def test_tj0319_check_dates_names_a_missing_parsed_source(self):
        good = _cli(self.base, "check-dates", "--json")
        self.assertEqual(good.returncode, 0, good.stderr[-2000:])
        self.assertEqual(json.loads(good.stdout)["errors"], 0)
        root = self.copy("missingparse")
        (root / "work" / "margin_tt_trades.json").unlink()
        r = _cli(root, "check-dates", "--json")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        doc = json.loads(r.stdout)
        self.assertEqual(doc["errors"], 1)
        self.assertEqual([i["code"] for i in doc["issues"]], ["missing"])
        self.assertIn("work/margin_tt_trades.json is missing",
                      doc["issues"][0]["message"])
        t = _cli(root, "check-dates")
        self.assertEqual(t.returncode, 1)
        self.assertIn("missing: 1", t.stdout)
        self.assertIn("re-run `taxjson run`", " ".join(t.stdout.split()))

    def test_tj0319_renames_with_no_books_refuses(self):
        root = self.copy("nobook")
        shutil.rmtree(root / "work")
        r = _cli(root, "renames", "--json")
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertEqual(r.stdout.strip(), "")
        self.assertIn("run `taxjson run` first", r.stderr)
        good = _cli(self.base, "renames", "--json")
        self.assertEqual(good.returncode, 0, good.stderr[-2000:])
        self.assertEqual(json.loads(good.stdout)["pending"], 0)


class TestMalformedBookRows(_RunOnce):
    """TJ-0320: a book row that is not an object, or a `transactions`
    that is not a list, is refused naming the file and the row; a valid
    empty list stays a clean, empty view."""

    def _book(self, name: str, text: str) -> Path:
        root = self.copy(name)
        (root / "work" / "margin_base.json").write_text(text,
                                                        encoding="utf-8")
        return root

    def test_tj0320_a_null_row_is_refused_by_each_view(self):
        root = self._book("badrows", '{"transactions": [null]}')
        for cmd in ("splits", "spinoffs", "renames"):
            r = _cli(root, cmd, "--json")
            self.assertNotEqual(r.returncode, 0, (cmd, r.stdout))
            self.assertEqual(r.stdout.strip(), "", cmd)
            self.assertIn("margin_base.json", r.stderr, cmd)
            self.assertIn("(row 0 is null)", r.stderr, cmd)
            self.assertIn("re-run `taxjson run`", r.stderr, cmd)

    def test_tj0320_renames_refuses_a_non_list(self):
        root = self._book("badshape", '{"transactions": 42}')
        r = _cli(root, "renames", "--json")
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn('"transactions" must be a list, got int', r.stderr)

    def test_tj0320_a_valid_empty_book_is_still_clean(self):
        root = self._book("empty", '{"transactions": []}')
        for cmd, key in (("splits", "splits"), ("spinoffs", "spinoffs"),
                         ("renames", "renames")):
            r = _cli(root, cmd, "--json")
            self.assertEqual(r.returncode, 0, (cmd, r.stderr[-2000:]))
            self.assertEqual(json.loads(r.stdout)[key], [], cmd)


class TestJournalsDamagedState(unittest.TestCase):
    """TJ-0321: every record list and record of the journal state is
    checked before its lists are taken as the verdict."""

    def _project(self, td: str) -> Path:
        from test_journals_cmd import _leg, _project, _state
        legs = [_leg("ZZQ.US", "2025-03-03", -10, "ZZQ CORPORATION"),
                _leg("ZZQ.TO", "2025-03-03", 10, "OTHER MINING LTD")]
        root = _project(td, _state(legs))
        cfg = root / "taxjson.toml"
        cfg.write_text(cfg.read_text().replace('type = "tfsa"',
                                               'type = "sheltered"'))
        return root

    def test_tj0321_a_damaged_record_list_is_named_not_read_as_none(self):
        from taxjson.lib import cross_listings as XL
        from taxjson.lib import journals as J
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td)
            r = _cli(root, "journals", "--pending", "--json")
            self.assertEqual(r.returncode, 1, r.stderr[-2000:])
            self.assertEqual(json.loads(r.stdout)["pending"], 1)
            f = root / "work" / XL.STATE
            good = json.loads(f.read_text(encoding="utf-8"))
            cfg = {"settings": {"country": "canada",
                                "base_currency": "CAD", "year": 2025},
                   "accounts": {"margin": {"type": "taxable"}}}
            for key, bad, why in (
                    ("refused", [None], "refused[0] is null"),
                    ("refused", 7, "'refused' as int"),
                    ("refused", [{"from": "ZZQ.US"}],
                     "without its 'in' listing (refused[0]"),
                    ("refused", [dict(good["refused"][0], out=3)],
                     "whose 'out' leg is not a JSON object"),
                    ("joined", [{}], "without its 'out' listing (joined[0]"),
                    ("collisions", [{"names": []}],
                     "collision record without its symbol")):
                doc = dict(good)
                doc[key] = bad
                f.write_text(json.dumps(doc), encoding="utf-8")
                with self.assertRaises(J.JournalsError) as cm:
                    J.report(root, cfg)
                self.assertIn(why, str(cm.exception), key)
            f.write_text(json.dumps(dict(good, refused=[None])),
                         encoding="utf-8")
            r = _cli(root, "journals", "--pending", "--json")
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual(r.stdout.strip(), "")
            self.assertIn("work/cross_listings.state has a record that is "
                          "not a JSON object", r.stderr)
            self.assertIn("run `taxjson run` first", r.stderr)
            # A state with no refused list at all is still valid.
            f.write_text(json.dumps({k: v for k, v in good.items()
                                     if k != "refused"}), encoding="utf-8")
            self.assertEqual(J.report(root, cfg)["pending"], 0)


class TestCheckTtOpeningSnapshots(unittest.TestCase):
    """TJ-0322: check-tt names what the run refuses — one snapshot
    date per symbol per account — before the run."""

    OPEN = ("OPENING 2024-12-31 ZZQ.TO 1 CAD 100\n"
            "OPENING 2025-01-31 ZZQ.TO 1 CAD 100\n")

    def _project(self, td: str, files: dict) -> Path:
        root = Path(td) / "p"
        (root / "inputs" / "margin").mkdir(parents=True)
        _config(root)
        for name, text in files.items():
            (root / "inputs" / "margin" / name).write_text(
                text, encoding="utf-8")
        return root

    def _errors(self, r):
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        doc = json.loads(r.stdout)
        return [(p["where"], p["message"]) for p in doc["problems"]
                if p["severity"] == "error"]

    def test_tj0322_project_mode_names_the_second_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, {"opening.tt": self.OPEN})
            errs = self._errors(_cli(root, "check-tt", "--json"))
            self.assertEqual([w for w, _m in errs],
                             ["inputs/margin/opening.tt:2"])
            self.assertIn("one snapshot date per symbol per account",
                          errs[0][1])
            self.assertIn("The other line: inputs/margin/opening.tt:1",
                          errs[0][1])
            # The run refuses the same project.
            run = _cli(root, "run", "--no-input")
            self.assertNotEqual(run.returncode, 0)
            self.assertIn("one snapshot date per symbol per account",
                          run.stdout + run.stderr)
            text = _cli(root, "check-tt")
            self.assertIn("1 problem the run refuses", text.stdout)

    def test_tj0322_across_two_files_and_named_files(self):
        with tempfile.TemporaryDirectory() as td:
            a, b = self.OPEN.splitlines(keepends=True)
            root = self._project(td, {"a.tt": a, "b.tt": b})
            errs = self._errors(_cli(root, "check-tt", "margin", "--json"))
            self.assertEqual([w for w, _m in errs], ["inputs/margin/b.tt:1"])
            # Named: either file brings the error up (it names the other).
            for name in ("a.tt", "b.tt"):
                errs = self._errors(_cli(root, "check-tt", "--json",
                                         f"inputs/margin/{name}"))
                self.assertEqual(len(errs), 1, name)
                self.assertIn("inputs/margin/a.tt:1", errs[0][1], name)
            # Outside any project: the named files read as one account.
            loose = Path(td) / "loose" / "margin"
            loose.mkdir(parents=True)
            (loose / "o.tt").write_text(self.OPEN, encoding="utf-8")
            errs = self._errors(_cli(Path(td) / "loose", "check-tt",
                                     "--json", str(loose / "o.tt")))
            self.assertEqual(len(errs), 1)
            self.assertTrue(errs[0][0].endswith("o.tt:2"), errs)

    def test_tj0322_one_date_per_symbol_is_fine(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._project(td, {"opening.tt": (
                "OPENING 2024-12-31 ZZQ.TO 1 CAD 100\n"
                "OPENING 2024-12-31 ZZQ.TO 2 CAD 300\n"
                "OPENING 2025-01-31 ZZR.TO 1 CAD 100\n")})
            r = _cli(root, "check-tt", "--json")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(json.loads(r.stdout)["errors"], 0)


class TestChecklistFormatSeesTobaseMap(_RunOnce):
    """TJ-0250 (TJ-0340, TJ-0112): the format step counts the problems
    of the map `tjs run` reads, ticker.map with its tobase.map, and
    names the first failing line."""

    def _format(self, root: Path) -> dict:
        r = _cli(root, "checklist", "--run", "--only", "format", "--json")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        step = [s for s in json.loads(r.stdout)["steps"]
                if s["id"] == "format"][0]
        self.assertEqual(step["effective"], "attention", step)
        return step

    def test_tj0250_a_tobase_map_line_the_run_refuses(self):
        root = self.copy("tobase")
        (root / "ticker.map").write_text("", encoding="utf-8")
        (root / "tobase.map").write_text("GLOBAL ZZQ.US ZZQ.TO\n",
                                         encoding="utf-8")
        self.assertEqual(_cli(root, "run", "--no-input").returncode, 1)
        step = self._format(root)
        self.assertIn("1 tobase.map problem(s) `tjs run` refuses — "
                      "tobase.map:1: ", step["detail"])

    def test_tj0250_a_shared_tobase_map_is_named_where_it_is(self):
        with tempfile.TemporaryDirectory() as td:
            top = Path(td) / "top"
            r = _cli(Path(td), "init", "--country", "canada", "--year",
                     "2025", str(top))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            with open(top / "tobase.map", "a", encoding="utf-8") as fh:
                fh.write("TOBASE ZZQ.US\n")
            n = len((top / "tobase.map").read_text(
                encoding="utf-8").splitlines())
            step = self._format(top / "2025")
            self.assertIn(f"— ../tobase.map:{n}: ", step["detail"])

    def test_tj0250_the_checks_do_not_repeat_the_loader_lines(self):
        # Shown to a person (the loader says each line once per
        # process): the format step's line is the only one.
        root = self.copy("loader")
        (root / "ticker.map").write_text("", encoding="utf-8")
        (root / "tobase.map").write_text("GLOBAL ZZQ.US ZZQ.TO\n",
                                         encoding="utf-8")
        r = _cli(root, "checklist", "--run", width="120")
        self.assertIn("! 19 format: 1 tobase.map problem(s)", r.stdout)
        self.assertNotIn("ticker.map problem:", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
