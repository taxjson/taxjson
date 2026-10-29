"""Fix round 2026-10-29, group transfers: moves of your own between two
taxable accounts, and the views of them.

TJ-0269 (the worktree audit's TJ-0380): a US project paired its
          transfer rows twice. The arrival stage (lib/transfer_in.
          arrivals: the same quantity, then the closest date) decided
          which transfer-ins came from outside the books; the lot carry
          (_custody_pairing: the first in leg of the same quantity in
          file order) decided which account received the sender's lots.
          With two candidates they chose different partners: qa's lots
          went to qb, whose own arrival was booked at its book value as
          well (held twice), and qc, the arrival's real partner, sold
          shares it never held (a short). One pairing now: the run's
          own-move plan (own_move_plan, the arrival stage's call) is
          what the lot carry reads.

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

from taxjson.lib.dispatch import python_module_argv

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
_WIDTH = CapturedWidth()


def setUpModule():
    os.environ["TAXJSON_LOCAL_TZ"] = "America/Toronto"
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


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


def _qt(acct_no: str, cur: str, *rows) -> str:
    """A Questrade activity export: rows of (date, action, description,
    quantity, price, net amount, activity type)."""
    out = _QH
    for d, act, desc, q, px, net, kind in rows:
        out += (f"{d},{d},{act},ZZQ,{desc},{q},{px:.2f},{net:.2f},0.00,"
                f"{net:.2f},{cur},{kind},{acct_no},Margin\n")
    return out


def _write(root: Path, country: str, accounts: dict) -> Path:
    """A project: `accounts` {name: export text}, each a taxable
    account."""
    root.mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        settings_for(country, year=2024, source_currencies=[])
        + "".join(f'[accounts.{n}]\ntype = "taxable"\n' for n in accounts))
    for n, text in accounts.items():
        (root / "inputs" / n).mkdir(parents=True)
        (root / "inputs" / n / "questrade_2024.csv").write_text(text)
    return root


def _filed(root: Path):
    """(gain, open positions) of the project's tax year."""
    s = json.loads(_cli(root, "sum", "--json").stdout)
    held = json.loads(_cli(root, "list", "--json").stdout)
    return (round(s["filing"]["engine_gain_unrounded"], 2),
            sorted((r["account"], r["qty"]) for r in held["rows"]))


def _three_accounts(root: Path, country: str) -> Path:
    """The audit's case (TJ-0380): qa buys 100 ZZQ at 2.00 and sends
    them out on 2024-03-01; qc receives 100 on 03-02 (the closest
    date: the move's other leg) and sells them at 4.00; qb receives 100
    on 03-08 from outside the books (TRANSFER BOOK VALUE 250.00) and
    sells them at 3.00. Gains: qc 400 - 200, qb 300 - 250: 250."""
    cur = "CAD" if country == "canada" else "USD"
    return _write(root, country, {
        "qa": _qt("99900001", cur,
                  ("2024-01-10", "Buy", "ZZQ CORP", 100, 2.0, -200.0,
                   "Trades"),
                  ("2024-03-01", "TF6", "ZZQ CORP TRANSFER OUT", -100,
                   0.0, 0.0, "Transfers")),
        "qb": _qt("99900002", cur,
                  ("2024-03-08", "TF6",
                   "ZZQ CORP TRANSFER BOOK VALUE 250.00", 100, 0.0, 0.0,
                   "Transfers"),
                  ("2024-06-03", "Sell", "ZZQ CORP", -100, 3.0, 300.0,
                   "Trades")),
        "qc": _qt("99900003", cur,
                  ("2024-03-02", "TF6",
                   "ZZQ CORP TRANSFER BOOK VALUE 250.00", 100, 0.0, 0.0,
                   "Transfers"),
                  ("2024-06-03", "Sell", "ZZQ CORP", -100, 4.0, 400.0,
                   "Trades"))})


class TestOnePairing(unittest.TestCase):
    """TJ-0269 / TJ-0380: the arrival booking and the lot carry read
    one pairing."""

    @rule("US-BASIS-05")
    def test_us_lots_go_where_the_arrival_stage_paired_them(self):
        with tempfile.TemporaryDirectory() as td:
            root = _three_accounts(Path(td) / "p", "usa")
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertIn("own-account move: ZZQ.US 100 qa -> qc", r.stdout)
            self.assertNotIn("qa -> qb", r.stdout)
            self.assertEqual(_filed(root), (250.0, []))
            # The view reads the same pairing.
            j = json.loads(_cli(root, "transfers", "--json").stdout)
            self.assertEqual(sorted((t["account"], t["arrival"])
                                    for t in j["transfers"]
                                    if t["account"] in ("qb", "qc")
                                    and t["where"] == "sidecar"),
                             [("qb", "book value"), ("qc", "own move")])

    @rule("CA-ACB-OWN-MOVE")
    def test_canada_figures_are_unchanged(self):
        # Canada always read the arrival stage's pairing: 250, nothing
        # held (the pooled figure this fix must not move).
        with tempfile.TemporaryDirectory() as td:
            root = _three_accounts(Path(td) / "p", "canada")
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertEqual(_filed(root), (250.0, []))


class TestOwnMoveConsoleLine(unittest.TestCase):
    """TJ-0270: on the run's console the US `Own-account move` Info
    line wrapped onto a second line without `--details` (it was shown
    whole, wrapped, rather than as a message): one line now, its
    detail with `--details` (docs/output-style.md, The run's
    console)."""

    def _project(self, td, in_date):
        return _write(Path(td) / "p", "usa", {
            "qa": _qt("99900001", "USD",
                      ("2024-01-10", "Buy", "ZZQ CORP", 100, 2.0, -200.0,
                       "Trades"),
                      ("2024-03-01", "TF6", "ZZQ CORP TRANSFER OUT", -100,
                       0.0, 0.0, "Transfers")),
            "qb": _qt("99900002", "USD",
                      (in_date, "TF6", "ZZQ CORP TRANSFER IN", 100, 0.0,
                       0.0, "Transfers"))})

    def test_one_line_without_details(self):
        from _style import assert_console
        for in_date in ("2024-03-02", "2024-03-16"):        # a slow move
            with self.subTest(in_date=in_date), \
                    tempfile.TemporaryDirectory() as td:
                root = self._project(td, in_date)
                env = dict(os.environ, PYTHONPATH=str(SRC),
                           TAXJSON_OFFLINE="1", TAXJSON_WIDTH="120")
                run = subprocess.run(
                    python_module_argv("taxjson.bin.taxjson_run",
                                       ["-C", str(root), "run",
                                        "--no-input"]),
                    capture_output=True, text=True, env=env,
                    stdin=subprocess.DEVNULL, timeout=300)
                self.assertEqual(run.returncode, 0, run.stderr[-2000:])
                lines = run.stdout.splitlines()
                at = [i for i, ln in enumerate(lines)
                      if ln.startswith("Info: Own-account move: ZZQ.US 100 "
                                       "qa -> qb")]
                self.assertEqual(len(at), 1, run.stdout)
                nxt = lines[at[0] + 1]
                self.assertTrue(nxt.startswith(("==> ", "Info: ",
                                                "Warning: ")), nxt)
                assert_console(self, run.stdout, width=120)
                detail = subprocess.run(
                    python_module_argv("taxjson.bin.taxjson_run",
                                       ["-C", str(root), "run",
                                        "--no-input", "--details"]),
                    capture_output=True, text=True, env=env,
                    stdin=subprocess.DEVNULL, timeout=300)
                self.assertIn("The lots' basis and purchase dates go to "
                              "qb.", detail.stdout)
                if in_date == "2024-03-16":
                    self.assertIn("NOTOWN 2024-03-16 ZZQ.US 100 qa "
                                  "2024-03-01", detail.stdout)


class TestTransfersView(unittest.TestCase):
    """TJ-0276 / TJ-0389: `tjs transfers` in a US project listed one
    move of your own as four rows (the broker's out and in rows, and the
    books' two lot-move legs as `book` rows); Canada's legs were hidden
    since TJ-0093. The legs are hidden in both countries."""

    def test_one_move_is_its_two_broker_rows(self):
        for country in ("usa", "canada"):
            with self.subTest(country=country), \
                    tempfile.TemporaryDirectory() as td:
                cur = "CAD" if country == "canada" else "USD"
                root = _write(Path(td) / "p", country, {
                    "qa": _qt("99900001", cur,
                              ("2024-01-10", "Buy", "ZZQ CORP", 100, 2.0,
                               -200.0, "Trades"),
                              ("2024-03-01", "TF6", "ZZQ CORP TRANSFER OUT",
                               -100, 0.0, 0.0, "Transfers")),
                    "qb": _qt("99900002", cur,
                              ("2024-03-02", "TF6", "ZZQ CORP TRANSFER IN",
                               100, 0.0, 0.0, "Transfers"))})
                r = _cli(root, "run", "--no-input")
                self.assertEqual(r.returncode, 0, r.stderr[-2000:])
                self.assertTrue((root / "work" / "qb_own_moves.json")
                                .is_file())
                j = json.loads(_cli(root, "transfers", "--json").stdout)
                self.assertEqual(j["count"], 2)
                self.assertEqual(
                    sorted((t["account"], t["where"], t["arrival"])
                           for t in j["transfers"]),
                    [("qa", "sidecar", "-"), ("qb", "sidecar", "own move")])


class TestMergerAfterOwnMove(unittest.TestCase):
    """TJ-0280 / TJ-0364: a merger whose accounts received different
    ratios weighs each account's holdings just before it — after a move
    of your own between them. The Canadian engine dropped the move's
    legs (one ACB pool, s.47) before it weighed, so the weights were
    the holdings without the move: 100 / 100 rather than 50 / 150."""

    def _book(self):
        from tax_rules.dual import tx
        legs = [tx("TRANSFER", "2025-02-01", "ZZOLD.US", q, 0, account=a,
                   currency="CAD", type="lot_move", time="00:00:00",
                   description="own-account move #1: a -> b (not a "
                               "sale: the ACB stays one pool)",
                   id=f"leg-{a}")
                for a, q in (("a", -50), ("b", 50))]
        return ([tx("BUYSELL", "2025-01-02", "ZZOLD.US", 100, 500,
                    account=a, currency="CAD", id=f"buy-{a}")
                 for a in ("a", "b")] + legs
                + [tx("SPLIT", "2025-03-03", "ZZOLD.US", r, 0, account=a,
                      currency="CAD", symbol_new="ZZNEW.US",
                      id=f"split-{a}")
                   for a, r in (("a", 1), ("b", 1.02))]
                + [tx("BUYSELL", "2025-04-01", "ZZNEW.US", -203, 1200,
                      account="b", currency="CAD", id="sale")])

    @rule("CA-CORP-13", "CA-ACB-OWN-MOVE")
    def test_the_weights_see_the_move(self):
        import contextlib
        import io
        from taxjson.lib.pipeline import GainsRequest, run_gains
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            r = run_gains(self._book(), req=GainsRequest(
                country="canada", year=2025, taxable=True,
                phantom_hint=False))
        # 50 x 1 + 150 x 1.02 = 203 shares, the 1000 of cost: the sale
        # of all 203 gains 200, and nothing is left (no phantom short).
        self.assertEqual(
            (round(sum(t.get("gain", 0) for t in r["transactions"]), 6),
             [i for i in r["inventory"] if abs(i["qty"]) > 1e-9]),
            (200.0, []))
        self.assertIn("holdings-weighted ratio 1.015", err.getvalue())


_CB = ("ID,Timestamp,Transaction Type,Asset,Quantity Transacted,"
       "Price Currency,Price at Transaction,Subtotal,"
       "Total (inclusive of fees and/or spread),Fees and/or Spread,"
       "Notes\n")


def _crypto_move(root: Path, country: str) -> Path:
    """The audit's case (TJ-0388): `sender` buys 1 ETH for 100 and
    sends it to `receiver` (another of your Coinbase accounts) on
    2025-07-01; `receiver` sells 0.5 for 100 on 07-10."""
    cur = "CAD" if country == "canada" else "USD"
    root.mkdir(parents=True)
    (root / "taxjson.toml").write_text(
        settings_for(country, year=2025, source_currencies=[])
        + '[accounts.sender]\ntype = "taxable"\ncrypto = true\n'
        + '[accounts.receiver]\ntype = "taxable"\ncrypto = true\n')
    for a, body in (
            ("sender",
             f"x1,2025-05-01 12:00:00 UTC,Buy,ETH,1,{cur},100,100,100,0,"
             f"Bought 1 ETH\n"
             f"x2,2025-07-01 12:00:00 UTC,Send,ETH,1,{cur},150,150,150,0,"
             f"Sent 1 ETH\n"),
            ("receiver",
             f"y1,2025-07-01 13:00:00 UTC,Receive,ETH,1,{cur},150,150,150,"
             f"0,Received 1 ETH\n"
             f"y2,2025-07-10 12:00:00 UTC,Sell,ETH,0.5,{cur},200,100,100,"
             f"0,Sold 0.5 ETH\n")):
        (root / "inputs" / a).mkdir(parents=True)
        (root / "inputs" / a / "coinbase.csv").write_text(_CB + body)
    return root


class TestCanadianCryptoMove(unittest.TestCase):
    """TJ-0281 / TJ-0388: a coin moved between two of your taxable
    crypto accounts in a Canadian project stayed in the sender's books
    (the ACB is one pool across them, s.47, so nothing was carried):
    `list` showed the sender still holding it and the receiver's sale
    short ("missing history?"), as shares were before TJ-0093. The
    accounts' own books now carry the move as unit legs, as Canada's
    share accounts' do; the pooled figures do not change."""

    @rule("CA-ACB-OWN-MOVE")
    def test_the_receiver_holds_the_coins(self):
        with tempfile.TemporaryDirectory() as td:
            root = _crypto_move(Path(td) / "p", "canada")
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            # The pooled figure: 0.5 ETH sold for 100 at half the 100.
            s = json.loads(_cli(root, "sum", "--json").stdout)
            self.assertEqual(round(s["filing"]["engine_gain_unrounded"], 2),
                             50.0)
            held = json.loads(_cli(root, "list", "--json").stdout)
            self.assertEqual(
                [(x["account"], x["qty"], x["missing_history_suspect"])
                 for x in held["rows"]], [("receiver", 0.5, False)])
            self.assertTrue((root / "work" / "sender_own_moves.json")
                            .is_file())
            fm = _cli(root, "find-missing-history", "--json")
            self.assertNotIn("receiver", fm.stdout)
            # No US wording (lots carried) on a Canadian run.
            self.assertNotIn("lots' basis", r.stdout)


class TestRegisteredMoveInACluster(unittest.TestCase):
    """TJ-0362: the netter of moves between your registered accounts
    judged a symbol's transfer cluster whole: a move rrsp -> rrsp2 with
    another transfer of the symbol within 35 days (a contribution in
    kind into the TFSA) did not net to zero, so the move's legs stayed
    custody rows (under transfers_as_acquisitions = true, a disposal
    and an acquisition). Each move in such a cluster now nets on its
    own."""

    def _rows(self):
        from tax_rules.dual import tx
        return [tx("TRANSFER", "2026-03-02", "QZT.TO", -100, 0,
                   account="rrsp", currency="CAD", id="out"),
                tx("TRANSFER", "2026-03-04", "QZT.TO", 100, 0,
                   account="rrsp2", currency="CAD", id="in"),
                tx("TRANSFER", "2026-03-20", "QZT.TO", 50, 2500,
                   account="tfsa", currency="CAD", id="contribution")]

    def test_the_move_nets_and_the_contribution_stays(self):
        from taxjson.lib.pipeline import _net_cross_account_transfers
        netted: list = []
        segs: list = []
        out = _net_cross_account_transfers(self._rows(), netted_out=netted,
                                           near_trade_guard=False,
                                           netted_segments=segs)
        self.assertEqual([t.id for t in out], ["contribution"])
        self.assertEqual(sorted(t.id for t in netted), ["in", "out"])
        self.assertEqual([[t.id for t in s] for s in segs], [["out", "in"]])

    def test_a_guard_still_refuses_the_move(self):
        # A taxable trade of the symbol near the move (the strict
        # policy's guard) still leaves it un-netted.
        from tax_rules.dual import tx
        from taxjson.lib.pipeline import _net_cross_account_transfers
        main = [tx("BUYSELL", "2026-03-10", "QZT.TO", -10, 400,
                   account="margin", currency="CAD")]
        out = _net_cross_account_transfers(self._rows(),
                                           main_transactions=main)
        self.assertEqual(len(out), 3)

    def test_two_moves_of_one_size_pair_by_the_closest_dates(self):
        from tax_rules.dual import tx
        from taxjson.lib.pipeline import _net_cross_account_transfers
        rows = [tx("TRANSFER", d, "QZT.TO", q, 0, account=a,
                   currency="CAD", id=i)
                for d, q, a, i in (
                    ("2026-03-02", -100, "rrsp", "o1"),
                    ("2026-03-03", 100, "rrsp2", "i1"),
                    ("2026-03-20", -100, "rrsp2", "o2"),
                    ("2026-03-21", 100, "tfsa", "i2"),
                    ("2026-03-25", 30, "tfsa", "c"))]
        netted: list = []
        out = _net_cross_account_transfers(rows, netted_out=netted,
                                           near_trade_guard=False)
        self.assertEqual([t.id for t in out], ["c"])
        self.assertEqual(len(netted), 4)


def _in_kind_and_partial(root: Path, country: str,
                         ambiguous: bool = False) -> Path:
    """qa buys 100 ZZQ at 2.00 and sends them on 2024-03-01 to its
    registered plan (the plan's transfer-in the next day: a contribution
    in kind, not booked — US: cash only; offline here: no value); qb
    receives 90 on 03-05 from outside the books (TRANSFER BOOK VALUE
    270.00) and sells them at 3.00."""
    cur = "CAD" if country == "canada" else "USD"
    root = _write(root, country, {
        "qa": _qt("99900001", cur,
                  ("2024-01-10", "Buy", "ZZQ CORP", 100, 2.0, -200.0,
                   "Trades"),
                  ("2024-03-01", "TF6", "ZZQ CORP TRANSFER OUT", -100,
                   0.0, 0.0, "Transfers")),
        "qb": _qt("99900002", cur,
                  ("2024-03-05", "TF6",
                   "ZZQ CORP TRANSFER BOOK VALUE 270.00", 90, 0.0, 0.0,
                   "Transfers"),
                  ("2024-06-03", "Sell", "ZZQ CORP", -90, 3.0, 270.0,
                   "Trades"))})
    with open(root / "taxjson.toml", "a", encoding="utf-8") as f:
        f.write('[accounts.plan]\ntype = "sheltered"\ntransfers = true\n')
    (root / "inputs" / "plan").mkdir()
    (root / "inputs" / "plan" / "questrade_2024.csv").write_text(
        _qt("99900003", cur, ("2024-03-02", "TF6", "ZZQ CORP TRANSFER",
                              100, 0.0, 0.0, "Transfers")))
    if ambiguous:
        # A second plan's transfer-in of the same units: which plan got
        # them is not clear, so the move is not booked.
        with open(root / "taxjson.toml", "a", encoding="utf-8") as f:
            f.write('[accounts.plan2]\ntype = "sheltered"\n'
                    'transfers = true\n')
        (root / "inputs" / "plan2").mkdir()
        (root / "inputs" / "plan2" / "questrade_2024.csv").write_text(
            _qt("99900004", cur, ("2024-03-03", "TF6", "ZZQ CORP TRANSFER",
                                  100, 0.0, 0.0, "Transfers")))
    return root


class TestInKindLegIsNoOwnMove(unittest.TestCase):
    """TJ-0230: a transfer-out that an in-kind move pairs with a
    registered account's transfer-in (booked or not) is never a move of
    your own between two taxable accounts. Only the slower tier knew
    it: within 10 days a transfer-in of another quantity in another
    taxable account took part of it as a move of your own (qb's 90 had
    qa's ACB, US: qa's lots), and booking the in-kind move later
    flipped qb's arrival to its stated book value."""

    @rule("US-BASIS-05", "US-BASIS-TRANSFER-BV")
    def test_us_the_arrival_is_booked_at_its_book_value(self):
        # The contribution is not booked (cash only), qa keeps its 100;
        # qb's 90 at their stated 270, sold for 270: gain 0 (was 90:
        # qa's lots).
        self._check("usa", 0.0)

    @rule("CA-ACB-TRANSFER-BV", "CA-ACB-OWN-MOVE")
    def test_canada_the_arrival_is_booked_at_its_book_value(self):
        # The plan's leg ambiguous, not booked: the pool holds qa's 100
        # at 200 and qb's 90 at 270; 90 sold for 270 at the average
        # 470 / 190: gain 47.37 (was 90: the 90 moved from qa).
        self._check("canada", 47.37)

    def _check(self, country, gain):
        with tempfile.TemporaryDirectory() as td:
            root = _in_kind_and_partial(Path(td) / "p", country,
                                        ambiguous=country == "canada")
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertNotIn("own-account move", r.stdout.lower())
            self.assertNotIn("do not pair", r.stdout)
            held = dict((a, q) for a, q in _filed(root)[1])
            self.assertEqual((_filed(root)[0], held.get("qa"),
                              held.get("qb")), (gain, 100.0, None))
            j = json.loads(_cli(root, "transfers", "--json").stdout)
            self.assertEqual([t["arrival"] for t in j["transfers"]
                              if t["account"] == "qb"], ["book value"])
            f = _cli(root, "find-missing-history", "--write-purchases")
            flat = " ".join(f.stdout.split())
            self.assertNotIn("a move from your account", flat)
            self.assertIn("the run already books it at the stated book "
                          "value", flat)

    def test_the_drafts_name_the_runs_pairing(self):
        # The audit's case (TJ-0380): the run pairs qa's transfer-out
        # with qc's (the closest date) and books qb's at its book value;
        # the drafts said qb's came from qa.
        with tempfile.TemporaryDirectory() as td:
            root = _three_accounts(Path(td) / "p", "canada")
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            f = _cli(root, "find-missing-history", "--write-purchases")
            flat = " ".join(f.stdout.split())
            self.assertIn("[qc] 2024-03-02 100 units — a move from your "
                          "account qa", flat)
            self.assertIn("[qb] 2024-03-08 100 units — the run already "
                          "books it at the stated book value", flat)


if __name__ == "__main__":
    unittest.main()
