"""Backlog fix round 7 (wave E) of 2026-10-30, group `fetchforms`:
taxjson-export, the report tools and carryover reading wrong-shape or
unusual files.

TJ-1196  taxjson-sum-gains and the option reports refuse an `inventory`
         or `manual_reporting_required` that is not a list, in one line.
TJ-1232  An input at quantity 0 holds nothing: a blended holding beside
         it keeps its cost.
TJ-1233, TJ-1234  The trade history reads a clock as the engine's loader
         writes it: no traceback on one that is no text, and '9:30:00'
         is the same moment as '09:30:00'.
TJ-1195  A partial claim keeps what it leaves over sub-cent rows: the
         ledger absorbs only the real gap to the filed loss, on its side.

Every fixture is SYNTHETIC: invented tickers, amounts and account ids.
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.bin import taxjson_export as export
from taxjson.bin.taxjson_carryover import build_canada_ledger, yearly_nets
from taxjson.lib.dispatch import python_module_argv
from taxjson.lib.tomlcompat import tomllib

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"


def _env():
    return dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_WIDTH="0",
                TAXJSON_OFFLINE="1")


def _module(mod, *args, stdin=None):
    return subprocess.run(
        python_module_argv(mod) + [str(a) for a in args],
        capture_output=True, text=True, env=_env(), input=stdin,
        stdin=None if stdin is not None else subprocess.DEVNULL, timeout=300)


class TestWrongShapeRowLists(unittest.TestCase):
    """TJ-1196: "inventory": 5 or "manual_reporting_required": 5 was a
    TypeError traceback from taxjson-sum-gains FILE and the option
    reports."""

    TOOLS = ("taxjson.bin.taxjson_sum_gains",
             "taxjson.bin.taxjson_leaps_gains",
             "taxjson.bin.taxjson_ccd_gains")

    def _check_refused(self, r, key):
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn(f'"{key}" must be a list', r.stderr)
        self.assertEqual(len(r.stderr.strip().splitlines()), 1, r.stderr)

    def test_a_list_field_of_another_shape_is_refused(self):
        for tool in self.TOOLS:
            for key in ("inventory", "manual_reporting_required"):
                for bad in (5, True, "abc", {"symbol": "SYN.TO"}, [5]):
                    doc = json.dumps({"transactions": [], key: bad,
                                      "summary": {"year": 2025}})
                    with self.subTest(tool=tool, key=key, bad=bad), \
                            tempfile.TemporaryDirectory() as d:
                        p = Path(d) / "g.json"
                        p.write_text(doc)
                        self._check_refused(_module(tool, p), key)
                        self._check_refused(_module(tool, stdin=doc), key)

    def test_an_absent_or_empty_field_still_reads(self):
        for tool in self.TOOLS:
            for extra in ({}, {"inventory": None}, {"inventory": []},
                          {"manual_reporting_required": None},
                          {"manual_reporting_required": []},
                          {"inventory": [{"symbol": "SYN.TO", "qty": 1,
                                          "total_cost": 1.0}]}):
                doc = json.dumps({"transactions": [],
                                  "summary": {"year": 2025}, **extra})
                with self.subTest(tool=tool, extra=extra), \
                        tempfile.TemporaryDirectory() as d:
                    p = Path(d) / "g.json"
                    p.write_text(doc)
                    r = _module(tool, p)
                    self.assertEqual(r.returncode, 0, r.stderr)
                    r = _module(tool, stdin=doc)
                    self.assertEqual(r.returncode, 0, r.stderr)


def _export(files, *args):
    """taxjson-export with `files` ({name: text}) in a folder of their
    own; `args` name them as {name}. (CompletedProcess, parsed snapshot
    or None)."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for name, text in files.items():
            (root / name).write_text(text)
        argv = [str(root / a[1:-1]) if a.startswith("{") else a
                for a in args]
        r = _module("taxjson.bin.taxjson_export", *argv)
    return r, (tomllib.loads(r.stdout) if r.returncode == 0 else None)


def _toml(*holdings, account="acct1"):
    return (f'schema_version = "1.2"\n[meta]\naccount = "{account}"\n'
            f'generated_at = 2026-01-02T15:00:00Z\n'
            f'holdings_count = {len(holdings)}\n'
            + "".join(f"[[holding]]\n{h}" for h in holdings))


_BLENDED = ('symbol = "ZZQ.TO"\naccount = "acct1"\nquantity = 4.0\n'
            'currency = "CAD"\ntotal_cost = 80.0\ncost_per_share = 20.0\n'
            'cost_source = "blended"\nbase_currency = "CAD"\n'
            'base_total_cost = 80.0\nposition_start_date = 2024-03-01\n')
_NIL = ('symbol = "ZZQ.TO"\naccount = "acct2"\nquantity = 0.0\n'
        'currency = "CAD"\ntotal_cost = 0.0\n')


class TestBlendedBesideAnEmptyInput(unittest.TestCase):
    """TJ-1232: an input naming the holding at quantity 0 holds nothing,
    yet it counted as a second holder, and TJ-1071 then wrote the known
    blended cost as cost_unknown."""

    def _kept(self, r, doc):
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("more than one input", r.stderr)
        h = [x for x in doc["holding"] if x["symbol"] == "ZZQ.TO"][0]
        self.assertEqual(
            (h["quantity"], h["total_cost"], h["cost_source"],
             h["base_total_cost"]), (4.0, 80.0, "blended", 80.0))
        self.assertNotIn("cost_unknown", h)

    def test_a_snapshot_at_quantity_zero_either_order(self):
        for names in (("{a.toml}", "{b.toml}"), ("{b.toml}", "{a.toml}")):
            with self.subTest(names=names):
                self._kept(*_export(
                    {"a.toml": _toml(_BLENDED),
                     "b.toml": _toml(_NIL, account="acct2")},
                    "--holdings-toml", "--base-currency", "CAD", *names))

    def test_a_gains_file_at_quantity_zero(self):
        gains = json.dumps({"inventory": [
            {"symbol": "ZZQ.TO", "qty": 0, "total_cost": 0.0,
             "currency": "CAD"}]})
        self._kept(*_export(
            {"a.toml": _toml(_BLENDED), "g.json": gains},
            "--holdings-toml", "--base-currency", "CAD", "{a.toml}",
            "{g.json}"))

    def test_a_real_second_holder_still_leaves_it_out(self):
        for qty in ("3.0", "-3.0"):
            with self.subTest(qty=qty):
                r, doc = _export(
                    {"a.toml": _toml(_BLENDED),
                     "b.toml": _toml(_NIL.replace("0.0\ncurrency",
                                                  qty + "\ncurrency"),
                                     account="acct2")},
                    "--holdings-toml", "--base-currency", "CAD", "{a.toml}",
                    "{b.toml}")
                self.assertEqual(r.returncode, 0, r.stderr)
                h = doc["holding"][0]
                self.assertTrue(h["cost_unknown"])
                self.assertNotIn("total_cost", h)
                self.assertIn("more than one input holds it", r.stderr)

    def test_a_blended_row_at_quantity_zero_still_counts(self):
        # Its figure describes no units here: it does not become the
        # figure of the units another input holds.
        nil_blended = _BLENDED.replace("quantity = 4.0", "quantity = 0.0")
        plain = ('symbol = "ZZQ.TO"\nquantity = 4.0\ncurrency = "CAD"\n'
                 'total_cost = 40.0\n')
        r, doc = _export(
            {"a.toml": _toml(nil_blended),
             "b.toml": _toml(plain, account="acct2")},
            "--holdings-toml", "--base-currency", "CAD", "{a.toml}",
            "{b.toml}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(doc["holding"][0]["cost_unknown"])


def _row(day, qty, price=0.0, t="10:00:00", settle=None, action="BUYSELL"):
    return {"date": day, "date_settle": settle or day, "time": t,
            "action": action, "symbol": "SYN.TO", "quantity": qty,
            "price": price, "currency": "CAD", "account": "acct1"}


def _events(rows, country="canada"):
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "raw.json"
        p.write_text(json.dumps({"transactions": rows}))
        return export._load_trade_events([p], country=country)


class TestTradeHistoryClocks(unittest.TestCase):
    """TJ-1233: a clock that is not text on a split's day was a TypeError
    traceback. TJ-1234: '9:30:00' sorted after '20:25:00', so the split
    read as not after the execution and the closed round stayed charted.
    The engine's loader writes every clock HH:MM:SS first."""

    @staticmethod
    def _book(clock):
        return [_row("2025-01-10", 10, 10.0, settle="2025-01-13"),
                _row("2025-06-02", 10, 10.0, t=clock, settle="2025-06-03"),
                _row("2025-06-02", 2, t="20:25:00", action="SPLIT"),
                _row("2025-06-05", -40, 6.0),
                _row("2025-06-09", 1, 6.0)]

    def test_an_unpadded_clock_is_the_padded_one(self):
        for clock in ("15:00:00", "09:30:00", "9:30:00", "9:30", "09:30",
                      " 9:30:00 ", None):
            with self.subTest(clock=clock):
                self.assertEqual(
                    _events(self._book(clock))["SYN.TO"],
                    [{"date": "2025-06-09", "action": "BUY", "qty": 1.0,
                      "price": 6.0}])

    def test_a_split_before_the_execution_is_no_straddle(self):
        # The split itself at 9:30, before the 15:00 execution: outside
        # the lag in either spelling, so the 10 stay as executed.
        def book(clock):
            rows = self._book("15:00:00")
            rows[2]["time"] = clock
            return rows[:3]
        self.assertEqual(_events(book("9:30:00")), _events(book("09:30:00")))
        self.assertEqual(
            [e["qty"] for e in _events(book("9:30:00"))["SYN.TO"]],
            [10.0, 10.0])

    def test_an_unpadded_clock_keeps_the_day_s_order(self):
        # The sale at 9:30 closes the round, in either country: sorted as
        # text, it came after the 10:00 purchase and closed that too.
        rows = [_row("2025-01-10", 10, 10.0),
                _row("2025-06-02", -10, 12.0, t="9:30:00"),
                _row("2025-06-02", 5, 11.0, t="10:00:00")]
        for country in ("canada", "usa"):
            with self.subTest(country=country):
                self.assertEqual(
                    _events(rows, country)["SYN.TO"],
                    [{"date": "2025-06-02", "action": "BUY", "qty": 5.0,
                      "price": 11.0}])

    def test_a_clock_that_is_no_text_is_no_traceback(self):
        for trade, split in ((1500, "20:25:00"), ("15:00:00", 2025),
                             (1500, 2025), (True, None), ([1], {"a": 1}),
                             (None, None)):
            rows = self._book("15:00:00")
            rows[1]["time"], rows[2]["time"] = trade, split
            with self.subTest(trade=trade, split=split), \
                    tempfile.TemporaryDirectory() as d:
                raw = Path(d) / "raw.json"
                raw.write_text(json.dumps({"transactions": rows}))
                gains = Path(d) / "g.json"
                gains.write_text(json.dumps({"inventory": [
                    {"symbol": "SYN.TO", "qty": 1, "total_cost": 6.0,
                     "currency": "CAD"}]}))
                r = _module("taxjson.bin.taxjson_export", "--holdings-toml",
                            "--country", "canada", "--trades", raw, gains)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertNotIn("Traceback", r.stderr)


def _tjs(root, *args):
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run")
        + ["-C", str(root), *args], capture_output=True, text=True,
        env=_env(), stdin=subprocess.DEVNULL, timeout=600)


def _losses(n, first_cost=("10.01", "30.03")):
    """`n` securities, each a 2024 loss of a sub-cent amount: three bought
    at `first_cost`, one at 10.00, one sold at 5."""
    rows = "".join(
        f"BUYSELL 2024-01-02 10:00:00 SYN{i}.TO 3 CAD {first_cost[0]} "
        f"{first_cost[1]} 0\n"
        f"BUYSELL 2024-01-03 10:00:00 SYN{i}.TO 1 CAD 10.00 10.00 0\n"
        f"BUYSELL 2024-03-01 10:00:00 SYN{i}.TO -1 CAD 5 5 0\n"
        for i in range(n))
    return rows + ("BUYSELL 2025-01-02 10:00:00 ZZOTHER.TO 1 CAD 10 10 0\n"
                   "BUYSELL 2025-03-03 10:00:00 ZZOTHER.TO -1 CAD 2010 2010 "
                   "0\n")


def _carry_project(root, tt, claim=None, country="canada", accounts=("margin",)):
    root = Path(root)
    cur = "CAD" if country == "canada" else "USD"
    root.mkdir(parents=True, exist_ok=True)
    tail = f"\n[carryover]\nclaimed = {{2025 = {claim}}}\n" if claim else ""
    (root / "taxjson.toml").write_text(
        f'[settings]\nlocal_timezone = "America/Toronto"\nyear = 2025\n'
        f'country = "{country}"\nbase_currency = "{cur}"\n'
        f'source_currencies = []\noption_grant_timing_since = 2024\n\n'
        + "".join(f'[accounts.{a}]\ntype = "taxable"\n' for a in accounts)
        + tail)
    (root / "ticker.map").write_text("")
    for a in accounts:
        (root / "inputs" / a).mkdir(parents=True)
    if isinstance(tt, str):
        tt = (tt,)
    for a, text in zip(accounts, tt):
        (root / "inputs" / a / "trades.tt").write_text(text)
    return root


class TestPartialClaimOverSubCentRows(unittest.TestCase):
    """TJ-1195: ten 2024 losses of 5.00833 each: 50.08 on the ledger,
    50.10 on Schedule 3. A claim of 50.05, below both, kept nothing: the
    slack was 0.005 per sub-cent row whatever the real gap."""

    def _final(self, tt, claim, accounts=("margin",)):
        with tempfile.TemporaryDirectory() as d:
            p = _carry_project(Path(d) / "p", tt, claim, accounts=accounts)
            r = _tjs(p, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr)
            r = _tjs(p, "carryover", "--json")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            return json.loads(r.stdout)

    def test_filed_loss_above_the_ledger(self):
        for claim, left, over in (("50.00", 0.08, None), ("50.05", 0.03, None),
                                  ("50.07", 0.01, None), ("50.08", 0.0, None),
                                  ("50.10", 0.0, None), ("50.15", 0.0, 0.07)):
            with self.subTest(claim=claim):
                doc = self._final(_losses(10), claim)
                self.assertAlmostEqual(doc["final_carryforward"], left,
                                       places=2)
                if over is None:
                    self.assertNotIn("unmatched_claims", doc)
                else:
                    self.assertAlmostEqual(doc["unmatched_claims"], over,
                                           places=2)

    def test_filed_loss_below_the_ledger(self):
        # 5.0025 a row (40.01 for four): 50.03 on the ledger, 50.00 filed.
        # Only the side of the real gap is absorbed.
        tt = _losses(10, ("10.00", "30.00")).replace(
            "1 CAD 10.00 10.00 0", "1 CAD 10.01 10.01 0")
        for claim, left in (("50.00", 0.0), ("49.99", 0.03), ("49.95", 0.07)):
            with self.subTest(claim=claim):
                doc = self._final(tt, claim)
                self.assertAlmostEqual(doc["final_carryforward"], left,
                                       places=2)
                self.assertNotIn("unmatched_claims", doc)

    def test_rows_of_one_security_round_as_one_schedule3_row(self):
        # Two sales of one security: one Schedule 3 row, whose cents are
        # those of its sum. Per sale it would read 10.00; it reads 10.01.
        tt = ("BUYSELL 2024-01-02 10:00:00 SYN.TO 3 CAD 10.00 30.00 0\n"
              "BUYSELL 2024-01-03 10:00:00 SYN.TO 1 CAD 10.01 10.01 0\n"
              "BUYSELL 2024-03-01 10:00:00 SYN.TO -1 CAD 5 5 0\n"
              "BUYSELL 2024-03-04 10:00:00 SYN.TO -1 CAD 5 5 0\n"
              "BUYSELL 2025-01-02 10:00:00 ZZOTHER.TO 1 CAD 10 10 0\n"
              "BUYSELL 2025-03-03 10:00:00 ZZOTHER.TO -1 CAD 2010 2010 0\n")
        doc = self._final(tt, "10.01")
        self.assertAlmostEqual(doc["final_carryforward"], 0.0, places=6)
        self.assertNotIn("unmatched_claims", doc)

    def test_two_accounts_sum_to_one_schedule3_row(self):
        one = ("BUYSELL 2024-01-02 10:00:00 SYN.TO 3 CAD 10.00 30.00 0\n"
               "BUYSELL 2024-01-03 10:00:00 SYN.TO 1 CAD 10.01 10.01 0\n"
               "BUYSELL 2024-03-01 10:00:00 SYN.TO -1 CAD 5 5 0\n")
        two = one.replace("2024-03-01", "2024-03-04") + (
            "BUYSELL 2025-01-02 10:00:00 ZZOTHER.TO 1 CAD 10 10 0\n"
            "BUYSELL 2025-03-03 10:00:00 ZZOTHER.TO -1 CAD 2010 2010 0\n")
        doc = self._final((one, two), "9.00", accounts=("margin", "cash"))
        self.assertGreater(doc["final_carryforward"], 0.99)
        self.assertLess(doc["final_carryforward"], 1.02)

    def test_the_claim_of_the_form_s_total_leaves_nothing(self):
        # The figure the 2024 form files, whatever the rounding.
        with tempfile.TemporaryDirectory() as d:
            p = _carry_project(Path(d) / "p", _losses(10))
            cfg = p / "taxjson.toml"
            cfg.write_text(cfg.read_text().replace("year = 2025", "year = 2024"))
            self.assertEqual(_tjs(p, "run", "--no-input").returncode, 0)
            form = json.loads(_tjs(p, "form-export", "--json").stdout)
        filed = -form["totals"]["gain_all"]
        self.assertAlmostEqual(filed, 50.10, places=2)
        doc = self._final(_losses(10), f"{filed:.2f}")
        self.assertEqual(doc["final_carryforward"], 0.0)
        self.assertNotIn("unmatched_claims", doc)

    def test_the_ledger_without_a_gap_keeps_the_bound(self):
        # A year from another source (a lock, a return row) has no gap.
        led = build_canada_ledger(
            {2024: {"net": -100.0, "dispositions": 10},
             2025: {"net": 200.0, "dispositions": 1}}, {2025: 99.98})
        self.assertEqual(led["final_carryforward"], 0.0)

    def test_a_us_ledger_has_no_gap(self):
        nets = yearly_nets({"transactions": [
            {"action": "BUYSELL", "symbol": "SYN.US", "qty": 1,
             "gain": -5.00833, "date": "2024-03-01",
             "date_settle": "2024-03-04"}]}, "settle", s1256=True)
        self.assertNotIn("filed_gap", nets[2024])

    def test_the_gap_is_the_rounding_of_each_row(self):
        rows = [{"action": "BUYSELL", "symbol": s, "qty": 1, "gain": g,
                 "date": "2024-08-01", "date_settle": "2024-08-02"}
                for s, g in (("A.TO", -5.004), ("A.TO", -5.004),
                             ("B.TO", -1.006), ("C.TO", 2.0))]
        rec = yearly_nets({"transactions": rows}, "settle",
                          filed_gap=True)[2024]
        # A: -10.008 -> -10.01; B: -1.006 -> -1.01; C: 2.00.
        self.assertAlmostEqual(rec["net"], -9.014, places=6)
        self.assertAlmostEqual(rec["filed_gap"], -0.006, places=6)


if __name__ == "__main__":
    unittest.main()
