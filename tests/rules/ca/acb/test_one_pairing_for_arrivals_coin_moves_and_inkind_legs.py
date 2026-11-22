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

Formerly: test_fix_1029_transfers
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from _support.transfer_cases import _qt, _write, _three_accounts, _crypto_move
from taxjson.rules import ids as R
from _style import CapturedWidth

from taxjson.lib.dispatch import python_module_argv
import _paths

REPO_ROOT = _paths.REPO
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


def _filed(root: Path):
    """(gain, open positions) of the project's tax year."""
    s = json.loads(_cli(root, "sum", "--json").stdout)
    held = json.loads(_cli(root, "list", "--json").stdout)
    return (round(s["filing"]["engine_gain_unrounded"], 2),
            sorted((r["account"], r["qty"]) for r in held["rows"]))


class TestOnePairing(unittest.TestCase):
    """TJ-0269 / TJ-0380: the arrival booking and the lot carry read
    one pairing.

    Formerly: test_fix_1029_transfers.TestOnePairing"""

    @rule(R.US_BASIS_05)
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

    @rule(R.CA_ACB_OWN_MOVE)
    def test_canada_figures_are_unchanged(self):
        # Canada always read the arrival stage's pairing: 250, nothing
        # held (the pooled figure this fix must not move).
        with tempfile.TemporaryDirectory() as td:
            root = _three_accounts(Path(td) / "p", "canada")
            r = _cli(root, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertEqual(_filed(root), (250.0, []))


class TestCanadianCryptoMove(unittest.TestCase):
    """TJ-0281 / TJ-0388: a coin moved between two of your taxable
    crypto accounts in a Canadian project stayed in the sender's books
    (the ACB is one pool across them, s.47, so nothing was carried):
    `list` showed the sender still holding it and the receiver's sale
    short ("missing history?"), as shares were before TJ-0093. The
    accounts' own books now carry the move as unit legs, as Canada's
    share accounts' do; the pooled figures do not change.

    Formerly: test_fix_1029_transfers.TestCanadianCryptoMove"""

    @rule(R.CA_ACB_OWN_MOVE)
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
    flipped qb's arrival to its stated book value.

    Formerly: test_fix_1029_transfers.TestInKindLegIsNoOwnMove"""

    @rule(R.US_BASIS_05, R.US_BASIS_TRANSFER_BV)
    def test_us_the_arrival_is_booked_at_its_book_value(self):
        # The contribution is not booked (cash only), qa keeps its 100;
        # qb's 90 at their stated 270, sold for 270: gain 0 (was 90:
        # qa's lots).
        self._check("usa", 0.0)

    @rule(R.CA_ACB_TRANSFER_BV, R.CA_ACB_OWN_MOVE)
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
