"""Backlog TJ-0075 (decided 2026-10-26): a .tt NOTOWN line undoes one
own-move pairing.

The run pairs a transfer-out and a transfer-in of the same security and
quantity in your taxable accounts as one move of your own — within 10
days (exact), or 11 to transfer_in.SLOW_MOVE_DAYS (kept at 45) days
apart when neither leg has another candidate (slow) — so an unrelated
departure and arrival paired too: the arrival took the departure's cost
and nothing let the user undo it. Now

    NOTOWN <date> <symbol> <qty> <from-account> <from-date>

in the receiving account's .tt files undoes that one pairing, exact or
slow, and never makes one: the transfer-in is then an arrival from
outside the books (booked at the broker's stated book value, or kept
out with no cost) and the transfer-out an ordinary one; in a US project
no lots move. Every other leg pairs as without the line. The slow
pairing's Info line names the line to add; a line that undoes no
pairing is a Warning naming it; `tjs check-tt` knows the keyword.
Figures change only when a user writes the line.

Every fixture is synthetic: invented tickers, amounts and account ids.
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from tax_rules.dual import settings_for
from _style import CapturedWidth

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


def _leg(acct, d, q, sym="ZZQ.TO", **kw):
    """One transfer sidecar row as transfer_in.arrivals takes it."""
    return (acct, "questrade",
            dict(action="TRANSFER", symbol=sym, date=d, date_settle=d,
                 time="09:30:00", quantity=q, net_amount=0,
                 currency="CAD", account=acct, **kw))


def _line(text: str, account: str = "qb") -> TI.NotOwn:
    p = TI.parse_not_own_line(text)
    return TI.NotOwn(account=account, date=p["date"], symbol=p["symbol"],
                     quantity=p["quantity"], from_account=p["from_account"],
                     from_date=p["from_date"],
                     where=f"inputs/{account}/x.tt:1", line=p["line"])


def _arrivals(rows, lines=(), **kw):
    slow = []
    found = TI.arrivals(rows, slow=slow, not_own=list(lines), **kw)
    return found, slow


def _shape(found, slow):
    """What a pairing decides, comparable across runs."""
    return ([(a.account, a.key, a.date, a.quantity, a.status, a.departures)
             for a in found],
            [(m.out_account, m.out_date, m.in_account, m.in_date,
              m.quantity) for m in slow])


class TestNotOwnLine(unittest.TestCase):
    """TJ-0075: the line's form, as the .tt readers take it."""

    def test_tj0075_the_line_parses_and_round_trips(self):
        p = TI.parse_not_own_line(
            "NOTOWN 2024-03-16 zzq.to 100 qa 2024-03-01  # not one move")
        self.assertEqual(
            {k: p[k] for k in ("date", "symbol", "quantity", "from_account",
                               "from_date")},
            {"date": "2024-03-16", "symbol": "ZZQ.TO", "quantity": 100.0,
             "from_account": "qa", "from_date": "2024-03-01"})
        self.assertIsNone(TI.parse_not_own_line(
            "BUYSELL 2024-01-10 09:30:00 ZZQ.TO 100 CAD 2.00 200.00"))
        self.assertIsNone(TI.parse_not_own_line("# NOTOWN in a comment"))
        text = TI.not_own_text("2024-03-16", "ZZQ.TO", 1250000.5, "qa",
                               "2024-03-01")
        self.assertEqual(text,
                         "NOTOWN 2024-03-16 ZZQ.TO 1250000.5 qa 2024-03-01")
        self.assertEqual(TI.parse_not_own_line(text)["quantity"], 1250000.5)
        m = TI.SlowMove(key="ZZQ.TO", symbol="ZZQ.TO", quantity=100.0,
                        out_account="qa", out_date="2024-03-01",
                        in_account="qb", in_date="2024-03-16")
        self.assertEqual(m.not_own_line(),
                         "NOTOWN 2024-03-16 ZZQ.TO 100 qa 2024-03-01")

    def test_tj0075_a_malformed_line_is_refused_naming_the_form(self):
        for bad in ("NOTOWN ZZQ.TO 2024-03-16 100 qa 2024-03-01",
                    "NOTOWN 2024-03-16 ZZQ.TO 100 qa",
                    "NOTOWN 2024-03-16 ZZQ.TO -100 qa 2024-03-01",
                    "NOTOWN 2024-03-16 ZZQ.TO 0 qa 2024-03-01",
                    "NOTOWN 2024-03-16 ZZQ.TO 1,5 qa 2024-03-01",
                    "NOTOWN 2024-02-30 ZZQ.TO 100 qa 2024-03-01",
                    "NOTOWN 2024-03-16 ZZQ.TO 100 qa 2024-03-01 extra"):
            with self.assertRaises(ValueError, msg=bad) as cm:
                TI.parse_not_own_line(bad, "inputs/qb/x.tt:3")
            self.assertIn("inputs/qb/x.tt:3", str(cm.exception))
            self.assertIn(TI.NOTOWN_FORM, str(cm.exception))

    def test_tj0075_the_converter_and_check_tt_read_it_as_a_declaration(self):
        from taxjson.bin.taxjson_convert_tt import tt_to_json
        from taxjson.lib import check_tt as CK
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.tt"
            p.write_text("NOTOWN 2024-03-16 ZZQ.TO 100 qa 2024-03-01\n")
            # Was "unknown .tt action 'NOTOWN'": the run stopped.
            self.assertEqual(tt_to_json(p, "qb", equity=True)
                             ["transactions"], [])
            rep = CK.check_file(p, file="x.tt", account="qb", crypto=False,
                                equity=True)
            self.assertEqual((rep.events, rep.rows, rep.problems),
                             (1, 0, []))
            p.write_text("NOTOWN 2024-03-16 ZZQ.TO 100 qa\n")
            rep = CK.check_file(p, file="x.tt", account="qb", crypto=False,
                                equity=True)
            self.assertEqual([(x.severity, x.where) for x in rep.problems],
                             [(CK.ERROR, "x.tt:1")])

    def test_tj0075_check_tt_names_a_line_no_pairing_can_match(self):
        from taxjson.lib import check_tt as CK
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                settings_for("canada", year=2024, source_currencies=[])
                + '[accounts.qb]\ntype = "taxable"\n'
                + '[accounts.tfsa]\ntype = "sheltered"\n')
            for acct, text in (
                    ("qb", "NOTOWN 2024-03-16 ZZQ.TO 100 qa 2024-03-01\n"
                           "NOTOWN 2024-03-16 ZZQ.TO 100 qb 2024-03-10\n"),
                    ("tfsa", "NOTOWN 2024-03-16 ZZQ.TO 100 qb 2024-03-01\n")):
                (root / "inputs" / acct).mkdir(parents=True)
                (root / "inputs" / acct / "x.tt").write_text(text)
            accounts = {"qb": {"type": "taxable"},
                        "tfsa": {"type": "sheltered"}}
            doc = CK.check_project(root, accounts)
            got = sorted((p["severity"], p["where"]) for p in doc["problems"])
            self.assertEqual(got, [(CK.WARNING, "inputs/qb/x.tt:1"),
                                   (CK.WARNING, "inputs/tfsa/x.tt:1")])
            msgs = " ".join(p["message"] for p in doc["problems"])
            self.assertIn("'qa', which taxjson.toml does not declare", msgs)
            self.assertIn("account tfsa is not a taxable account", msgs)
            self.assertEqual(doc["errors"], 0)


class TestNotOwnPairing(unittest.TestCase):
    """TJ-0075: transfer_in.arrivals undoes the one pairing a line
    names, exact or slow, and pairs everything else as before."""

    def test_tj0075_a_slow_pairing_is_undone(self):
        rows = [_leg("qa", "2024-03-01", -100),
                _leg("qb", "2024-03-16", 100, book_value=250)]
        found, slow = _arrivals(rows)
        self.assertEqual((found, len(slow)), ([], 1))
        line = _line(slow[0].not_own_line())
        found, slow = _arrivals(rows, [line])
        self.assertEqual(slow, [])
        # An arrival from outside the books, booked at its book value;
        # the departure the line names is not "maybe its source".
        self.assertEqual([(a.account, a.status, a.book_value, a.departures)
                          for a in found], [("qb", "book_value", 250.0, [])])
        self.assertEqual(len(TI.booked_rows(found)), 1)
        self.assertEqual(line.matched, "slow")
        self.assertEqual(line.legs,
                         (("qa", "ZZQ.TO", "2024-03-01", -100.0),
                          ("qb", "ZZQ.TO", "2024-03-16", 100.0)))
        (msg,) = TI.attention_lines(found, "canada")
        self.assertNotIn("transfer-out of yours", msg)
        # The same as two legs further apart than any pairing.
        far, _ = _arrivals([_leg("qa", "2024-01-15", -100),
                            _leg("qb", "2024-03-16", 100, book_value=250)])
        self.assertEqual(_shape(found, slow), _shape(far, []))

    def test_tj0075_an_exact_pairing_is_undone(self):
        for out_acct in ("qa", "qb"):       # two accounts, or out and back
            rows = [_leg(out_acct, "2024-03-01", -100),
                    _leg("qb", "2024-03-06", 100, book_value=250)]
            self.assertEqual(_arrivals(rows), ([], []))
            line = _line(f"NOTOWN 2024-03-06 ZZQ.TO 100 {out_acct} "
                         f"2024-03-01")
            found, slow = _arrivals(rows, [line])
            self.assertEqual([(a.account, a.status, a.departures)
                              for a in found], [("qb", "book_value", [])])
            self.assertEqual((slow, line.matched), ([], "exact"))

    def test_tj0075_a_line_never_makes_a_pairing(self):
        # Two departures for one arrival: nothing pairs, so the line
        # undoes nothing and changes nothing.
        rows = [_leg("qa", "2024-03-01", -100),
                _leg("qc", "2024-03-03", -100),
                _leg("qb", "2024-03-20", 100, book_value=250)]
        line = _line("NOTOWN 2024-03-20 ZZQ.TO 100 qa 2024-03-01")
        self.assertEqual(_shape(*_arrivals(rows, [line])),
                         _shape(*_arrivals(rows)))
        self.assertEqual(line.matched, "")
        # An exact pairing undone: the departure does not pair with the
        # other arrival in the window instead.
        rows = [_leg("qa", "2024-03-01", -100),
                _leg("qb", "2024-03-03", 100, book_value=250),
                _leg("qc", "2024-03-05", 100, book_value=260)]
        before, _ = _arrivals(rows)
        self.assertEqual([a.account for a in before], ["qc"])
        found, slow = _arrivals(
            rows, [_line("NOTOWN 2024-03-03 ZZQ.TO 100 qa 2024-03-01")])
        self.assertEqual(slow, [])
        self.assertEqual([(a.account, a.status) for a in found],
                         [("qb", "book_value"), ("qc", "book_value")])
        # The departure stays an ordinary one: named on the other
        # arrival's line, never on the one the line names.
        self.assertEqual([a.departures for a in found],
                         [[], [("qa", "2024-03-01", 100.0)]])
        # An exact pairing undone: neither leg becomes a slow pair's.
        rows = [_leg("qa", "2024-03-01", -100),
                _leg("qb", "2024-03-03", 100),
                _leg("qd", "2024-03-25", 100)]
        found, slow = _arrivals(
            rows, [_line("NOTOWN 2024-03-03 ZZQ.TO 100 qa 2024-03-01")])
        self.assertEqual(slow, [])
        self.assertEqual(sorted(a.account for a in found), ["qb", "qd"])

    def test_tj0075_every_other_pairing_is_as_without_the_line(self):
        rows = [_leg("qa", "2024-03-01", -100),
                _leg("qb", "2024-03-16", 100, book_value=250),
                # another move of the same stock, within the window
                _leg("qa", "2024-06-01", -40),
                _leg("qc", "2024-06-03", 40, book_value=90),
                # and a slow one of another stock
                _leg("qc", "2024-04-01", -30, sym="ZZR.TO"),
                _leg("qb", "2024-04-20", 30, sym="ZZR.TO", book_value=60),
                # and an arrival from outside
                _leg("qc", "2024-08-01", 10, sym="ZZS.TO", book_value=20)]
        line = _line("NOTOWN 2024-03-16 ZZQ.TO 100 qa 2024-03-01")
        before = _shape(*_arrivals(rows))
        after = _shape(*_arrivals(rows, [line]))
        self.assertEqual(line.matched, "slow")
        self.assertEqual(
            [x for x in after[0] if x[:3] != ("qb", "ZZQ.TO", "2024-03-16")],
            before[0])
        self.assertEqual(after[1], [x for x in before[1]
                                    if x[2:4] != ("qb", "2024-03-16")])
        self.assertEqual(len(after[1]), 1)          # ZZR still pairs

    def test_tj0075_only_an_exact_or_slow_pairing_is_undone(self):
        # 60 + 40 delivered against one departure of 100: no pairing of
        # the same units, so a line naming one part undoes nothing.
        rows = [_leg("qa", "2024-03-01", -100),
                _leg("qb", "2024-03-03", 60, book_value=150),
                _leg("qb", "2024-03-05", 40, book_value=100)]
        line = _line("NOTOWN 2024-03-03 ZZQ.TO 60 qa 2024-03-01")
        self.assertEqual(_shape(*_arrivals(rows, [line])),
                         _shape(*_arrivals(rows)))
        self.assertEqual(line.matched, "")
        # Another date, units or account: nothing.
        rows = [_leg("qa", "2024-03-01", -100),
                _leg("qb", "2024-03-16", 100, book_value=250)]
        for text in ("NOTOWN 2024-03-17 ZZQ.TO 100 qa 2024-03-01",
                     "NOTOWN 2024-03-16 ZZQ.TO 10 qa 2024-03-01",
                     "NOTOWN 2024-03-16 ZZQ.TO 100 qc 2024-03-01",
                     "NOTOWN 2024-03-16 ZZR.TO 100 qa 2024-03-01"):
            line = _line(text)
            self.assertEqual(_arrivals(rows, [line])[0], [], text)
            self.assertEqual(line.matched, "", text)
        # A line in another account's folder names another arrival.
        line = _line("NOTOWN 2024-03-16 ZZQ.TO 100 qa 2024-03-01",
                     account="qc")
        self.assertEqual(_arrivals(rows, [line])[0], [])
        self.assertEqual(line.matched, "")

    def test_tj0075_the_symbol_may_be_written_as_ticker_map_renames_it(self):
        rows = [_leg("qa", "2024-03-01", -100, sym="ZZOLD.TO"),
                _leg("qb", "2024-03-16", 100, sym="ZZOLD.TO")]

        def key(s):
            return "ZZNEW.TO" if s.upper() == "ZZOLD.TO" else s.upper()
        for sym in ("ZZOLD.TO", "ZZNEW.TO"):
            line = _line(f"NOTOWN 2024-03-16 {sym} 100 qa 2024-03-01")
            found, _ = _arrivals(rows, [line], key=key)
            self.assertEqual([(a.account, a.status) for a in found],
                             [("qb", "no_cost")], sym)
            self.assertEqual(line.matched, "slow", sym)

    @rule("US-BASIS-05")
    def test_tj0075_the_us_custody_pairing_leaves_the_legs_alone(self):
        # The lot carry reads the arrival stage's pairing (backlog
        # TJ-0269), NOTOWN lines and all.
        from taxjson.bin.taxjson_run import _custody_pairing
        accounts = {"qa": {"type": "taxable"}, "qb": {"type": "taxable"}}
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cache = root / "work"
            cache.mkdir()
            for acct, d, q in (("qa", "2024-03-01", -100.0),
                               ("qb", "2024-03-16", 100.0)):
                (cache / f"{acct}_questrade_transfers.json").write_text(
                    json.dumps({
                        "metadata": {"account": acct,
                                     "brokerage": "questrade",
                                     "kind": "transfer_sidecar"},
                        "transactions": [{"action": "TRANSFER",
                                          "symbol": "ZZQ.US",
                                          "quantity": q, "date": d}]}))
            moves, _ = _custody_pairing(root, cache, accounts)
            self.assertEqual([(m["from"], m["to"], m["slow"])
                              for m in moves], [("qa", "qb", 15)])
            (root / "inputs" / "qb").mkdir(parents=True)
            (root / "inputs" / "qb" / "notown.tt").write_text(
                "NOTOWN 2024-03-16 ZZQ.US 100 qa 2024-03-01\n")
            moves, partial = _custody_pairing(root, cache, accounts)
            self.assertEqual((moves, partial), ([], []))


# --------------------------------------------------------------- the run

def _cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(SRC), TAXJSON_OFFLINE="1",
               TAXJSON_WIDTH="0")
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *args]),
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
        timeout=300)


_QH = ("Transaction Date,Settlement Date,Action,Symbol,Description,"
       "Quantity,Price,Gross Amount,Commission,Net Amount,Currency,"
       "Activity Type,Account #,Account Type\n")


def _project(root: Path, country: str, in_date: str,
             notown: str = "") -> Path:
    """Account qa buys 100 ZZQ at 2.00 and transfers them out on
    2024-03-01; account qb receives 100 (TRANSFER BOOK VALUE 250.00) on
    `in_date` and sells them at 3.00 on 2024-06-03. `notown`: qb's
    notown.tt."""
    cur = "CAD" if country == "canada" else "USD"
    root.mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        settings_for(country, year=2024, source_currencies=[])
        + '[accounts.qa]\ntype = "taxable"\n'
        + '[accounts.qb]\ntype = "taxable"\n')
    for acct in ("qa", "qb"):
        (root / "inputs" / acct).mkdir(parents=True)
    (root / "inputs" / "qa" / "questrade_2024.csv").write_text(
        _QH + f"2024-01-10,2024-01-11,Buy,ZZQ,ZZQ CORP,100,2.00,-200.00,"
        f"0.00,-200.00,{cur},Trades,99900001,Margin\n"
        f"2024-03-01,2024-03-01,TF6,ZZQ,ZZQ CORP TRANSFER OUT,-100,0.00,"
        f"0.00,0.00,0.00,{cur},Transfers,99900001,Margin\n")
    (root / "inputs" / "qb" / "questrade_2024.csv").write_text(
        _QH + f"{in_date},{in_date},TF6,ZZQ,ZZQ CORP TRANSFER BOOK VALUE "
        f"250.00,100,0.00,0.00,0.00,0.00,{cur},Transfers,99900002,Margin\n"
        f"2024-06-03,2024-06-04,Sell,ZZQ,ZZQ CORP,-100,3.00,300.00,0.00,"
        f"300.00,{cur},Trades,99900002,Margin\n")
    if notown:
        (root / "inputs" / "qb" / "notown.tt").write_text(notown)
    return root


def _filed(root: Path):
    """(cost, gain) of the project's tax year."""
    s = json.loads(_cli(root, "sum", "--json").stdout)
    accts = s["filing"]["accounts"]
    cost = sum(a.get("acb", a.get("cost", 0.0)) for a in accts)
    return round(cost, 2), round(s["filing"]["engine_gain_unrounded"], 2)


def _arrival(root: Path) -> str:
    j = json.loads(_cli(root, "transfers", "--json").stdout)
    (got,) = [t["arrival"] for t in j["transfers"] if t["quantity"] > 0]
    return got


class TestNotOwnRunCanada(unittest.TestCase):
    """TJ-0075 end to end, Canada: the pooled ACB."""

    @rule("CA-ACB-TRANSFER-BV")
    def test_tj0075_canada_a_notown_line_books_the_arrival_from_outside(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            # Without a line: one move of your own, the purchase's 200;
            # the Info line's detail names the line that undoes it.
            plain = _project(td / "plain", "canada", "2024-03-16")
            r = _cli(plain, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertEqual(_filed(plain), (200.0, 100.0))
            self.assertIn("If they are not one move, add `NOTOWN 2024-03-16 "
                          "ZZQ.TO 100 qa 2024-03-01` to a .tt file in "
                          "inputs/qb/", r.stdout)
            # Two legs 46 days apart pair nothing: the book value 250
            # joins qa's 200 in the pool (225 for the 100 sold).
            far = _project(td / "far", "canada", "2024-04-16")
            self.assertEqual(_cli(far, "run", "--no-input").returncode, 0)
            want = _filed(far)
            self.assertEqual(want, (225.0, 75.0))
            # The slow pairing (15 days) and the exact one (5 days),
            # each undone by its line: as if no pair had been found.
            for name, day in (("slow", "2024-03-16"), ("exact", "2024-03-06")):
                root = _project(
                    td / name, "canada", day,
                    f"NOTOWN {day} ZZQ.TO 100 qa 2024-03-01\n")
                r = _cli(root, "run", "--no-input")
                self.assertEqual(r.returncode, 0, r.stderr[-2000:])
                self.assertEqual(_filed(root), want, name)
                self.assertNotIn("Own-account move", r.stdout)
                self.assertNotIn("undoes no pairing", r.stdout + r.stderr)
                self.assertIn("booked at the ACB the broker states on the "
                              "row", r.stdout + r.stderr)
                self.assertTrue((root / "work" /
                                 "qb_transfer_costs.json").is_file())
                self.assertEqual(_arrival(root), "book value")
            # The drafts read the line too: the run books that arrival
            # (never "a move from your account qa").
            f = _cli(root, "find-missing-history", "--write-purchases")
            flat = " ".join(f.stdout.split())
            self.assertNotIn("a move from your account qa", flat)
            self.assertIn("the run already books it at the stated book "
                          "value", flat)

    @rule("CA-ACB-TRANSFER-BV")
    def test_tj0075_canada_a_line_that_undoes_nothing_is_a_warning(self):
        with tempfile.TemporaryDirectory() as td:
            root = _project(Path(td) / "p", "canada", "2024-03-16",
                            "NOTOWN 2024-03-17 ZZQ.TO 100 qa 2024-03-01\n")
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            out = r.stdout + r.stderr
            self.assertIn("warning: inputs/qb/notown.tt:1: this NOTOWN line "
                          "undoes no pairing, so nothing changed", out)
            self.assertIn("The line: `NOTOWN 2024-03-17 ZZQ.TO 100 qa "
                          "2024-03-01`.", out)
            # The books are as without the line.
            self.assertEqual(_filed(root), (200.0, 100.0))
            self.assertEqual(_arrival(root), "own move")


class TestNotOwnRunUSA(unittest.TestCase):
    """TJ-0075 end to end, US: no lots move for an undone pairing."""

    @rule("US-BASIS-05", "US-BASIS-TRANSFER-BV")
    def test_tj0075_us_a_notown_line_moves_no_lots(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            plain = _project(td / "plain", "usa", "2024-03-16")
            r = _cli(plain, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertEqual(_filed(plain), (200.0, 100.0))
            self.assertIn("go to qb. If they are not one move, add `NOTOWN "
                          "2024-03-16 ZZQ.US 100 qa 2024-03-01` to a .tt "
                          "file in inputs/qb/", r.stdout)
            for name, day in (("slow", "2024-03-16"), ("exact", "2024-03-06")):
                root = _project(
                    td / name, "usa", day,
                    f"NOTOWN {day} ZZQ.US 100 qa 2024-03-01\n")
                r = _cli(root, "run", "--no-input")
                self.assertEqual(r.returncode, 0, r.stderr[-2000:])
                # qb's lot is the arrival at its stated basis 250; qa
                # keeps its own lot.
                self.assertEqual(_filed(root), (250.0, 50.0), name)
                self.assertNotIn("own-account move", r.stdout)
                for acct in ("qa", "qb"):
                    self.assertFalse((root / "work" /
                                      f"{acct}_own_moves.json").exists())
                self.assertEqual(_arrival(root), "book value")


if __name__ == "__main__":
    unittest.main()
